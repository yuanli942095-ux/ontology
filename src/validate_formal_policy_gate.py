from __future__ import annotations

"""执行六个语义事件的形式策略硬门禁，并在全部决策后加载Oracle评分。

策略来自受控开发集的人工形式化，不声称能够从自然语言自动抽取。在线门禁阶段
只读取公开事件、候选以及rules目录中的显式事实和规则。
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from semantic_v2_common import BENCHMARK_DIR, OUTPUT_DIR, write_csv
from run_semantic_benchmark_v2 import load_oracle_after_predictions, load_public_events


POLICY_DIR = BENCHMARK_DIR / "rules"
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


def assert_no_policy_leakage(value: Any, path: str = "policy") -> None:
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
        return conditions
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
    """独立加载并最小化策略，不依赖消融脚本。"""
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
                "conditions": conditions,
                "allowed_values": allowed_values,
            }
        )
    return {
        "semantics": str(raw.get("semantics", "explicit_facts_prioritized_rules")),
        "facts": facts,
        "rules": normalized_rules,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="形式策略硬门禁验证")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    parser.add_argument("--only", default="", help="只验证指定事件，多个事件用逗号分隔")
    return parser.parse_args()


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


def clause_matches(facts: dict[str, Any], clause: dict[str, Any]) -> bool:
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


def select_rule(policy: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    facts = policy["facts"]
    applicable = [
        rule
        for rule in policy["rules"]
        if all(clause_matches(facts, clause) for clause in rule["conditions"])
    ]
    if not applicable:
        return None, "没有适用规则"
    highest = max(int(rule["priority"]) for rule in applicable)
    winners = [rule for rule in applicable if int(rule["priority"]) == highest]
    if len(winners) != 1:
        return None, f"最高优先级规则不唯一：{[rule['rule_id'] for rule in winners]}"
    return winners[0], ""


def main() -> int:
    args = parse_args()
    events = load_public_events(args.split)
    if args.only:
        wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
        events = [event for event in events if str(event.get("event_id", "")).upper() in wanted]
        if not events:
            raise RuntimeError(f"--only={sorted(wanted)}没有匹配事件")

    # 在线阶段：完成全部规则匹配和候选过滤，不读取Oracle。
    rows: list[dict[str, Any]] = []
    print("形式策略硬门禁验证（Oracle后加载）")
    for event in events:
        policy = load_neutral_policy(event)
        rule, error = select_rule(policy)
        allowed = set(rule["allowed_values"]) if rule else set()
        survivors = [
            candidate
            for candidate in event["candidates"]
            if str(candidate.get("display_value", "")).strip() in allowed
        ]
        selected = survivors[0] if len(survivors) == 1 else None
        status = "SAFE_ACCEPT" if selected else "ABSTAIN"
        if not error and len(survivors) != 1:
            error = f"存活候选数量={len(survivors)}"
        row = {
            "event_id": event["event_id"],
            "semantic_type": event["semantic_type"],
            "status": status,
            "applicable_rule_id": rule["rule_id"] if rule else "",
            "rule_priority": rule["priority"] if rule else "",
            "allowed_values": "|".join(sorted(allowed)),
            "survivor_count": len(survivors),
            "selected_candidate_id": selected["candidate_id"] if selected else "",
            "selected_value": selected["display_value"] if selected else "",
            "message": error,
        }
        rows.append(row)
        print(
            f"[{event['event_id']}] rule={row['applicable_rule_id'] or '-'} | "
            f"allowed={sorted(allowed)} | survivors={len(survivors)} | {status}"
        )

    # 隔离点：全部门禁决策完成后才读取私有Oracle。
    oracles = load_oracle_after_predictions()
    for row in rows:
        oracle = oracles.get(str(row["event_id"]))
        if oracle is None:
            raise RuntimeError(f"{row['event_id']}缺少READY Oracle")
        row["oracle_loaded_after_gate"] = True
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        row["oracle_value"] = oracle["oracle_value"]
        row["oracle_match"] = (
            row["status"] == "SAFE_ACCEPT"
            and row["selected_candidate_id"] == oracle["oracle_candidate_id"]
        )

    passed = sum(bool(row["oracle_match"]) for row in rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "formal-policy-gate-validation.csv"
    json_path = OUTPUT_DIR / "formal-policy-gate-validation.json"
    log_path = OUTPUT_DIR / "formal-policy-gate-validation.log"
    write_csv(csv_path, rows)
    result = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "split": args.split,
        "only": args.only,
        "events": len(rows),
        "safe_accepts": sum(row["status"] == "SAFE_ACCEPT" for row in rows),
        "oracle_matches": passed,
        "oracle_loaded_after_gate": True,
        "rows": rows,
    }
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "形式策略硬门禁验证",
        f"事件={len(rows)}",
        f"安全接受={result['safe_accepts']}/{len(rows)}",
        f"离线Oracle匹配={passed}/{len(rows)}",
        "Oracle在全部门禁后加载=True",
        "边界：策略为受控开发集人工形式化输入，不是自动文档抽取结果。",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n汇总：安全接受={result['safe_accepts']}/{len(rows)}，Oracle匹配={passed}/{len(rows)}")
    print(f"CSV：{csv_path}")
    print(f"JSON：{json_path}")
    print(f"日志：{log_path}")
    print("[边界] 策略为受控开发集人工形式化输入，不是自动文档抽取结果。")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[形式策略门禁停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
