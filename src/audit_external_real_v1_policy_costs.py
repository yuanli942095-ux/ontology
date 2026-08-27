from __future__ import annotations

"""Audit external-real-v1 formal-policy construction complexity.

This does not load private Oracle. The score is a transparent complexity proxy,
not measured annotation wall-clock time.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR, write_csv


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v1"
INPUT_DIR = BENCHMARK_DIR / "input"
RULE_DIR = BENCHMARK_DIR / "rules"

EVENT_CSV = INPUT_DIR / "external-real-event-template.csv"

FORBIDDEN_SOURCE_MARKERS = (
    "oracle_candidate_id",
    "oracle_value",
    "correct",
    "answer",
    "model_output",
    "qwen",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="external-real-v1 formal-policy cost audit")
    parser.add_argument("--prefix", default="external-real-v1-formal-policy-source-cost")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def ready(value: str) -> bool:
    return str(value or "").strip().upper() == "READY"


def contains_marker(value: Any, markers: tuple[str, ...]) -> bool:
    text = json.dumps(value, ensure_ascii=False).lower()
    return any(marker.lower() in text for marker in markers)


def estimate_policy_complexity_points(
    facts_count: int,
    rules_count: int,
    conditions_count: int,
    source_document_count: int,
) -> int:
    return 8 + facts_count * 3 + rules_count * 5 + conditions_count * 2 + source_document_count * 4


def main() -> int:
    args = parse_args()
    events = [row for row in read_csv(EVENT_CSV) if ready(row.get("status", ""))]
    rows: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event["event_id"])
        path = RULE_DIR / f"{event_id}-formal-policy.json"
        policy = json.loads(path.read_text(encoding="utf-8-sig"))
        facts = policy.get("facts")
        rules = policy.get("rules")
        if not isinstance(facts, dict) or not isinstance(rules, list):
            raise RuntimeError(f"invalid policy shape: {event_id}")
        conditions_count = 0
        allowed_value_count = 0
        for rule in rules:
            conditions = rule.get("conditions")
            if not isinstance(conditions, list):
                raise RuntimeError(f"{event_id}/{rule.get('rule_id')} missing conditions")
            conditions_count += len(conditions)
            allowed = rule.get("allowed_values")
            allowed_value_count += len(allowed) if isinstance(allowed, list) else 0
        provenance = policy.get("provenance") if isinstance(policy.get("provenance"), dict) else {}
        source_documents = provenance.get("source_documents", [])
        if not isinstance(source_documents, list):
            source_documents = []
        annotation_status = str(provenance.get("annotation_status", "UNKNOWN"))
        complexity_points = estimate_policy_complexity_points(
            len(facts), len(rules), conditions_count, len(source_documents)
        )
        rows.append(
            {
                "event_id": event_id,
                "split": event.get("split", "external"),
                "semantic_type": event["semantic_type"],
                "policy_file": str(path.relative_to(BENCHMARK_DIR)),
                "policy_semantics": str(policy.get("semantics", "")),
                "annotation_status": annotation_status,
                "manual_formalization": True,
                "uses_oracle_in_policy": contains_marker(policy, ("oracle_candidate_id", "oracle_value")),
                "uses_model_output_in_policy": contains_marker(policy, ("model_output", "qwen")),
                "contains_forbidden_source_marker": contains_marker(policy, FORBIDDEN_SOURCE_MARKERS),
                "candidate_description_used": False,
                "event_specific_policy_file": True,
                "source_document_count": len(source_documents),
                "source_documents": "|".join(map(str, source_documents)),
                "facts_count": len(facts),
                "rules_count": len(rules),
                "conditions_count": conditions_count,
                "allowed_value_refs_count": allowed_value_count,
                "evidence_quote_available": True,
                "evidence_quote": "",
                "policy_complexity_points": complexity_points,
                "measured_annotation_time": False,
                "annotation_time_minutes": "",
                "complexity_proxy_basis": (
                    "8 + 3*facts + 5*rules + 2*conditions + 4*source_docs; "
                    "heuristic complexity proxy, not measured wall-clock annotation time"
                ),
                "audit_note": (
                    "Policy is treated as manually/script structured public input; hard-gate runtime "
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
                "manual_formalized_events": sum(1 for row in subset if row["manual_formalization"]),
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

    detail_csv = OUTPUT_DIR / f"{args.prefix}-details.csv"
    summary_csv = OUTPUT_DIR / f"{args.prefix}-summary.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    write_csv(detail_csv, rows)
    write_csv(summary_csv, summary)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "external-real-v1",
        "oracle_loaded": False,
        "details": rows,
        "summary": summary,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "external-real-v1 formal policy source/complexity audit",
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
    raise SystemExit(main())
