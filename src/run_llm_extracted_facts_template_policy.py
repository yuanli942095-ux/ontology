from __future__ import annotations

"""Run LLM fact extraction followed by template-policy hard gating.

This experiment isolates one specific question: if manually designed policy
templates and fact schemas are available, can Qwen recover the per-event facts
from the public case/documents well enough for the symbolic gate to choose a
candidate? The decision prompt does not include candidate descriptions,
allowed_values, formal rules, or Oracle labels.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from run_semantic_benchmark_v2 import (
    load_oracle_after_predictions,
    load_public_events,
    qwen_call,
    read_documents,
)
from run_template_policy_hard_gate import load_policy_slots
from semantic_v2_common import OUTPUT_DIR, check_ollama, stable_seed, wilson_interval, write_csv
from validate_formal_policy_gate import select_rule


METHOD = "LLM_EXTRACTED_FACTS_TEMPLATE_POLICY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Qwen抽取facts，再执行模板formal-policy hard gate"
    )
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--only", default="", help="只运行指定事件，多个事件用逗号分隔")
    parser.add_argument(
        "--prefix",
        default="llm-extracted-facts-template-policy-test-r5-seed20260820",
    )
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--num-predict", type=int, default=500)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--keep-alive", default="30m")
    return parser.parse_args()


def expected_json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def fact_schema_from_policy(policy: dict[str, Any]) -> dict[str, str]:
    facts = policy.get("facts")
    if not isinstance(facts, dict) or not facts:
        raise RuntimeError("policy lacks facts")
    return {str(name): expected_json_type(value) for name, value in facts.items()}


def extraction_schema(fact_schema: dict[str, str]) -> dict[str, Any]:
    properties = {
        name: {"type": json_type}
        for name, json_type in sorted(fact_schema.items())
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["facts", "missing_facts", "evidence_notes"],
        "properties": {
            "facts": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(fact_schema),
                "properties": properties,
            },
            "missing_facts": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(fact_schema)},
            },
            "evidence_notes": {"type": "string", "maxLength": 500},
        },
    }


def system_prompt() -> str:
    return (
        "你是受控规范文档事实抽取器。只能依据案件上下文、目标字段和给定文档抽取facts；"
        "禁止外部常识，禁止选择候选，禁止生成候选ID、规则、allowed_values或Oracle。"
        "日期使用YYYY-MM-DD；整数输出JSON number；布尔值输出true/false；代码和枚举值必须照抄文档原文。"
        "证据不完整时仍输出最受文档支持的fact值，并在missing_facts和evidence_notes中说明。"
        "只输出严格JSON，不要Markdown、思考过程或额外文字。"
    )


def build_prompt(event: dict[str, Any], fact_schema: dict[str, str]) -> str:
    public_documents = read_documents(event)
    payload = {
        "event_id": event["event_id"],
        "semantic_type": event["semantic_type"],
        "case_context": event["case_context"],
        "target": event["target"],
        "documents": public_documents,
        "fact_schema": [
            {"fact": name, "json_type": fact_schema[name]}
            for name in sorted(fact_schema)
        ],
        "instruction": (
            "只抽取fact_schema中列出的facts。不要读取、推断或输出候选描述、候选ID、"
            "formal rules、allowed_values、Oracle或最终答案。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def coerce_fact_value(value: Any, json_type: str) -> tuple[Any, bool]:
    if json_type == "boolean":
        if isinstance(value, bool):
            return value, False
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true", True
        raise ValueError(f"expected boolean, got {value!r}")
    if json_type == "integer":
        if isinstance(value, bool):
            raise ValueError(f"expected integer, got boolean {value!r}")
        if isinstance(value, int):
            return value, False
        if isinstance(value, float) and value.is_integer():
            return int(value), True
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip()), True
        raise ValueError(f"expected integer, got {value!r}")
    if json_type == "number":
        if isinstance(value, bool):
            raise ValueError(f"expected number, got boolean {value!r}")
        if isinstance(value, int | float):
            return value, False
        if isinstance(value, str):
            return float(value.strip()), True
        raise ValueError(f"expected number, got {value!r}")
    if json_type == "string":
        if isinstance(value, str):
            return value.strip(), False
        return str(value).strip(), True
    raise ValueError(f"unsupported json type {json_type!r}")


def normalize_facts(
    parsed: dict[str, Any], fact_schema: dict[str, str]
) -> tuple[dict[str, Any], list[str]]:
    facts = parsed.get("facts")
    if not isinstance(facts, dict):
        raise ValueError("facts is not an object")
    normalized: dict[str, Any] = {}
    coerced: list[str] = []
    for fact_name, json_type in sorted(fact_schema.items()):
        if fact_name not in facts:
            raise ValueError(f"missing fact {fact_name}")
        value, did_coerce = coerce_fact_value(facts[fact_name], json_type)
        normalized[fact_name] = value
        if did_coerce:
            coerced.append(fact_name)
    return normalized, coerced


def fact_matches_manual(extracted: dict[str, Any], manual: dict[str, Any]) -> tuple[int, int, list[str]]:
    mismatches: list[str] = []
    matched = 0
    for fact_name, expected in sorted(manual.items()):
        actual = extracted.get(fact_name)
        if type(actual) is type(expected) and actual == expected:
            matched += 1
        else:
            mismatches.append(f"{fact_name}: expected={expected!r}, actual={actual!r}")
    return matched, len(manual), mismatches


def execute_policy(
    event: dict[str, Any], template_policy: dict[str, Any], extracted_facts: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, set[str], int, str]:
    policy = deepcopy(template_policy)
    policy["facts"] = extracted_facts
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
    return selected, rule, allowed, len(survivors), error


def run_one(event: dict[str, Any], run_index: int, args: argparse.Namespace) -> dict[str, Any]:
    template_policy = load_policy_slots(event)
    manual_facts = dict(template_policy["facts"])
    fact_schema = fact_schema_from_policy(template_policy)
    seed = stable_seed(args.seed, run_index, event["event_id"], METHOD)

    row: dict[str, Any] = {
        "run_index": run_index,
        "event_id": event["event_id"],
        "split": event["split"],
        "semantic_type": event["semantic_type"],
        "method": METHOD,
        "status": "EXTRACTION_ERROR",
        "selected_candidate_id": "",
        "selected_value": "",
        "template_id": template_policy["template_id"],
        "source_policy_file": template_policy["source_policy_file"],
        "fact_names": "|".join(sorted(fact_schema)),
        "fact_schema": json.dumps(fact_schema, ensure_ascii=False, sort_keys=True),
        "extracted_facts": "",
        "manual_fact_match_count": 0,
        "manual_fact_total": len(manual_facts),
        "manual_fact_accuracy": 0,
        "manual_fact_mismatches": "",
        "missing_facts_reported": "",
        "coerced_facts": "",
        "applicable_rule_slot": "",
        "allowed_values": "",
        "survivor_count": 0,
        "decision_error": "",
        "extraction_error": "",
        "evidence_notes": "",
        "manual_schema_used": True,
        "manual_rule_template_used": True,
        "manual_fact_values_used_in_prompt": False,
        "candidate_descriptions_used_in_prompt": False,
        "allowed_values_used_in_prompt": False,
        "oracle_loaded_in_decision_phase": False,
        "qwen_called": True,
        "runtime_ms": 0,
        "response_mode": "",
        "prompt_eval_count": 0,
        "eval_count": 0,
        "done_reason": "",
        "raw_content": "",
    }

    try:
        parsed, runtime_ms, mode, raw, metadata = qwen_call(
            args,
            system_prompt(),
            build_prompt(event, fact_schema),
            extraction_schema(fact_schema),
            seed,
        )
        row.update(
            {
                "runtime_ms": runtime_ms,
                "response_mode": mode,
                "raw_content": raw,
                "prompt_eval_count": metadata.get("prompt_eval_count", 0),
                "eval_count": metadata.get("eval_count", 0),
                "done_reason": metadata.get("done_reason", ""),
            }
        )
        extracted_facts, coerced = normalize_facts(parsed, fact_schema)
        matched, total, mismatches = fact_matches_manual(extracted_facts, manual_facts)
        row.update(
            {
                "extracted_facts": json.dumps(
                    extracted_facts, ensure_ascii=False, sort_keys=True
                ),
                "manual_fact_match_count": matched,
                "manual_fact_total": total,
                "manual_fact_accuracy": matched / total if total else 0,
                "manual_fact_mismatches": " || ".join(mismatches),
                "missing_facts_reported": "|".join(parsed.get("missing_facts", []))
                if isinstance(parsed.get("missing_facts"), list)
                else "",
                "coerced_facts": "|".join(coerced),
                "evidence_notes": str(parsed.get("evidence_notes", "")),
            }
        )
        selected, rule, allowed, survivor_count, decision_error = execute_policy(
            event, template_policy, extracted_facts
        )
        row.update(
            {
                "status": "SELECTED" if selected else "ABSTAIN",
                "selected_candidate_id": selected.get("candidate_id", "") if selected else "",
                "selected_value": selected.get("display_value", "") if selected else "",
                "applicable_rule_slot": rule.get("rule_id", "") if rule else "",
                "allowed_values": "|".join(sorted(allowed)),
                "survivor_count": survivor_count,
                "decision_error": decision_error,
            }
        )
    except Exception as exc:
        row["status"] = "EXTRACTION_ERROR"
        row["extraction_error"] = f"{type(exc).__name__}: {exc}"
    return row


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    groups["ALL"] = rows
    for row in rows:
        groups[str(row["semantic_type"])].append(row)

    order = {
        "ALL": 0,
        "TEMPORAL_VERSION": 1,
        "GENERAL_RULE_EXCEPTION": 2,
        "CROSS_SENTENCE_SCOPE": 3,
    }
    result: list[dict[str, Any]] = []
    for semantic_type, subset in sorted(groups.items(), key=lambda item: order.get(item[0], 99)):
        attempts = len(subset)
        correct = sum(1 for row in subset if row.get("oracle_correct") is True)
        selected = sum(1 for row in subset if row["status"] == "SELECTED")
        wrong = sum(
            1
            for row in subset
            if row["status"] == "SELECTED" and row.get("oracle_correct") is False
        )
        abstain = sum(1 for row in subset if row["status"] == "ABSTAIN")
        extraction_errors = sum(1 for row in subset if row["status"] == "EXTRACTION_ERROR")
        fact_match = sum(int(row["manual_fact_match_count"]) for row in subset)
        fact_total = sum(int(row["manual_fact_total"]) for row in subset)
        runtimes = [int(row["runtime_ms"]) for row in subset if int(row["runtime_ms"]) > 0]
        prompt_tokens = sum(int(row["prompt_eval_count"]) for row in subset)
        completion_tokens = sum(int(row["eval_count"]) for row in subset)
        low, high = wilson_interval(correct, attempts)
        result.append(
            {
                "method": METHOD,
                "semantic_type": semantic_type,
                "attempts": attempts,
                "selected": selected,
                "oracle_accuracy": correct / attempts if attempts else 0,
                "oracle_ci95_low": low,
                "oracle_ci95_high": high,
                "wrong_selection_rate": wrong / attempts if attempts else 0,
                "abstain_rate": abstain / attempts if attempts else 0,
                "extraction_error_rate": extraction_errors / attempts if attempts else 0,
                "manual_fact_accuracy": fact_match / fact_total if fact_total else 0,
                "qwen_calls": attempts,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "mean_runtime_ms": round(statistics.mean(runtimes), 2) if runtimes else 0,
                "manual_schema_used": True,
                "manual_rule_template_used": True,
                "manual_fact_values_used_in_prompt": False,
                "candidate_descriptions_used_in_prompt": False,
                "allowed_values_used_in_prompt": False,
            }
        )
    return result


def summarize_by_event(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["event_id"])].append(row)

    result: list[dict[str, Any]] = []
    for event_id, subset in sorted(groups.items()):
        attempts = len(subset)
        correct = sum(1 for row in subset if row.get("oracle_correct") is True)
        selected_values = sorted(
            {
                str(row.get("selected_value", ""))
                for row in subset
                if str(row.get("selected_value", ""))
            }
        )
        fact_accuracy = statistics.mean(
            float(row["manual_fact_accuracy"]) for row in subset
        )
        result.append(
            {
                "event_id": event_id,
                "semantic_type": subset[0]["semantic_type"],
                "attempts": attempts,
                "oracle_accuracy": correct / attempts if attempts else 0,
                "strict_event_success": correct == attempts,
                "selected_values": "|".join(selected_values),
                "selection_stable": len(selected_values) <= 1,
                "manual_fact_accuracy": round(fact_accuracy, 6),
                "extraction_errors": sum(
                    1 for row in subset if row["status"] == "EXTRACTION_ERROR"
                ),
                "abstains": sum(1 for row in subset if row["status"] == "ABSTAIN"),
            }
        )
    return result


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs must be positive")

    events = load_public_events(args.split)
    if args.only:
        wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
        events = [event for event in events if str(event.get("event_id", "")).upper() in wanted]
        if not events:
            raise RuntimeError(f"--only={sorted(wanted)} has no matching events")

    print(f"[连接检查] Ollama={args.ollama_url}，模型={args.model}")
    check_ollama(args.ollama_url, args.model, args.timeout)

    rows: list[dict[str, Any]] = []
    print(
        f"[开始] method={METHOD} | split={args.split} | events={len(events)} | "
        f"runs={args.runs} | seed={args.seed}"
    )
    for run_index in range(1, args.runs + 1):
        for event in events:
            row = run_one(event, run_index, args)
            rows.append(row)
            print(
                f"[{METHOD}] run={run_index}/{args.runs} event={event['event_id']} "
                f"status={row['status']} selected={row['selected_value'] or '-'} "
                f"fact_acc={float(row['manual_fact_accuracy']):.2%} "
                f"runtime={row['runtime_ms']}ms"
            )

    # Offline scoring boundary: Oracle is loaded only after all predictions.
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
    by_event = summarize_by_event(rows)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    detail_csv = OUTPUT_DIR / f"{args.prefix}-details.csv"
    summary_csv = OUTPUT_DIR / f"{args.prefix}-summary.csv"
    by_event_csv = OUTPUT_DIR / f"{args.prefix}-by-event.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    write_csv(detail_csv, rows)
    write_csv(summary_csv, summary)
    write_csv(by_event_csv, by_event)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_loaded_after_decisions": True,
        "method": METHOD,
        "split": args.split,
        "runs": args.runs,
        "seed": args.seed,
        "limitation": (
            "Uses manually specified fact names/types and manually specified template "
            "rules. Qwen extracts only fact values from public case/documents; it does "
            "not learn rules or see candidates, allowed_values, or Oracle during extraction."
        ),
        "summary": summary,
        "by_event": by_event,
        "details": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "LLM-extracted facts + template policy",
        f"split={args.split}",
        f"runs={args.runs}",
        f"seed={args.seed}",
        "manual_schema_used=True",
        "manual_rule_template_used=True",
        "manual_fact_values_used_in_prompt=False",
        "candidate_descriptions_used_in_prompt=False",
        "allowed_values_used_in_prompt=False",
        "oracle_loaded_after_decisions=True",
        "",
    ]
    for row in summary:
        lines.append(
            f"[{row['semantic_type']}] Oracle={row['oracle_accuracy']:.2%} "
            f"wrong={row['wrong_selection_rate']:.2%} "
            f"abstain={row['abstain_rate']:.2%} "
            f"extract_err={row['extraction_error_rate']:.2%} "
            f"fact_acc={row['manual_fact_accuracy']:.2%} "
            f"qwen_calls={row['qwen_calls']} tokens={row['total_tokens']}"
        )
    lines.extend(
        ["", f"details={detail_csv}", f"summary={summary_csv}", f"by_event={by_event_csv}", f"json={json_path}"]
    )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[llm extracted facts template policy stopped] {type(exc).__name__}: {exc}")
        raise SystemExit(2)
