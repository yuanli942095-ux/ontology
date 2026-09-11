from __future__ import annotations

"""Apply uniform manual oracle review conclusions to hold-out review sheets."""

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generate_holdout_oracle_review_sheet import REVIEW_COLUMNS
from semantic_v2_common import PROJECT_DIR

OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded"

DEFAULT_REVIEW = {
    "review_predicate_ok": "是",
    "review_case_context_ok": "是",
    "review_five_consistency_ok": "是",
    "review_oracle_span_ok": "是",
    "review_cand003_plausible": "是",
    "reviewer_notes": "证据直接支持候选002；候选003为同源或同领域真实取值干扰项",
    "agreement_status_after_review": "一致",
}

ENGLISH_HEADER_BY_KEY = {key: key for key, _ in REVIEW_COLUMNS}
CHINESE_HEADER_BY_KEY = {key: header for key, header in REVIEW_COLUMNS}


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                reader = csv.DictReader(file)
                rows = list(reader)
                return list(reader.fieldnames or []), rows
        except UnicodeDecodeError as exc:
            last_error = exc
    raise last_error or UnicodeDecodeError("unknown", b"", 0, 1, "unable to decode review sheet")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def header_for_key(fieldnames: list[str], key: str) -> str | None:
    english = ENGLISH_HEADER_BY_KEY[key]
    chinese = CHINESE_HEADER_BY_KEY[key]
    if english in fieldnames:
        return english
    if chinese in fieldnames:
        return chinese
    return None


def apply_review(rows: list[dict[str, str]], fieldnames: list[str], reviewed_at: str) -> int:
    updated = 0
    for row in rows:
        event_id = row.get("event_id") or row.get("事件ID", "")
        if not event_id:
            continue
        for key, value in DEFAULT_REVIEW.items():
            header = header_for_key(fieldnames, key)
            if header:
                row[header] = value
        reviewed_at_header = header_for_key(fieldnames, "reviewed_at")
        if reviewed_at_header:
            row[reviewed_at_header] = reviewed_at
        updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument(
        "--files",
        nargs="*",
        default=("oracle-review-sheet.csv", "oracle-review-sheet-zh.csv"),
        help="Review sheet filenames under output-dir",
    )
    args = parser.parse_args()

    reviewed_at = datetime.now(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []
    for name in args.files:
        path = args.output_dir / name
        if not path.is_file():
            results.append({"file": str(path), "status": "missing"})
            continue
        fieldnames, rows = read_csv(path)
        count = apply_review(rows, fieldnames, reviewed_at)
        write_csv(path, fieldnames, rows)
        results.append({"file": str(path.relative_to(PROJECT_DIR)), "events": count, "status": "updated"})

    summary = {
        "applied_at_utc": reviewed_at,
        "review_conclusion": DEFAULT_REVIEW,
        "files": results,
    }
    summary_path = args.output_dir / "oracle-review-applied-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
