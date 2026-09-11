from __future__ import annotations

"""Offline coverage-accuracy sweep over AUTO_POLICY_V4 ranking gates.

Replays stored candidate scores from details.csv. Does not call Qwen, change
prompts, rematerialize OWL, or re-run the reasoner. Oracle IDs are used only
for post-hoc evaluation of simulated selections.

IR_FAIL_CLOSED stays fail-closed. IR_EXACT_MATCH keeps the original unique
semantic match. Only the ranking path (IR_TOPK_RANK / IR_RANK_ABSTAIN) is
re-gated by min_score / min_margin.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from analyze_auto_policy_v4_rank_abstain import (
    DEFAULT_DETAILS,
    classify_rank_abstain,
    parse_scores,
    read_csv,
    write_csv,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v4-ir"
DEFAULT_PREFIX = "auto-policy-v4-ir-coverage-accuracy"
BASELINE_SCORE = 0.30
BASELINE_MARGIN = 0.08
SCORE_GRID = (0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)
MARGIN_GRID = (0.00, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline V4 coverage-accuracy curve")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--baseline-score", type=float, default=BASELINE_SCORE)
    parser.add_argument("--baseline-margin", type=float, default=BASELINE_MARGIN)
    parser.add_argument("--k", type=int, default=3)
    return parser.parse_args()


def prepare_row(row: dict[str, str], *, min_score: float, min_margin: float, k: int) -> dict[str, Any]:
    scores = parse_scores(row.get("candidate_scores_json", ""))
    oracle = (row.get("oracle_candidate_id") or "").strip()
    oracle_rank: int | None = None
    oracle_score: float | None = None
    for index, item in enumerate(scores, start=1):
        if str(item.get("candidate_id") or "") == oracle:
            oracle_rank = index
            oracle_score = float(item.get("score") or 0)
            break
    top1 = scores[0] if scores else None
    top2 = scores[1] if len(scores) > 1 else None
    top1_id = str(top1.get("candidate_id") or "") if top1 else ""
    top1_score = float(top1.get("score") or 0) if top1 else 0.0
    top2_score = float(top2.get("score") or 0) if top2 else 0.0
    margin = top1_score - top2_score
    subtype = ""
    if row.get("decision_path") == "IR_RANK_ABSTAIN":
        subtype = classify_rank_abstain(
            oracle_rank=oracle_rank,
            oracle_score=oracle_score,
            margin=margin,
            min_score=min_score,
            min_margin=min_margin,
            k=k,
        )
    return {
        "event_id": row.get("event_id", ""),
        "run": row.get("run", ""),
        "semantic_type": row.get("semantic_type", ""),
        "ir_status": row.get("ir_status", ""),
        "decision_path": row.get("decision_path", ""),
        "original_selected_id": (row.get("selected_candidate_id") or "").strip(),
        "oracle_candidate_id": oracle,
        "oracle_rank": oracle_rank,
        "oracle_score": oracle_score,
        "top1_candidate_id": top1_id,
        "top1_score": top1_score,
        "margin": margin,
        "n_candidates": len(scores),
        "rank_abstain_subtype": subtype,
        "has_scores": bool(scores),
    }


def simulate_selection(prepared: dict[str, Any], *, min_score: float, min_margin: float) -> dict[str, Any]:
    if prepared["ir_status"] != "OK":
        return {
            "selected_id": "",
            "status": "ABSTAIN",
            "decision_path": "IR_FAIL_CLOSED",
        }
    if prepared["decision_path"] == "IR_EXACT_MATCH":
        return {
            "selected_id": prepared["original_selected_id"],
            "status": "SELECTED",
            "decision_path": "IR_EXACT_MATCH",
        }
    if not prepared["has_scores"]:
        return {
            "selected_id": "",
            "status": "ABSTAIN",
            "decision_path": "NO_CANDIDATES",
        }
    if prepared["top1_score"] >= min_score and prepared["margin"] >= min_margin:
        return {
            "selected_id": prepared["top1_candidate_id"],
            "status": "SELECTED",
            "decision_path": "IR_TOPK_RANK",
        }
    return {
        "selected_id": "",
        "status": "ABSTAIN",
        "decision_path": "IR_RANK_ABSTAIN",
    }


def evaluate_point(prepared_rows: list[dict[str, Any]], *, min_score: float, min_margin: float) -> dict[str, Any]:
    n = len(prepared_rows)
    selected = 0
    correct = 0
    path_counts: dict[str, int] = {}
    recovered = {"R2": 0, "R3": 0, "R4": 0, "R2_correct": 0, "R3_correct": 0, "R4_correct": 0}
    for row in prepared_rows:
        sim = simulate_selection(row, min_score=min_score, min_margin=min_margin)
        path = sim["decision_path"]
        path_counts[path] = path_counts.get(path, 0) + 1
        is_selected = sim["status"] == "SELECTED"
        is_correct = is_selected and sim["selected_id"] == row["oracle_candidate_id"]
        if is_selected:
            selected += 1
        if is_correct:
            correct += 1
        subtype = row["rank_abstain_subtype"]
        if subtype.startswith("R2") and is_selected:
            recovered["R2"] += 1
            recovered["R2_correct"] += int(is_correct)
        elif subtype.startswith("R3") and is_selected:
            recovered["R3"] += 1
            recovered["R3_correct"] += int(is_correct)
        elif subtype.startswith("R4") and is_selected:
            recovered["R4"] += 1
            recovered["R4_correct"] += int(is_correct)
    precision = (correct / selected) if selected else 0.0
    return {
        "min_score": min_score,
        "min_margin": min_margin,
        "n": n,
        "selected": selected,
        "abstains": n - selected,
        "oracle_correct": correct,
        "coverage": selected / n if n else 0.0,
        "oracle_accuracy": correct / n if n else 0.0,
        "precision_given_select": precision,
        "abstain_rate": (n - selected) / n if n else 0.0,
        "decision_path_counts": json.dumps(path_counts, sort_keys=True),
        "recovered_r2": recovered["R2"],
        "recovered_r2_correct": recovered["R2_correct"],
        "recovered_r3": recovered["R3"],
        "recovered_r3_correct": recovered["R3_correct"],
        "recovered_r4": recovered["R4"],
        "recovered_r4_correct": recovered["R4_correct"],
        "is_baseline": int(min_score == BASELINE_SCORE and min_margin == BASELINE_MARGIN),
    }


def format_pct(value: float) -> str:
    return f"{value:.1%}"


def print_point(label: str, point: dict[str, Any]) -> None:
    print(
        f"{label:28s} score={point['min_score']:.2f} margin={point['min_margin']:.2f}  "
        f"cov={format_pct(point['coverage'])}  "
        f"oracle={format_pct(point['oracle_accuracy'])}  "
        f"P|sel={format_pct(point['precision_given_select'])}  "
        f"sel={point['selected']}/{point['n']}  "
        f"R3+={point['recovered_r3']} R4+={point['recovered_r4']} R2+={point['recovered_r2']}"
    )


def main() -> int:
    args = parse_args()
    raw_rows = read_csv(args.details.resolve())
    prepared = [
        prepare_row(
            row,
            min_score=args.baseline_score,
            min_margin=args.baseline_margin,
            k=args.k,
        )
        for row in raw_rows
    ]

    grid_rows = [
        evaluate_point(prepared, min_score=score, min_margin=margin)
        for score in SCORE_GRID
        for margin in MARGIN_GRID
    ]
    score_at_baseline_margin = [
        evaluate_point(prepared, min_score=score, min_margin=args.baseline_margin)
        for score in SCORE_GRID
    ]
    score_at_zero_margin = [
        evaluate_point(prepared, min_score=score, min_margin=0.0)
        for score in SCORE_GRID
    ]
    margin_at_baseline_score = [
        evaluate_point(prepared, min_score=args.baseline_score, min_margin=margin)
        for margin in MARGIN_GRID
    ]
    margin_at_zero_score = [
        evaluate_point(prepared, min_score=0.0, min_margin=margin)
        for margin in MARGIN_GRID
    ]

    baseline = evaluate_point(
        prepared, min_score=args.baseline_score, min_margin=args.baseline_margin
    )
    always_top1 = evaluate_point(prepared, min_score=0.0, min_margin=0.0)
    default_argparse = evaluate_point(prepared, min_score=0.42, min_margin=0.08)

    out_dir = args.output_dir.resolve()
    prefix = args.prefix
    write_csv(out_dir / f"{prefix}-grid.csv", grid_rows)
    write_csv(out_dir / f"{prefix}-score-sweep-margin0.08.csv", score_at_baseline_margin)
    write_csv(out_dir / f"{prefix}-score-sweep-margin0.csv", score_at_zero_margin)
    write_csv(out_dir / f"{prefix}-margin-sweep-score0.30.csv", margin_at_baseline_score)
    write_csv(out_dir / f"{prefix}-margin-sweep-score0.csv", margin_at_zero_score)

    print("AUTO_POLICY_V4 coverage-accuracy sweep")
    print(f"details={args.details.resolve()}")
    print("fail-closed stays abstain; IR_EXACT_MATCH kept; ranking path re-gated")
    print()
    print_point("current V4 (0.30/0.08)", baseline)
    print_point("argparse default (0.42/0.08)", default_argparse)
    print_point("always Top-1 (0/0)", always_top1)
    print()
    print(f"Score sweep at min_margin={args.baseline_margin:.2f}:")
    for point in score_at_baseline_margin:
        print_point(f"  score={point['min_score']:.2f}", point)
    print()
    print(f"Margin sweep at min_score={args.baseline_score:.2f}:")
    for point in margin_at_baseline_score:
        print_point(f"  margin={point['min_margin']:.2f}", point)
    print()
    print("Always Top-1 minus current V4 is the gate-only headroom:")
    print(
        f"  oracle {baseline['oracle_correct']} -> {always_top1['oracle_correct']} "
        f"(+{always_top1['oracle_correct'] - baseline['oracle_correct']})"
    )
    print(
        f"  selected {baseline['selected']} -> {always_top1['selected']} "
        f"(+{always_top1['selected'] - baseline['selected']})"
    )
    print(
        f"  P|sel {format_pct(baseline['precision_given_select'])} -> "
        f"{format_pct(always_top1['precision_given_select'])}"
    )
    print(f"wrote {out_dir / prefix}-*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
