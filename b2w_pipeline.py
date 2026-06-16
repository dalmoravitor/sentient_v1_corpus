"""
Pipeline de anotação emocional — Corpus B2W → Sentient
Usa claude -p (headless) em vez do SDK — mais estável para batch

Uso:
    python b2w_pipeline.py reviews.csv corpus.jsonl
    python b2w_pipeline.py reviews.csv corpus.jsonl --limit 1000
    python b2w_pipeline.py reviews.csv corpus.jsonl --resume
    python b2w_pipeline.py reviews.csv corpus.jsonl --concurrency 8
"""

import asyncio
import csv
import json
import os
import re
import shutil
import sys
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timezone

SONNET = "claude-sonnet-4-6"
OPUS   = "claude-opus-4-8"
PROMPT_VERSION = "b2w-v1.1"

# ---------------------------------------------------------------------------
# Mapeamento rating → âncora de valência
# ---------------------------------------------------------------------------

RATING_ANCHOR = {
    1: "muito negativo (1 estrela)",
    2: "negativo (2 estrelas)",
    3: "neutro ou misto (3 estrelas)",
    4: "positivo (4 estrelas)",
    5: "muito positivo (5 estrelas)",
}

# ---------------------------------------------------------------------------
# Heurística local — zero custo, puro Python
# ---------------------------------------------------------------------------

COMPLEXITY_PATTERNS = [
    (r'\btá bom\b|\bsem problema\b|\bfui obrigad\w+\b',         "supressão",    0.30),
    (r'\bmas\b.{0,40}\bmas\b|\bporém\b|\bentretanto\b',         "ambivalência", 0.35),
    (r'\bné\b|\bclaro\b|\bóbvio que\b|\bparabéns.*mesmo\b',     "ironia",       0.35),
    (r'\bmesmo\b|\bfica assim\b|\bque jeito\b|\bresignado\b',    "resignação",   0.25),
    (r'\bapesar\b|\bembora\b|\bainda assim\b|\bde toda forma\b', "concessão",    0.25),
]

def heuristic(title: str, text: str, rating: int) -> tuple[float, list[str]]:
    combined = f"{title} {text}".lower()
    score, signals = 0.0, []
    for pattern, label, weight in COMPLEXITY_PATTERNS:
        if re.search(pattern, combined):
            score = min(1.0, score + weight)
            signals.append(label)
    if rating == 3 and len(text) > 150:
        score = min(1.0, score + 0.20)
        signals.append("neutro_longo")
    if rating in (1, 5) and len(text) < 80:
        score = max(0.0, score - 0.15)
    return round(score, 2), signals

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def build_prompt(title: str, text: str, rating: int, category: str) -> str:
    anchor = RATING_ANCHOR.get(rating, "desconhecido")
    cat_ctx = f"Categoria do produto: {category}." if category else ""
    return f"""\
Você é um anotador especialista em linguística afetiva para o projeto Sentient.
Retorne SOMENTE um JSON válido, sem markdown, sem texto antes ou depois.

CONTEXTO:
{cat_ctx}
Avaliação do usuário: {anchor} — use como âncora, mas confie no texto.
Título: "{title}"
Texto: "{text}"

ÂNCORAS VAD:
- Valência    1=extremamente negativo | 5=extremamente positivo
- Arousal     1=calmo, apático        | 5=agitado, intenso
- Dominância  1=vulnerável, impotente | 5=no controle, assertivo

ATENÇÃO:
- Frustração com produto tem dominância BAIXA (usuário se sente impotente)
- "Apesar do X, o Y..." indica emoção mista — emocao_mista: true
- Ironia como "entregou rápido, só demorou 3 semanas" inverte a valência
- Rating 3 com texto longo quase sempre é ambivalente

Responda com este JSON exato (sem nenhum texto fora do JSON):
{{
  "emocao_primaria": "<alegria|tristeza|raiva|frustração|surpresa|decepção|satisfação|nojo|neutro>",
  "emocao_secundaria": "<mesma lista ou null>",
  "valenca":     <1.0-5.0>,
  "arousal":     <1.0-5.0>,
  "dominancia":  <1.0-5.0>,
  "intensidade": <0.0-1.0>,
  "emocao_mista": <true|false>,
  "intencao": "<reclamar|elogiar|alertar|compartilhar|desabafar|indefinido>",
  "registro": "<formal|informal|ironico|agressivo>",
  "confianca": <0.0-1.0>,
  "raciocinio": "<max 50 palavras>"
}}"""

# ---------------------------------------------------------------------------
# Chamada ao claude -p via subprocess — mais estável que o SDK para batch
# ---------------------------------------------------------------------------

# Caminho do executável `claude`. Resolução, em ordem:
#   1. variável de ambiente CLAUDE_BIN, se definida
#   2. `claude` encontrado no PATH (autodetecção — funciona na maioria das máquinas)
#   3. fallback fixo (ajuste se o claude não estiver no PATH)
CLAUDE_BIN = (
    os.environ.get("CLAUDE_BIN")
    or shutil.which("claude")
    or "/home/vitor/.local/bin/claude"
)

async def call_claude(prompt: str, model: str) -> str:
    """Chama claude -p via stdin — mais robusto que passar como argumento."""
    loop = asyncio.get_event_loop()

    def _run():
        result = subprocess.run(
            [CLAUDE_BIN, "-p", "--model", model,
             "--output-format", "text"],
            input=prompt,          # prompt via stdin, não como argumento
            capture_output=True,
            text=True,
            timeout=90,
        )
        if result.returncode != 0:
            # claude às vezes escreve o erro (ex.: "Not logged in") em stdout
            err = (result.stderr or "").strip() or (result.stdout or "").strip()
            err = err[:300] if err else "(sem saída)"
            raise RuntimeError(
                f"claude -p falhou (código {result.returncode}): {err}"
            )
        return result.stdout.strip()

    return await loop.run_in_executor(None, _run)

# ---------------------------------------------------------------------------
# Anotação de um exemplo
# ---------------------------------------------------------------------------

async def annotate(title: str, text: str, rating: int,
                   category: str, model: str) -> dict:
    prompt = build_prompt(title, text, rating, category)
    raw = await call_claude(prompt, model)
    raw = re.sub(r"```json|```", "", raw).strip()
    return json.loads(raw)

# ---------------------------------------------------------------------------
# Roteador
# ---------------------------------------------------------------------------

async def route(row: dict) -> dict | None:
    title    = str(row.get("review_title", "") or "").strip()
    text     = str(row.get("review_text",  "") or "").strip()
    category = str(row.get("site_category_lv1", "") or "").strip()

    try:
        rating = int(float(row.get("overall_rating", 3) or 3))
    except (ValueError, TypeError):
        rating = 3

    if len(text) < 15 or len(text.split()) < 4:
        return None

    score, signals = heuristic(title, text, rating)
    model = OPUS if score >= 0.55 else SONNET
    escalated = model == OPUS

    try:
        ann = await annotate(title, text, rating, category, model)
    except Exception as e:
        return {
            "source_row":    row,
            "error":         str(e),
            "needs_review":  True,
            "model":         model,
            "annotated_at":  datetime.now(timezone.utc).isoformat(),
            "prompt_version": PROMPT_VERSION,
        }

    # Escalada de emergência: Sonnet com confiança muito baixa → tenta Opus
    if not escalated and ann.get("confianca", 1.0) < 0.55:
        try:
            ann = await annotate(title, text, rating, category, OPUS)
            model, escalated = OPUS, True
        except Exception:
            pass

    return {
        "source_row":       row,
        "model":            model,
        "escalated":        escalated,
        "complexity_score": score,
        "signals":          signals,
        "rating_anchor":    rating,
        "annotation":       ann,
        "needs_review":     ann.get("confianca", 0.0) < 0.80,
        "annotated_at":     datetime.now(timezone.utc).isoformat(),
        "prompt_version":   PROMPT_VERSION,
    }

# ---------------------------------------------------------------------------
# Carregamento do CSV com detecção de encoding
# ---------------------------------------------------------------------------

def load_csv(path: str, limit: int = None) -> list[dict]:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            rows = []
            with open(path, encoding=enc, errors="strict") as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    if limit and len(rows) >= limit:
                        break
                    text = str(row.get("review_text", "") or "").strip()
                    if len(text) >= 15 and len(text.split()) >= 4:
                        rows.append(row)
            print(f"Encoding detectado: {enc} | {len(rows)} linhas válidas")
            return rows
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Não foi possível detectar o encoding de {path}")

# ---------------------------------------------------------------------------
# IDs já processados — para --resume
# ---------------------------------------------------------------------------

def load_done_ids(output_path: str) -> set:
    done = set()
    p = Path(output_path)
    if not p.exists():
        return done
    for line in p.read_text(errors="replace").splitlines():
        try:
            obj = json.loads(line)
            src = obj.get("source_row", {})
            uid = str(src.get("reviewer_id", "")) + str(src.get("product_id", ""))
            if uid:
                done.add(uid)
        except Exception:
            pass
    print(f"Retomando — {len(done)} exemplos já processados")
    return done

# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

async def run(input_path: str, output_path: str,
              limit: int = None, resume: bool = False,
              concurrency: int = 5) -> None:

    rows = load_csv(input_path, limit)
    done = load_done_ids(output_path) if resume else set()

    if done:
        rows = [r for r in rows
                if (str(r.get("reviewer_id","")) + str(r.get("product_id","")))
                not in done]
        print(f"Restam {len(rows)} para anotar")

    if not rows:
        print("Nada a fazer.")
        return

    sem = asyncio.Semaphore(concurrency)
    counts = {"ok": 0, "err": 0, "opus": 0, "review": 0, "skip": 0}

    async def bounded(row):
        async with sem:
            return await route(row)

    out = open(output_path, "a", encoding="utf-8")
    total = len(rows)

    try:
        tasks = [bounded(r) for r in rows]
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            result = await coro

            if result is None:
                counts["skip"] += 1
                continue

            if "error" in result:
                counts["err"] += 1
            else:
                counts["ok"] += 1
                if result.get("model") == OPUS:
                    counts["opus"] += 1
                if result.get("needs_review"):
                    counts["review"] += 1

            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            out.flush()

            if i % 25 == 0 or i == total:
                done_n  = counts["ok"] + counts["err"]
                sonnet_n = counts["ok"] - counts["opus"]
                pct = round(i / total * 100)
                print(
                    f"[{pct:3}%] {i}/{total} | "
                    f"Sonnet={sonnet_n} Opus={counts['opus']} "
                    f"Erros={counts['err']} Revisão={counts['review']}"
                )
    finally:
        out.close()

    done_n = counts["ok"] + counts["err"]
    print(f"\n{'─'*55}")
    print(f"Concluído:       {done_n} anotados, {counts['skip']} pulados")
    print(f"Opus usado:      {counts['opus']} ({round(counts['opus']/max(done_n,1)*100)}%)")
    print(f"Fila de revisão: {counts['review']} ({round(counts['review']/max(done_n,1)*100)}%)")
    print(f"Saída:           {output_path}")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline B2W → Sentient")
    parser.add_argument("input",   help="Caminho do CSV de entrada")
    parser.add_argument("output",  help="Caminho do JSONL de saída")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limitar a N linhas (para testes)")
    parser.add_argument("--resume", action="store_true",
                        help="Retomar processamento interrompido")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="Chamadas paralelas (padrão: 5)")
    args = parser.parse_args()

    asyncio.run(run(
        args.input,
        args.output,
        limit=args.limit,
        resume=args.resume,
        concurrency=args.concurrency,
    ))