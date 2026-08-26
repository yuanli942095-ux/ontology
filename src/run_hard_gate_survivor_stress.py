from __future__ import annotations

"""Stress-test hard-gate behavior when survivor count is not exactly one.

This script does not call Qwen. Oracle is loaded only after all gate decisions.
The test intentionally perturbs candidate lists or policy slots to verify the
executor fails closed for zero-survivor and multi-survivor cases.
"""

import argparse
import copy
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from run_semantic_benchmark_v2 import load_oracle_after_predictions, load_public_events
from run_template_policy_hard_gate import load_policy_slots
from semantic_v2_common import OUTPUT_DIR, write_csv
from validate_formal_policy_gate import select_rule


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="hard gate survivor stress test")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--only", default="", help="只运行指定事件，多个事件用逗号分隔")
    parser.add_argument("--prefix", default="hard-gate-survivor-stress-test")
    return parser.parse_args()


def candidate_value(candidate: dict[str, Any]) -> str:
    return str(candidate.get("display_value", "")).strip()


def decide(event: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    try:
        rule, error = select_rule(policy)
    except Exception as exc:
        return {
            "status": "CONFIG_ERROR",
            "applicable_rule_slot": "",
            "allowed_values": "",
            "survivor_count": 0,
            "selected_candidate_id": "",
            "selected_value": "",
            "decision_error": f"{type(exc).__name__}: {exc}",
        }

    allowed = set(rule["allowed_values"]) if rule else set()
    survivors = [
        candidate for candidate in event["candidates"] if candidate_value(candidate) in allowed
    ]
    selected = survivors[0] if len(survivors) == 1 else None
    if not error and len(survivors) != 1:
        error = f"survivor_count={len(survivors)}"
    return {
        "status": "SAFE_ACCEPT" if selected else "ABSTAIN",
        "applicable_rule_slot": rule.get("rule_id", "") if rule else "",
        "allowed_values": "|".join(sorted(allowed)),
        "survivor_count": len(survivors),
        "selected_candidate_id": selected.get("candidate_id", "") if selected else "",
        "selected_value": candidate_value(selected) if selected else "",
        "decision_error": error,
    }


def original_survivor(event: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    decision = decide(event, policy)
    if decision["status"] != "SAFE_ACCEPT":
        raise RuntimeError(
            f"{event['event_id']} control gate is not SAFE_ACCEPT: {decision}"
        )
    matches = [
        candidate
        for candidate in event["candidates"]
        if candidate.get("candidate_id") == decision["selected_candidate_id"]
    ]
    if len(matches) != 1:
        raise RuntimeError(f"{event['event_id']} cannot recover selected candidate")
    return matches[0]


def non_allowed_value(event: dict[str, Any], policy: dict[str, Any]) -> str:
    rule, error = select_rule(policy)
    if rule is None:
        raise RuntimeError(f"control policy has no selected rule: {error}")
    allowed = {str(value).strip() for value in rule["allowed_values"]}
    for candidate in event["candidates"]:
        value = candidate_value(candidate)
        if value not in allowed:
            return value
    raise RuntimeError(f"{event['event_id']} has no non-allowed candidate value")


def zero_survivor_candidate_mismatch(event: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    stressed_event = copy.deepcopy(event)
    for index, candidate in enumerate(stressed_event["candidates"], start=1):
        candidate["display_value"] = f"__NON_MATCHING_VALUE_{index}__"
    return stressed_event, copy.deepcopy(policy)


def multi_survivor_duplicate_value(event: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    stressed_event = copy.deepcopy(event)
    survivor = copy.deepcopy(original_survivor(event, policy))
    survivor["candidate_id"] = f"{survivor['candidate_id']}__DUPLICATE"
    survivor["original_candidate_id"] = f"{survivor.get('original_candidate_id', survivor['candidate_id'])}__DUPLICATE"
    stressed_event["candidates"].append(survivor)
    return stressed_event, copy.deepcopy(policy)


def multi_survivor_broad_allowed_values(event: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    stressed_policy = copy.deepcopy(policy)
    rule, error = select_rule(stressed_policy)
    if rule is None:
        raise RuntimeError(f"control policy has no selected rule: {error}")
    extra = non_allowed_value(event, stressed_policy)
    for candidate_rule in stressed_policy["rules"]:
        if candidate_rule["rule_id"] == rule["rule_id"]:
            candidate_rule["allowed_values"] = list(dict.fromkeys([*candidate_rule["allowed_values"], extra]))
            break
    return copy.deepcopy(event), stressed_policy


def no_applicable_rule(event: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    stressed_policy = copy.deepcopy(policy)
    first_fact = next(iter(stressed_policy["facts"]))
    for rule in stressed_policy["rules"]:
        rule["conditions"] = [
            {"fact": first_fact, "operator": "equals", "value": "__IMPOSSIBLE_VALUE__"}
        ]
    return copy.deepcopy(event), stressed_policy


VARIANTS = {
    "CONTROL_UNIQUE": {
        "expected_status": "SAFE_ACCEPT",
        "description": "Original event and policy; exactly one survivor should be selected.",
        "builder": lambda event, policy: (copy.deepcopy(event), copy.deepcopy(policy)),
    },
    "ZERO_SURVIVOR_CANDIDATE_MISMATCH": {
        "expected_status": "ABSTAIN",
        "description": "Candidate display values no longer match policy allowed values.",
        "builder": zero_survivor_candidate_mismatch,
    },
    "MULTI_SURVIVOR_DUPLICATE_VALUE": {
        "expected_status": "ABSTAIN",
        "description": "A duplicate candidate has the same allowed value as the survivor.",
        "builder": multi_survivor_duplicate_value,
    },
    "MULTI_SURVIVOR_BROAD_ALLOWED_VALUES": {
        "expected_status": "ABSTAIN",
        "description": "The selected rule is broadened to allow another candidate value.",
        "builder": multi_survivor_broad_allowed_values,
    },
    "NO_APPLICABLE_RULE": {
        "expected_status": "ABSTAIN",
        "description": "All rules are made inapplicable while facts remain well-formed.",
        "builder": no_applicable_rule,
    },
}


def run_variant(
    source_event: dict[str, Any],
    source_policy: dict[str, Any],
    variant_name: str,
    variant_spec: dict[str, Any],
) -> dict[str, Any]:
    stressed_event, stressed_policy = variant_spec["builder"](source_event, source_policy)
    decision = decide(stressed_event, stressed_policy)
    expected_status = str(variant_spec["expected_status"])
    pass_expected = decision["status"] == expected_status
    return {
        "event_id": source_event["event_id"],
        "split": source_event["split"],
        "semantic_type": source_event["semantic_type"],
        "variant": variant_name,
        "variant_description": variant_spec["description"],
        "expected_status": expected_status,
        "status": decision["status"],
        "pass_expected_behavior": pass_expected,
        "applicable_rule_slot": decision["applicable_rule_slot"],
        "allowed_values": decision["allowed_values"],
        "survivor_count": decision["survivor_count"],
        "selected_candidate_id": decision["selected_candidate_id"],
        "selected_value": decision["selected_value"],
        "decision_error": decision["decision_error"],
        "event_id_branch_used": False,
        "manual_slots_used": True,
        "qwen_called": False,
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["variant"]), "ALL")].append(row)
        groups[(str(row["variant"]), str(row["semantic_type"]))].append(row)

    result: list[dict[str, Any]] = []
    for (variant, semantic_type), subset in sorted(groups.items()):
        attempts = len(subset)
        expected_passes = sum(1 for row in subset if row["pass_expected_behavior"])
        statuses = Counter(str(row["status"]) for row in subset)
        zero = sum(1 for row in subset if int(row["survivor_count"]) == 0)
        multi = sum(1 for row in subset if int(row["survivor_count"]) > 1)
        result.append(
            {
                "variant": variant,
                "semantic_type": semantic_type,
                "attempts": attempts,
                "expected_behavior_passes": expected_passes,
                "expected_behavior_rate": expected_passes / attempts if attempts else 0,
                "safe_accepts": statuses.get("SAFE_ACCEPT", 0),
                "abstains": statuses.get("ABSTAIN", 0),
                "config_errors": statuses.get("CONFIG_ERROR", 0),
                "zero_survivor_cases": zero,
                "multi_survivor_cases": multi,
                "qwen_calls": 0,
            }
        )
    order = {
        "ALL": 0,
        "TEMPORAL_VERSION": 1,
        "GENERAL_RULE_EXCEPTION": 2,
        "CROSS_SENTENCE_SCOPE": 3,
    }
    return sorted(result, key=lambda row: (str(row["variant"]), order.get(str(row["semantic_type"]), 99)))


def main() -> int:
    args = parse_args()
    events = load_public_events(args.split)
    if args.only:
        wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
        events = [event for event in events if str(event.get("event_id", "")).upper() in wanted]
        if not events:
            raise RuntimeError(f"--only={sorted(wanted)} has no matching events")

    rows: list[dict[str, Any]] = []
    for event in events:
        policy = load_policy_slots(event)
        for variant_name, variant_spec in VARIANTS.items():
            rows.append(run_variant(event, policy, variant_name, variant_spec))

    # Offline scoring boundary: Oracle is not needed for pass/fail behavior, but
    # we load it after all decisions to keep the same audit boundary.
    oracles = load_oracle_after_predictions()
    for row in rows:
        oracle = oracles.get(str(row["event_id"]))
        row["oracle_loaded_after_decisions"] = True
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"] if oracle else ""
        row["oracle_value"] = oracle["oracle_value"] if oracle else ""

    summary = summarize(rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    detail_csv = OUTPUT_DIR / f"{args.prefix}-details.csv"
    summary_csv = OUTPUT_DIR / f"{args.prefix}-summary.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    write_csv(detail_csv, rows)
    write_csv(summary_csv, summary)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_loaded_after_decisions": True,
        "split": args.split,
        "variants": {
            name: {
                "expected_status": spec["expected_status"],
                "description": spec["description"],
            }
            for name, spec in VARIANTS.items()
        },
        "summary": summary,
        "details": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    total_pass = sum(1 for row in rows if row["pass_expected_behavior"])
    lines = [
        "hard gate survivor stress test",
        f"split={args.split}",
        f"events={len(events)}",
        f"variants={len(VARIANTS)}",
        f"attempts={len(rows)}",
        f"expected_behavior_passes={total_pass}/{len(rows)}",
        "qwen_calls=0",
        "oracle_loaded_after_decisions=True",
        "",
    ]
    for row in summary:
        if row["semantic_type"] != "ALL":
            continue
        lines.append(
            f"[{row['variant']}] pass={row['expected_behavior_rate']:.2%} "
            f"safe={row['safe_accepts']} abstain={row['abstains']} "
            f"zero={row['zero_survivor_cases']} multi={row['multi_survivor_cases']}"
        )
    lines.extend(["", f"details={detail_csv}", f"summary={summary_csv}", f"json={json_path}"])
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if total_pass == len(rows) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[hard gate survivor stress stopped] {type(exc).__name__}: {exc}")
        raise SystemExit(2)
