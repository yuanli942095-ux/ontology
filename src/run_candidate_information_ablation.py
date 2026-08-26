from __future__ import annotations

"""比较候选信息层级对Qwen语义决策的影响。

预测阶段只读取公开事件、文档和形式安全候选。全部预测结束后才加载私有
Oracle评分，避免答案泄漏。
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from semantic_v2_common import BENCHMARK_DIR, OUTPUT_DIR, check_ollama, stable_seed, write_csv
from run_semantic_benchmark_v2 import (
    base_system_prompt,
    blinded_event,
    by_option,
    by_value,
    load_oracle_after_predictions,
    load_public_events,
    qwen_call,
    qwen_constrained,
    qwen_direct,
    summarize,
)


METHODS = (
    "DIRECT_FREE",
    "VALUE_ENUM",
    "OPTION_VALUE_ONLY",
    "OPTION_FORMAL_OPERATION",
    "OPTION_FORMAL_POLICY",
    "OPTION_FORMAL_POLICY_HARD_GATE",
    "OPTION_DESCRIPTION",
    "OPTION_DESCRIPTION_MASKED",
    "OPTION_DESCRIPTION_SHUFFLED",
)

METHOD_SPECS = {
    "DIRECT_FREE": "自由生成值，不提供候选集合",
    "VALUE_ENUM": "只通过输出Schema限定可选值",
    "OPTION_VALUE_ONLY": "提供随机化选项编号和值",
    "OPTION_FORMAL_OPERATION": "提供随机化选项编号和真实有限修复算子，不提供人工描述",
    "OPTION_FORMAL_POLICY": "提供真实有限修复算子及结构化事实和优先级规则",
    "OPTION_FORMAL_POLICY_HARD_GATE": "程序执行形式策略硬门禁；唯一候选直接选择，多候选才调用Qwen",
    "OPTION_DESCRIPTION": "提供随机化选项编号、值和人工候选描述",
    "OPTION_DESCRIPTION_MASKED": "保留带描述选项格式，但将人工候选描述替换为中性占位文本",
    "OPTION_DESCRIPTION_SHUFFLED": "保留带描述选项格式，但在同一事件内错配人工候选描述",
}

POLICY_DIR = BENCHMARK_DIR / "rules"

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="候选信息层级消融实验")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    parser.add_argument("--only", default="", help="只运行指定事件，如E33；留空运行整个split")
    parser.add_argument(
        "--methods",
        default="",
        help="逗号分隔的方法名；留空运行全部方法",
    )
    parser.add_argument(
        "--prefix",
        default="candidate-information-ablation",
        help="输出文件名前缀，避免覆盖主实验结果",
    )
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--num-predict", type=int, default=300)
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--keep-alive", default="30m")
    return parser.parse_args()


def common_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "semantic_type": event["semantic_type"],
        "case_context": event["case_context"],
        "target": event["target"],
        "documents": event["document_contents"],
    }


def qwen_value_enum(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    """只在JSON Schema中封闭可输出值，不提供候选描述或候选编号。"""
    values = [str(item["display_value"]).strip() for item in event["candidates"]]
    payload = {
        **common_payload(event),
        "instruction": "直接输出适用值；value只能使用接口允许值，证据不足则ABSTAIN。",
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["abstain", "value", "reason"],
        "properties": {
            "abstain": {"type": "boolean"},
            "value": {"type": "string", "enum": values + ["ABSTAIN"]},
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
    abstain = parsed.get("abstain")
    value = parsed.get("value")
    if not isinstance(abstain, bool) or not isinstance(value, str):
        return None, "REJECTED_SCHEMA", "字段类型不符合Schema", extra
    if abstain or value == "ABSTAIN":
        return None, "ABSTAIN", str(parsed.get("reason", "")), extra
    selected = by_value(event, value)
    if selected is None:
        return None, "REJECTED_OUT_OF_CANDIDATE", f"值不在候选白名单：{value!r}", extra
    return selected, "SELECTED", str(parsed.get("reason", "")), extra


def qwen_option_value_only(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    """只提供随机化OPTION与值，不提供描述、算子或形式效果。"""
    option_ids = [str(item["candidate_id"]) for item in event["candidates"]]
    payload = {
        **common_payload(event),
        "candidate_values": [
            {"option_id": item["candidate_id"], "display_value": item["display_value"]}
            for item in event["candidates"]
        ],
        "instruction": "选择当前案件适用的option_id；证据不足则ABSTAIN。",
    }
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
    abstain = parsed.get("abstain")
    option_id = parsed.get("option_id")
    if not isinstance(abstain, bool) or not isinstance(option_id, str):
        return None, "REJECTED_SCHEMA", "字段类型不符合Schema", extra
    if abstain or option_id == "ABSTAIN":
        return None, "ABSTAIN", str(parsed.get("reason", "")), extra
    selected = by_option(event, option_id)
    if selected is None:
        return None, "REJECTED_OUT_OF_CANDIDATE", f"非法option_id={option_id!r}", extra
    return selected, "SELECTED", str(parsed.get("reason", "")), extra


def assert_neutral_operation(value: Any, path: str = "operation") -> None:
    """阻止人工判断、评测答案或候选效果混入形式操作。"""
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_OPERATION_KEY_PARTS):
                raise RuntimeError(f"形式操作含禁止字段：{path}.{key}")
            assert_neutral_operation(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_neutral_operation(child, f"{path}[{index}]")


def attach_neutral_operations(
    source_event: dict[str, Any], blinded: dict[str, Any]
) -> dict[str, Any]:
    """在候选随机化后恢复真实操作，但不恢复任何额外语义提示。"""
    operations: dict[str, dict[str, Any]] = {}
    for candidate in source_event["candidates"]:
        candidate_id = str(candidate["candidate_id"])
        operation = candidate.get("operation")
        if not isinstance(operation, dict) or not operation:
            raise RuntimeError(f"{source_event['event_id']}/{candidate_id}缺少可执行operation")
        assert_neutral_operation(operation)
        operations[candidate_id] = deepcopy(operation)

    for candidate in blinded["candidates"]:
        original_id = str(candidate["original_candidate_id"])
        if original_id not in operations:
            raise RuntimeError(f"随机化候选{original_id}无法映射到真实operation")
        candidate["neutral_operation"] = deepcopy(operations[original_id])
    return blinded


def qwen_option_formal_operation(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    """提供真实有限算子，但不提供人工候选描述、形式效果或Oracle。"""
    option_ids = [str(item["candidate_id"]) for item in event["candidates"]]
    operations: list[dict[str, Any]] = []
    for item in event["candidates"]:
        operation = item.get("neutral_operation")
        if not isinstance(operation, dict):
            raise RuntimeError(f"{event['event_id']}/{item['candidate_id']}缺少neutral_operation")
        assert_neutral_operation(operation)
        operations.append({"option_id": item["candidate_id"], "operation": operation})

    payload = {
        **common_payload(event),
        "formally_executable_options": operations,
        "instruction": (
            "根据案件与文档，从给定的真实有限修复操作中选择适用的option_id；"
            "不得改写或生成新操作，证据不足则ABSTAIN。"
        ),
    }
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
    abstain = parsed.get("abstain")
    option_id = parsed.get("option_id")
    if not isinstance(abstain, bool) or not isinstance(option_id, str):
        return None, "REJECTED_SCHEMA", "字段类型不符合Schema", extra
    if abstain or option_id == "ABSTAIN":
        return None, "ABSTAIN", str(parsed.get("reason", "")), extra
    selected = by_option(event, option_id)
    if selected is None:
        return None, "REJECTED_OUT_OF_CANDIDATE", f"非法option_id={option_id!r}", extra
    return selected, "SELECTED", str(parsed.get("reason", "")), extra


def assert_no_policy_leakage(value: Any, path: str = "policy") -> None:
    """策略可以包含规则和值，但不能包含Oracle或候选答案标识。"""
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_POLICY_KEY_PARTS):
                raise RuntimeError(f"形式策略含禁止字段：{path}.{key}")
            assert_no_policy_leakage(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_policy_leakage(child, f"{path}[{index}]")


def normalize_policy_conditions(
    event_id: str,
    rule_id: str,
    conditions: Any,
    when: Any,
) -> list[dict[str, Any]]:
    if conditions is not None:
        if not isinstance(conditions, list):
            raise RuntimeError(f"{event_id}/{rule_id}的conditions不是数组")
        return deepcopy(conditions)
    if not isinstance(when, dict):
        raise RuntimeError(f"{event_id}/{rule_id}缺少conditions或when")

    normalized: list[dict[str, Any]] = []
    for fact, requirement in when.items():
        if isinstance(requirement, dict):
            for operator, expected in requirement.items():
                operator_text = str(operator)
                if operator_text not in SUPPORTED_POLICY_OPERATORS:
                    raise RuntimeError(
                        f"{event_id}/{rule_id}不支持的when算子：{operator_text}"
                    )
                normalized.append(
                    {"fact": str(fact), "operator": operator_text, "value": expected}
                )
        else:
            normalized.append(
                {"fact": str(fact), "operator": "equals", "value": requirement}
            )
    return normalized


def load_neutral_policy(event: dict[str, Any]) -> dict[str, Any]:
    """加载并最小化公开形式策略，主动剥离来源说明和人工解释。"""
    event_id = str(event["event_id"])
    path = POLICY_DIR / f"{event_id}-formal-policy.json"
    if not path.is_file():
        raise FileNotFoundError(f"缺少形式策略：{path}")
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"形式策略顶层不是对象：{path}")
    assert_no_policy_leakage(raw)
    if raw.get("event_id") != event_id:
        raise RuntimeError(f"策略事件不匹配：期望{event_id}，实际{raw.get('event_id')}")
    facts = raw.get("facts")
    rules = raw.get("rules")
    if not isinstance(facts, dict) or not facts:
        raise RuntimeError(f"{event_id}策略缺少显式facts")
    if not isinstance(rules, list) or not rules:
        raise RuntimeError(f"{event_id}策略缺少rules")

    candidate_values = {
        str(candidate.get("display_value", "")).strip()
        for candidate in event["candidates"]
    }
    normalized_rules: list[dict[str, Any]] = []
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise RuntimeError(f"{event_id}第{index}条规则不是对象")
        rule_id = str(rule.get("rule_id", "")).strip()
        allowed = rule.get("allowed_values")
        if not rule_id or not isinstance(allowed, list) or not allowed:
            raise RuntimeError(f"{event_id}第{index}条规则缺少rule_id或allowed_values")
        allowed_values = [str(value).strip() for value in allowed]
        if not set(allowed_values).issubset(candidate_values):
            raise RuntimeError(
                f"{event_id}/{rule_id}引用候选外值："
                f"{sorted(set(allowed_values) - candidate_values)}"
            )
        conditions = normalize_policy_conditions(
            event_id, rule_id, rule.get("conditions"), rule.get("when")
        )
        for clause in conditions:
            if not isinstance(clause, dict):
                raise RuntimeError(f"{event_id}/{rule_id}条件不是对象")
            if set(clause) != {"fact", "operator", "value"}:
                raise RuntimeError(f"{event_id}/{rule_id}条件字段必须为fact/operator/value")
            if str(clause["fact"]) not in facts:
                raise RuntimeError(f"{event_id}/{rule_id}引用未知事实{clause['fact']}")
            if str(clause["operator"]) not in SUPPORTED_POLICY_OPERATORS:
                raise RuntimeError(
                    f"{event_id}/{rule_id}不支持的条件算子：{clause['operator']}"
                )
        normalized_rules.append(
            {
                "rule_id": rule_id,
                "priority": int(rule.get("priority", 0)),
                "conditions": deepcopy(conditions),
                "allowed_values": allowed_values,
            }
        )
    return {
        "semantics": str(raw.get("semantics", "explicit_facts_prioritized_rules")),
        "facts": deepcopy(facts),
        "rules": normalized_rules,
    }


def qwen_option_formal_policy(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    """提供形式操作、显式事实与优先级规则，不提供人工描述或Oracle。"""
    option_ids = [str(item["candidate_id"]) for item in event["candidates"]]
    operations: list[dict[str, Any]] = []
    for item in event["candidates"]:
        operation = item.get("neutral_operation")
        if not isinstance(operation, dict):
            raise RuntimeError(f"{event['event_id']}/{item['candidate_id']}缺少neutral_operation")
        assert_neutral_operation(operation)
        operations.append({"option_id": item["candidate_id"], "operation": operation})
    policy = load_neutral_policy(event)
    payload = {
        **common_payload(event),
        "formally_executable_options": operations,
        "formal_policy": policy,
        "instruction": (
            "严格按照formal_policy中的显式事实、条件和优先级判断适用规则，再从"
            "formally_executable_options选择新值符合该规则的option_id；不得生成新操作，"
            "没有唯一结论则ABSTAIN。"
        ),
    }
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
    abstain = parsed.get("abstain")
    option_id = parsed.get("option_id")
    if not isinstance(abstain, bool) or not isinstance(option_id, str):
        return None, "REJECTED_SCHEMA", "字段类型不符合Schema", extra
    if abstain or option_id == "ABSTAIN":
        return None, "ABSTAIN", str(parsed.get("reason", "")), extra
    selected = by_option(event, option_id)
    if selected is None:
        return None, "REJECTED_OUT_OF_CANDIDATE", f"非法option_id={option_id!r}", extra
    return selected, "SELECTED", str(parsed.get("reason", "")), extra


def event_with_description_variant(
    event: dict[str, Any], variant: str, seed: int
) -> dict[str, Any]:
    """Perturb only the human-written candidate descriptions."""
    changed = deepcopy(event)
    if variant == "MASKED":
        for candidate in changed["candidates"]:
            candidate["description"] = (
                "人工候选说明已遮蔽；不得从说明文字推断答案，只能依据公开文档、"
                "目标字段、候选值和形式效果判断。"
            )
        return changed
    if variant == "SHUFFLED":
        descriptions = [candidate.get("description", "") for candidate in event["candidates"]]
        shuffled = descriptions[:]
        rng = random.Random(stable_seed(seed, event["event_id"], "description-shuffle"))
        for _ in range(20):
            rng.shuffle(shuffled)
            if len(shuffled) < 2 or all(
                original != replacement
                for original, replacement in zip(descriptions, shuffled)
            ):
                break
        if len(shuffled) > 1 and any(
            original == replacement
            for original, replacement in zip(descriptions, shuffled)
        ):
            shuffled = shuffled[1:] + shuffled[:1]
        for candidate, description in zip(changed["candidates"], shuffled):
            candidate["description"] = description
        return changed
    raise RuntimeError(f"未知候选描述扰动：{variant}")


def qwen_description_variant(
    event: dict[str, Any], seed: int, args: argparse.Namespace, variant: str
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    perturbed = event_with_description_variant(event, variant, seed)
    selected, status, reason, extra = qwen_constrained(perturbed, seed, args)
    extra["description_variant"] = variant
    return selected, status, reason, extra


def as_date(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"不是ISO日期：{value!r}") from exc


def as_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"不是数值：{value!r}") from exc


def policy_clause_matches(facts: dict[str, Any], clause: dict[str, Any]) -> bool:
    """确定性执行受控策略条件；不调用模型，也不读取Oracle。"""
    fact_name = str(clause["fact"])
    actual = facts[fact_name]
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
    raise ValueError(f"不支持的条件算子：{operator}")


def select_applicable_policy_rule(
    policy: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """选择唯一最高优先级适用规则；歧义时返回错误而不猜测。"""
    facts = policy["facts"]
    applicable = [
        rule
        for rule in policy["rules"]
        if all(policy_clause_matches(facts, clause) for clause in rule["conditions"])
    ]
    if not applicable:
        return None, "没有适用规则"
    highest = max(int(rule["priority"]) for rule in applicable)
    winners = [rule for rule in applicable if int(rule["priority"]) == highest]
    if len(winners) != 1:
        return None, f"最高优先级规则不唯一：{[rule['rule_id'] for rule in winners]}"
    return winners[0], ""


def option_formal_policy_hard_gate(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    """先确定性执行策略并过滤候选；只有多个存活候选时才让Qwen排序。"""
    policy = load_neutral_policy(event)
    rule, error = select_applicable_policy_rule(policy)
    allowed_values = {
        str(value).strip() for value in rule["allowed_values"]
    } if rule else set()
    survivors = [
        candidate
        for candidate in event["candidates"]
        if str(candidate.get("display_value", "")).strip() in allowed_values
    ]
    gate_extra: dict[str, Any] = {
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
        gate_extra["decision_path"] = "ABSTAIN_NO_UNIQUE_RULE"
        return None, "ABSTAIN", error, gate_extra
    if not survivors:
        gate_extra["decision_path"] = "ABSTAIN_NO_SURVIVOR"
        return None, "ABSTAIN", "形式策略门禁没有存活候选", gate_extra
    if len(survivors) == 1:
        gate_extra["decision_path"] = "DETERMINISTIC_SELECT"
        return (
            survivors[0],
            "SELECTED",
            f"硬门禁：规则{rule['rule_id']}唯一允许值={sorted(allowed_values)}，唯一存活候选直接选择",
            gate_extra,
        )

    gated_event = deepcopy(event)
    gated_event["candidates"] = deepcopy(survivors)
    selected, status, reason, qwen_extra = qwen_option_formal_policy(
        gated_event, seed, args
    )
    gate_extra.update(qwen_extra)
    gate_extra.update(
        {
            "policy_loaded": True,
            "applicable_rule": rule["rule_id"],
            "allowed_values": "|".join(sorted(allowed_values)),
            "survivor_count": len(survivors),
            "decision_path": "QWEN_RANK_AFTER_GATE",
            "qwen_called": True,
        }
    )
    return selected, status, reason, gate_extra


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
        raise ValueError("--runs必须大于0")
    events = load_public_events(args.split)
    if args.only:
        wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
        events = [event for event in events if str(event.get("event_id", "")).upper() in wanted]
        if not events:
            raise RuntimeError(f"--only={sorted(wanted)}没有匹配事件")
    selected_methods = list(METHODS)
    if args.methods:
        wanted_methods = [
            item.strip().upper() for item in args.methods.split(",") if item.strip()
        ]
        unknown = [item for item in wanted_methods if item not in METHODS]
        if unknown:
            raise RuntimeError(f"--methods包含未知方法：{unknown}")
        selected_methods = wanted_methods

    print(f"[连接检查] Ollama={args.ollama_url}，模型={args.model}")
    check_ollama(args.ollama_url, args.model, args.timeout)
    print("[连接检查] Ollama与模型均可用。")

    total = len(events) * args.runs * len(selected_methods)
    print("\n候选信息层级消融实验")
    print(f"split={args.split}，事件={len(events)}，runs={args.runs}，方法尝试={total}")
    print("说明：硬门禁组在唯一候选时不调用Qwen，实际Qwen调用数见输出明细。")
    details: list[dict[str, Any]] = []
    counter = 0
    for run in range(1, args.runs + 1):
        run_seed = args.seed + run - 1
        for source_event in events:
            event = attach_neutral_operations(
                source_event, blinded_event(source_event, run_seed)
            )
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
                    elif method == "VALUE_ENUM":
                        selected, status, reason, extra = qwen_value_enum(event, call_seed, args)
                    elif method == "OPTION_VALUE_ONLY":
                        selected, status, reason, extra = qwen_option_value_only(event, call_seed, args)
                    elif method == "OPTION_FORMAL_OPERATION":
                        selected, status, reason, extra = qwen_option_formal_operation(
                            event, call_seed, args
                        )
                    elif method == "OPTION_FORMAL_POLICY":
                        selected, status, reason, extra = qwen_option_formal_policy(
                            event, call_seed, args
                        )
                    elif method == "OPTION_FORMAL_POLICY_HARD_GATE":
                        selected, status, reason, extra = option_formal_policy_hard_gate(
                            event, call_seed, args
                        )
                    elif method == "OPTION_DESCRIPTION":
                        selected, status, reason, extra = qwen_constrained(event, call_seed, args)
                    elif method == "OPTION_DESCRIPTION_MASKED":
                        selected, status, reason, extra = qwen_description_variant(
                            event, call_seed, args, "MASKED"
                        )
                    elif method == "OPTION_DESCRIPTION_SHUFFLED":
                        selected, status, reason, extra = qwen_description_variant(
                            event, call_seed, args, "SHUFFLED"
                        )
                    else:
                        raise RuntimeError(f"未知方法：{method}")
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
                        "description_variant": extra.get("description_variant", ""),
                        "qwen_called": extra.get("qwen_called", method != "OPTION_FORMAL_POLICY_HARD_GATE"),
                    }
                )
                print(
                    f"  结果={status} | candidate={original_id or '-'} | "
                    f"value={display_value or '-'} | {extra.get('runtime_ms', 0)}ms",
                    flush=True,
                )

    # 隔离点：所有模型调用完成后才读取私有Oracle。
    oracles = load_oracle_after_predictions()
    for row in details:
        oracle = oracles.get(str(row["event_id"]))
        if oracle is None:
            raise RuntimeError(f"{row['event_id']}缺少READY Oracle")
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
    prefix = args.prefix
    detail_csv = OUTPUT_DIR / f"{prefix}-details.csv"
    summary_csv = OUTPUT_DIR / f"{prefix}-summary.csv"
    by_event_csv = OUTPUT_DIR / f"{prefix}-by-event.csv"
    event_method_csv = OUTPUT_DIR / f"{prefix}-event-level.csv"
    json_path = OUTPUT_DIR / f"{prefix}.json"
    log_path = OUTPUT_DIR / f"{prefix}.log"
    write_csv(detail_csv, details)
    write_csv(summary_csv, summary)
    write_csv(by_event_csv, by_event)
    write_csv(event_method_csv, event_methods)

    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_loaded_after_predictions": True,
        "method_specs": METHOD_SPECS,
        "formal_operation_leakage_guard": list(FORBIDDEN_OPERATION_KEY_PARTS),
        "formal_policy_leakage_guard": list(FORBIDDEN_POLICY_KEY_PARTS),
        "split": args.split,
        "only": args.only,
        "methods": selected_methods,
        "runs": args.runs,
        "attempt_summary": summary,
        "event_level_summary": event_methods,
        "by_event": by_event,
        "details": details,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["候选信息层级消融实验", "Oracle在全部预测结束后加载=True", ""]
    print("\n调用级汇总")
    for row in summary:
        if row["semantic_type"] != "ALL":
            continue
        line = (
            f"[{row['method']}] Oracle={row['oracle_accuracy']:.2%} | "
            f"错误选择={row['wrong_selection_rate']:.2%} | "
            f"无效={row['invalid_output_rate']:.2%} | ABSTAIN={row['abstain_rate']:.2%}"
        )
        print(line)
        lines.append(line)
    print("\n事件级汇总（独立单位）")
    lines.append("")
    for row in event_methods:
        line = (
            f"[{row['method']}] 严格事件成功={row['all_runs_correct_events']}/"
            f"{row['independent_events']} ({row['strict_event_accuracy']:.2%}) | "
            f"事件宏平均={row['macro_event_accuracy']:.2%} | "
            f"选择稳定事件={row['stable_events']}/{row['independent_events']}"
        )
        print(line)
        lines.append(line)
    lines.extend(
        [
            "",
            f"明细：{detail_csv}",
            f"调用级：{summary_csv}",
            f"按事件：{by_event_csv}",
            f"事件级：{event_method_csv}",
            f"JSON：{json_path}",
        ]
    )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n明细：{detail_csv}")
    print(f"调用级：{summary_csv}")
    print(f"按事件：{by_event_csv}")
    print(f"事件级：{event_method_csv}")
    print(f"JSON：{json_path}")
    print(
        "[边界] 本实验区分自由生成、枚举值、无描述选项、真实形式操作、形式策略和"
        "带描述选项；形式操作与形式策略组不读取人工候选描述或Oracle。"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[消融实验停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
