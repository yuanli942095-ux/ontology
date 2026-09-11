from __future__ import annotations

"""Build the V4 R2 ranking diagnostic set and evaluate the V4.2 reranker on it.

Does not call Qwen. Reconstructs Semantic IR from stored V4 details and reranks
public candidates. Oracle IDs are post-hoc labels only.
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from analyze_auto_policy_v4_rank_abstain import (
    DEFAULT_DETAILS,
    classify_rank_abstain,
    parse_scores,
    read_csv,
    write_csv,
)
from auto_policy_v4_constraint_rerank import rank_candidates, select_ranked
from run_auto_policy_v4_ir_candidate_repair import (
    SemanticIR,
    configure_paths,
    load_candidates,
    tokens,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v4.2-ir"
DEFAULT_PREFIX = "auto-policy-v4.2-r2-diagnostic"
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate V4.2 constraint rerank on the R2 diagnostic set")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--min-score", type=float, default=0.30)
    parser.add_argument("--min-margin", type=float, default=0.00)
    parser.add_argument("--k", type=int, default=3)
    return parser.parse_args()


def reconstruct_ir(row: dict[str, str]) -> SemanticIR:
    try:
        fields = json.loads(row.get("ir_json") or "{}")
    except json.JSONDecodeError:
        fields = {}
    if not isinstance(fields, dict):
        fields = {}
    numbers = [item for item in str(row.get("normalized_numbers") or "").split("|") if item]
    dates = [item for item in str(row.get("normalized_dates") or "").split("|") if item]
    codes = [item for item in str(row.get("normalized_codes") or "").split("|") if item]
    return SemanticIR(
        event_id=row.get("event_id", ""),
        semantic_type=row.get("semantic_type", ""),
        ir_status=row.get("ir_status", ""),
        ir_reason=row.get("ir_reason", ""),
        fields=fields,
        normalized_terms=sorted(tokens(row.get("ir_semantic_string", ""))),
        numbers=numbers,
        dates=dates,
        codes=codes,
        source_family=row.get("source_family", ""),
    )


def main() -> int:
    args = parse_args()
    details = read_csv(args.details.resolve())
    paths = configure_paths(args.benchmark_dir.resolve())
    candidates_by_event = load_candidates(paths["candidate_csv"])
    diagnostic: list[dict[str, Any]] = []
    eval_rows: list[dict[str, Any]] = []

    for row in details:
        scores = parse_scores(row.get("candidate_scores_json", ""))
        oracle = (row.get("oracle_candidate_id") or "").strip()
        oracle_rank = None
        oracle_score = None
        for index, item in enumerate(scores, start=1):
            if item.get("candidate_id") == oracle:
                oracle_rank = index
                oracle_score = float(item.get("score") or 0)
                break
        top1 = scores[0] if scores else None
        top2 = scores[1] if len(scores) > 1 else None
        margin = float(top1.get("score") or 0) - float(top2.get("score") or 0) if top1 else 0.0
        if row.get("decision_path") != "IR_RANK_ABSTAIN":
            continue
        subtype = classify_rank_abstain(
            oracle_rank=oracle_rank,
            oracle_score=oracle_score,
            margin=margin,
            min_score=0.30,
            min_margin=0.08,
            k=args.k,
        )
        if subtype != "R2_ORACLE_IN_TOPK_NOT_TOP1":
            continue
        event = {
            "event_id": row["event_id"],
            "semantic_type": row["semantic_type"],
            "domain": row.get("domain", ""),
        }
        ir = reconstruct_ir(row)
        candidates = candidates_by_event.get(row["event_id"], [])
        ranked = rank_candidates(ir, candidates, row["semantic_type"])
        selected_row, status, reason, path = select_ranked(
            ranked, min_score=args.min_score, min_margin=args.min_margin
        )
        new_rank = None
        for index, item in enumerate(ranked, start=1):
            if item["candidate_id"] == oracle:
                new_rank = index
                break
        selected_id = selected_row["candidate_id"] if selected_row else ""
        eval_rows.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "semantic_type": row["semantic_type"],
                "domain": row.get("domain", ""),
                "oracle_candidate_id": oracle,
                "v4_top1_candidate_id": row.get("selected_candidate_id") or (top1.get("candidate_id") if top1 else ""),
                "v4_oracle_rank": oracle_rank,
                "v42_top1_candidate_id": ranked[0]["candidate_id"] if ranked else "",
                "v42_top1_score": ranked[0]["score"] if ranked else "",
                "v42_oracle_rank": new_rank if new_rank is not None else "",
                "v42_selection_status": status,
                "v42_decision_path": path,
                "v42_selected_candidate_id": selected_id,
                "v42_top1_correct": bool(ranked) and ranked[0]["candidate_id"] == oracle,
                "v42_selected_correct": status == "SELECTED" and selected_id == oracle,
                "v4_top1_display": top1.get("display_value", "") if top1 else "",
                "v42_top1_display": ranked[0].get("display_value", "") if ranked else "",
                "oracle_display": next((item.get("display_value") for item in scores if item.get("candidate_id") == oracle), ""),
                "v42_top1_new_status": ranked[0].get("new_status", "") if ranked else "",
                "v42_contradiction_penalty": ranked[0].get("contradiction_penalty", "") if ranked else "",
                "selection_reason": reason,
            }
        )
        diagnostic.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "semantic_type": row["semantic_type"],
                "domain": row.get("domain", ""),
                "oracle_candidate_id": oracle,
                "ir_status": row.get("ir_status", ""),
                "ir_json": row.get("ir_json", ""),
                "source_family": row.get("source_family", ""),
                "v4_oracle_rank": oracle_rank,
                "candidates": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "display_value": candidate["display_value"],
                    }
                    for candidate in candidates
                ],
                "v42_ranked": [
                    {key: value for key, value in item.items() if key != "structure"}
                    for item in ranked
                ],
            }
        )

    n = len(eval_rows)
    top1 = sum(1 for row in eval_rows if row["v42_top1_correct"])
    mrr = sum((1.0 / int(row["v42_oracle_rank"])) for row in eval_rows if row["v42_oracle_rank"]) / n if n else 0.0
    selected_correct = sum(1 for row in eval_rows if row["v42_selected_correct"])
    path_counts = Counter(row["v42_decision_path"] for row in eval_rows)
    still_wrong = [row for row in eval_rows if not row["v42_top1_correct"]]
    summary = [
        {"metric": "n_r2", "value": n},
        {"metric": "v4_top1", "value": 0},
        {"metric": "v42_top1", "value": top1},
        {"metric": "v42_top1_accuracy", "value": top1 / n if n else 0.0},
        {"metric": "v42_mrr", "value": mrr},
        {"metric": "v42_selected_correct", "value": selected_correct},
        {"metric": "v42_selected_correct_rate", "value": selected_correct / n if n else 0.0},
        {"metric": "v42_still_wrong_top1", "value": len(still_wrong)},
        {"metric": "decision_path_counts", "value": json.dumps(dict(path_counts), sort_keys=True)},
    ]
    out_dir = args.output_dir.resolve()
    prefix = args.prefix
    write_csv(out_dir / f"{prefix}-eval.csv", eval_rows)
    write_csv(out_dir / f"{prefix}-summary.csv", summary)
    (out_dir / f"{prefix}-set.json").write_text(
        json.dumps({"n": n, "rows": diagnostic}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("AUTO_POLICY_V4.2 R2 diagnostic rerank")
    print(f"n_r2={n}")
    print(f"V4 Top-1=0/{n}")
    print(f"V4.2 Top-1={top1}/{n} ({(top1 / n if n else 0):.1%})")
    print(f"V4.2 MRR={mrr:.4f}")
    print(f"V4.2 selected correct={selected_correct}/{n} path={dict(path_counts)}")
    print(f"still wrong Top-1={len(still_wrong)}")
    remaining = Counter((row["semantic_type"], row["domain"]) for row in still_wrong)
    for key, count in remaining.most_common(8):
        print(f"  remaining {key[0]} / {key[1]}: {count}")
    print(f"wrote {out_dir / prefix}-*")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
