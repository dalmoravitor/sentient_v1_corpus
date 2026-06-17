"""
Limpa o corpus removendo o título do campo `text`.

Versões antigas do pipeline gravavam `text = f"{título} {corpo}"`. Este script
recupera o corpo puro cruzando cada linha do corpus com o reviews.csv (chave =
título+corpo, valor = corpo) e reescreve apenas o corpo no campo `text`.

É seguro re-rodar: linhas que já estão limpas (text == corpo conhecido) são
mantidas como estão. Faz backup automático antes de sobrescrever.

Uso:
    python strip_title.py corpus.jsonl reviews.csv
    python strip_title.py corpus.jsonl reviews.csv --output corpus_clean.jsonl
"""

import csv
import json
import argparse
from pathlib import Path


def load_csv(path: str) -> list[dict]:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            with open(path, encoding=enc, errors="strict") as f:
                rows = list(csv.DictReader(f))
            print(f"Encoding detectado: {enc} | {len(rows)} linhas no CSV")
            return rows
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Não foi possível detectar o encoding de {path}")


def build_lookup(rows: list[dict]) -> tuple[dict, set]:
    """full_text (título+corpo) -> corpo  +  conjunto de corpos válidos."""
    lookup, bodies = {}, set()
    for r in rows:
        title = str(r.get("review_title", "") or "").strip()
        body  = str(r.get("review_text",  "") or "").strip()
        full  = f"{title} {body}".strip() if title else body
        lookup[full] = body
        bodies.add(body)
    return lookup, bodies


def strip_titles(input_path: str, csv_path: str, output_path: str) -> None:
    lookup, bodies = build_lookup(load_csv(csv_path))

    lines = Path(input_path).read_text(errors="replace").splitlines()
    out_lines = []
    stripped = already_clean = unmatched = invalid = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue

        text = obj.get("text", "")
        if text in lookup and lookup[text] != text:
            obj["text"] = lookup[text]   # corpo puro, sem título
            stripped += 1
        elif text in bodies or text in lookup:
            already_clean += 1           # já é o corpo (sem título a remover)
        else:
            unmatched += 1               # não achou origem — preserva intacto

        out_lines.append(json.dumps(obj, ensure_ascii=False))

    Path(output_path).write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    print(f"\n{'─'*50}")
    print(f"Títulos removidos:  {stripped}")
    print(f"Já limpas:          {already_clean}")
    print(f"Sem correspondência:{unmatched}")
    print(f"Inválidas:          {invalid}")
    print(f"Salvo em:           {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remove títulos do campo text do corpus")
    parser.add_argument("input",  help="corpus.jsonl")
    parser.add_argument("csv",    help="reviews.csv (fonte para recuperar o corpo)")
    parser.add_argument("--output", default=None,
                        help="Arquivo de saída (padrão: sobrescreve o original)")
    args = parser.parse_args()

    output = args.output or args.input
    if output == args.input:
        backup = args.input.replace(".jsonl", "_pretitle_backup.jsonl")
        Path(backup).write_bytes(Path(args.input).read_bytes())
        print(f"Backup criado: {backup}")

    strip_titles(args.input, args.csv, output)
