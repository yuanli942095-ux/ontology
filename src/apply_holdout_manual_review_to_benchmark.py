from __future__ import annotations

"""Write hold-out manual review conclusions back into benchmark oracle/events."""

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generate_holdout_oracle_review_sheet import REVIEW_COLUMNS
from semantic_v2_common import PROJECT_DIR, write_csv

BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded"
REVIEW_SHEET = OUTPUT / "oracle-review-sheet.csv"

ORACLE_AGREEMENT_STATUS = "AGREED"
EVENT_SUPPORT_STATUS = "SEMANTIC_REVIEW_PASSED"
EVENT_SUPPORT_ADJUDICATION = "holdout_manual_oracle_review_agreed"
ORACLE_ADJUDICATOR = "HOLDOUT_MANUAL_REVIEW"
ORACLE_NOTE = (
    "Manual oracle review completed; five-consistency passed; evidence supports CAND_002; "
    "CAND_003 is a plausible non-oracle distractor from source/domain value pools."
)
EVENT_NOTE = (
    "Manual semantic review passed; predicate/case_context/oracle span agreed; "
    "ready for benchmark freeze."
)

CHINESE_HEADER_BY_KEY = {key: header for key, header in REVIEW_COLUMNS}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def review_header(key: str) -> str:
    return CHINESE_HEADER_BY_KEY[key]


def load_review_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"review sheet not found: {path}")
    return read_csv(path)


def review_is_agreed(row: dict[str, str]) -> bool:
    agreement = row.get(review_header("agreement_status_after_review"), "").strip()
    if agreement not in {"一致", "AGREED"}:
        return False
    expected = {
        review_header("review_predicate_ok"): "是",
        review_header("review_case_context_ok"): "是",
        review_header("review_five_consistency_ok"): "是",
        review_header("review_oracle_span_ok"): "是",
        review_header("review_cand003_plausible"): "是",
    }
    return all(row.get(header, "").strip() == value for header, value in expected.items())


def apply_to_oracle(
    oracle_rows: list[dict[str, str]],
    *,
    reviewed_at: str,
    reviewer_name: str,
) -> tuple[list[dict[str, str]], int]:
    by_event = {row["event_id"]: row for row in oracle_rows}
    updated = 0
    for event_id, row in by_event.items():
        row["agreement_status"] = ORACLE_AGREEMENT_STATUS
        row["adjudicator"] = ORACLE_ADJUDICATOR
        if reviewer_name:
            row["annotator_1"] = reviewer_name
        row["notes"] = f"{ORACLE_NOTE} reviewed_at={reviewed_at}."
        updated += 1
    return oracle_rows, updated


def apply_to_events(event_rows: list[dict[str, str]], *, reviewed_at: str) -> tuple[list[dict[str, str]], int]:
    updated = 0
    for row in event_rows:
        row["support_status"] = EVENT_SUPPORT_STATUS
        row["support_adjudication_method"] = EVENT_SUPPORT_ADJUDICATION
        row["notes"] = f"{EVENT_NOTE} reviewed_at={reviewed_at}."
        updated += 1
    return event_rows, updated


def apply_to_support_adjudication(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    updated = 0
    for row in rows:
        if row.get("manual_review_required", "").strip().lower() in {"true", "1", "yes"}:
            row["manual_review_required"] = "false"
            updated += 1
    return rows, updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--review-sheet", type=Path, default=REVIEW_SHEET)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    args.benchmark_dir = args.benchmark_dir.resolve()
    args.review_sheet = args.review_sheet.resolve()

    reviewed_at = datetime.now(timezone.utc).isoformat()
    review_rows = load_review_rows(args.review_sheet)
    if not review_rows:
        raise SystemExit("review sheet is empty")

    disagreed = [row.get("事件ID") or row.get("event_id", "") for row in review_rows if not review_is_agreed(row)]
    if disagreed:
        raise SystemExit(f"review sheet has {len(disagreed)} non-agreed events; refusing writeback")

    reviewer_name = ""
    for row in review_rows:
        reviewer_name = row.get("复核人", "").strip() or row.get("reviewer_name", "").strip()
        if reviewer_name:
            break

    event_csv = args.benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    oracle_csv = args.benchmark_dir / "private" / "oracle" / "external-real-oracle-template.csv"
    support_csv = args.benchmark_dir / "public" / "retrieval" / "support-adjudication.csv"

    event_rows = read_csv(event_csv)
    oracle_rows = read_csv(oracle_csv)
    support_rows = read_csv(support_csv) if support_csv.is_file() else []

    review_event_ids = {row.get("事件ID") or row.get("event_id", "") for row in review_rows}
    benchmark_event_ids = {row["event_id"] for row in event_rows}
    if review_event_ids != benchmark_event_ids:
        missing = sorted(benchmark_event_ids - review_event_ids)
        extra = sorted(review_event_ids - benchmark_event_ids)
        raise SystemExit(f"review/benchmark event mismatch missing={missing[:5]} extra={extra[:5]}")

    oracle_rows, oracle_updated = apply_to_oracle(
        oracle_rows,
        reviewed_at=reviewed_at,
        reviewer_name=reviewer_name,
    )
    event_rows, event_updated = apply_to_events(event_rows, reviewed_at=reviewed_at)
    support_rows, support_updated = apply_to_support_adjudication(support_rows)

    summary: dict[str, Any] = {
        "benchmark": args.benchmark_dir.name,
        "applied_at_utc": reviewed_at,
        "review_sheet": str(args.review_sheet.relative_to(PROJECT_DIR.resolve())),
        "events_reviewed": len(review_rows),
        "oracle_agreement_status": ORACLE_AGREEMENT_STATUS,
        "event_support_status": EVENT_SUPPORT_STATUS,
        "event_support_adjudication_method": EVENT_SUPPORT_ADJUDICATION,
        "oracle_rows_updated": oracle_updated,
        "event_rows_updated": event_updated,
        "support_rows_updated": support_updated,
        "dry_run": args.dry_run,
    }

    if not args.dry_run:
        write_csv(event_csv, event_rows)
        write_csv(oracle_csv, oracle_rows)
        if support_rows:
            write_csv(support_csv, support_rows)
        args.review_sheet.parent.mkdir(parents=True, exist_ok=True)
        (args.review_sheet.parent / "holdout-manual-review-writeback-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
