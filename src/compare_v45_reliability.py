from __future__ import annotations

"""Compare V4.4 vs V4.5 on the 42 handoff INVALID_JSON slots."""

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


V44_DETAILS = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.4-handoff-ir"
    / "auto-policy-v4.4-handoff-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
V45_DETAILS = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.5-reliability-ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
CLASSIFICATION = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.4-invalid-json-recovery"
    / "invalid-json-by-attempt.csv"
)
OUTPUT = PROJECT_DIR / "output" / "auto-policy-v4.5-reliability"


def load_details(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    return {(row["event_id"], row["run"]): row for row in rows}


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in rows if row["selection_status"] == "SELECTED"]
    return {
        "attempts": len(rows),
        "valid_json": sum(row["generation_status"] in {"GENERATED", "INVALID_SCHEMA", "ABSTAIN"} for row in rows),
        "generated": sum(row["generation_status"] == "GENERATED" for row in rows),
        "invalid_json": sum(row["generation_status"] == "INVALID_JSON" for row in rows),
        "ir_ok": sum(row["ir_status"] == "OK" for row in rows),
        "selected": len(selected),
        "oracle_correct": sum(truthy(row["selection_oracle_correct"]) for row in rows),
        "full_closure": sum(truthy(row["full_closure_success"]) for row in rows),
        "precision_given_select": (
            sum(truthy(row["selection_oracle_correct"]) for row in selected) / len(selected) if selected else 0.0
        ),
    }


def main() -> int:
    slots = list(csv.DictReader(CLASSIFICATION.open(encoding="utf-8-sig")))
    keys = {(row["event_id"], row["run"]) for row in slots}
    bucket_by_key = {(row["event_id"], row["run"]): row["failure_bucket"] for row in slots}

    v44 = load_details(V44_DETAILS)
    v45 = load_details(V45_DETAILS) if V45_DETAILS.is_file() else {}

    rows: list[dict[str, Any]] = []
    for key in sorted(keys):
        before = v44.get(key, {})
        after = v45.get(key, {})
        rows.append(
            {
                "event_id": key[0],
                "run": key[1],
                "v4_4_failure_bucket": bucket_by_key.get(key, ""),
                "v4_4_generation_status": before.get("generation_status", ""),
                "v4_5_generation_status": after.get("generation_status", ""),
                "v4_4_ir_status": before.get("ir_status", ""),
                "v4_5_ir_status": after.get("ir_status", ""),
                "v4_4_selection_status": before.get("selection_status", ""),
                "v4_5_selection_status": after.get("selection_status", ""),
                "v4_5_generation_reliability_mode": after.get("generation_reliability_mode", ""),
                "v4_5_full_closure": after.get("full_closure_success", ""),
            }
        )

    before_rows = [v44[key] for key in keys if key in v44]
    after_rows = [v45[key] for key in keys if key in v45]
    summary = {
        "slots": len(keys),
        "v4_4": summarize(before_rows),
        "v4_5": summarize(after_rows) if after_rows else {},
        "bucket_breakdown_v4_5": {
            f"{bucket}|{status}": count
            for (bucket, status), count in Counter(
                (bucket_by_key[key], after.get("generation_status", "MISSING"))
                for key, after in ((k, v45.get(k, {})) for k in keys)
            ).items()
        },
        "rows": rows,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT / "v45-vs-v44-42-slots.csv", rows)
    (OUTPUT / "v45-vs-v44-42-slots.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
