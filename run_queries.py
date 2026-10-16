"""
Executa as consultas de queries.sql contra data/lol.db e mostra os resultados
em tabelas no terminal (nao precisa ter o cliente sqlite3 instalado).

Uso:
    python run_queries.py                 # roda tudo
    python run_queries.py --only 3        # roda so a Q3 (o setup da view roda sempre)
    python run_queries.py --db data/lol.db --file queries.sql
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path


def split_statements(text):
    """Separa o arquivo em comandos SQL completos (terminados em ';')."""
    statements, buf = [], []
    for line in text.splitlines(keepends=True):
        buf.append(line)
        chunk = "".join(buf)
        if sqlite3.complete_statement(chunk):
            statements.append(chunk.strip())
            buf = []
    return statements


def strip_comments(sql):
    return re.sub(r"--.*", "", sql).strip()


def show(headers, rows):
    cells = [[("-" if v is None else str(v)) for v in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h)
              for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in cells:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/lol.db")
    parser.add_argument("--file", default=str(Path(__file__).resolve().parent / "queries.sql"),
                        help="Arquivo SQL (padrao: queries.sql ao lado deste script)")
    parser.add_argument("--only", type=int, help="Numero da consulta (ex.: 3 para a Q3)")
    args = parser.parse_args()

    if not Path(args.file).exists():
        sys.exit(f"{args.file} nao encontrado.")
    if not Path(args.db).exists():
        sys.exit(f"{args.db} nao encontrado. Rode build_db.py primeiro.")
    conn = sqlite3.connect(args.db)
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='matches'").fetchone()
    if not has_table:
        sys.exit("Tabela 'matches' nao encontrada. Rode build_db.py primeiro.")

    for stmt in split_statements(Path(args.file).read_text(encoding="utf-8")):
        keyword = strip_comments(stmt).split(None, 1)[0].upper()
        if keyword in ("DROP", "CREATE"):
            conn.execute(stmt)
            continue
        found = re.search(r"^--\s*Q(\d+)\.\s*(.+)$", stmt, flags=re.MULTILINE)
        number = int(found.group(1)) if found else None
        title = f"Q{number}. {found.group(2)}" if found else "Consulta"
        if args.only is not None and number != args.only:
            continue
        cur = conn.execute(stmt)
        print(f"\n== {title} ==")
        show([d[0] for d in cur.description], cur.fetchall())
    conn.close()


if __name__ == "__main__":
    main()
