from __future__ import annotations

r"""Create failure-case tables from the frozen semantic-v2 final run."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DETAILS = PROJECT_DIR / "output" / "final-main-table-test-r5-seed20260820-details.csv"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output"
DEFAULT_METHODS = (
    "DIRECT_FREE",
    "OPTION_VALUE_ONLY",
    "OPTION_FORMAL_OPERATION",
    "OPTION_FORMAL_POLICY",
)
FOCUS_EVENTS = {"E17", "E27", "E37", "E42", "E45", "E47"}


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def compact_counter(counter: Counter[str]) -> str:
    return "|".join(f"{key}:{count}" for key, count in sorted(counter.items()) if key)


def classify_pattern(
    method: str,
    event_id: str,
    selected_values: set[str],
    abstains: int,
    wrong: int,
) -> str:
    if method == "DIRECT_FREE":
        if abstains and not wrong:
            return "free generation lacks candidate-level mapping; model abstains"
        return "free generation selects/abstains without candidate grounding"
    if method == "OPTION_VALUE_ONLY":
        if len(selected_values) > 1:
            return "value-only options cause unstable numeric choice"
        if abstains:
            return "candidate values alone do not identify applicable rule"
        return "candidate values alone point to a wrong value"
    if method == "OPTION_FORMAL_OPERATION":
        if abstains and wrong:
            return "formal operation is insufficient; mixed wrong choices and abstains"
        if abstains:
            return "formal operation is insufficient; model abstains"
        if len(selected_values) > 1:
            return "formal operation is insufficient; selected value drifts"
        return "formal operation is insufficient; stable wrong rule/value"
    if method == "OPTION_FORMAL_POLICY":
        return "LLM policy interpretation is not fully stable; hard execution is needed"
    return "failure"


def focus_family(event_id: str) -> str:
    if event_id in {"E17", "E42"}:
        return "TEMPORAL_CODE_MAPPING"
    if event_id in {"E27", "E45"}:
        return "GENERAL_RULE_CODE_MAPPING"
    if event_id in {"E37", "E47"}:
        return "CROSS_SENTENCE_CODE_MAPPING"
    return ""


def summarize_failures(rows: list[dict[str, str]], methods: set[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("method", "") in methods:
            grouped[(row.get("method", ""), row.get("event_id", ""))].append(row)

    failures: list[dict[str, Any]] = []
    for (method, event_id), items in sorted(grouped.items()):
        failed = [row for row in items if not parse_bool(row.get("oracle_correct", ""))]
        if not failed:
            continue
        attempts = len(items)
        wrong = sum(row.get("status") == "SELECTED" for row in failed)
        abstains = sum(row.get("status") == "ABSTAIN" for row in failed)
        invalid = sum(str(row.get("status", "")).startswith("REJECTED") for row in failed)
        selected_candidate_counter = Counter(
            row.get("selected_candidate_id", "") for row in failed if row.get("selected_candidate_id", "")
        )
        selected_value_counter = Counter(
            row.get("selected_value", "") for row in failed if row.get("selected_value", "")
        )
        status_counter = Counter(row.get("status", "") for row in failed)
        selected_values = set(selected_value_counter)
        failures.append(
            {
                "method": method,
                "event_id": event_id,
                "semantic_type": items[0].get("semantic_type", ""),
                "focus_event": event_id in FOCUS_EVENTS,
                "focus_family": focus_family(event_id),
                "attempts": attempts,
                "oracle_successes": sum(parse_bool(row.get("oracle_correct", "")) for row in items),
                "failures": len(failed),
                "failure_rate": len(failed) / attempts if attempts else 0.0,
                "wrong_selections": wrong,
                "abstains": abstains,
                "invalid_outputs": invalid,
                "status_counts": compact_counter(status_counter),
                "failed_selected_candidate_counts": compact_counter(selected_candidate_counter),
                "failed_selected_value_counts": compact_counter(selected_value_counter),
                "oracle_candidate_id": items[0].get("oracle_candidate_id", ""),
                "oracle_value": items[0].get("oracle_value", ""),
                "failure_pattern": classify_pattern(method, event_id, selected_values, abstains, wrong),
            }
        )
    return failures


def summarize_by_method(failures: list[dict[str, Any]], rows: list[dict[str, str]], methods: set[str]) -> list[dict[str, Any]]:
    attempts_by_method = Counter(row.get("method", "") for row in rows if row.get("method", "") in methods)
    failures_by_method = Counter()
    failed_events_by_method: dict[str, set[str]] = defaultdict(set)
    focus_failed_events_by_method: dict[str, set[str]] = defaultdict(set)
    wrong_by_method = Counter()
    abstain_by_method = Counter()
    invalid_by_method = Counter()
    for row in failures:
        method = str(row["method"])
        failures_by_method[method] += int(row["failures"])
        failed_events_by_method[method].add(str(row["event_id"]))
        if row["focus_event"]:
            focus_failed_events_by_method[method].add(str(row["event_id"]))
        wrong_by_method[method] += int(row["wrong_selections"])
        abstain_by_method[method] += int(row["abstains"])
        invalid_by_method[method] += int(row["invalid_outputs"])
    result: list[dict[str, Any]] = []
    for method in sorted(methods):
        attempts = attempts_by_method[method]
        failures_count = failures_by_method[method]
        result.append(
            {
                "method": method,
                "attempts": attempts,
                "failed_runs": failures_count,
                "failure_rate": failures_count / attempts if attempts else 0,
                "wrong_selections": wrong_by_method[method],
                "abstains": abstain_by_method[method],
                "invalid_outputs": invalid_by_method[method],
                "failed_events": len(failed_events_by_method[method]),
                "failed_event_ids": "|".join(sorted(failed_events_by_method[method])),
                "focus_failed_events": len(focus_failed_events_by_method[method]),
                "focus_failed_event_ids": "|".join(sorted(focus_failed_events_by_method[method])),
            }
        )
    return result


def detailed_failed_runs(rows: list[dict[str, str]], methods: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("method", "") not in methods:
            continue
        if parse_bool(row.get("oracle_correct", "")):
            continue
        result.append(
            {
                "method": row.get("method", ""),
                "event_id": row.get("event_id", ""),
                "semantic_type": row.get("semantic_type", ""),
                "run": row.get("run", ""),
                "seed": row.get("seed", ""),
                "status": row.get("status", ""),
                "selected_candidate_id": row.get("selected_candidate_id", ""),
                "selected_value": row.get("selected_value", ""),
                "oracle_candidate_id": row.get("oracle_candidate_id", ""),
                "oracle_value": row.get("oracle_value", ""),
                "candidate_order": row.get("candidate_order", ""),
                "reason": row.get("reason", ""),
            }
        )
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, summary: list[dict[str, Any]]) -> None:
    lines = [
        "# Failure Case Analysis",
        "",
        "| Method | Event | Type | Success | Failures | Failure mode | Failed selections | Oracle value |",
        "|---|---:|---|---:|---:|---|---|---:|",
    ]
    for row in summary:
        success = f"{row['oracle_successes']}/{row['attempts']}"
        failed_values = row["failed_selected_value_counts"] or "ABSTAIN"
        lines.append(
            "| {method} | {event_id} | {semantic_type} | {success} | {failures} | "
            "{failure_pattern} | {failed_values} | {oracle_value} |".format(
                success=success,
                failed_values=failed_values,
                **row,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="最终主表错误案例分析")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default="final-failure-cases")
    parser.add_argument("--methods", default=",".join(DEFAULT_METHODS))
    args = parser.parse_args()

    if not args.details.exists():
        raise SystemExit(f"details file not found: {args.details}")
    methods = {item.strip().upper() for item in args.methods.split(",") if item.strip()}
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows = load_rows(args.details)
    summary = summarize_failures(rows, methods)
    failed_runs = detailed_failed_runs(rows, methods)
    method_summary = summarize_by_method(summary, rows, methods)

    summary_csv = args.output_dir / f"{args.prefix}-summary.csv"
    method_summary_csv = args.output_dir / f"{args.prefix}-by-method.csv"
    failed_runs_csv = args.output_dir / f"{args.prefix}-failed-runs.csv"
    markdown_path = args.output_dir / f"{args.prefix}.md"
    json_path = args.output_dir / f"{args.prefix}.json"
    log_path = args.output_dir / f"{args.prefix}.log"

    write_csv(summary_csv, summary)
    write_csv(method_summary_csv, method_summary)
    write_csv(failed_runs_csv, failed_runs)
    write_markdown(markdown_path, summary)
    json_path.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_details": str(args.details),
                "methods": sorted(methods),
                "focus_events": sorted(FOCUS_EVENTS),
                "method_summary": method_summary,
                "summary": summary,
                "failed_runs": failed_runs,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        f"source={args.details}",
        f"summary={summary_csv}",
        f"by_method={method_summary_csv}",
        f"failed_runs={failed_runs_csv}",
        f"markdown={markdown_path}",
        f"json={json_path}",
        "",
    ]
    for row in summary:
        marker = "*" if row["focus_event"] else " "
        lines.append(
            "{marker}[{method}/{event_id}] success={oracle_successes}/{attempts} | "
            "failures={failures} | wrong={wrong_selections} | abstain={abstains} | "
            "failed_values={failed_selected_value_counts}".format(marker=marker, **row)
        )
    lines.append("")
    for row in method_summary:
        lines.append(
            "[{method}] failed_runs={failed_runs}/{attempts} | failed_events={failed_events} | "
            "focus_failed={focus_failed_event_ids}".format(**row)
        )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
