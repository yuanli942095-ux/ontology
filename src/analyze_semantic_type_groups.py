from __future__ import annotations

r"""Summarize final semantic-v2 results by semantic type."""

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DETAILS = PROJECT_DIR / "output" / "final-main-table-test-r5-seed20260820-details.csv"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output"
SEMANTIC_TYPES = (
    "TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE",
)


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def summarize(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row.get("method", ""), row.get("semantic_type", ""))].append(row)

    result: list[dict[str, Any]] = []
    for (method, semantic_type), items in sorted(grouped.items()):
        attempts = len(items)
        events = sorted({row.get("event_id", "") for row in items})
        correct = sum(parse_bool(row.get("oracle_correct", "")) for row in items)
        wrong = sum(
            row.get("status") == "SELECTED"
            and not parse_bool(row.get("oracle_correct", ""))
            for row in items
        )
        abstains = sum(row.get("status") == "ABSTAIN" for row in items)
        invalid = sum(str(row.get("status", "")).startswith("REJECTED") for row in items)

        strict_success = 0
        stable_events = 0
        for event_id in events:
            event_rows = [row for row in items if row.get("event_id", "") == event_id]
            if event_rows and all(parse_bool(row.get("oracle_correct", "")) for row in event_rows):
                strict_success += 1
            selected_ids = {
                row.get("selected_candidate_id", "")
                for row in event_rows
                if row.get("selected_candidate_id", "")
            }
            if len(selected_ids) == 1:
                stable_events += 1

        result.append(
            {
                "method": method,
                "semantic_type": semantic_type,
                "events": len(events),
                "attempts": attempts,
                "oracle_successes": correct,
                "oracle_accuracy": correct / attempts if attempts else 0.0,
                "wrong_selections": wrong,
                "wrong_selection_rate": wrong / attempts if attempts else 0.0,
                "abstains": abstains,
                "abstain_rate": abstains / attempts if attempts else 0.0,
                "invalid_outputs": invalid,
                "strict_event_successes": strict_success,
                "strict_event_accuracy": strict_success / len(events) if events else 0.0,
                "stable_events": stable_events,
                "event_ids": "|".join(events),
            }
        )
    return result


def pivot_accuracy(summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    methods = sorted({row["method"] for row in summary})
    by_key = {(row["method"], row["semantic_type"]): row for row in summary}
    rows: list[dict[str, Any]] = []
    for method in methods:
        row: dict[str, Any] = {"method": method}
        for semantic_type in SEMANTIC_TYPES:
            item = by_key.get((method, semantic_type), {})
            row[f"{semantic_type}_oracle_accuracy"] = item.get("oracle_accuracy", "")
            row[f"{semantic_type}_strict_events"] = (
                f"{item.get('strict_event_successes', '')}/{item.get('events', '')}"
                if item
                else ""
            )
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="按语义类型汇总semantic-v2最终结果")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default="semantic-type-groups")
    args = parser.parse_args()

    if not args.details.exists():
        raise SystemExit(f"details file not found: {args.details}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows = load_rows(args.details)
    summary = summarize(rows)
    pivot = pivot_accuracy(summary)

    summary_csv = args.output_dir / f"{args.prefix}-summary.csv"
    pivot_csv = args.output_dir / f"{args.prefix}-pivot.csv"
    json_path = args.output_dir / f"{args.prefix}.json"
    log_path = args.output_dir / f"{args.prefix}.log"
    write_csv(summary_csv, summary)
    write_csv(pivot_csv, pivot)
    json_path.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_details": str(args.details),
                "summary": summary,
                "pivot": pivot,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        f"source={args.details}",
        f"summary={summary_csv}",
        f"pivot={pivot_csv}",
        f"json={json_path}",
        "",
    ]
    for row in summary:
        lines.append(
            "[{method}/{semantic_type}] Oracle={oracle_accuracy:.2%} | "
            "strict={strict_event_successes}/{events} | wrong={wrong_selection_rate:.2%} | "
            "abstain={abstain_rate:.2%}".format(**row)
        )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
