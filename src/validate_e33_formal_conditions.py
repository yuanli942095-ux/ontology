from __future__ import annotations

"""用显式闭世界事实和优先级规则验证E33候选。

该脚本不声称自动从自然语言抽取规则。E33-formal-policy.json是人工形式化的
开发集规则，用来验证硬门禁能否阻止未满足前提的36个月候选。
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import BUILD_DIR, ORACLE_CSV, OUTPUT_DIR, PROJECT_DIR, load_csv, write_csv


DEFAULT_EVENT_FILE = BUILD_DIR / "semantic-events.json"
DEFAULT_POLICY = PROJECT_DIR / "benchmark" / "semantic-v2" / "rules" / "E33-formal-policy.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证E33显式形式条件门禁")
    parser.add_argument("--event-file", default=str(DEFAULT_EVENT_FILE))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    return parser.parse_args()


def load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON顶层不是对象：{path}")
    return value


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_DIR / path


def matches_rule(facts: dict[str, Any], condition: dict[str, Any]) -> bool:
    """显式闭世界匹配；条件涉及的事实必须存在且类型和值完全一致。"""
    for key, expected in condition.items():
        if key not in facts or type(facts[key]) is not type(expected) or facts[key] != expected:
            return False
    return True


def select_applicable_rule(policy: dict[str, Any]) -> dict[str, Any]:
    facts = policy.get("facts")
    rules = policy.get("rules")
    if not isinstance(facts, dict) or not isinstance(rules, list) or not rules:
        raise RuntimeError("policy必须包含facts对象和非空rules数组")
    for key, value in facts.items():
        if not isinstance(key, str) or not isinstance(value, bool):
            raise RuntimeError("E33事实必须是显式JSON布尔值，不能用缺失表示false")

    applicable: list[dict[str, Any]] = []
    for raw_rule in rules:
        if not isinstance(raw_rule, dict):
            raise RuntimeError("rules中的每项必须是对象")
        condition = raw_rule.get("when")
        allowed = raw_rule.get("allowed_values")
        if not isinstance(condition, dict) or not isinstance(allowed, list) or not allowed:
            raise RuntimeError(f"规则{raw_rule.get('rule_id')}缺少when或allowed_values")
        if matches_rule(facts, condition):
            applicable.append(raw_rule)
    if not applicable:
        raise RuntimeError("没有规则适用于当前显式事实；安全策略应ABSTAIN")

    highest = max(int(rule.get("priority", 0)) for rule in applicable)
    winners = [rule for rule in applicable if int(rule.get("priority", 0)) == highest]
    if len(winners) != 1:
        raise RuntimeError(f"最高优先级规则不唯一：{[rule.get('rule_id') for rule in winners]}")
    return winners[0]


def load_e33_candidates(event_file: Path) -> list[dict[str, Any]]:
    payload = load_json_object(event_file)
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    if "oracle_candidate_id" in serialized or "oracle_value" in serialized:
        raise RuntimeError("公开事件文件包含Oracle字段，形式门禁中止")
    events = payload.get("events")
    if not isinstance(events, list):
        raise RuntimeError("semantic-events.json缺少events数组")
    matches = [event for event in events if isinstance(event, dict) and event.get("event_id") == "E33"]
    if len(matches) != 1:
        raise RuntimeError(f"E33事件数量={len(matches)}")
    candidates = matches[0].get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("E33缺少候选")
    return candidates


def main() -> int:
    args = parse_args()
    event_file = resolve_path(args.event_file)
    policy_file = resolve_path(args.policy)
    policy = load_json_object(policy_file)
    if policy.get("event_id") != "E33":
        raise RuntimeError("policy.event_id必须为E33")
    if policy.get("semantics") != "closed_world_explicit_facts":
        raise RuntimeError("policy必须声明closed_world_explicit_facts语义")

    # 在线形式门禁：这里只读公开候选和显式规则，不读取Oracle。
    candidates = load_e33_candidates(event_file)
    rule = select_applicable_rule(policy)
    allowed_values = {str(value).strip() for value in rule["allowed_values"]}
    rows: list[dict[str, Any]] = []
    print("E33显式形式条件门禁")
    print(f"事实={json.dumps(policy['facts'], ensure_ascii=False)}")
    print(f"适用规则={rule['rule_id']} | 允许值={sorted(allowed_values)}")
    for candidate in candidates:
        value = str(candidate.get("display_value", "")).strip()
        gate_pass = value in allowed_values
        rows.append(
            {
                "event_id": "E33",
                "candidate_id": candidate.get("candidate_id", ""),
                "display_value": value,
                "applicable_rule_id": rule.get("rule_id", ""),
                "rule_priority": rule.get("priority", 0),
                "formal_condition_pass": gate_pass,
                "decision": "SURVIVE" if gate_pass else "FILTER_OUT",
                "reason": rule.get("explanation", ""),
            }
        )
        print(
            f"[{candidate.get('candidate_id')}] value={value} | "
            f"{'SURVIVE' if gate_pass else 'FILTER_OUT'}"
        )

    survivors = [row for row in rows if row["formal_condition_pass"]]
    if len(survivors) != 1:
        raise RuntimeError(f"硬门禁后存活候选={len(survivors)}，部署策略必须ABSTAIN")

    # 隔离点：门禁和唯一候选选择完成后才读取私有Oracle做离线评估。
    oracle_rows = [
        row for row in load_csv(ORACLE_CSV)
        if row.get("event_id") == "E33" and row.get("status", "").strip().upper() == "READY"
    ]
    if len(oracle_rows) != 1:
        raise RuntimeError(f"E33 READY Oracle数量={len(oracle_rows)}")
    oracle = oracle_rows[0]
    selected_id = str(survivors[0]["candidate_id"])
    oracle_match = selected_id == oracle["oracle_candidate_id"]
    for row in rows:
        row["oracle_loaded_after_gate"] = True
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        row["oracle_match"] = bool(row["formal_condition_pass"] and row["candidate_id"] == oracle["oracle_candidate_id"])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "e33-formal-condition-validation.csv"
    json_path = OUTPUT_DIR / "e33-formal-condition-validation.json"
    log_path = OUTPUT_DIR / "e33-formal-condition-validation.log"
    write_csv(csv_path, rows)
    result = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "event_id": "E33",
        "semantics": "closed_world_explicit_facts",
        "facts": policy["facts"],
        "applicable_rule": rule,
        "survivor_candidate_id": selected_id,
        "survivor_value": survivors[0]["display_value"],
        "oracle_loaded_after_gate": True,
        "oracle_match": oracle_match,
        "rows": rows,
    }
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "E33显式形式条件门禁",
        f"适用规则={rule['rule_id']}",
        f"存活候选={selected_id}，值={survivors[0]['display_value']}",
        f"离线Oracle匹配={oracle_match}",
        "Oracle在门禁后加载=True",
        "边界：规则与事实为受控开发集人工形式化输入，不是自动文档抽取结果。",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n唯一存活={selected_id} | value={survivors[0]['display_value']}")
    print(f"离线Oracle匹配={oracle_match}")
    print(f"CSV：{csv_path}")
    print(f"JSON：{json_path}")
    print(f"日志：{log_path}")
    print("[边界] 规则和事实是人工形式化的受控开发输入，不是自动从文档抽取。")
    return 0 if oracle_match else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[E33形式门禁停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
