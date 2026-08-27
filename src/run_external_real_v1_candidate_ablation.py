from __future__ import annotations

"""Run candidate-information ablations on external-real-v1.

The online phase reads only public event rows, document metadata/evidence notes,
candidate operations, and formal-policy files. Private Oracle rows are loaded
only after every selection is fixed.
"""

import argparse
import csv
import json
import random
import statistics
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from run_semantic_benchmark_v2 import base_system_prompt, qwen_call
from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR, check_ollama, stable_seed, wilson_interval, write_csv


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v1"
INPUT_DIR = BENCHMARK_DIR / "input"
PRIVATE_DIR = BENCHMARK_DIR / "private"
DOCUMENT_DIR = BENCHMARK_DIR / "documents"
EXCERPT_DIR = DOCUMENT_DIR / "excerpts"
RULE_DIR = BENCHMARK_DIR / "rules"

EVENT_CSV = INPUT_DIR / "external-real-event-template.csv"
DOCUMENT_CSV = INPUT_DIR / "external-real-document-template.csv"
CANDIDATE_CSV = INPUT_DIR / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE_DIR / "external-real-oracle-template.csv"

METHODS = (
    "DIRECT_FREE",
    "OPTION_VALUE_ONLY",
    "OPTION_FORMAL_OPERATION",
    "OPTION_FORMAL_POLICY",
    "OPTION_FORMAL_POLICY_HARD_GATE",
)

METHOD_SPECS = {
    "DIRECT_FREE": "free generation from public event and evidence note; no candidate list",
    "OPTION_VALUE_ONLY": "candidate option IDs and display values only",
    "OPTION_FORMAL_OPERATION": "candidate option IDs and formal repair operations; no policy",
    "OPTION_FORMAL_POLICY": "formal repair operations plus public facts and prioritized rules",
    "OPTION_FORMAL_POLICY_HARD_GATE": "deterministic formal-policy gate; Qwen only if multiple candidates survive",
}

SUPPORTED_POLICY_OPERATORS = {
    "equals",
    "not_equals",
    "on_or_after",
    "on_or_before",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
}

FORBIDDEN_OPERATION_KEY_PARTS = (
    "description",
    "oracle",
    "evidence",
    "reason",
    "condition",
    "applicable",
    "document",
    "formal_effect",
)

FORBIDDEN_POLICY_KEY_PARTS = (
    "oracle",
    "candidate_id",
    "correct",
    "answer",
    "formal_effect",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="external-real-v1 candidate ablation")
    parser.add_argument("--only", default="", help="comma-separated event IDs")
    parser.add_argument("--methods", default="", help="comma-separated method names")
    parser.add_argument("--prefix", default="external-real-v1-candidate-ablation-r5-seed20260820")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--num-predict", type=int, default=300)
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--keep-alive", default="30m")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def ready(value: str) -> bool:
    return str(value or "").strip().upper() == "READY"


def load_public_events(only: set[str]) -> list[dict[str, Any]]:
    events = [row for row in read_csv(EVENT_CSV) if ready(row.get("status", ""))]
    if only:
        events = [row for row in events if str(row["event_id"]).upper() in only]
    if not events:
        raise RuntimeError("no READY external-real-v1 events matched")

    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(DOCUMENT_CSV):
        if ready(row.get("status", "")):
            docs_by_event[str(row["event_id"])].append(row)

    candidates_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_csv(CANDIDATE_CSV):
        if not ready(row.get("status", "")):
            continue
        operation = json.loads(row["operation_json"])
        candidate = {
            "candidate_id": row["candidate_id"],
            "display_value": row["display_value"],
            "operation": operation,
            "notes": row.get("notes", ""),
        }
        candidates_by_event[str(row["event_id"])].append(candidate)

    result: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event["event_id"])
        excerpt_path = EXCERPT_DIR / f"{event_id}-evidence.md"
        if not excerpt_path.is_file():
            raise FileNotFoundError(excerpt_path)
        documents = docs_by_event.get(event_id, [])
        candidates = candidates_by_event.get(event_id, [])
        if not documents or not candidates:
            raise RuntimeError(f"{event_id} missing public documents or candidates")
        result.append(
            {
                **event,
                "split": event.get("split", "external"),
                "target": {
                    "subject_label": event["subject_label"],
                    "predicate_label": event["predicate_label"],
                    "value_kind": event["value_kind"],
                    "allowed_min": event.get("allowed_min", ""),
                    "allowed_max": event.get("allowed_max", ""),
                },
                "document_contents": [
                    {
                        "document_id": row["document_id"],
                        "source_title": row["source_title"],
                        "source_url": row["source_url"],
                        "publisher": row["publisher"],
                        "publication_date": row["publication_date"],
                        "effective_from": row["effective_from"],
                        "effective_to": row["effective_to"],
                        "document_type": row["document_type"],
                        "source_type": row["source_type"],
                        "file_name": row["file_name"],
                    }
                    for row in documents
                ],
                "public_evidence_note": excerpt_path.read_text(encoding="utf-8-sig"),
                "candidates": candidates,
            }
        )
    return result


def blinded_event(event: dict[str, Any], run_seed: int) -> dict[str, Any]:
    candidates = deepcopy(event["candidates"])
    random.Random(stable_seed(run_seed, event["event_id"], "external-order")).shuffle(candidates)
    for index, candidate in enumerate(candidates):
        candidate["original_candidate_id"] = candidate["candidate_id"]
        candidate["candidate_id"] = f"OPTION_{chr(ord('A') + index)}"
    return {**event, "candidates": candidates}


def by_option(event: dict[str, Any], option_id: str) -> dict[str, Any] | None:
    matches = [item for item in event["candidates"] if item["candidate_id"] == option_id]
    return matches[0] if len(matches) == 1 else None


def by_value(event: dict[str, Any], value: object) -> dict[str, Any] | None:
    text = str(value).strip()
    matches = [
        item
        for item in event["candidates"]
        if str(item["display_value"]).strip() == text
    ]
    return matches[0] if len(matches) == 1 else None


def common_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark": "external-real-v1",
        "event_id": event["event_id"],
        "semantic_type": event["semantic_type"],
        "domain": event["domain"],
        "case_context": event["case_context"],
        "target": event["target"],
        "source_documents": event["document_contents"],
        "public_evidence_note": event["public_evidence_note"],
    }


def qwen_direct(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    payload = {
        **common_payload(event),
        "instruction": "Output the exact value supported by the public evidence. If evidence is insufficient, set abstain=true and value=''.",
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["abstain", "value", "reason"],
        "properties": {
            "abstain": {"type": "boolean"},
            "value": {"type": "string"},
            "reason": {"type": "string", "maxLength": 180},
        },
    }
    parsed, runtime_ms, mode, raw, metadata = qwen_call(
        args,
        base_system_prompt(False),
        json.dumps(payload, ensure_ascii=False, indent=2),
        schema,
        seed,
    )
    extra = {"runtime_ms": runtime_ms, "response_mode": mode, "raw_content": raw, **metadata}
    if not isinstance(parsed.get("abstain"), bool) or not isinstance(parsed.get("value"), str):
        return None, "REJECTED_SCHEMA", "schema field type mismatch", extra
    if parsed["abstain"]:
        return None, "ABSTAIN", str(parsed.get("reason", "")), extra
    selected = by_value(event, parsed["value"])
    if selected is None:
        return None, "REJECTED_OUT_OF_CANDIDATE", f"value outside candidate set: {parsed['value']!r}", extra
    return selected, "SELECTED", str(parsed.get("reason", "")), extra


def qwen_option_value_only(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    option_ids = [str(item["candidate_id"]) for item in event["candidates"]]
    payload = {
        **common_payload(event),
        "candidate_values": [
            {"option_id": item["candidate_id"], "display_value": item["display_value"]}
            for item in event["candidates"]
        ],
        "instruction": "Select the applicable option_id. If evidence is insufficient or non-unique, select ABSTAIN.",
    }
    return qwen_option_select(event, seed, args, payload, option_ids)


def assert_neutral_operation(value: Any, path: str = "operation") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_OPERATION_KEY_PARTS):
                raise RuntimeError(f"operation contains forbidden field: {path}.{key}")
            assert_neutral_operation(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_neutral_operation(child, f"{path}[{index}]")


def qwen_option_formal_operation(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    option_ids = [str(item["candidate_id"]) for item in event["candidates"]]
    operations = []
    for item in event["candidates"]:
        operation = deepcopy(item["operation"])
        assert_neutral_operation(operation)
        operations.append({"option_id": item["candidate_id"], "operation": operation})
    payload = {
        **common_payload(event),
        "formally_executable_options": operations,
        "instruction": "Select one existing formal repair operation by option_id. Do not generate a new operation. If evidence is insufficient or non-unique, select ABSTAIN.",
    }
    return qwen_option_select(event, seed, args, payload, option_ids)


def assert_no_policy_leakage(value: Any, path: str = "policy") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_POLICY_KEY_PARTS):
                raise RuntimeError(f"policy contains forbidden field: {path}.{key}")
            assert_no_policy_leakage(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_policy_leakage(child, f"{path}[{index}]")


def normalize_policy(event: dict[str, Any]) -> dict[str, Any]:
    event_id = str(event["event_id"])
    path = RULE_DIR / f"{event_id}-formal-policy.json"
    policy = json.loads(path.read_text(encoding="utf-8-sig"))
    assert_no_policy_leakage(policy)
    if policy.get("event_id") != event_id:
        raise RuntimeError(f"policy event mismatch for {event_id}")
    facts = policy.get("facts")
    rules = policy.get("rules")
    if not isinstance(facts, dict) or not isinstance(rules, list):
        raise RuntimeError(f"invalid policy shape for {event_id}")
    candidate_values = {str(item["display_value"]).strip() for item in event["candidates"]}
    normalized_rules: list[dict[str, Any]] = []
    for rule in rules:
        conditions = rule.get("conditions")
        allowed_values = [str(value).strip() for value in rule.get("allowed_values", [])]
        if not isinstance(conditions, list) or not allowed_values:
            raise RuntimeError(f"invalid rule in {event_id}: {rule.get('rule_id')}")
        if not set(allowed_values).issubset(candidate_values):
            raise RuntimeError(f"policy references non-candidate values for {event_id}")
        for clause in conditions:
            if set(clause) != {"fact", "operator", "value"}:
                raise RuntimeError(f"invalid condition fields in {event_id}")
            if str(clause["fact"]) not in facts:
                raise RuntimeError(f"unknown fact in {event_id}: {clause['fact']}")
            if str(clause["operator"]) not in SUPPORTED_POLICY_OPERATORS:
                raise RuntimeError(f"unsupported operator in {event_id}: {clause['operator']}")
        normalized_rules.append(
            {
                "rule_id": str(rule["rule_id"]),
                "priority": int(rule.get("priority", 0)),
                "conditions": deepcopy(conditions),
                "allowed_values": allowed_values,
            }
        )
    return {
        "semantics": str(policy.get("semantics", "explicit_facts_prioritized_rules")),
        "facts": deepcopy(facts),
        "rules": normalized_rules,
    }


def qwen_option_formal_policy(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    option_ids = [str(item["candidate_id"]) for item in event["candidates"]]
    operations = []
    for item in event["candidates"]:
        operation = deepcopy(item["operation"])
        assert_neutral_operation(operation)
        operations.append({"option_id": item["candidate_id"], "operation": operation})
    payload = {
        **common_payload(event),
        "formally_executable_options": operations,
        "formal_policy": normalize_policy(event),
        "instruction": "Execute the explicit facts, conditions, priorities, and allowed values in formal_policy, then select the unique matching option_id. If there is no unique conclusion, select ABSTAIN.",
    }
    selected, status, reason, extra = qwen_option_select(event, seed, args, payload, option_ids)
    extra["policy_loaded"] = True
    return selected, status, reason, extra


def qwen_option_select(
    event: dict[str, Any],
    seed: int,
    args: argparse.Namespace,
    payload: dict[str, Any],
    option_ids: list[str],
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["abstain", "option_id", "reason"],
        "properties": {
            "abstain": {"type": "boolean"},
            "option_id": {"type": "string", "enum": option_ids + ["ABSTAIN"]},
            "reason": {"type": "string", "maxLength": 180},
        },
    }
    parsed, runtime_ms, mode, raw, metadata = qwen_call(
        args,
        base_system_prompt(True),
        json.dumps(payload, ensure_ascii=False, indent=2),
        schema,
        seed,
    )
    extra = {"runtime_ms": runtime_ms, "response_mode": mode, "raw_content": raw, **metadata}
    if not isinstance(parsed.get("abstain"), bool) or not isinstance(parsed.get("option_id"), str):
        return None, "REJECTED_SCHEMA", "schema field type mismatch", extra
    if parsed["abstain"] or parsed["option_id"] == "ABSTAIN":
        return None, "ABSTAIN", str(parsed.get("reason", "")), extra
    selected = by_option(event, parsed["option_id"])
    if selected is None:
        return None, "REJECTED_OUT_OF_CANDIDATE", f"illegal option_id={parsed['option_id']!r}", extra
    return selected, "SELECTED", str(parsed.get("reason", "")), extra


def as_date(value: Any) -> date:
    return date.fromisoformat(str(value))


def as_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"not numeric: {value!r}") from exc


def policy_clause_matches(facts: dict[str, Any], clause: dict[str, Any]) -> bool:
    actual = facts[str(clause["fact"])]
    expected = clause["value"]
    operator = str(clause["operator"])
    if operator == "equals":
        return type(actual) is type(expected) and actual == expected
    if operator == "not_equals":
        return not (type(actual) is type(expected) and actual == expected)
    if operator == "on_or_after":
        return as_date(actual) >= as_date(expected)
    if operator == "on_or_before":
        return as_date(actual) <= as_date(expected)
    if operator == "greater_than":
        return as_decimal(actual) > as_decimal(expected)
    if operator == "greater_or_equal":
        return as_decimal(actual) >= as_decimal(expected)
    if operator == "less_than":
        return as_decimal(actual) < as_decimal(expected)
    if operator == "less_or_equal":
        return as_decimal(actual) <= as_decimal(expected)
    raise ValueError(f"unsupported operator: {operator}")


def select_applicable_policy_rule(policy: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    applicable = [
        rule
        for rule in policy["rules"]
        if all(policy_clause_matches(policy["facts"], clause) for clause in rule["conditions"])
    ]
    if not applicable:
        return None, "no applicable rule"
    highest = max(int(rule["priority"]) for rule in applicable)
    winners = [rule for rule in applicable if int(rule["priority"]) == highest]
    if len(winners) != 1:
        return None, f"non-unique highest priority rules: {[rule['rule_id'] for rule in winners]}"
    return winners[0], ""


def option_formal_policy_hard_gate(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    policy = normalize_policy(event)
    rule, error = select_applicable_policy_rule(policy)
    allowed_values = {str(value).strip() for value in rule["allowed_values"]} if rule else set()
    survivors = [
        candidate
        for candidate in event["candidates"]
        if str(candidate["display_value"]).strip() in allowed_values
    ]
    extra: dict[str, Any] = {
        "runtime_ms": 0,
        "response_mode": "formal_policy_hard_gate",
        "raw_content": "",
        "policy_loaded": True,
        "applicable_rule": rule["rule_id"] if rule else "",
        "allowed_values": "|".join(sorted(allowed_values)),
        "survivor_count": len(survivors),
        "decision_path": "",
        "qwen_called": False,
    }
    if rule is None:
        extra["decision_path"] = "ABSTAIN_NO_UNIQUE_RULE"
        return None, "ABSTAIN", error, extra
    if not survivors:
        extra["decision_path"] = "ABSTAIN_NO_SURVIVOR"
        return None, "ABSTAIN", "formal policy gate has no survivor", extra
    if len(survivors) == 1:
        extra["decision_path"] = "DETERMINISTIC_SELECT"
        return survivors[0], "SELECTED", f"deterministic gate selected {rule['rule_id']}", extra

    gated_event = deepcopy(event)
    gated_event["candidates"] = deepcopy(survivors)
    selected, status, reason, qwen_extra = qwen_option_formal_policy(gated_event, seed, args)
    extra.update(qwen_extra)
    extra.update(
        {
            "policy_loaded": True,
            "applicable_rule": rule["rule_id"],
            "allowed_values": "|".join(sorted(allowed_values)),
            "survivor_count": len(survivors),
            "decision_path": "QWEN_RANK_AFTER_GATE",
            "qwen_called": True,
        }
    )
    return selected, status, reason, extra


def load_oracle_after_predictions() -> dict[str, dict[str, str]]:
    return {
        str(row["event_id"]): row
        for row in read_csv(ORACLE_CSV)
        if ready(row.get("status", ""))
    }


def summarize(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        groups[(str(row["method"]), str(row["semantic_type"]))].append(row)
        groups[(str(row["method"]), "ALL")].append(row)
    rows: list[dict[str, Any]] = []
    for (method, semantic_type), items in sorted(groups.items()):
        total = len(items)
        selected = sum(item["status"] == "SELECTED" for item in items)
        correct = sum(bool(item["oracle_correct"]) for item in items)
        invalid = sum(str(item["status"]).startswith("REJECTED") for item in items)
        abstain = sum(item["status"] == "ABSTAIN" for item in items)
        wrong = sum(item["status"] == "SELECTED" and not item["oracle_correct"] for item in items)
        low, high = wilson_interval(correct, total)
        runtimes = [int(item["runtime_ms"]) for item in items if int(item["runtime_ms"]) > 0]
        rows.append(
            {
                "method": method,
                "semantic_type": semantic_type,
                "attempts": total,
                "coverage": selected / total,
                "oracle_accuracy": correct / total,
                "oracle_ci95_low": low,
                "oracle_ci95_high": high,
                "wrong_selection_rate": wrong / total,
                "invalid_output_rate": invalid / total,
                "abstain_rate": abstain / total,
                "mean_runtime_ms": round(statistics.mean(runtimes), 2) if runtimes else 0,
            }
        )
    return rows


def event_summary(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        groups[(str(row["method"]), str(row["event_id"]))].append(row)
    result: list[dict[str, Any]] = []
    for (method, event_id), items in sorted(groups.items()):
        successes = sum(bool(item["oracle_correct"]) for item in items)
        result.append(
            {
                "method": method,
                "event_id": event_id,
                "semantic_type": items[0]["semantic_type"],
                "attempts": len(items),
                "oracle_successes": successes,
                "oracle_accuracy": successes / len(items),
                "all_runs_correct": successes == len(items),
                "selection_stable": len({str(item["selected_candidate_id"]) for item in items}) == 1,
                "invalid_outputs": sum(str(item["status"]).startswith("REJECTED") for item in items),
                "abstains": sum(item["status"] == "ABSTAIN" for item in items),
            }
        )
    return result


def event_level_method_summary(by_event: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in by_event:
        groups[str(row["method"])].append(row)
    result: list[dict[str, Any]] = []
    for method, items in sorted(groups.items()):
        result.append(
            {
                "method": method,
                "independent_events": len(items),
                "all_runs_correct_events": sum(bool(item["all_runs_correct"]) for item in items),
                "strict_event_accuracy": sum(bool(item["all_runs_correct"]) for item in items) / len(items),
                "macro_event_accuracy": sum(float(item["oracle_accuracy"]) for item in items) / len(items),
                "stable_events": sum(bool(item["selection_stable"]) for item in items),
            }
        )
    return result


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs must be > 0")
    only = {item.strip().upper() for item in args.only.split(",") if item.strip()}
    events = load_public_events(only)
    selected_methods = list(METHODS)
    if args.methods:
        wanted_methods = [item.strip().upper() for item in args.methods.split(",") if item.strip()]
        unknown = [item for item in wanted_methods if item not in METHODS]
        if unknown:
            raise RuntimeError(f"unknown methods: {unknown}")
        selected_methods = wanted_methods

    if any(method != "OPTION_FORMAL_POLICY_HARD_GATE" for method in selected_methods):
        print(f"[connection] Ollama={args.ollama_url}, model={args.model}")
        check_ollama(args.ollama_url, args.model, args.timeout)
        print("[connection] Ollama and model are available.")

    total = len(events) * args.runs * len(selected_methods)
    print("external-real-v1 candidate ablation")
    print(f"events={len(events)}, runs={args.runs}, attempts={total}")
    details: list[dict[str, Any]] = []
    counter = 0
    for run in range(1, args.runs + 1):
        run_seed = args.seed + run - 1
        for source_event in events:
            event = blinded_event(source_event, run_seed)
            for method in selected_methods:
                counter += 1
                print(f"[{counter}/{total}] {event['event_id']} run={run} {method}", flush=True)
                selected: dict[str, Any] | None = None
                status = ""
                reason = ""
                extra: dict[str, Any] = {}
                call_seed = stable_seed(run_seed, event["event_id"], method)
                try:
                    if method == "DIRECT_FREE":
                        selected, status, reason, extra = qwen_direct(event, call_seed, args)
                    elif method == "OPTION_VALUE_ONLY":
                        selected, status, reason, extra = qwen_option_value_only(event, call_seed, args)
                    elif method == "OPTION_FORMAL_OPERATION":
                        selected, status, reason, extra = qwen_option_formal_operation(event, call_seed, args)
                    elif method == "OPTION_FORMAL_POLICY":
                        selected, status, reason, extra = qwen_option_formal_policy(event, call_seed, args)
                    elif method == "OPTION_FORMAL_POLICY_HARD_GATE":
                        selected, status, reason, extra = option_formal_policy_hard_gate(event, call_seed, args)
                    else:
                        raise RuntimeError(f"unknown method: {method}")
                except Exception as exc:
                    status = "REJECTED_ERROR"
                    reason = f"{type(exc).__name__}: {exc}"

                original_id = selected.get("original_candidate_id", "") if selected else ""
                displayed_id = selected.get("candidate_id", "") if selected else ""
                display_value = selected.get("display_value", "") if selected else ""
                details.append(
                    {
                        "split": source_event["split"],
                        "event_id": event["event_id"],
                        "semantic_type": event["semantic_type"],
                        "run": run,
                        "seed": run_seed,
                        "method": method,
                        "status": status,
                        "selected_option_id": displayed_id,
                        "selected_candidate_id": original_id,
                        "selected_value": display_value,
                        "reason": reason,
                        "candidate_order": "|".join(
                            f"{item['candidate_id']}={item['original_candidate_id']}"
                            for item in event["candidates"]
                        ),
                        "runtime_ms": extra.get("runtime_ms", 0),
                        "response_mode": extra.get("response_mode", ""),
                        "prompt_eval_count": extra.get("prompt_eval_count", 0),
                        "eval_count": extra.get("eval_count", 0),
                        "done_reason": extra.get("done_reason", ""),
                        "raw_content": extra.get("raw_content", ""),
                        "policy_loaded": extra.get("policy_loaded", ""),
                        "applicable_rule": extra.get("applicable_rule", ""),
                        "allowed_values": extra.get("allowed_values", ""),
                        "survivor_count": extra.get("survivor_count", ""),
                        "decision_path": extra.get("decision_path", ""),
                        "qwen_called": extra.get("qwen_called", method != "OPTION_FORMAL_POLICY_HARD_GATE"),
                    }
                )
                print(
                    f"  status={status} | candidate={original_id or '-'} | "
                    f"value={display_value or '-'} | {extra.get('runtime_ms', 0)}ms",
                    flush=True,
                )

    oracles = load_oracle_after_predictions()
    for row in details:
        oracle = oracles.get(str(row["event_id"]))
        if oracle is None:
            raise RuntimeError(f"{row['event_id']} missing READY private Oracle")
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        row["oracle_value"] = oracle["oracle_value"]
        row["oracle_correct"] = (
            row["status"] == "SELECTED"
            and row["selected_candidate_id"] == oracle["oracle_candidate_id"]
        )

    summary = summarize(details)
    by_event = event_summary(details)
    event_methods = event_level_method_summary(by_event)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    detail_csv = OUTPUT_DIR / f"{args.prefix}-details.csv"
    summary_csv = OUTPUT_DIR / f"{args.prefix}-summary.csv"
    by_event_csv = OUTPUT_DIR / f"{args.prefix}-by-event.csv"
    event_method_csv = OUTPUT_DIR / f"{args.prefix}-event-level.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    write_csv(detail_csv, details)
    write_csv(summary_csv, summary)
    write_csv(by_event_csv, by_event)
    write_csv(event_method_csv, event_methods)

    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "external-real-v1",
        "oracle_loaded_after_predictions": True,
        "method_specs": METHOD_SPECS,
        "methods": selected_methods,
        "runs": args.runs,
        "attempt_summary": summary,
        "event_level_summary": event_methods,
        "by_event": by_event,
        "details": details,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["external-real-v1 candidate ablation", "Oracle loaded after predictions=True", ""]
    print("\ncall-level summary")
    for row in summary:
        if row["semantic_type"] != "ALL":
            continue
        line = (
            f"[{row['method']}] Oracle={row['oracle_accuracy']:.2%} | "
            f"wrong={row['wrong_selection_rate']:.2%} | invalid={row['invalid_output_rate']:.2%} | "
            f"ABSTAIN={row['abstain_rate']:.2%}"
        )
        print(line)
        lines.append(line)
    print("\nevent-level summary")
    lines.append("")
    for row in event_methods:
        line = (
            f"[{row['method']}] strict={row['all_runs_correct_events']}/"
            f"{row['independent_events']} ({row['strict_event_accuracy']:.2%}) | "
            f"macro={row['macro_event_accuracy']:.2%} | stable={row['stable_events']}/{row['independent_events']}"
        )
        print(line)
        lines.append(line)
    lines.extend(
        [
            "",
            f"details={detail_csv}",
            f"summary={summary_csv}",
            f"by_event={by_event_csv}",
            f"event_level={event_method_csv}",
            f"json={json_path}",
        ]
    )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[external-real-v1 candidate ablation stopped] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
