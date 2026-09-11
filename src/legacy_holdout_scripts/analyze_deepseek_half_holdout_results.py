from __future__ import annotations

"""Analyze the repaired DeepSeek half hold-out run.

Outputs compact diagnostic tables for failure composition, TEMPORAL successes,
and GRE/CSS failure cases.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv

DEFAULT_DETAILS = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v1-expanded"
    / "final-blind-eval-r5-model-ablation-deepseek-repaired-half"
    / "main-method"
    / "m13-pilot"
    / "arm-d-rule-refinement"
    / "ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
DEFAULT_OUTPUT = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v1-expanded"
    / "final-blind-eval-r5-model-ablation-deepseek-repaired-half"
    / "analysis"
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def truth(value: str) -> bool:
    return str(value).strip().lower() == "true"


def pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.00"
    return f"{numerator / denominator * 100:.2f}"


def safe_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def raw_fields(row: dict[str, str]) -> dict[str, str]:
    raw_path = Path(row.get("raw_output_file", ""))
    if not raw_path.is_absolute():
        raw_path = PROJECT_DIR / raw_path
    payload = safe_json(raw_path)
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    facts = response.get("facts") if isinstance(response.get("facts"), dict) else {}
    refined = facts.get("refined_rule") if isinstance(facts.get("refined_rule"), dict) else {}
    audit = payload.get("m13_rule_refinement_audit")
    audit = audit if isinstance(audit, dict) else {}
    selected_units = audit.get("selected_units")
    selected_units = selected_units if isinstance(selected_units, list) else []
    return {
        "raw_status": str(payload.get("status", "")),
        "answerable": str(refined.get("answerable", "")),
        "faithfulness_status": str(facts.get("faithfulness_status") or audit.get("faithfulness_status") or ""),
        "rule_type": str(refined.get("rule_type", "")),
        "rejection_reason": str(refined.get("rejection_reason", "")),
        "focused_units": str(len(selected_units)),
        "focused_top_score": str(selected_units[0].get("score", "")) if selected_units else "",
    }


def failure_bucket(row: dict[str, str]) -> str:
    if truth(row.get("full_closure_success", "")):
        return "CORRECT_CLOSURE"
    if is_selected(row):
        return "WRONG_SELECTION"
    path = row.get("decision_path", "")
    if path == "IR_FAIL_CLOSED":
        return "FAIL_CLOSED_INCOMPLETE_IR"
    if path == "IR_RANK_ABSTAIN":
        return "RANK_ABSTAIN"
    if path == "IR_RANK_TIE_ABSTAIN":
        return "TIE_ABSTAIN"
    return path or "OTHER_ABSTAIN"


def is_selected(row: dict[str, str]) -> bool:
    status = row.get("selection_status", "").strip().upper()
    return status in {"SELECT", "SELECTED"} or bool(row.get("selected_candidate_id", "").strip())


def summarize(rows: list[dict[str, str]], group_key: str) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get(group_key, "ALL") if group_key != "ALL" else "ALL"].append(row)
    out: list[dict[str, str]] = []
    for group, items in sorted(grouped.items()):
        total = len(items)
        buckets = Counter(failure_bucket(row) for row in items)
        selected = sum(1 for row in items if is_selected(row))
        correct = buckets["CORRECT_CLOSURE"]
        abstain = total - selected
        wrong = buckets["WRONG_SELECTION"]
        out.append(
            {
                group_key.lower(): group,
                "attempts": str(total),
                "selected": str(selected),
                "selected_pct": pct(selected, total),
                "closure_correct": str(correct),
                "closure_pct": pct(correct, total),
                "wrong_selection": str(wrong),
                "wrong_selection_pct": pct(wrong, total),
                "abstain": str(abstain),
                "abstain_pct": pct(abstain, total),
                "fail_closed_incomplete_ir": str(buckets["FAIL_CLOSED_INCOMPLETE_IR"]),
                "fail_closed_pct": pct(buckets["FAIL_CLOSED_INCOMPLETE_IR"], total),
                "rank_abstain": str(buckets["RANK_ABSTAIN"]),
                "rank_abstain_pct": pct(buckets["RANK_ABSTAIN"], total),
                "tie_abstain": str(buckets["TIE_ABSTAIN"]),
                "tie_abstain_pct": pct(buckets["TIE_ABSTAIN"], total),
            }
        )
    return out


def failure_only(rows: list[dict[str, str]], group_key: str) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get(group_key, "ALL") if group_key != "ALL" else "ALL"].append(row)
    out: list[dict[str, str]] = []
    for group, items in sorted(grouped.items()):
        failures = [row for row in items if not truth(row.get("full_closure_success", ""))]
        total = len(failures)
        buckets = Counter(failure_bucket(row) for row in failures)
        out.append(
            {
                group_key.lower(): group,
                "failure_attempts": str(total),
                "wrong_selection": str(buckets["WRONG_SELECTION"]),
                "wrong_selection_failure_pct": pct(buckets["WRONG_SELECTION"], total),
                "fail_closed_incomplete_ir": str(buckets["FAIL_CLOSED_INCOMPLETE_IR"]),
                "fail_closed_failure_pct": pct(buckets["FAIL_CLOSED_INCOMPLETE_IR"], total),
                "rank_abstain": str(buckets["RANK_ABSTAIN"]),
                "rank_abstain_failure_pct": pct(buckets["RANK_ABSTAIN"], total),
                "tie_abstain": str(buckets["TIE_ABSTAIN"]),
                "tie_abstain_failure_pct": pct(buckets["TIE_ABSTAIN"], total),
            }
        )
    return out


def event_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_event[row["event_id"]].append(row)
    out: list[dict[str, str]] = []
    for event_id, items in sorted(by_event.items()):
        first = items[0]
        bucket_counts = Counter(failure_bucket(row) for row in items)
        path_counts = Counter(row.get("decision_path", "") for row in items)
        ir_counts = Counter(row.get("ir_status", "") for row in items)
        faith_counts = Counter(row.get("faithfulness_status", "") for row in items)
        selected = [row for row in items if is_selected(row)]
        correct = [row for row in items if truth(row.get("full_closure_success", ""))]
        example = next((row for row in items if not truth(row.get("full_closure_success", ""))), items[0])
        out.append(
            {
                "event_id": event_id,
                "semantic_type": first.get("semantic_type", ""),
                "domain": first.get("domain", ""),
                "attempts": str(len(items)),
                "selected": str(len(selected)),
                "selected_pct": pct(len(selected), len(items)),
                "closure_correct": str(len(correct)),
                "closure_pct": pct(len(correct), len(items)),
                "strict_success": str(len(correct) == len(items)),
                "dominant_bucket": bucket_counts.most_common(1)[0][0],
                "bucket_counts": json.dumps(dict(bucket_counts), ensure_ascii=False, sort_keys=True),
                "decision_path_counts": json.dumps(dict(path_counts), ensure_ascii=False, sort_keys=True),
                "ir_status_counts": json.dumps(dict(ir_counts), ensure_ascii=False, sort_keys=True),
                "faithfulness_counts": json.dumps(dict(faith_counts), ensure_ascii=False, sort_keys=True),
                "example_ir_reason": example.get("ir_reason", ""),
                "example_selection_reason": example.get("selection_reason", ""),
                "example_selected_candidate_id": example.get("selected_candidate_id", ""),
                "example_oracle_candidate_id": example.get("oracle_candidate_id", ""),
            }
        )
    return out


def write_markdown(
    path: Path,
    *,
    overall: list[dict[str, str]],
    by_type: list[dict[str, str]],
    temporal_successes: list[dict[str, str]],
    gre_css_failures: list[dict[str, str]],
) -> None:
    def lines_table(rows: list[dict[str, str]], columns: list[str]) -> list[str]:
        if not rows:
            return ["_No rows._", ""]
        lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
        for row in rows:
            lines.append("| " + " | ".join(str(row.get(col, "")).replace("|", "\\|") for col in columns) + " |")
        lines.append("")
        return lines

    content: list[str] = [
        "# DeepSeek Half Hold-out Analysis",
        "",
        "Scope: repaired `external-real-holdout-v1-expanded`, first 118 events x 5 runs = 590 attempts.",
        "",
        "## Failure Composition",
        "",
    ]
    content.extend(
        lines_table(
            overall,
            [
                "all",
                "attempts",
                "selected",
                "selected_pct",
                "closure_correct",
                "closure_pct",
                "wrong_selection",
                "wrong_selection_pct",
                "abstain",
                "abstain_pct",
                "fail_closed_incomplete_ir",
                "rank_abstain",
                "tie_abstain",
            ],
        )
    )
    content.extend(["## By Semantic Type", ""])
    content.extend(
        lines_table(
            by_type,
            [
                "semantic_type",
                "attempts",
                "selected",
                "selected_pct",
                "closure_correct",
                "closure_pct",
                "wrong_selection",
                "wrong_selection_pct",
                "abstain",
                "abstain_pct",
                "fail_closed_incomplete_ir",
                "rank_abstain",
                "tie_abstain",
            ],
        )
    )
    content.extend(["## TEMPORAL Successful Events", ""])
    content.extend(
        lines_table(
            temporal_successes[:20],
            [
                "event_id",
                "domain",
                "attempts",
                "selected",
                "closure_correct",
                "closure_pct",
                "strict_success",
                "decision_path_counts",
            ],
        )
    )
    content.extend(["## GRE/CSS Failure Events", ""])
    content.extend(
        lines_table(
            gre_css_failures[:30],
            [
                "event_id",
                "semantic_type",
                "domain",
                "selected",
                "closure_correct",
                "dominant_bucket",
                "decision_path_counts",
                "ir_status_counts",
                "faithfulness_counts",
                "example_ir_reason",
            ],
        )
    )
    path.write_text("\n".join(content), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.details = args.details.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(args.details)
    enriched = []
    for row in rows:
        merged = dict(row)
        merged.update(raw_fields(row))
        enriched.append(merged)

    overall = summarize(enriched, "ALL")
    by_type = summarize(enriched, "semantic_type")
    failure_only_overall = failure_only(enriched, "ALL")
    failure_only_by_type = failure_only(enriched, "semantic_type")
    events = event_rows(enriched)
    temporal_successes = [
        row
        for row in events
        if row["semantic_type"] == "TEMPORAL_VERSION" and int(row["closure_correct"]) > 0
    ]
    temporal_successes.sort(key=lambda row: (-int(row["closure_correct"]), row["event_id"]))
    gre_css_failures = [
        row
        for row in events
        if row["semantic_type"] in {"GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}
        and int(row["closure_correct"]) == 0
    ]
    gre_css_failures.sort(key=lambda row: (row["semantic_type"], row["dominant_bucket"], row["event_id"]))

    write_csv(args.output_dir / "deepseek-half-failure-composition.csv", overall)
    write_csv(args.output_dir / "deepseek-half-failure-by-type.csv", by_type)
    write_csv(args.output_dir / "deepseek-half-failure-only-composition.csv", failure_only_overall)
    write_csv(args.output_dir / "deepseek-half-failure-only-by-type.csv", failure_only_by_type)
    write_csv(args.output_dir / "deepseek-half-event-diagnostics.csv", events)
    write_csv(args.output_dir / "deepseek-half-temporal-success-cases.csv", temporal_successes)
    write_csv(args.output_dir / "deepseek-half-gre-css-failure-cases.csv", gre_css_failures)
    write_markdown(
        args.output_dir / "deepseek-half-analysis-report.md",
        overall=overall,
        by_type=by_type,
        temporal_successes=temporal_successes,
        gre_css_failures=gre_css_failures,
    )
    print(
        json.dumps(
            {
                "attempts": len(enriched),
                "events": len(events),
                "outputs": str(args.output_dir.relative_to(PROJECT_DIR)),
                "temporal_success_events": len(temporal_successes),
                "gre_css_zero_closure_events": len(gre_css_failures),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
