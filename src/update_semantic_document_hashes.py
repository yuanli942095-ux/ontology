from __future__ import annotations

"""计算语义文档SHA-256并回填文档CSV；不修改其他字段。"""

import sys

from semantic_v2_common import DOCUMENT_CSV, DOCUMENT_DIR, load_csv, sha256_file, write_csv


def main() -> int:
    rows = load_csv(DOCUMENT_CSV)
    updated = 0
    missing = 0
    for row in rows:
        file_name = row.get("file_name", "").strip()
        if not file_name or "待填写" in file_name:
            continue
        path = DOCUMENT_DIR / file_name
        if not path.is_file():
            print(f"[缺失] {row.get('document_id', '')}: {path}")
            missing += 1
            continue
        digest = sha256_file(path)
        if row.get("sha256", "").strip().lower() != digest:
            row["sha256"] = digest
            updated += 1
            print(f"[回填] {row.get('document_id', '')}: {digest}")
    write_csv(DOCUMENT_CSV, rows)
    print(f"完成：更新={updated}，缺失={missing}，文件={DOCUMENT_CSV}")
    return 2 if missing else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[回填停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
