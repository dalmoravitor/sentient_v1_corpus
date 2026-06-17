"""
Migra corpus.jsonl do formato antigo (com source_row + annotation aninhados)
para o formato limpo da Sentient.

Uso:
    python migrate_corpus.py corpus.jsonl
    python migrate_corpus.py corpus.jsonl --output corpus_clean.jsonl
"""

import json
import sys
import argparse
from pathlib import Path

PROMPT_VERSION_MIGRATED = "b2w-v1.1-migrated"

def migrate_line(obj: dict) -> dict | None:
    """Converte uma linha do formato antigo para o formato limpo."""

    # Se já está no novo formato (tem "text" direto), passa direto
    if "text" in obj and "source_row" not in obj:
        return obj

    # Formato antigo: source_row + annotation aninhados
    src = obj.get("source_row", {})
    ann = obj.get("annotation", {})

    # Linha de erro sem anotação — mantém com flag
    if not ann and obj.get("error"):
        text  = str(src.get("review_text",  "") or "").strip()
        return {
            "text":           text,
            "context":        str(src.get("site_category_lv1", "") or "").strip(),
            "rating_anchor":  _safe_rating(src.get("overall_rating")),
            "error":          obj.get("error"),
            "needs_review":   True,
            "model":          obj.get("model", ""),
            "prompt_version": PROMPT_VERSION_MIGRATED,
        }

    if not ann:
        return None  # linha vazia ou sem dados úteis

    text     = str(src.get("review_text",  "") or "").strip()
    category = str(src.get("site_category_lv1", "") or "").strip()
    rating   = _safe_rating(src.get("overall_rating"))

    if not text or len(text.split()) < 4:
        return None

    return {
        # Dado linguístico — apenas o corpo da review (sem o título)
        "text":              text,
        "context":           category,
        "rating_anchor":     rating,

        # Anotação emocional
        "emocao_primaria":   ann.get("emocao_primaria"),
        "emocao_secundaria": ann.get("emocao_secundaria"),
        "valenca":           ann.get("valenca"),
        "arousal":           ann.get("arousal"),
        "dominancia":        ann.get("dominancia"),
        "intensidade":       ann.get("intensidade"),
        "emocao_mista":      ann.get("emocao_mista"),
        "intencao":          ann.get("intencao"),
        "registro":          ann.get("registro"),
        "confianca":         ann.get("confianca"),
        "raciocinio":        ann.get("raciocinio"),

        # Rastreabilidade
        "needs_review":      obj.get("needs_review", ann.get("confianca", 1.0) < 0.80),
        "model":             obj.get("model", ""),
        "prompt_version":    PROMPT_VERSION_MIGRATED,
    }

def _safe_rating(value) -> int:
    try:
        return int(float(value or 3))
    except (ValueError, TypeError):
        return 3

def migrate(input_path: str, output_path: str) -> None:
    lines = Path(input_path).read_text(errors="replace").splitlines()
    total = len(lines)

    ok = skipped = errors = already_clean = 0
    out_lines = []

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            skipped += 1
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"  linha {i}: JSON inválido — {e}")
            errors += 1
            continue

        if "text" in obj and "source_row" not in obj:
            already_clean += 1
            out_lines.append(json.dumps(obj, ensure_ascii=False))
            continue

        migrated = migrate_line(obj)
        if migrated is None:
            skipped += 1
            continue

        out_lines.append(json.dumps(migrated, ensure_ascii=False))
        ok += 1

    Path(output_path).write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    print(f"\n{'─'*50}")
    print(f"Total de linhas:    {total}")
    print(f"Migradas:           {ok}")
    print(f"Já no novo formato: {already_clean}")
    print(f"Puladas/inválidas:  {skipped + errors}")
    print(f"Salvo em:           {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migra corpus para formato limpo")
    parser.add_argument("input",  help="corpus.jsonl original")
    parser.add_argument("--output", default=None,
                        help="Arquivo de saída (padrão: sobrescreve o original)")
    args = parser.parse_args()

    output = args.output or args.input
    if output == args.input:
        # Sobrescrevendo o original — faz backup automático
        backup = args.input.replace(".jsonl", "_backup.jsonl")
        Path(backup).write_bytes(Path(args.input).read_bytes())
        print(f"Backup criado: {backup}")

    migrate(args.input, output)