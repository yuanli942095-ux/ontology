from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_DETAILS = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "final-blind-eval-r5-m16-deepseek"
    / "m16-full-holdout-combined"
    / "m16-full-holdout-combined-full-details.csv"
)
DEFAULT_OUTPUT = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "final-blind-eval-r5-m16-deepseek"
    / "failure-analysis"
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def parse_scores(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    rows = [item for item in value if isinstance(item, dict)]
    return sorted(rows, key=lambda item: float(item.get("score") or 0), reverse=True)


def extract_threshold_reason(reason: str) -> dict[str, Any]:
    result: dict[str, Any] = {"reason_score": "", "reason_margin": ""}
    score = re.search(r"score=([0-9.]+)", reason or "")
    margin = re.search(r"margin=([0-9.]+)", reason or "")
    if score:
        result["reason_score"] = score.group(1)
    if margin:
        result["reason_margin"] = margin.group(1)
    return result


def classify(row: dict[str, str], scores: list[dict[str, Any]]) -> dict[str, Any]:
    oracle = row.get("oracle_candidate_id", "")
    path = row.get("decision_path", "")
    reason = row.get("selection_reason", "")
    top = scores[0] if scores else {}
    second = scores[1] if len(scores) > 1 else {}
    oracle_index = next((index for index, item in enumerate(scores) if item.get("candidate_id") == oracle), None)
    oracle_score = float(scores[oracle_index].get("score") or 0) if oracle_index is not None else None
    top_score = float(top.get("score") or 0) if top else None
    second_score = float(second.get("score") or 0) if second else None
    margin = None
    if top_score is not None and second_score is not None:
        margin = top_score - second_score

    if path == "IR_FAIL_CLOSED":
        if row.get("ir_status") and row.get("ir_status") != "OK":
            label = "F1_IR_NOT_OK_FAIL_CLOSED"
        else:
            label = "F2_FAIL_CLOSED_WITH_OK_OR_MISSING_AUDIT"
    elif oracle_index is None:
        label = "R1_ORACLE_NOT_IN_SCORED_TOPK"
    elif oracle_index > 0:
        label = "R2_ORACLE_IN_TOPK_NOT_TOP1"
    elif path == "IR_RANK_TIE_ABSTAIN":
        label = "R4_ORACLE_TOP1_TIE_OR_MARGIN_TOO_SMALL"
    elif top_score is not None and top_score < 0.30:
        label = "R3_ORACLE_TOP1_SCORE_BELOW_THRESHOLD"
    elif margin is not None and margin < 0.05:
        label = "R4_ORACLE_TOP1_TIE_OR_MARGIN_TOO_SMALL"
    elif path == "IR_RANK_ABSTAIN":
        label = "R6_ORACLE_TOP1_BUT_GATE_STILL_ABSTAINS"
    else:
        label = "R9_OTHER"

    threshold_reason = extract_threshold_reason(reason)
    return {
        "failure_class": label,
        "oracle_rank": "" if oracle_index is None else oracle_index + 1,
        "oracle_score": "" if oracle_score is None else round(oracle_score, 6),
        "top_candidate_id": top.get("candidate_id", ""),
        "top_score": "" if top_score is None else round(top_score, 6),
        "second_candidate_id": second.get("candidate_id", ""),
        "second_score": "" if second_score is None else round(second_score, 6),
        "margin": "" if margin is None else round(margin, 6),
        **threshold_reason,
    }


def ratio(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def summarize(rows: list[dict[str, Any]], group_keys: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row.get(key, "")) for key in group_keys)].append(row)
    result: list[dict[str, Any]] = []
    for key, group in sorted(groups.items()):
        total = len(group)
        class_counts = Counter(row["failure_class"] for row in group)
        out = {group_keys[index]: value for index, value in enumerate(key)}
        out.update(
            {
                "failures": total,
                "events": len({row["event_id"] for row in group}),
                "r1_oracle_not_topk": class_counts["R1_ORACLE_NOT_IN_SCORED_TOPK"],
                "r2_oracle_not_top1": class_counts["R2_ORACLE_IN_TOPK_NOT_TOP1"],
                "r3_low_score": class_counts["R3_ORACLE_TOP1_SCORE_BELOW_THRESHOLD"],
                "r4_tie_or_low_margin": class_counts["R4_ORACLE_TOP1_TIE_OR_MARGIN_TOO_SMALL"],
                "r6_gate_abstain_other": class_counts["R6_ORACLE_TOP1_BUT_GATE_STILL_ABSTAINS"],
                "f1_ir_not_ok": class_counts["F1_IR_NOT_OK_FAIL_CLOSED"],
                "f2_fail_closed_other": class_counts["F2_FAIL_CLOSED_WITH_OK_OR_MISSING_AUDIT"],
                "other": class_counts["R9_OTHER"],
                "avg_oracle_score": round(mean(float(row["oracle_score"]) for row in group if row["oracle_score"] != ""), 6)
                if any(row["oracle_score"] != "" for row in group)
                else "",
                "avg_margin": round(mean(float(row["margin"]) for row in group if row["margin"] != ""), 6)
                if any(row["margin"] != "" for row in group)
                else "",
            }
        )
        result.append(out)
    return result


def event_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["event_id"]].append(row)
    result = []
    for event_id, group in sorted(groups.items()):
        class_counts = Counter(row["failure_class"] for row in group)
        result.append(
            {
                "event_id": event_id,
                "semantic_type": group[0]["semantic_type"],
                "domain": group[0]["domain"],
                "failures": len(group),
                "dominant_failure_class": class_counts.most_common(1)[0][0],
                "failure_class_counts": json.dumps(dict(sorted(class_counts.items())), ensure_ascii=False),
                "avg_oracle_score": round(mean(float(row["oracle_score"]) for row in group if row["oracle_score"] != ""), 6)
                if any(row["oracle_score"] != "" for row in group)
                else "",
                "avg_margin": round(mean(float(row["margin"]) for row in group if row["margin"] != ""), 6)
                if any(row["margin"] != "" for row in group)
                else "",
                "decision_paths": json.dumps(dict(sorted(Counter(row["decision_path"] for row in group).items())), ensure_ascii=False),
            }
        )
    return result


def write_markdown(path: Path, overview: dict[str, Any], by_type: list[dict[str, Any]], by_class: list[dict[str, Any]], examples: list[dict[str, Any]]) -> None:
    lines = [
        "# M16 v3-large Failure Analysis",
        "",
        "## Overview",
        "",
        f"- Attempts: {overview['attempts']}",
        f"- Closure success: {overview['closure_success']} ({overview['closure_accuracy']:.2%})",
        f"- Failures: {overview['failures']} ({overview['failure_rate']:.2%})",
        f"- Wrong selected: {overview['wrong_selected']}",
        f"- No selection / abstain: {overview['no_selection']}",
        "",
        "## Failure Classes",
        "",
        "| Class | Failures | Share | Events |",
        "|---|---:|---:|---:|",
    ]
    for row in by_class:
        lines.append(f"| {row['failure_class']} | {row['failures']} | {float(row['share']):.2%} | {row['events']} |")
    lines.extend(["", "## By Semantic Type", "", "| Semantic Type | Failures | Events | R1 | R2 | R3 | R4 | F1 | Avg Oracle Score | Avg Margin |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row in by_type:
        lines.append(
            f"| {row['semantic_type']} | {row['failures']} | {row['events']} | {row['r1_oracle_not_topk']} | "
            f"{row['r2_oracle_not_top1']} | {row['r3_low_score']} | {row['r4_tie_or_low_margin']} | "
            f"{row['f1_ir_not_ok']} | {row['avg_oracle_score']} | {row['avg_margin']} |"
        )
    lines.extend(["", "## Representative Failed Events", "", "| Event | Type | Domain | Dominant Class | Failures | Avg Oracle Score | Avg Margin |", "|---|---|---|---|---:|---:|---:|"])
    for row in examples:
        lines.append(
            f"| {row['event_id']} | {row['semantic_type']} | {row['domain']} | {row['dominant_failure_class']} | "
            f"{row['failures']} | {row['avg_oracle_score']} | {row['avg_margin']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze M16 v3-large failures.")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(args.details)
    failures: list[dict[str, Any]] = []
    wrong_selected = 0
    no_selection = 0
    closure_success = 0
    for row in rows:
        if truth(row.get("closure_success")) or truth(row.get("full_closure_success")):
            closure_success += 1
            continue
        if row.get("selected_candidate_id", ""):
            if not truth(row.get("oracle_correct")) and not truth(row.get("selection_oracle_correct")):
                wrong_selected += 1
        else:
            no_selection += 1
        scores = parse_scores(row.get("candidate_scores_json", ""))
        failures.append({**row, **classify(row, scores)})

    by_type = summarize(failures, ["semantic_type"])
    by_domain = summarize(failures, ["domain"])
    by_type_domain = summarize(failures, ["semantic_type", "domain"])
    by_event = event_summary(failures)
    class_counts = Counter(row["failure_class"] for row in failures)
    by_class = [
        {
            "failure_class": failure_class,
            "failures": count,
            "share": ratio(count, len(failures)),
            "events": len({row["event_id"] for row in failures if row["failure_class"] == failure_class}),
        }
        for failure_class, count in class_counts.most_common()
    ]
    overview = {
        "attempts": len(rows),
        "closure_success": closure_success,
        "closure_accuracy": ratio(closure_success, len(rows)),
        "failures": len(failures),
        "failure_rate": ratio(len(failures), len(rows)),
        "wrong_selected": wrong_selected,
        "no_selection": no_selection,
    }

    write_csv(args.output_dir / "m16-v3-large-failure-analysis-details.csv", failures)
    write_csv(args.output_dir / "m16-v3-large-failure-analysis-by-class.csv", by_class)
    write_csv(args.output_dir / "m16-v3-large-failure-analysis-by-type.csv", by_type)
    write_csv(args.output_dir / "m16-v3-large-failure-analysis-by-domain.csv", by_domain)
    write_csv(args.output_dir / "m16-v3-large-failure-analysis-by-type-domain.csv", by_type_domain)
    write_csv(args.output_dir / "m16-v3-large-failure-analysis-by-event.csv", by_event)
    (args.output_dir / "m16-v3-large-failure-analysis-summary.json").write_text(
        json.dumps({"overview": overview, "by_class": by_class, "by_type": by_type}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    top_examples = sorted(by_event, key=lambda row: (-int(row["failures"]), row["event_id"]))[:15]
    write_markdown(args.output_dir / "m16-v3-large-failure-analysis.md", overview, by_type, by_class, top_examples)
    print(json.dumps({"overview": overview, "by_class": by_class[:10], "by_type": by_type}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
