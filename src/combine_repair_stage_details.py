from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from semantic_v2_common import write_csv


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def key(row: dict[str, str]) -> tuple[str, str, str]:
    return row["event_id"], row["run"], row["seed"]


def summarize(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    event_groups: dict[str, list[dict[str, str]]] = {}
    type_groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        event_groups.setdefault(row["event_id"], []).append(row)
        type_groups.setdefault(row["semantic_type"], []).append(row)

    by_event: list[dict[str, Any]] = []
    for event_id, group in sorted(event_groups.items()):
        closure = sum(truth(row.get("full_closure_success")) for row in group)
        by_event.append(
            {
                "event_id": event_id,
                "semantic_type": group[0]["semantic_type"],
                "domain": group[0].get("domain", ""),
                "attempts": len(group),
                "selected": sum(row.get("selection_status") == "SELECTED" for row in group),
                "abstains": sum(row.get("selection_status") == "ABSTAIN" for row in group),
                "oracle_accuracy": sum(truth(row.get("selection_oracle_correct")) for row in group) / len(group),
                "full_closure_accuracy": closure / len(group),
                "strict_event_success": closure == len(group),
            }
        )

    by_type: list[dict[str, Any]] = []
    for semantic_type, group in [("ALL", rows), *sorted(type_groups.items())]:
        events = [row for row in by_event if semantic_type == "ALL" or row["semantic_type"] == semantic_type]
        closure = sum(truth(row.get("full_closure_success")) for row in group)
        oracle = sum(truth(row.get("selection_oracle_correct")) for row in group)
        by_type.append(
            {
                "semantic_type": semantic_type,
                "events": len(events),
                "attempts": len(group),
                "selected": sum(row.get("selection_status") == "SELECTED" for row in group),
                "abstains": sum(row.get("selection_status") == "ABSTAIN" for row in group),
                "oracle_accuracy": oracle / len(group) if group else 0,
                "full_closure_accuracy": closure / len(group) if group else 0,
                "strict_event_successes": sum(truth(row.get("strict_event_success")) for row in events),
                "strict_event_accuracy": sum(truth(row.get("strict_event_success")) for row in events) / len(events) if events else 0,
            }
        )
    return by_event, by_type, [by_type[0]]


def main() -> int:
    parser = argparse.ArgumentParser(description="Combine base repair details with successful recovery rows.")
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--stage", action="append", type=Path, default=[])
    args = parser.parse_args()

    base_rows = read_csv(args.base)
    rows_by_key = {key(row): row for row in base_rows}
    stage_name_by_key: dict[tuple[str, str, str], str] = {}
    for stage_path in args.stage:
        for row in read_csv(stage_path):
            row_key = key(row)
            if truth(row.get("full_closure_success")):
                rows_by_key[row_key] = row
                stage_name_by_key[row_key] = stage_path.parent.parent.name if stage_path.parent.name == "ir" else stage_path.parent.name

    combined: list[dict[str, Any]] = []
    for base_row in base_rows:
        row_key = key(base_row)
        row = dict(rows_by_key[row_key])
        row["combined_source"] = stage_name_by_key.get(row_key, "base")
        combined.append(row)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / f"{args.prefix}-details.csv", combined)
    by_event, by_type, summary = summarize(combined)
    write_csv(args.output_dir / f"{args.prefix}-by-event.csv", by_event)
    write_csv(args.output_dir / f"{args.prefix}-by-type.csv", by_type)
    write_csv(args.output_dir / f"{args.prefix}-summary.csv", summary)
    print(summary[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
