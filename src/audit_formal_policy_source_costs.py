from __future__ import annotations

"""Audit the source and construction complexity of semantic-v2 policies.

This script does not run a model and does not load the private Oracle. It makes
the manual policy-construction assumption explicit, so the zero-token runtime
cost of hard-gate execution is not confused with zero construction effort. The
reported score is a heuristic complexity proxy, not measured annotation time.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_semantic_benchmark_v2 import load_public_events
from semantic_v2_common import BENCHMARK_DIR, OUTPUT_DIR, write_csv
from validate_formal_policy_gate import normalize_policy_conditions


POLICY_DIR = BENCHMARK_DIR / "rules"
REVIEW_DIR = BENCHMARK_DIR / "reviews"

FORBIDDEN_SOURCE_MARKERS = (
    "oracle_candidate_id",
    "oracle_value",
    "correct",
    "answer",
    "model_output",
    "qwen",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计formal-policy来源和构建复杂度")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument(
        "--prefix",
        default="formal-policy-source-cost-test",
        help="输出文件前缀",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON顶层不是对象：{path}")
    return value


def contains_marker(value: Any, markers: tuple[str, ...]) -> bool:
    text = json.dumps(value, ensure_ascii=False).lower()
    return any(marker.lower() in text for marker in markers)


def load_review_quotes() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in REVIEW_DIR.glob("*.csv"):
        # Keep this dependency light and avoid importing pandas.
        import csv

        with path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                event_id = str(row.get("event_id", "")).strip()
                quote = str(row.get("evidence_quote", "")).strip()
                if event_id and quote and event_id not in result:
                    result[event_id] = quote
    return result


def normalized_rule_stats(event_id: str, raw_policy: dict[str, Any]) -> tuple[int, int, int]:
    rules = raw_policy.get("rules")
    if not isinstance(rules, list):
        raise RuntimeError(f"{event_id} policy缺少rules数组")
    condition_count = 0
    allowed_value_count = 0
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise RuntimeError(f"{event_id}第{index}条规则不是对象")
        rule_id = str(rule.get("rule_id") or f"RULE_{index}")
        conditions = normalize_policy_conditions(
            event_id,
            rule_id,
            rule.get("conditions"),
            rule.get("when"),
        )
        condition_count += len(conditions)
        allowed = rule.get("allowed_values")
        if isinstance(allowed, list):
            allowed_value_count += len(allowed)
    return len(rules), condition_count, allowed_value_count


def estimate_policy_complexity_points(
    facts_count: int,
    rules_count: int,
    conditions_count: int,
    source_document_count: int,
) -> int:
    """Complexity proxy, not measured annotation time."""
    return (
        8
        + facts_count * 3
        + rules_count * 5
        + conditions_count * 2
        + source_document_count * 4
    )


def main() -> int:
    args = parse_args()
    events = load_public_events(args.split)
    review_quotes = load_review_quotes()

    rows: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event["event_id"])
        path = POLICY_DIR / f"{event_id}-formal-policy.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        raw = read_json(path)
        facts = raw.get("facts")
        if not isinstance(facts, dict) or not facts:
            raise RuntimeError(f"{event_id} policy缺少facts对象")
        provenance = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {}
        source_documents = provenance.get("source_documents", [])
        if not isinstance(source_documents, list):
            source_documents = []
        rules_count, conditions_count, allowed_value_count = normalized_rule_stats(
            event_id, raw
        )
        complexity_points = estimate_policy_complexity_points(
            len(facts), rules_count, conditions_count, len(source_documents)
        )
        status = str(provenance.get("annotation_status", "UNKNOWN"))
        manual = "MANUAL" in status.upper() or status == "UNKNOWN"
        rows.append(
            {
                "event_id": event_id,
                "split": event["split"],
                "semantic_type": event["semantic_type"],
                "policy_file": str(path.relative_to(BENCHMARK_DIR)),
                "policy_semantics": str(raw.get("semantics", "")),
                "annotation_status": status,
                "manual_formalization": manual,
                "uses_oracle_in_policy": contains_marker(raw, ("oracle_candidate_id", "oracle_value")),
                "uses_model_output_in_policy": contains_marker(raw, ("model_output", "qwen")),
                "contains_forbidden_source_marker": contains_marker(raw, FORBIDDEN_SOURCE_MARKERS),
                "candidate_description_used": False,
                "event_specific_policy_file": True,
                "source_document_count": len(source_documents),
                "source_documents": "|".join(map(str, source_documents)),
                "facts_count": len(facts),
                "rules_count": rules_count,
                "conditions_count": conditions_count,
                "allowed_value_refs_count": allowed_value_count,
                "evidence_quote_available": bool(review_quotes.get(event_id)),
                "evidence_quote": review_quotes.get(event_id, ""),
                "policy_complexity_points": complexity_points,
                "measured_annotation_time": False,
                "annotation_time_minutes": "",
                "complexity_proxy_basis": (
                    "8 + 3*facts + 5*rules + 2*conditions + 4*source_docs; "
                    "heuristic complexity proxy, not measured wall-clock annotation time"
                ),
                "audit_note": (
                    "Policy is treated as manually structured input; hard-gate runtime "
                    "cost excludes this construction effort."
                ),
            }
        )

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_type[str(row["semantic_type"])].append(row)
    summary: list[dict[str, Any]] = []
    for semantic_type, subset in [("ALL", rows), *sorted(by_type.items())]:
        count = len(subset)
        total_complexity = sum(int(row["policy_complexity_points"]) for row in subset)
        facts = sum(int(row["facts_count"]) for row in subset)
        rules = sum(int(row["rules_count"]) for row in subset)
        conditions = sum(int(row["conditions_count"]) for row in subset)
        markers = Counter(str(row["contains_forbidden_source_marker"]) for row in subset)
        summary.append(
            {
                "semantic_type": semantic_type,
                "events": count,
                "manual_formalized_events": sum(
                    1 for row in subset if row["manual_formalization"]
                ),
                "forbidden_source_marker_events": markers.get("True", 0),
                "total_facts": facts,
                "total_rules": rules,
                "total_conditions": conditions,
                "mean_facts_per_event": facts / count if count else 0,
                "mean_rules_per_event": rules / count if count else 0,
                "mean_conditions_per_event": conditions / count if count else 0,
                "total_policy_complexity_points": total_complexity,
                "mean_policy_complexity_points": total_complexity / count if count else 0,
                "measured_annotation_time": False,
            }
        )

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
        "oracle_loaded": False,
        "split": args.split,
        "details": rows,
        "summary": summary,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "formal policy source/complexity audit",
        f"split={args.split}",
        "oracle_loaded=False",
        "policy_complexity_points is a heuristic proxy, not measured annotation time.",
        "",
        f"details={detail_csv}",
        f"summary={summary_csv}",
        f"json={json_path}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[policy source/cost audit stopped] {type(exc).__name__}: {exc}")
        raise SystemExit(2)
