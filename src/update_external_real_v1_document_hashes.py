from __future__ import annotations

"""Backfill SHA-256 values for external-real-v1 source documents.

This script modifies only
`benchmark/external-real-v1/input/external-real-document-template.csv`.
It does not read or write private Oracle files.
"""

import argparse
import sys
from pathlib import Path

from semantic_v2_common import PROJECT_DIR, load_csv, sha256_file, write_csv


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v1"
DOCUMENT_DIR = BENCHMARK_DIR / "documents"
DOCUMENT_CSV = BENCHMARK_DIR / "input" / "external-real-document-template.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="update external-real-v1 document hashes")
    parser.add_argument("--documents", type=Path, default=DOCUMENT_CSV)
    parser.add_argument("--document-dir", type=Path, default=DOCUMENT_DIR)
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="exit 0 when the document table has no data rows",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_csv(args.documents, allow_empty=True)
    if not rows:
        print(f"no document rows: {args.documents}")
        return 0 if args.allow_empty else 2

    updated = 0
    missing = 0
    skipped = 0
    for row in rows:
        document_id = str(row.get("document_id", "")).strip()
        file_name = str(row.get("file_name", "")).strip()
        if not file_name:
            skipped += 1
            continue
        path = args.document_dir / file_name
        if not path.is_file():
            print(f"[missing] {document_id or '-'}: {path}")
            missing += 1
            continue
        digest = sha256_file(path)
        if str(row.get("sha256", "")).strip().lower() != digest:
            row["sha256"] = digest
            updated += 1
            print(f"[updated] {document_id or file_name}: {digest}")

    write_csv(args.documents, rows)
    print(
        "external-real-v1 document hashes: "
        f"updated={updated}, missing={missing}, skipped={skipped}, file={args.documents}"
    )
    return 2 if missing else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[external-real hash update stopped] {type(exc).__name__}: {exc}")
        raise SystemExit(2)
