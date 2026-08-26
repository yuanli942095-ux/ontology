from __future__ import annotations

r"""Summarize candidate-order robustness from semantic-v2 ablation details.

The candidate-information ablation runner already shuffles candidates for each
run and records the resulting OPTION_X -> CAND_XXX mapping. This script turns
those repeated calls into a dedicated order-robustness report without issuing
new model calls.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DETAILS = PROJECT_DIR / "output" / "candidate-information-ablation-details.csv"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output"


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def compact_set(values: set[str]) -> str:
    return "|".join(sorted(item for item in values if item))


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def summarize_by_event(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row.get("method", ""), row.get("event_id", ""))].append(row)

    by_event: list[dict[str, Any]] = []
    for (method, event_id), items in sorted(grouped.items()):
        attempts = len(items)
        status_counts = Counter(row.get("status", "") for row in items)
        selected_rows = [row for row in items if row.get("status") == "SELECTED"]
        selected_candidate_ids = {
            row.get("selected_candidate_id", "") for row in selected_rows
        }
        selected_values = {row.get("selected_value", "") for row in selected_rows}
        selected_option_ids = {row.get("selected_option_id", "") for row in selected_rows}
        candidate_orders = {row.get("candidate_order", "") for row in items}
        oracle_successes = sum(parse_bool(row.get("oracle_correct", "")) for row in items)
        abstains = status_counts.get("ABSTAIN", 0)
        invalid = status_counts.get("INVALID", 0)
        selected = status_counts.get("SELECTED", 0)
        wrong = attempts - oracle_successes - abstains - invalid

        stable_selected_candidate = selected > 0 and len(selected_candidate_ids) == 1
        strict_order_robust = (
            attempts > 0
            and selected == attempts
            and oracle_successes == attempts
            and len(selected_candidate_ids) == 1
        )
        outcome_consistent = oracle_successes in {0, attempts}
        order_sensitive = (
            len(selected_candidate_ids) > 1
            or (selected > 0 and (abstains > 0 or invalid > 0))
            or (0 < oracle_successes < attempts)
        )

        by_event.append(
            {
                "method": method,
                "event_id": event_id,
                "semantic_type": items[0].get("semantic_type", ""),
                "attempts": attempts,
                "distinct_candidate_orders": len(candidate_orders),
                "oracle_successes": oracle_successes,
                "accuracy": oracle_successes / attempts if attempts else 0.0,
                "wrong": wrong,
                "abstains": abstains,
                "invalid": invalid,
                "selected_candidate_ids": compact_set(selected_candidate_ids),
                "selected_values": compact_set(selected_values),
                "selected_option_ids": compact_set(selected_option_ids),
                "stable_selected_candidate": stable_selected_candidate,
                "strict_order_robust": strict_order_robust,
                "outcome_consistent": outcome_consistent,
                "order_sensitive": order_sensitive,
            }
        )
    return by_event


def summarize_methods(by_event: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in by_event:
        grouped[str(row["method"])].append(row)

    summary: list[dict[str, Any]] = []
    for method, items in sorted(grouped.items()):
        events = len(items)
        attempts = sum(int(row["attempts"]) for row in items)
        oracle_successes = sum(int(row["oracle_successes"]) for row in items)
        wrong = sum(int(row["wrong"]) for row in items)
        abstains = sum(int(row["abstains"]) for row in items)
        invalid = sum(int(row["invalid"]) for row in items)
        summary.append(
            {
                "method": method,
                "events": events,
                "attempts": attempts,
                "oracle_successes": oracle_successes,
                "accuracy": oracle_successes / attempts if attempts else 0.0,
                "wrong_rate": wrong / attempts if attempts else 0.0,
                "abstain_rate": abstains / attempts if attempts else 0.0,
                "invalid_rate": invalid / attempts if attempts else 0.0,
                "strict_order_robust_events": sum(
                    bool(row["strict_order_robust"]) for row in items
                ),
                "stable_selected_candidate_events": sum(
                    bool(row["stable_selected_candidate"]) for row in items
                ),
                "outcome_consistent_events": sum(
                    bool(row["outcome_consistent"]) for row in items
                ),
                "order_sensitive_events": sum(bool(row["order_sensitive"]) for row in items),
            }
        )
    return summary


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--details", type=Path, nargs="+", default=[DEFAULT_DETAILS])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default="order-robustness")
    args = parser.parse_args()

    missing = [path for path in args.details if not path.exists()]
    if missing:
        raise SystemExit(f"details file not found: {missing}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for detail_path in args.details:
        rows.extend(load_rows(detail_path))
    if not rows:
        raise SystemExit(f"details file is empty: {args.details}")

    by_event = summarize_by_event(rows)
    summary = summarize_methods(by_event)

    by_event_csv = args.output_dir / f"{args.prefix}-by-event.csv"
    summary_csv = args.output_dir / f"{args.prefix}-summary.csv"
    output_json = args.output_dir / f"{args.prefix}.json"
    log_file = args.output_dir / f"{args.prefix}.log"

    by_event_fields = [
        "method",
        "event_id",
        "semantic_type",
        "attempts",
        "distinct_candidate_orders",
        "oracle_successes",
        "accuracy",
        "wrong",
        "abstains",
        "invalid",
        "selected_candidate_ids",
        "selected_values",
        "selected_option_ids",
        "stable_selected_candidate",
        "strict_order_robust",
        "outcome_consistent",
        "order_sensitive",
    ]
    summary_fields = [
        "method",
        "events",
        "attempts",
        "oracle_successes",
        "accuracy",
        "wrong_rate",
        "abstain_rate",
        "invalid_rate",
        "strict_order_robust_events",
        "stable_selected_candidate_events",
        "outcome_consistent_events",
        "order_sensitive_events",
    ]

    write_csv(by_event_csv, by_event, by_event_fields)
    write_csv(summary_csv, summary, summary_fields)
    output_json.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_details": [str(path) for path in args.details],
                "rows": len(rows),
                "summary": summary,
                "by_event": by_event,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        f"source={','.join(str(path) for path in args.details)}",
        f"rows={len(rows)}",
        f"by_event={by_event_csv}",
        f"summary={summary_csv}",
        f"json={output_json}",
    ]
    for row in summary:
        lines.append(
            "[{method}] strict={strict_order_robust_events}/{events} | "
            "stable={stable_selected_candidate_events}/{events} | "
            "order_sensitive={order_sensitive_events}/{events} | "
            "accuracy={accuracy:.2%} | abstain={abstain_rate:.2%}".format(**row)
        )
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
