from __future__ import annotations

"""Report Qwen runtime/token cost alongside formal-policy construction complexity.

The runtime details come from the 30-test main-table reproduction. The policy
construction cost comes from audit_formal_policy_source_costs.py and is a
complexity proxy, not measured annotation wall time. Runtime minutes and policy
complexity points are reported separately because their units are different.
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DETAILS = PROJECT_DIR / "output" / "final-main-table-test-r5-seed20260820-details.csv"
DEFAULT_POLICY_COSTS = PROJECT_DIR / "output" / "formal-policy-source-cost-test-details.csv"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output"

POLICY_REQUIRED_METHODS = {
    "OPTION_FORMAL_POLICY",
    "OPTION_FORMAL_POLICY_HARD_GATE",
    "TEMPLATE_POLICY_HARD_GATE",
}


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_int(value: object) -> int:
    try:
        return int(float(str(value).strip() or "0"))
    except (TypeError, ValueError):
        return 0


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def policy_cost_by_event(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_id = str(row.get("event_id", "")).strip()
        if not event_id:
            continue
        complexity_points = parse_int(
            row.get("policy_complexity_points", row.get("manual_minutes_estimate", 0))
        )
        result[event_id] = {
            "policy_complexity_points": complexity_points,
            "facts_count": parse_int(row.get("facts_count", 0)),
            "rules_count": parse_int(row.get("rules_count", 0)),
            "conditions_count": parse_int(row.get("conditions_count", 0)),
            "manual_formalization": parse_bool(row.get("manual_formalization", "")),
            "uses_oracle_in_policy": parse_bool(row.get("uses_oracle_in_policy", "")),
            "uses_model_output_in_policy": parse_bool(row.get("uses_model_output_in_policy", "")),
            "contains_forbidden_source_marker": parse_bool(
                row.get("contains_forbidden_source_marker", "")
            ),
        }
    return result


def summarize_runtime(rows: list[dict[str, str]], policy_costs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("method", ""))].append(row)

    result: list[dict[str, Any]] = []
    for method, items in sorted(grouped.items()):
        attempts = len(items)
        events = sorted({str(row.get("event_id", "")) for row in items})
        qwen_calls = sum(parse_bool(row.get("qwen_called", "")) for row in items)
        direct_decisions = attempts - qwen_calls
        runtimes = [parse_int(row.get("runtime_ms", 0)) for row in items]
        qwen_runtimes = [
            parse_int(row.get("runtime_ms", 0))
            for row in items
            if parse_bool(row.get("qwen_called", ""))
        ]
        prompt_counts = [parse_int(row.get("prompt_eval_count", 0)) for row in items]
        eval_counts = [parse_int(row.get("eval_count", 0)) for row in items]
        qwen_prompt_counts = [
            parse_int(row.get("prompt_eval_count", 0))
            for row in items
            if parse_bool(row.get("qwen_called", ""))
        ]
        qwen_eval_counts = [
            parse_int(row.get("eval_count", 0))
            for row in items
            if parse_bool(row.get("qwen_called", ""))
        ]
        selected = sum(row.get("status") == "SELECTED" for row in items)
        correct = sum(parse_bool(row.get("oracle_correct", "")) for row in items)
        wrong = sum(
            row.get("status") == "SELECTED"
            and not parse_bool(row.get("oracle_correct", ""))
            for row in items
        )
        abstains = sum(row.get("status") == "ABSTAIN" for row in items)

        policy_required = method in POLICY_REQUIRED_METHODS
        included_policy_events = [
            event_id for event_id in events if policy_required and event_id in policy_costs
        ]
        total_policy_complexity = sum(
            int(policy_costs[event_id]["policy_complexity_points"])
            for event_id in included_policy_events
        )
        total_policy_facts = sum(
            int(policy_costs[event_id]["facts_count"]) for event_id in included_policy_events
        )
        total_policy_rules = sum(
            int(policy_costs[event_id]["rules_count"]) for event_id in included_policy_events
        )
        total_policy_conditions = sum(
            int(policy_costs[event_id]["conditions_count"]) for event_id in included_policy_events
        )
        policy_marker_events = sum(
            1
            for event_id in included_policy_events
            if policy_costs[event_id]["uses_oracle_in_policy"]
            or policy_costs[event_id]["uses_model_output_in_policy"]
            or policy_costs[event_id]["contains_forbidden_source_marker"]
        )

        total_runtime_ms = sum(runtimes)
        qwen_runtime_minutes = total_runtime_ms / 60000
        total_tokens = sum(prompt_counts) + sum(eval_counts)
        qwen_prompt_total = sum(qwen_prompt_counts)
        qwen_eval_total = sum(qwen_eval_counts)
        result.append(
            {
                "method": method,
                "attempts": attempts,
                "events": len(events),
                "selected": selected,
                "oracle_successes": correct,
                "oracle_accuracy": correct / attempts if attempts else 0,
                "wrong_selection_rate": wrong / attempts if attempts else 0,
                "abstain_rate": abstains / attempts if attempts else 0,
                "qwen_calls": qwen_calls,
                "qwen_call_rate": qwen_calls / attempts if attempts else 0,
                "direct_decisions": direct_decisions,
                "direct_decision_rate": direct_decisions / attempts if attempts else 0,
                "total_runtime_ms": total_runtime_ms,
                "qwen_runtime_minutes": qwen_runtime_minutes,
                "mean_runtime_ms_all_attempts": mean(runtimes) if runtimes else 0,
                "mean_runtime_ms_qwen_calls": mean(qwen_runtimes) if qwen_runtimes else 0,
                "total_prompt_eval_count": sum(prompt_counts),
                "total_eval_count": sum(eval_counts),
                "total_token_count": total_tokens,
                "qwen_prompt_eval_count": qwen_prompt_total,
                "qwen_eval_count": qwen_eval_total,
                "qwen_token_count": qwen_prompt_total + qwen_eval_total,
                "mean_total_tokens_qwen_calls": (
                    mean(
                        prompt + completion
                        for prompt, completion in zip(qwen_prompt_counts, qwen_eval_counts)
                    )
                    if qwen_prompt_counts
                    else 0
                ),
                "formal_policy_required": policy_required,
                "formal_policy_events_counted": len(included_policy_events),
                "formal_policy_complexity_points": total_policy_complexity,
                "formal_policy_complexity_unit": (
                    "heuristic_points_not_measured_minutes" if policy_required else ""
                ),
                "measured_annotation_time": False if policy_required else "",
                "formal_policy_facts_count": total_policy_facts,
                "formal_policy_rules_count": total_policy_rules,
                "formal_policy_conditions_count": total_policy_conditions,
                "formal_policy_forbidden_marker_events": policy_marker_events,
                "policy_complexity_basis": (
                    "policy cost from formal-policy-source-cost-test-details.csv; "
                    "heuristic complexity proxy, not measured annotation wall time"
                )
                if policy_required
                else "not policy-available method; formal-policy construction cost not counted",
            }
        )
    return result


def summarize_by_type(
    rows: list[dict[str, str]],
    policy_costs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("method", "")), str(row.get("semantic_type", "")))].append(row)

    result: list[dict[str, Any]] = []
    for (method, semantic_type), items in sorted(grouped.items()):
        events = sorted({str(row.get("event_id", "")) for row in items})
        policy_required = method in POLICY_REQUIRED_METHODS
        policy_complexity = sum(
            int(policy_costs[event_id]["policy_complexity_points"])
            for event_id in events
            if policy_required and event_id in policy_costs
        )
        qwen_calls = sum(parse_bool(row.get("qwen_called", "")) for row in items)
        runtime_ms = sum(parse_int(row.get("runtime_ms", 0)) for row in items)
        prompt = sum(parse_int(row.get("prompt_eval_count", 0)) for row in items)
        completion = sum(parse_int(row.get("eval_count", 0)) for row in items)
        correct = sum(parse_bool(row.get("oracle_correct", "")) for row in items)
        result.append(
            {
                "method": method,
                "semantic_type": semantic_type,
                "events": len(events),
                "attempts": len(items),
                "oracle_accuracy": correct / len(items) if items else 0,
                "qwen_calls": qwen_calls,
                "qwen_token_count": prompt + completion,
                "qwen_runtime_minutes": runtime_ms / 60000,
                "formal_policy_required": policy_required,
                "formal_policy_complexity_points": policy_complexity,
                "formal_policy_complexity_unit": (
                    "heuristic_points_not_measured_minutes" if policy_required else ""
                ),
                "measured_annotation_time": False if policy_required else "",
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="并列报告运行成本和formal-policy构建复杂度")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--policy-costs", type=Path, default=DEFAULT_POLICY_COSTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--prefix",
        default="final-main-table-test-r5-seed20260820-runtime-policy-costs",
    )
    args = parser.parse_args()

    if not args.details.exists():
        raise SystemExit(f"details file not found: {args.details}")
    if not args.policy_costs.exists():
        raise SystemExit(f"policy costs file not found: {args.policy_costs}")

    detail_rows = load_csv(args.details)
    policy_rows = load_csv(args.policy_costs)
    policies = policy_cost_by_event(policy_rows)
    summary = summarize_runtime(detail_rows, policies)
    by_type = summarize_by_type(detail_rows, policies)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = args.output_dir / f"{args.prefix}-summary.csv"
    by_type_csv = args.output_dir / f"{args.prefix}-by-type.csv"
    json_path = args.output_dir / f"{args.prefix}.json"
    log_path = args.output_dir / f"{args.prefix}.log"
    write_csv(summary_csv, summary)
    write_csv(by_type_csv, by_type)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_details": str(args.details),
        "source_policy_costs": str(args.policy_costs),
        "policy_required_methods": sorted(POLICY_REQUIRED_METHODS),
        "rows": len(detail_rows),
        "policy_cost_events": len(policies),
        "summary": summary,
        "by_type": by_type,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"source_details={args.details}",
        f"source_policy_costs={args.policy_costs}",
        f"rows={len(detail_rows)}",
        f"summary={summary_csv}",
        f"by_type={by_type_csv}",
        f"json={json_path}",
        "",
    ]
    for row in summary:
        lines.append(
            "[{method}] acc={oracle_accuracy:.2%} | qwen={qwen_calls}/{attempts} | "
            "tokens={qwen_token_count} | runtime_min={qwen_runtime_minutes:.2f} | "
            "policy_complexity_points={formal_policy_complexity_points}".format(**row)
        )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
