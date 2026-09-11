from __future__ import annotations

"""Semantic evaluation for AUTO_POLICY_V2_TYPE_AWARE_CANDIDATE_BLIND outputs.

This replaces strict JSON/string equality with family-level canonical matching:
insurance numeric/formula targets, WCAG criterion+level targets, and NIST
revision-change targets. It does not use candidate IDs or private Oracle.
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from external_real_v8_layout import construction_paths, hard_gate_paths, is_staged_layout
from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v1"
RULE_DIR = BENCHMARK_DIR / "rules"
EVENT_CSV = BENCHMARK_DIR / "input" / "external-real-event-template.csv"
DEFAULT_RAW_DIR = PROJECT_DIR / "output" / "auto-policy-v2" / "raw"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v2"

RUNS = 5
SEED = 20260820


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="semantic evaluator for auto-policy-v2")
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--prefix", default="auto-policy-v2-semantic-evaluation")
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--only", default="", help="comma-separated event IDs for smoke runs")
    return parser.parse_args()


def configure_benchmark(benchmark_dir: Path) -> None:
    global BENCHMARK_DIR, RULE_DIR, EVENT_CSV
    global DEFAULT_RAW_DIR, DEFAULT_OUTPUT_DIR

    BENCHMARK_DIR = benchmark_dir.resolve()
    if is_staged_layout(BENCHMARK_DIR):
        EVENT_CSV = construction_paths(BENCHMARK_DIR)["event_csv"]
        RULE_DIR = hard_gate_paths(BENCHMARK_DIR)["rules_dir"]
    else:
        RULE_DIR = BENCHMARK_DIR / "rules"
        EVENT_CSV = BENCHMARK_DIR / "input" / "external-real-event-template.csv"
    DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / BENCHMARK_DIR.name / "auto-policy-v3"
    DEFAULT_RAW_DIR = DEFAULT_OUTPUT_DIR / "raw"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def norm(value: object) -> str:
    text = str(value or "").strip().lower()
    replacements = {
        "×": "*",
        "＝": "=",
        "：": ":",
        "；": ";",
        "，": ",",
        "（": "(",
        "）": ")",
        "-": "_",
        "/": "_",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def compact(value: object) -> str:
    return re.sub(r"\s+", "", norm(value))


def simple_stems(value: object) -> set[str]:
    text = norm(value)
    raw_tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text)
    result: set[str] = set()
    for token in raw_tokens:
        if not token:
            continue
        result.add(token)
        for suffix in ("ing", "ed", "es", "s"):
            if token.endswith(suffix) and len(token) > len(suffix) + 2:
                result.add(token[: -len(suffix)])
        if token.endswith("ated") and len(token) > 6:
            result.add(token[:-4] + "ate")
    return result


def gold_target(gold_policy: dict[str, Any]) -> str:
    rules = gold_policy.get("rules", [])
    if not isinstance(rules, list) or not rules:
        return ""
    top = max(rules, key=lambda rule: int(rule.get("priority", 0)))
    allowed = top.get("allowed_values", [])
    if not isinstance(allowed, list) or not allowed:
        return ""
    return str(allowed[0]).strip()


def auto_semantic_result(auto_record: dict[str, Any]) -> str:
    response = auto_record.get("response", {})
    if not isinstance(response, dict):
        return ""
    rules = response.get("rules", [])
    if not isinstance(rules, list) or not rules:
        return ""
    top = max(rules, key=lambda rule: int(rule.get("priority", 0)))
    return str(top.get("semantic_result", "")).strip()


def parse_wcag_target(target: str) -> tuple[str, str, str] | None:
    text = compact(target)
    match = re.search(r"(\d+\.\d+\.\d+)", text)
    level_match = re.search(r"level=?(aaa|aa|a)\b", text)
    if not match or not level_match:
        return None
    if text.startswith("wcag22_added"):
        family = "wcag22_added"
    elif text.startswith("wcag21_cross_scope"):
        family = "wcag21_cross_scope"
    elif text.startswith("wcag21_input_rule"):
        family = "wcag21_input_rule"
    else:
        return None
    return family, match.group(1), level_match.group(1).upper()


def has_level(text: str, level: str) -> bool:
    level_lower = level.lower()
    return (
        re.search(rf"\blevel[=: ]*{re.escape(level_lower)}\b", text) is not None
        or re.search(rf"\bconformance level {re.escape(level_lower)}\b", text) is not None
        or re.search(rf"\b{re.escape(level_lower)}\b", text) is not None
    )


def criterion_name_tokens(event: dict[str, str], code: str) -> set[str]:
    text = f"{event.get('case_context', '')} {event.get('title', '')}"
    match = re.search(rf"{re.escape(code)}\s+([A-Za-z][A-Za-z \-]+?)(?:\.|;|,|$)", text)
    if not match:
        return set()
    tokens = {
        token
        for token in simple_stems(match.group(1))
        if len(token) > 2 and token not in {"the", "and", "for", "with", "success", "criterion"}
    }
    return tokens


def evaluate_wcag(target: str, auto: str, event: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    parsed = parse_wcag_target(target)
    if parsed is None:
        return False, {"reason": "not_wcag_target"}
    family, code, level = parsed
    text = norm(auto)
    compact_text = compact(auto)
    name_tokens = criterion_name_tokens(event, code)
    auto_tokens = simple_stems(auto)
    matched_name_tokens = {
        token
        for token in name_tokens
        if any(item.startswith(token) or token.startswith(item) for item in auto_tokens)
    }
    has_name = bool(name_tokens) and len(matched_name_tokens) >= max(1, min(2, len(name_tokens)))
    has_code = code in compact_text
    has_code_or_name = has_code or has_name
    level_ok = has_level(text, level)
    has_wcag22 = any(marker in compact_text for marker in ("wcag2.2", "wcag22"))
    has_wcag21 = any(marker in compact_text for marker in ("wcag2.1", "wcag21"))
    change_ok = any(
        marker in text
        for marker in (
            "addition",
            "added",
            "new",
            "active",
            "update",
            "updated",
            "include",
            "effective",
            "valid",
            "applicable",
            "current",
            "新增",
            "增加",
            "生效",
            "加入",
        )
    )
    change_ok = change_ok or "wcag22_added" in compact_text
    scope_ok = any(marker in text for marker in ("scope", "cross-reference", "resolved", "applies", "范围", "指代"))
    scope_ok = scope_ok or "wcag21_cross_scope" in compact_text
    input_ok = any(marker in text for marker in ("input modality", "input rule", "pointer", "label", "requirement", "added", "输入", "指针", "标签"))
    input_ok = input_ok or "wcag21_input_rule" in compact_text
    if family == "wcag22_added":
        correct = has_code_or_name and level_ok and has_wcag22 and change_ok
    elif family == "wcag21_cross_scope":
        correct = has_code_or_name and level_ok and (has_wcag21 or "wcag" in compact_text) and scope_ok
    else:
        correct = has_code_or_name and level_ok and (has_wcag21 or "wcag" in compact_text) and input_ok
    return correct, {
        "family": family,
        "code": code,
        "level": level,
        "has_code": has_code,
        "has_name": has_name,
        "expected_name_tokens": "|".join(sorted(name_tokens)),
        "matched_name_tokens": "|".join(sorted(matched_name_tokens)),
        "has_level": level_ok,
        "has_wcag22": has_wcag22,
        "has_wcag21": has_wcag21,
        "change_ok": change_ok,
        "scope_ok": scope_ok,
        "input_ok": input_ok,
    }


def evaluate_insurance(target: str, auto: str) -> tuple[bool, dict[str, Any]]:
    text = compact(auto)
    if target.startswith("spring=1100;summer_autumn=900"):
        correct = "1100" in text and "900" in text
        return correct, {"family": "insurance_amount_split", "has_1100": "1100" in text, "has_900": "900" in text}
    if target.startswith("five_level_definition"):
        has_30 = "30" in text
        has_45 = "45" in text
        has_60 = "60" in text
        has_level_language = any(
            marker in text
            for marker in ("level", "five", "五级", "5级", "级", "无倒伏", "轻微", "中等", "中度", "较重", "严重")
        )
        correct = has_30 and has_45 and has_60 and has_level_language
        return correct, {
            "family": "insurance_lodging_levels",
            "has_30": has_30,
            "has_45": has_45,
            "has_60": has_60,
            "has_level_language": has_level_language,
        }
    if target.startswith("formula=accident_date_limit_times_loss_rate"):
        canonical = "accident_date_limit_times_loss_rate" in compact(auto)
        natural = (
            any(marker in text for marker in ("出险日期", "事故日期", "事故日"))
            and any(marker in text for marker in ("亩赔偿限额", "每亩赔偿限额"))
            and "损失" in text
        )
        symbolic = "*" in text and ("limit" in text or "限额" in text) and ("loss" in text or "损失" in text)
        return canonical or natural or symbolic, {
            "family": "insurance_formula",
            "canonical_match": canonical,
            "natural_language_match": natural,
            "symbolic_match": symbolic,
        }
    return False, {"reason": "not_insurance_target"}


NIST_TARGET_TOKENS = {
    "risk_management_text=updated_context_setting": {"risk", "management", "text", "context", "setting", "update"},
    "continuous_evaluation_metrics=recommended_added": {"continuous", "evaluation", "metrics", "recommend"},
    "fraud_requirements=expanded_identity_proofing": {"fraud", "requirement", "expand", "identity", "proofing"},
    "identity_proofing_controls=restructured_roles_and_types": {"identity", "proofing", "control", "restructure", "role", "type"},
    "controls_added=injection_attacks_and_forged_media": {"control", "injection", "attack", "forged", "media"},
    "syncable_authenticators=integrated": {"syncable", "authenticator", "integrat"},
}


def evaluate_nist(target: str, auto: str) -> tuple[bool, dict[str, Any]]:
    expected = NIST_TARGET_TOKENS.get(target)
    if expected is None:
        return False, {"reason": "not_nist_target"}
    stems = simple_stems(auto)
    matched = {token for token in expected if any(item.startswith(token) or token.startswith(item) for item in stems)}
    required = len(expected)
    correct = len(matched) >= required
    return correct, {
        "family": "nist_revision_change",
        "expected_tokens": "|".join(sorted(expected)),
        "matched_tokens": "|".join(sorted(matched)),
        "matched_count": len(matched),
        "required_count": required,
    }


def evaluate_semantic(target: str, auto: str, event: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    correct, evidence = evaluate_insurance(target, auto)
    if evidence.get("family"):
        return correct, evidence
    correct, evidence = evaluate_wcag(target, auto, event)
    if evidence.get("family"):
        return correct, evidence
    correct, evidence = evaluate_nist(target, auto)
    if evidence.get("family"):
        return correct, evidence
    return compact(target) == compact(auto), {"family": "exact_fallback"}


def select_event_rows(
    rows: list[dict[str, str]],
    only: set[str] | None = None,
) -> list[dict[str, str]]:
    selected = [
        row for row in rows if str(row.get("status", "")).strip().upper() == "READY"
    ]
    if only:
        selected = [row for row in selected if str(row.get("event_id", "")).upper() in only]
    return selected


def event_rows(only: set[str] | None = None) -> list[dict[str, str]]:
    rows = select_event_rows(read_csv(EVENT_CSV), only)
    if not rows:
        raise RuntimeError(f"no READY events in {EVENT_CSV}")
    return rows


def experiment_name(prefix: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", prefix.upper()).strip("_")


def summarize_details(
    details: list[dict[str, Any]],
    experiment: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_event_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_type_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        by_event_groups[row["event_id"]].append(row)
        by_type_groups[row["semantic_type"]].append(row)
    by_event: list[dict[str, Any]] = []
    for event_id, items in sorted(by_event_groups.items()):
        correct = sum(bool(row["semantic_correct"]) for row in items)
        by_event.append(
            {
                "event_id": event_id,
                "semantic_type": items[0]["semantic_type"],
                "attempts": len(items),
                "semantic_correct": correct,
                "semantic_accuracy": correct / len(items) if items else 0,
                "strict_event_success": correct == len(items),
                "generation_successes": sum(row["generation_status"] == "GENERATED" for row in items),
                "families": "|".join(sorted({str(row["semantic_family"]) for row in items})),
            }
        )
    by_type: list[dict[str, Any]] = []
    for semantic_type, items in [("ALL", details), *sorted(by_type_groups.items())]:
        correct = sum(bool(row["semantic_correct"]) for row in items)
        events = sorted({row["event_id"] for row in items})
        strict = [
            item
            for item in by_event
            if item["semantic_type"] == semantic_type or semantic_type == "ALL"
        ]
        by_type.append(
            {
                "semantic_type": semantic_type,
                "events": len(events),
                "attempts": len(items),
                "semantic_correct": correct,
                "semantic_accuracy": correct / len(items) if items else 0,
                "strict_event_successes": sum(bool(row["strict_event_success"]) for row in strict),
                "strict_event_accuracy": (
                    sum(bool(row["strict_event_success"]) for row in strict) / len(strict)
                    if strict
                    else 0
                ),
                "generation_successes": sum(row["generation_status"] == "GENERATED" for row in items),
            }
        )
    summary = [
        {
            "experiment": experiment,
            "events": by_type[0]["events"],
            "attempts": by_type[0]["attempts"],
            "semantic_correct": by_type[0]["semantic_correct"],
            "semantic_accuracy": by_type[0]["semantic_accuracy"],
            "strict_event_successes": by_type[0]["strict_event_successes"],
            "strict_event_accuracy": by_type[0]["strict_event_accuracy"],
            "generation_successes": by_type[0]["generation_successes"],
            "boundary": (
                "semantic evaluator uses family-level canonical matching; it is not a "
                "new manual per-event oracle and does not read candidate IDs"
            ),
        }
    ]
    return by_event, by_type, summary


def main() -> int:
    args = parse_args()
    configure_benchmark(args.benchmark_dir)
    args.raw_dir = args.raw_dir or DEFAULT_RAW_DIR
    args.output_dir = args.output_dir or DEFAULT_OUTPUT_DIR
    experiment = experiment_name(args.prefix)
    only = {item.strip().upper() for item in args.only.split(",") if item.strip()}
    details: list[dict[str, Any]] = []
    for event in event_rows(only):
        event_id = event["event_id"]
        gold_policy = load_json(RULE_DIR / f"{event_id}-formal-policy.json")
        target = gold_target(gold_policy)
        for run in range(1, args.runs + 1):
            seed = args.seed + run - 1
            raw_path = args.raw_dir / f"{event_id}-run{run}-seed{seed}.json"
            auto_record = load_json(raw_path)
            status = str(auto_record.get("status", ""))
            auto_result = auto_semantic_result(auto_record)
            if status == "GENERATED":
                semantic_correct, evidence = evaluate_semantic(target, auto_result, event)
            else:
                semantic_correct, evidence = False, {"family": "not_generated"}
            details.append(
                {
                    "event_id": event_id,
                    "semantic_type": event["semantic_type"],
                    "run": run,
                    "seed": seed,
                    "generation_status": status,
                    "schema_valid": auto_record.get("schema_valid", ""),
                    "oracle_used": auto_record.get("oracle_used", ""),
                    "candidate_used": auto_record.get("candidate_used", ""),
                    "manual_formal_policy_used": auto_record.get("manual_formal_policy_used", ""),
                    "gold_target": target,
                    "auto_semantic_result": auto_result,
                    "semantic_family": evidence.get("family", ""),
                    "semantic_correct": semantic_correct,
                    "semantic_evidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    "runtime_ms": auto_record.get("runtime_ms", 0),
                    "prompt_eval_count": auto_record.get("prompt_eval_count", 0),
                    "eval_count": auto_record.get("eval_count", 0),
                }
            )

    by_event, by_type, summary = summarize_details(details, experiment)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    details_csv = args.output_dir / f"{args.prefix}-details.csv"
    by_event_csv = args.output_dir / f"{args.prefix}-by-event.csv"
    by_type_csv = args.output_dir / f"{args.prefix}-by-type.csv"
    summary_json = args.output_dir / f"{args.prefix}-summary.json"
    log_path = args.output_dir / f"{args.prefix}.log"
    write_csv(details_csv, details)
    write_csv(by_event_csv, by_event)
    write_csv(by_type_csv, by_type)
    summary_json.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "details": str(details_csv),
                "by_event": str(by_event_csv),
                "by_type": str(by_type_csv),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    lines = [
        f"{experiment} semantic evaluation",
        f"details={details_csv}",
        f"by_event={by_event_csv}",
        f"by_type={by_type_csv}",
        f"summary={summary_json}",
        "",
    ]
    for row in by_type:
        lines.append(
            "[{semantic_type}] semantic_accuracy={semantic_accuracy:.2%} "
            "strict={strict_event_successes}/{events} generation={generation_successes}/{attempts}".format(**row)
        )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
