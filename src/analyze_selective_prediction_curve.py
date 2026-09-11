from __future__ import annotations

"""Risk-coverage / selective prediction curve from V4 candidate score details.

This is an offline calibration analysis. It does not call a model and does not
re-run OWL materialization for hypothetical thresholds. Success is measured by
whether the top candidate under a threshold equals the private oracle candidate
already present in the evaluation details file.
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from semantic_v2_common import write_csv


def frange(start: float, stop: float, step: float) -> list[float]:
    values: list[float] = []
    current = start
    while current <= stop + 1e-9:
        values.append(round(current, 4))
        current += step
    return values


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def score_rows(row: dict[str, str]) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(row.get("candidate_scores_json", "") or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out = [item for item in parsed if isinstance(item, dict) and item.get("candidate_id")]
    return sorted(out, key=lambda item: (-float(item.get("score", 0.0) or 0.0), str(item.get("candidate_id", ""))))


def hypothetical_select(row: dict[str, str], min_score: float, min_margin: float) -> tuple[str, str, float, float]:
    if row.get("ir_status") != "OK":
        return "", "FAIL_CLOSED_IR_NOT_OK", 0.0, 0.0
    ranked = score_rows(row)
    if not ranked:
        return "", "FAIL_CLOSED_NO_SCORES", 0.0, 0.0
    top = ranked[0]
    top_score = float(top.get("score", 0.0) or 0.0)
    second_score = float(ranked[1].get("score", 0.0) or 0.0) if len(ranked) > 1 else 0.0
    margin = top_score - second_score
    if len(ranked) > 1 and top_score == second_score:
        return "", "ABSTAIN_TIE", top_score, margin
    if top_score >= min_score and margin >= min_margin:
        return str(top["candidate_id"]), "SELECT", top_score, margin
    return "", "ABSTAIN_THRESHOLD", top_score, margin


def summarize_group(rows: list[dict[str, str]], min_score: float, min_margin: float, group: str) -> dict[str, Any]:
    selected = 0
    correct = 0
    wrong = 0
    fail_closed = 0
    threshold_abstain = 0
    top_scores: list[float] = []
    margins: list[float] = []
    for row in rows:
        candidate_id, status, top_score, margin = hypothetical_select(row, min_score, min_margin)
        top_scores.append(top_score)
        margins.append(margin)
        if status == "SELECT":
            selected += 1
            if candidate_id == row.get("oracle_candidate_id"):
                correct += 1
            else:
                wrong += 1
        elif status.startswith("FAIL_CLOSED"):
            fail_closed += 1
        else:
            threshold_abstain += 1
    attempts = len(rows)
    abstains = attempts - selected
    precision = correct / selected if selected else 0.0
    risk = wrong / selected if selected else 0.0
    return {
        "group": group,
        "min_score": min_score,
        "min_margin": min_margin,
        "attempts": attempts,
        "selected": selected,
        "coverage": selected / attempts if attempts else 0.0,
        "oracle_correct": correct,
        "oracle_accuracy": correct / attempts if attempts else 0.0,
        "selected_precision": precision,
        "selected_risk": risk,
        "wrong_selection": wrong,
        "wrong_selection_rate": wrong / attempts if attempts else 0.0,
        "abstains": abstains,
        "abstain_rate": abstains / attempts if attempts else 0.0,
        "fail_closed_ir": fail_closed,
        "threshold_abstain": threshold_abstain,
        "mean_top_score": sum(top_scores) / attempts if attempts else 0.0,
        "mean_margin": sum(margins) / attempts if attempts else 0.0,
    }


def best_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["group"])].append(row)
    chosen = []
    for group, items in sorted(grouped.items()):
        no_risk = [row for row in items if float(row["selected_risk"]) == 0.0]
        pool = no_risk or items
        best = sorted(
            pool,
            key=lambda row: (
                -float(row["oracle_accuracy"]),
                -float(row["coverage"]),
                float(row["min_score"]),
                float(row["min_margin"]),
            ),
        )[0]
        chosen.append({**best, "selection_rule": "max_oracle_accuracy_under_zero_risk" if no_risk else "max_oracle_accuracy"})
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--score-start", type=float, default=0.0)
    parser.add_argument("--score-stop", type=float, default=0.6)
    parser.add_argument("--score-step", type=float, default=0.02)
    parser.add_argument("--margin-start", type=float, default=0.0)
    parser.add_argument("--margin-stop", type=float, default=0.12)
    parser.add_argument("--margin-step", type=float, default=0.02)
    args = parser.parse_args()

    rows = read_csv(args.details)
    curve: list[dict[str, Any]] = []
    for min_score in frange(args.score_start, args.score_stop, args.score_step):
        for min_margin in frange(args.margin_start, args.margin_stop, args.margin_step):
            curve.append(summarize_group(rows, min_score, min_margin, "ALL"))
            by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
            for row in rows:
                by_type[row.get("semantic_type", "")].append(row)
            for semantic_type, group_rows in sorted(by_type.items()):
                curve.append(summarize_group(group_rows, min_score, min_margin, semantic_type))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "risk-coverage-curve.csv", curve)
    write_csv(args.output_dir / "risk-coverage-best-zero-risk.csv", best_rows(curve))

    current = [row for row in curve if row["group"] == "ALL" and row["min_score"] == 0.3 and row["min_margin"] == 0.0]
    print(f"details={args.details}")
    print(f"curve={args.output_dir / 'risk-coverage-curve.csv'}")
    print(f"best={args.output_dir / 'risk-coverage-best-zero-risk.csv'}")
    if current:
        row = current[0]
        print(
            "current_like "
            f"coverage={row['coverage']:.2%} oracle_accuracy={row['oracle_accuracy']:.2%} "
            f"precision={row['selected_precision']:.2%} risk={row['selected_risk']:.2%} "
            f"selected={row['selected']}/{row['attempts']}"
        )
    best_all = [row for row in best_rows(curve) if row["group"] == "ALL"][0]
    print(
        "best_zero_risk "
        f"score={best_all['min_score']} margin={best_all['min_margin']} "
        f"coverage={best_all['coverage']:.2%} oracle_accuracy={best_all['oracle_accuracy']:.2%} "
        f"selected={best_all['selected']}/{best_all['attempts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
