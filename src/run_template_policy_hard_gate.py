from __future__ import annotations

"""Run a template-based hard gate over semantic-v2 policies.

The goal is narrow and auditable: test whether the deterministic result depends
on event_id-specific branching in code. This remains a policy-available setting:
facts and rule slots are manually structured inputs, not automatically extracted
from raw documents.
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from run_semantic_benchmark_v2 import load_oracle_after_predictions, load_public_events
from semantic_v2_common import BENCHMARK_DIR, OUTPUT_DIR, write_csv
from validate_formal_policy_gate import (
    assert_no_policy_leakage,
    normalize_policy_conditions,
    select_rule,
)


POLICY_DIR = BENCHMARK_DIR / "rules"
TEMPLATE_IDS = {
    "TEMPORAL_VERSION": "TEMPLATE_PRIORITIZED_TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION": "TEMPLATE_PRIORITIZED_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE": "TEMPLATE_PRIORITIZED_CROSS_SENTENCE_SCOPE",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="模板化formal-policy hard gate实验")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--only", default="", help="只运行指定事件，多个事件用逗号分隔")
    parser.add_argument("--prefix", default="template-policy-hard-gate-test")
    return parser.parse_args()


def template_id_for(event: dict[str, Any], policy: dict[str, Any]) -> str:
    semantic_type = str(event["semantic_type"])
    facts = policy.get("facts") if isinstance(policy.get("facts"), dict) else {}
    if any(str(key).endswith("_code") or "code" in str(key) for key in facts):
        return f"{TEMPLATE_IDS[semantic_type]}__CODE_MAPPING"
    return TEMPLATE_IDS[semantic_type]


def load_policy_slots(event: dict[str, Any]) -> dict[str, Any]:
    """Load event policy, then strip event-specific identifiers from rule slots."""
    event_id = str(event["event_id"])
    path = POLICY_DIR / f"{event_id}-formal-policy.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"policy top-level is not an object: {path}")
    assert_no_policy_leakage(raw)
    facts = raw.get("facts")
    rules = raw.get("rules")
    if not isinstance(facts, dict) or not facts:
        raise RuntimeError(f"{event_id} policy lacks facts")
    if not isinstance(rules, list) or not rules:
        raise RuntimeError(f"{event_id} policy lacks rules")

    candidate_values = {
        str(candidate.get("display_value", "")).strip()
        for candidate in event["candidates"]
    }
    normalized_rules: list[dict[str, Any]] = []
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise RuntimeError(f"{event_id} rule #{index} is not an object")
        source_rule_id = str(rule.get("rule_id") or f"SOURCE_RULE_{index}")
        allowed = rule.get("allowed_values")
        if not isinstance(allowed, list) or not allowed:
            raise RuntimeError(f"{event_id}/{source_rule_id} lacks allowed_values")
        allowed_values = [str(value).strip() for value in allowed]
        if not set(allowed_values).issubset(candidate_values):
            raise RuntimeError(
                f"{event_id}/{source_rule_id} references non-candidate values: "
                f"{sorted(set(allowed_values) - candidate_values)}"
            )
        conditions = normalize_policy_conditions(
            event_id,
            source_rule_id,
            rule.get("conditions"),
            rule.get("when"),
        )
        for clause in conditions:
            if str(clause["fact"]) not in facts:
                raise RuntimeError(
                    f"{event_id}/{source_rule_id} references missing fact {clause['fact']}"
                )
        normalized_rules.append(
            {
                "rule_id": f"RULE_SLOT_{index:02d}",
                "source_rule_id_hash": hashlib.sha256(
                    source_rule_id.encode("utf-8")
                ).hexdigest()[:12],
                "priority": int(rule.get("priority", 0)),
                "conditions": conditions,
                "allowed_values": allowed_values,
            }
        )
    return {
        "semantics": "template_prioritized_rules_over_manual_slots",
        "facts": facts,
        "rules": normalized_rules,
        "template_id": template_id_for(event, raw),
        "source_policy_file": str(path.relative_to(BENCHMARK_DIR)),
        "manual_slots_used": True,
        "event_id_branch_used": False,
        "oracle_loaded_in_decision_phase": False,
    }


def run_decision(event: dict[str, Any]) -> dict[str, Any]:
    policy = load_policy_slots(event)
    rule, error = select_rule(policy)
    allowed = set(rule["allowed_values"]) if rule else set()
    survivors = [
        candidate
        for candidate in event["candidates"]
        if str(candidate.get("display_value", "")).strip() in allowed
    ]
    selected = survivors[0] if len(survivors) == 1 else None
    if not error and len(survivors) != 1:
        error = f"survivor_count={len(survivors)}"
    return {
        "event_id": event["event_id"],
        "split": event["split"],
        "semantic_type": event["semantic_type"],
        "method": "TEMPLATE_POLICY_HARD_GATE",
        "status": "SELECTED" if selected else "ABSTAIN",
        "selected_candidate_id": selected.get("candidate_id", "") if selected else "",
        "selected_value": selected.get("display_value", "") if selected else "",
        "template_id": policy["template_id"],
        "source_policy_file": policy["source_policy_file"],
        "applicable_rule_slot": rule.get("rule_id", "") if rule else "",
        "allowed_values": "|".join(sorted(allowed)),
        "survivor_count": len(survivors),
        "decision_error": error,
        "manual_slots_used": True,
        "event_id_branch_used": False,
        "qwen_called": False,
        "runtime_ms": 0,
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    groups["ALL"] = rows
    for row in rows:
        groups[str(row["semantic_type"])].append(row)

    result: list[dict[str, Any]] = []
    for semantic_type, subset in groups.items():
        attempts = len(subset)
        correct = sum(1 for row in subset if row.get("oracle_correct") is True)
        abstains = sum(1 for row in subset if row["status"] == "ABSTAIN")
        wrong = sum(
            1
            for row in subset
            if row["status"] == "SELECTED" and row.get("oracle_correct") is False
        )
        zero_survivor = sum(1 for row in subset if int(row["survivor_count"]) == 0)
        multi_survivor = sum(1 for row in subset if int(row["survivor_count"]) > 1)
        result.append(
            {
                "method": "TEMPLATE_POLICY_HARD_GATE",
                "semantic_type": semantic_type,
                "attempts": attempts,
                "oracle_accuracy": correct / attempts if attempts else 0,
                "wrong_selection_rate": wrong / attempts if attempts else 0,
                "abstain_rate": abstains / attempts if attempts else 0,
                "zero_survivor_events": zero_survivor,
                "multi_survivor_events": multi_survivor,
                "event_id_branch_used": False,
                "manual_slots_used": True,
                "qwen_calls": 0,
            }
        )
    order = {
        "ALL": 0,
        "TEMPORAL_VERSION": 1,
        "GENERAL_RULE_EXCEPTION": 2,
        "CROSS_SENTENCE_SCOPE": 3,
    }
    return sorted(result, key=lambda row: order.get(str(row["semantic_type"]), 99))


def main() -> int:
    args = parse_args()
    events = load_public_events(args.split)
    if args.only:
        wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
        events = [event for event in events if str(event.get("event_id", "")).upper() in wanted]
        if not events:
            raise RuntimeError(f"--only={sorted(wanted)} has no matching events")

    rows = [run_decision(event) for event in events]

    # Offline scoring boundary: Oracle is loaded only after all decisions.
    oracles = load_oracle_after_predictions()
    for row in rows:
        oracle = oracles.get(str(row["event_id"]))
        if oracle is None:
            raise RuntimeError(f"{row['event_id']} lacks READY Oracle")
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        row["oracle_value"] = oracle["oracle_value"]
        row["oracle_correct"] = (
            row["status"] == "SELECTED"
            and row["selected_candidate_id"] == oracle["oracle_candidate_id"]
        )

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
        "method": "TEMPLATE_POLICY_HARD_GATE",
        "limitation": (
            "Uses manually structured facts and rule slots. This tests removal of "
            "event_id branching, not automatic policy extraction."
        ),
        "summary": summary,
        "details": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "template policy hard gate",
        f"split={args.split}",
        "event_id_branch_used=False",
        "manual_slots_used=True",
        "qwen_calls=0",
        "",
    ]
    for row in summary:
        lines.append(
            f"[{row['semantic_type']}] accuracy={row['oracle_accuracy']:.2%} "
            f"wrong={row['wrong_selection_rate']:.2%} abstain={row['abstain_rate']:.2%} "
            f"zero={row['zero_survivor_events']} multi={row['multi_survivor_events']}"
        )
    lines.extend(["", f"details={detail_csv}", f"summary={summary_csv}", f"json={json_path}"])
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[template policy hard gate stopped] {type(exc).__name__}: {exc}")
        raise SystemExit(2)
