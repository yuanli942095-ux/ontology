from __future__ import annotations

"""Subdivide INVALID_JSON attempts and attempt offline format-only recovery.

Does not call Qwen. Does not read Oracle/candidates for semantic completion.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from auto_policy_robust_json_recovery import (
    FAILURE_BUCKETS,
    RecoveryResult,
    classify_failure_metadata_only,
    looks_truncated_metadata,
    recover_json_object,
)
from run_auto_formal_policy_batch_v3 import extract_json
from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_DETAILS = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.4-handoff-ir"
    / "auto-policy-v4.4-handoff-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v4.4-invalid-json-recovery"

HANDOFF_EVENT_IDS = {
    "EXT_E002",
    "EXT_E003",
    "EXT_E057",
    "EXT_E058",
    "EXT_E059",
    "EXT_E060",
    "EXT_E061",
    "EXT_E062",
    "EXT_E069",
    "EXT_E070",
    "EXT_E071",
    "EXT_E072",
    "EXT_E073",
    "EXT_E074",
    "EXT_E075",
    "EXT_E076",
    "EXT_E077",
    "EXT_E078",
    "EXT_E079",
    "EXT_E080",
    "EXT_E081",
    "EXT_E082",
    "EXT_E083",
    "EXT_E084",
    "EXT_E085",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def raw_text_from_record(record: dict[str, Any]) -> str | None:
    for key in ("raw_response_text", "response_text"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def legacy_parse_recovered(text: str) -> Any:
    return extract_json(text)


def analyze_row(record: dict[str, Any], detail_row: dict[str, str]) -> dict[str, Any]:
    raw = raw_text_from_record(record)
    classified: RecoveryResult = classify_failure_metadata_only(record)
    legacy_parsed = legacy_parse_recovered(raw) if raw else None
    robust_parsed = classified.parsed
    recovery_lift = bool(robust_parsed and not legacy_parsed)

    return {
        "event_id": detail_row.get("event_id", record.get("event_id", "")),
        "semantic_type": detail_row.get("semantic_type", record.get("semantic_type", "")),
        "domain": detail_row.get("domain", ""),
        "run": detail_row.get("run", record.get("run", "")),
        "seed": detail_row.get("seed", record.get("seed", "")),
        "generation_status": detail_row.get("generation_status", record.get("status", "")),
        "done_reason": str(record.get("done_reason") or ""),
        "eval_count": record.get("eval_count", ""),
        "prompt_eval_count": record.get("prompt_eval_count", ""),
        "validation_reason": record.get("validation_reason", ""),
        "truncated_metadata": looks_truncated_metadata(record),
        "has_raw_response_text": bool(raw),
        "raw_text_chars": len(raw) if raw else 0,
        "failure_bucket": classified.bucket,
        "failure_bucket_label": FAILURE_BUCKETS.get(classified.bucket, classified.bucket),
        "recovery_method": classified.method,
        "salvageable_offline": classified.salvageable,
        "recovery_detail": classified.detail,
        "legacy_parse_ok": isinstance(legacy_parsed, dict),
        "robust_parse_ok": isinstance(robust_parsed, dict),
        "recovery_lift": recovery_lift,
        "raw_output_file": detail_row.get("raw_output_file", ""),
    }


def summarize(rows: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    bucket_counts = Counter(row["failure_bucket"] for row in rows)
    by_type = defaultdict(lambda: Counter())
    by_event = defaultdict(lambda: Counter())
    for row in rows:
        by_type[row["semantic_type"]][row["failure_bucket"]] += 1
        by_event[row["event_id"]][row["failure_bucket"]] += 1

    return {
        "label": label,
        "count": len(rows),
        "has_raw_response_text": sum(1 for row in rows if row["has_raw_response_text"]),
        "truncated_metadata": sum(1 for row in rows if row["truncated_metadata"]),
        "failure_bucket_counts": dict(bucket_counts),
        "failure_bucket_labels": {
            bucket: FAILURE_BUCKETS.get(bucket, bucket) for bucket in bucket_counts
        },
        "robust_parse_ok": sum(1 for row in rows if row["robust_parse_ok"]),
        "recovery_lift_over_legacy": sum(1 for row in rows if row["recovery_lift"]),
        "by_semantic_type": {k: dict(v) for k, v in sorted(by_type.items())},
        "by_event": {k: dict(v) for k, v in sorted(by_event.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--details-csv", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--subset",
        choices=("all", "handoff", "legacy"),
        default="handoff",
        help="handoff=new 42 from public-window re-gen; legacy=old 14; all=56",
    )
    args = parser.parse_args()

    details = list(csv.DictReader(args.details_csv.open(encoding="utf-8-sig")))
    invalid = [row for row in details if row.get("generation_status") == "INVALID_JSON"]
    if args.subset == "handoff":
        invalid = [row for row in invalid if row["event_id"] in HANDOFF_EVENT_IDS]
    elif args.subset == "legacy":
        invalid = [row for row in invalid if row["event_id"] not in HANDOFF_EVENT_IDS]

    rows: list[dict[str, Any]] = []
    for detail_row in invalid:
        record = load_json(PROJECT_DIR / detail_row["raw_output_file"])
        rows.append(analyze_row(record, detail_row))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "invalid-json-by-attempt.csv", rows)
    summary = {
        "details_csv": str(args.details_csv),
        "subset": args.subset,
        "attempts": summarize(rows, label=args.subset),
        "all_invalid_json": summarize(
            [
                analyze_row(
                    load_json(PROJECT_DIR / row["raw_output_file"]),
                    row,
                )
                for row in details
                if row.get("generation_status") == "INVALID_JSON"
            ],
            label="all_56",
        ),
        "handoff_invalid_json": summarize(
            [
                analyze_row(
                    load_json(PROJECT_DIR / row["raw_output_file"]),
                    row,
                )
                for row in details
                if row.get("generation_status") == "INVALID_JSON"
                and row["event_id"] in HANDOFF_EVENT_IDS
            ],
            label="handoff_42",
        ),
        "legacy_invalid_json": summarize(
            [
                analyze_row(
                    load_json(PROJECT_DIR / row["raw_output_file"]),
                    row,
                )
                for row in details
                if row.get("generation_status") == "INVALID_JSON"
                and row["event_id"] not in HANDOFF_EVENT_IDS
            ],
            label="legacy_14",
        ),
        "blocker": (
            "All INVALID_JSON raw records store response=null with no raw_response_text. "
            "Offline format recovery cannot run until raw text is captured."
            if rows and not any(row["has_raw_response_text"] for row in rows)
            else ""
        ),
    }
    (args.output_dir / "invalid-json-recovery-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
