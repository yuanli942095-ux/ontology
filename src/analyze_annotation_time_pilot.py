from __future__ import annotations

"""Create and summarize the semantic-v2 formal-policy annotation-time pilot.

This script does not read the private Oracle. The pilot CSV may contain an
`oracle_correct_after_blind_check` field filled by an experiment administrator
after annotation has finished.
"""

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import BENCHMARK_DIR, OUTPUT_DIR, load_csv, write_csv


PILOT_DIR = BENCHMARK_DIR / "annotation-time-pilot"
DEFAULT_POLICY_COSTS = OUTPUT_DIR / "formal-policy-source-cost-test-details.csv"
DEFAULT_TEMPLATE = PILOT_DIR / "annotation-time-pilot-template.csv"
DEFAULT_EVENTS = [
    "E14",
    "E17",
    "E21",
    "E42",
    "E27",
    "E30",
    "E31",
    "E45",
    "E34",
    "E37",
    "E41",
    "E47",
]


FIELDS = [
    "event_id",
    "semantic_type",
    "annotator_id",
    "annotator_background",
    "session_id",
    "start_time_iso",
    "end_time_iso",
    "elapsed_minutes",
    "facts_count",
    "rules_count",
    "conditions_count",
    "source_docs_count",
    "policy_complexity_points",
    "source_documents",
    "public_materials_used",
    "oracle_access_during_annotation",
    "model_output_access_during_annotation",
    "candidate_description_access",
    "produced_policy_file",
    "unique_gate_decision",
    "selected_candidate_id_after_gate",
    "oracle_correct_after_blind_check",
    "revision_count",
    "blocking_issue",
    "notes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="formal-policy人工标注时间pilot模板生成与汇总"
    )
    parser.add_argument("--policy-costs", type=Path, default=DEFAULT_POLICY_COSTS)
    parser.add_argument("--pilot-csv", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument(
        "--events",
        default=",".join(DEFAULT_EVENTS),
        help="逗号分隔的pilot事件ID；默认每类4个事件",
    )
    parser.add_argument(
        "--init-template",
        action="store_true",
        help="根据policy-cost audit生成空白标注时间模板",
    )
    parser.add_argument(
        "--prefix",
        default="annotation-time-pilot",
        help="输出文件前缀，写入output目录",
    )
    return parser.parse_args()


def as_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def as_int(value: Any, default: int = 0) -> int:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def parse_bool(value: Any) -> bool | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"true", "1", "yes", "y", "是"}:
        return True
    if text in {"false", "0", "no", "n", "否"}:
        return False
    return None


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denom_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    denom_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if denom_x == 0 or denom_y == 0:
        return None
    return numerator / (denom_x * denom_y)


def load_policy_costs(path: Path) -> dict[str, dict[str, str]]:
    rows = load_csv(path)
    return {str(row["event_id"]).strip().upper(): row for row in rows}


def build_template_rows(
    policy_costs: dict[str, dict[str, str]], event_ids: list[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event_id in event_ids:
        key = event_id.strip().upper()
        if key not in policy_costs:
            raise RuntimeError(f"policy-cost audit缺少事件：{key}")
        source = policy_costs[key]
        row = {field: "" for field in FIELDS}
        row.update(
            {
                "event_id": key,
                "semantic_type": source.get("semantic_type", ""),
                "facts_count": source.get("facts_count", ""),
                "rules_count": source.get("rules_count", ""),
                "conditions_count": source.get("conditions_count", ""),
                "source_docs_count": source.get("source_document_count", ""),
                "policy_complexity_points": source.get("policy_complexity_points", ""),
                "source_documents": source.get("source_documents", ""),
                "public_materials_used": (
                    "old/new document excerpts; target metadata; candidate formal operations"
                ),
                "oracle_access_during_annotation": "False",
                "model_output_access_during_annotation": "False",
                "candidate_description_access": "False",
                "unique_gate_decision": "",
                "oracle_correct_after_blind_check": "",
                "revision_count": "0",
            }
        )
        rows.append(row)
    return rows


def measured_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if as_float(row.get("elapsed_minutes")) is not None]


def summarize_subset(label: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    measured = measured_rows(rows)
    minutes = [as_float(row.get("elapsed_minutes")) for row in measured]
    minutes = [value for value in minutes if value is not None]
    complexities = [
        float(as_int(row.get("policy_complexity_points"))) for row in measured
    ]
    correctness = [
        parse_bool(row.get("oracle_correct_after_blind_check")) for row in measured
    ]
    correctness = [value for value in correctness if value is not None]
    unique = [parse_bool(row.get("unique_gate_decision")) for row in measured]
    unique = [value for value in unique if value is not None]
    revisions = [as_int(row.get("revision_count")) for row in measured]
    sorted_minutes = sorted(minutes)
    median = ""
    if sorted_minutes:
        midpoint = len(sorted_minutes) // 2
        if len(sorted_minutes) % 2:
            median = round(sorted_minutes[midpoint], 3)
        else:
            median = round((sorted_minutes[midpoint - 1] + sorted_minutes[midpoint]) / 2, 3)
    return {
        "group": label,
        "rows": len(rows),
        "measured_rows": len(minutes),
        "mean_minutes": round(sum(minutes) / len(minutes), 3) if minutes else "",
        "median_minutes": median,
        "min_minutes": round(min(minutes), 3) if minutes else "",
        "max_minutes": round(max(minutes), 3) if minutes else "",
        "total_minutes": round(sum(minutes), 3) if minutes else "",
        "mean_policy_complexity_points": (
            round(sum(complexities) / len(complexities), 3) if complexities else ""
        ),
        "minutes_per_complexity_point": (
            round(sum(minutes) / sum(complexities), 5)
            if minutes and sum(complexities) > 0
            else ""
        ),
        "unique_gate_decision_rate": (
            round(sum(1 for value in unique if value) / len(unique), 4) if unique else ""
        ),
        "oracle_correct_after_blind_check_rate": (
            round(sum(1 for value in correctness if value) / len(correctness), 4)
            if correctness
            else ""
        ),
        "mean_revision_count": (
            round(sum(revisions) / len(revisions), 3) if revisions else ""
        ),
    }


def summarize(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    measured = measured_rows(rows)
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("semantic_type", "UNKNOWN") or "UNKNOWN")].append(row)

    summary = [summarize_subset("ALL", rows)]
    for semantic_type in sorted(groups):
        summary.append(summarize_subset(semantic_type, groups[semantic_type]))

    measured_complexity = [
        float(as_int(row.get("policy_complexity_points"))) for row in measured
    ]
    measured_minutes = [
        as_float(row.get("elapsed_minutes")) or 0.0 for row in measured
    ]
    leakage_rows = [
        row
        for row in rows
        if parse_bool(row.get("oracle_access_during_annotation")) is True
        or parse_bool(row.get("model_output_access_during_annotation")) is True
    ]
    diagnostics = {
        "measurement_status": "MEASURED" if measured else "PENDING_MEASUREMENT",
        "rows": len(rows),
        "measured_rows": len(measured),
        "oracle_loaded": False,
        "leakage_flag_rows": len(leakage_rows),
        "pearson_complexity_minutes": pearson(measured_complexity, measured_minutes),
        "notes": (
            "oracle_correct_after_blind_check is summarized only if filled in the pilot CSV; "
            "this script does not load the private Oracle."
        ),
    }
    return summary, diagnostics


def main() -> int:
    args = parse_args()
    policy_costs = load_policy_costs(args.policy_costs)
    event_ids = [item.strip().upper() for item in args.events.split(",") if item.strip()]

    if args.init_template:
        rows = build_template_rows(policy_costs, event_ids)
        args.pilot_csv.parent.mkdir(parents=True, exist_ok=True)
        write_csv(args.pilot_csv, rows)
        print(f"template={args.pilot_csv}")

    if not args.pilot_csv.exists():
        raise SystemExit(f"pilot csv not found: {args.pilot_csv}")

    rows = load_csv(args.pilot_csv)
    summary, diagnostics = summarize(rows)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_csv = OUTPUT_DIR / f"{args.prefix}-summary.csv"
    details_csv = OUTPUT_DIR / f"{args.prefix}-details.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"

    write_csv(details_csv, rows)
    write_csv(summary_csv, summary)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_pilot_csv": str(args.pilot_csv),
        "source_policy_costs": str(args.policy_costs),
        "diagnostics": diagnostics,
        "summary": summary,
        "details": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "annotation time pilot",
        f"source_pilot_csv={args.pilot_csv}",
        f"measurement_status={diagnostics['measurement_status']}",
        f"rows={diagnostics['rows']}",
        f"measured_rows={diagnostics['measured_rows']}",
        f"leakage_flag_rows={diagnostics['leakage_flag_rows']}",
        f"pearson_complexity_minutes={diagnostics['pearson_complexity_minutes']}",
        "",
        f"details={details_csv}",
        f"summary={summary_csv}",
        f"json={json_path}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
