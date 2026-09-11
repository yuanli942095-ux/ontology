from __future__ import annotations

"""Offline diagnosis of V4 R2 ranking inversions.

R2 = IR_RANK_ABSTAIN where the oracle is in Top-K but not Top-1.
Does not call Qwen. Uses stored candidate_scores_json plus public candidate
operations. Oracle IDs are post-hoc labels only.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from analyze_auto_policy_v4_rank_abstain import (
    DEFAULT_DETAILS,
    classify_rank_abstain,
    parse_scores,
    read_csv,
    write_csv,
)
from external_real_v8_layout import candidate_selection_paths, is_staged_layout


PROJECT_DIR = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v4-ir"
DEFAULT_PREFIX = "auto-policy-v4-ir-r2-ranking"
SCORE_FIELDS = (
    "family_score",
    "token_score",
    "number_score",
    "code_score",
    "exact_score",
    "semantic_bonus",
    "negative_penalty",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose V4 R2 ranking inversions")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--min-score", type=float, default=0.30)
    parser.add_argument("--min-margin", type=float, default=0.08)
    parser.add_argument("--k", type=int, default=3)
    return parser.parse_args()


def weighted_parts(item: dict[str, Any]) -> dict[str, float]:
    family = float(item.get("family_score") or 0)
    token = float(item.get("token_score") or 0)
    number = float(item.get("number_score") or 0)
    code = float(item.get("code_score") or 0)
    exact = float(item.get("exact_score") or 0)
    bonus = float(item.get("semantic_bonus") or 0)
    penalty = float(item.get("negative_penalty") or 0)
    semantic = 0.26 * token + 0.04 * exact + bonus
    constraint = 0.30 * family + 0.20 * max(code, number) + 0.12 * number + 0.08 * code - penalty
    return {
        "semantic_score": round(semantic, 4),
        "constraint_score": round(constraint, 4),
        "reconstructed_score": round(max(0.0, min(1.0, semantic + constraint)), 4),
    }


def operation_key(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {"operator": "", "predicate": ""}
    predicate = str(payload.get("predicate_iri") or "")
    local = predicate.rsplit("#", 1)[-1] if predicate else ""
    return {
        "operator": str(payload.get("operator") or ""),
        "predicate": local or predicate,
    }


def load_operations(benchmark_dir: Path) -> dict[tuple[str, str], dict[str, str]]:
    if is_staged_layout(benchmark_dir):
        path = candidate_selection_paths(benchmark_dir)["candidate_csv"]
    else:
        path = benchmark_dir / "input" / "external-real-candidate-template.csv"
    out: dict[tuple[str, str], dict[str, str]] = {}
    for row in read_csv(path):
        try:
            payload = json.loads(row.get("operation_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        out[(row["event_id"], row["candidate_id"])] = operation_key(payload)
    return out


def dominant_field(top1: dict[str, Any], oracle: dict[str, Any]) -> tuple[str, float]:
    best_name = ""
    best_delta = -1e9
    for field in SCORE_FIELDS:
        top_value = float(top1.get(field) or 0)
        oracle_value = float(oracle.get(field) or 0)
        if field == "negative_penalty":
            delta = oracle_value - top_value
        else:
            delta = top_value - oracle_value
        if delta > best_delta:
            best_delta = delta
            best_name = field
    return best_name, round(best_delta, 4)


def find_item(scores: list[dict[str, Any]], candidate_id: str) -> dict[str, Any] | None:
    for item in scores:
        if str(item.get("candidate_id") or "") == candidate_id:
            return item
    return None


def main() -> int:
    args = parse_args()
    operations = load_operations(args.benchmark_dir.resolve())
    details = read_csv(args.details.resolve())
    r2_rows: list[dict[str, Any]] = []

    for row in details:
        scores = parse_scores(row.get("candidate_scores_json", ""))
        oracle_id = (row.get("oracle_candidate_id") or "").strip()
        oracle = find_item(scores, oracle_id)
        oracle_rank = None
        if oracle is not None:
            oracle_rank = scores.index(oracle) + 1
        top1 = scores[0] if scores else None
        top2 = scores[1] if len(scores) > 1 else None
        top1_score = float(top1.get("score") or 0) if top1 else 0.0
        top2_score = float(top2.get("score") or 0) if top2 else 0.0
        oracle_score = float(oracle.get("score") or 0) if oracle else None
        margin = top1_score - top2_score
        if row.get("decision_path") != "IR_RANK_ABSTAIN":
            continue
        subtype = classify_rank_abstain(
            oracle_rank=oracle_rank,
            oracle_score=oracle_score,
            margin=margin,
            min_score=args.min_score,
            min_margin=args.min_margin,
            k=args.k,
        )
        if subtype != "R2_ORACLE_IN_TOPK_NOT_TOP1" or top1 is None or oracle is None:
            continue
        inversion_margin = top1_score - float(oracle_score)
        if inversion_margin == 0:
            r2_kind = "R2_TIE_ID_BREAK" if top1_score == 0 else "R2_TIE"
        else:
            r2_kind = "R2_INVERSION"
        field, field_delta = dominant_field(top1, oracle)
        top_parts = weighted_parts(top1)
        oracle_parts = weighted_parts(oracle)
        top_op = operations.get((row["event_id"], str(top1.get("candidate_id") or "")), {})
        oracle_op = operations.get((row["event_id"], oracle_id), {})
        r2_rows.append(
            {
                "event_id": row.get("event_id", ""),
                "run": row.get("run", ""),
                "semantic_type": row.get("semantic_type", ""),
                "domain": row.get("domain", ""),
                "oracle_candidate_id": oracle_id,
                "top1_candidate_id": str(top1.get("candidate_id") or ""),
                "oracle_rank": oracle_rank,
                "n_candidates": len(scores),
                "r2_kind": r2_kind,
                "top1_score": round(top1_score, 4),
                "oracle_score": round(float(oracle_score), 4),
                "inversion_margin": round(inversion_margin, 4),
                "dominant_field": "" if r2_kind.startswith("R2_TIE") else field,
                "dominant_field_delta": "" if r2_kind.startswith("R2_TIE") else field_delta,
                "top1_semantic_score": top_parts["semantic_score"],
                "oracle_semantic_score": oracle_parts["semantic_score"],
                "semantic_delta": round(top_parts["semantic_score"] - oracle_parts["semantic_score"], 4),
                "top1_constraint_score": top_parts["constraint_score"],
                "oracle_constraint_score": oracle_parts["constraint_score"],
                "constraint_delta": round(top_parts["constraint_score"] - oracle_parts["constraint_score"], 4),
                "top1_semantic_match": bool(top1.get("semantic_match")),
                "oracle_semantic_match": bool(oracle.get("semantic_match")),
                "operation_operator_same": top_op.get("operator", "") == oracle_op.get("operator", "") and bool(top_op.get("operator")),
                "operation_predicate_same": top_op.get("predicate", "") == oracle_op.get("predicate", "") and bool(top_op.get("predicate")),
                "top1_operator": top_op.get("operator", ""),
                "oracle_operator": oracle_op.get("operator", ""),
                "top1_predicate": top_op.get("predicate", ""),
                "oracle_predicate": oracle_op.get("predicate", ""),
                "top1_family_score": top1.get("family_score"),
                "oracle_family_score": oracle.get("family_score"),
                "top1_token_score": top1.get("token_score"),
                "oracle_token_score": oracle.get("token_score"),
                "top1_number_score": top1.get("number_score"),
                "oracle_number_score": oracle.get("number_score"),
                "top1_code_score": top1.get("code_score"),
                "oracle_code_score": oracle.get("code_score"),
                "top1_exact_score": top1.get("exact_score"),
                "oracle_exact_score": oracle.get("exact_score"),
                "top1_negative_penalty": top1.get("negative_penalty"),
                "oracle_negative_penalty": oracle.get("negative_penalty"),
                "top1_display_value": top1.get("display_value", ""),
                "oracle_display_value": oracle.get("display_value", ""),
            }
        )

    n = len(r2_rows)
    kind_counts = Counter(row["r2_kind"] for row in r2_rows)
    rank_counts = Counter(int(row["oracle_rank"]) for row in r2_rows)
    field_counts = Counter(row["dominant_field"] for row in r2_rows if row["dominant_field"])
    type_counts = Counter(row["semantic_type"] for row in r2_rows)
    inversions = [row for row in r2_rows if row["r2_kind"] == "R2_INVERSION"]
    semantic_wins = sum(1 for row in inversions if float(row["semantic_delta"]) > float(row["constraint_delta"]) and float(row["semantic_delta"]) > 0)
    constraint_wins = sum(1 for row in inversions if float(row["constraint_delta"]) > float(row["semantic_delta"]) and float(row["constraint_delta"]) > 0)
    both_match_false = sum(1 for row in r2_rows if not row["top1_semantic_match"] and not row["oracle_semantic_match"])
    predicate_same = sum(1 for row in r2_rows if row["operation_predicate_same"])
    operator_same = sum(1 for row in r2_rows if row["operation_operator_same"])
    recall_at = {
        k: sum(1 for row in r2_rows if int(row["oracle_rank"]) <= k) / n if n else 0.0
        for k in (2, 3, 5)
    }

    summary_rows = [
        {"metric": "n_r2", "value": n},
        {"metric": "oracle_in_top2", "value": recall_at[2]},
        {"metric": "oracle_in_top3", "value": recall_at[3]},
        {"metric": "oracle_in_top5", "value": recall_at[5]},
        {"metric": "oracle_rank_2", "value": rank_counts.get(2, 0)},
        {"metric": "oracle_rank_3", "value": rank_counts.get(3, 0)},
        {"metric": "r2_inversion", "value": kind_counts.get("R2_INVERSION", 0)},
        {"metric": "r2_tie", "value": kind_counts.get("R2_TIE", 0)},
        {"metric": "r2_tie_zero_id_break", "value": kind_counts.get("R2_TIE_ID_BREAK", 0)},
        {"metric": "mean_inversion_margin", "value": (sum(float(row["inversion_margin"]) for row in inversions) / len(inversions)) if inversions else 0.0},
        {"metric": "inversions_driven_by_semantic", "value": semantic_wins},
        {"metric": "inversions_driven_by_constraint", "value": constraint_wins},
        {"metric": "semantic_match_both_false", "value": both_match_false},
        {"metric": "operation_operator_same", "value": operator_same},
        {"metric": "operation_predicate_same", "value": predicate_same},
    ]
    for name, count in field_counts.most_common():
        summary_rows.append({"metric": f"dominant_field_{name}", "value": count})
    breakdown_rows: list[dict[str, Any]] = []
    for axis, key in (("semantic_type", "semantic_type"), ("domain", "domain"), ("r2_kind", "r2_kind")):
        grouped: dict[str, int] = defaultdict(int)
        for row in r2_rows:
            grouped[str(row[key])] += 1
        for group, count in sorted(grouped.items(), key=lambda item: -item[1]):
            breakdown_rows.append({"axis": axis, "group": group, "n": count, "share": count / n if n else 0.0})

    out_dir = args.output_dir.resolve()
    prefix = args.prefix
    write_csv(out_dir / f"{prefix}-details.csv", r2_rows)
    write_csv(out_dir / f"{prefix}-summary.csv", summary_rows)
    write_csv(out_dir / f"{prefix}-by-group.csv", breakdown_rows)

    print("AUTO_POLICY_V4 R2 ranking diagnosis")
    print(f"details={args.details.resolve()}")
    print(f"n_r2={n}")
    print(f"oracle_rank={dict(sorted(rank_counts.items()))}")
    print(f"Recall@2={recall_at[2]:.1%} Recall@3={recall_at[3]:.1%} Recall@5={recall_at[5]:.1%}")
    print(f"kind={dict(kind_counts)}")
    if inversions:
        mean_margin = sum(float(row["inversion_margin"]) for row in inversions) / len(inversions)
        print(f"inversions={len(inversions)} mean_margin={mean_margin:.4f}")
        print(f"dominant_field={dict(field_counts)}")
        print(f"driven_by semantic={semantic_wins} constraint={constraint_wins}")
    print(f"semantic_match both false={both_match_false}/{n}")
    print(f"operation operator_same={operator_same}/{n} predicate_same={predicate_same}/{n}")
    print("by semantic_type:", dict(type_counts))
    print(f"wrote {out_dir / prefix}-*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
