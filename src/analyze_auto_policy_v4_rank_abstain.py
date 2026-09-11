from __future__ import annotations

"""Offline R1-R4 triage of AUTO_POLICY_V4 IR_RANK_ABSTAIN rows.

Reads an existing V4 details.csv. Does not call Qwen, change prompts, or
re-run repair. Oracle IDs are used only for post-hoc ranking diagnosis.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DETAILS = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4-ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v4-ir"
DEFAULT_PREFIX = "auto-policy-v4-ir-rank-abstain"

R_LABELS = {
    "R1_ORACLE_NOT_IN_TOPK": "Oracle not in Top-K (retrieval / alignment)",
    "R2_ORACLE_IN_TOPK_NOT_TOP1": "Oracle in Top-K but not Top-1 (ranking)",
    "R3_ORACLE_TOP1_SCORE_TOO_LOW": "Oracle is Top-1 but score below min_score",
    "R4_ORACLE_TOP1_MARGIN_TOO_SMALL": "Oracle is Top-1 but Top1-Top2 margin below min_margin",
    "R0_OTHER_GATE_ABSTAIN": "Other gate; ranking would have passed current thresholds",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_scores(raw: str) -> list[dict[str, Any]]:
    payload = json.loads(raw or "[]")
    if not isinstance(payload, list):
        return []
    scored: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        scored.append(item)
    return sorted(
        scored,
        key=lambda item: (-float(item.get("score") or 0), str(item.get("candidate_id") or "")),
    )


def classify_rank_abstain(
    *,
    oracle_rank: int | None,
    oracle_score: float | None,
    margin: float,
    min_score: float,
    min_margin: float,
    k: int,
) -> str:
    if oracle_rank is None or oracle_rank > k:
        return "R1_ORACLE_NOT_IN_TOPK"
    if oracle_rank != 1:
        return "R2_ORACLE_IN_TOPK_NOT_TOP1"
    if oracle_score is None or oracle_score < min_score:
        return "R3_ORACLE_TOP1_SCORE_TOO_LOW"
    if margin < min_margin:
        return "R4_ORACLE_TOP1_MARGIN_TOO_SMALL"
    return "R0_OTHER_GATE_ABSTAIN"


def oracle_rank_value(row: dict[str, Any]) -> int | None:
    value = row.get("oracle_rank")
    if value in (None, ""):
        return None
    return int(value)


def ranking_metrics(rows: list[dict[str, Any]], ks: tuple[int, ...]) -> dict[str, Any]:
    n = len(rows)
    ranks = [oracle_rank_value(row) for row in rows]
    ranked = [rank for rank in ranks if rank is not None]
    out: dict[str, Any] = {
        "n": n,
        "n_ranked": len(ranked),
        "n_unranked": n - len(ranked),
    }
    for k in ks:
        hits = sum(1 for rank in ranks if rank is not None and rank <= k)
        out[f"recall_at_{k}"] = hits / n if n else 0.0
        out[f"recall_at_{k}_n"] = hits
    mrr_all = sum((1.0 / rank) if rank else 0.0 for rank in ranks)
    out["mrr"] = mrr_all / n if n else 0.0
    out["mrr_ranked_only"] = (sum(1.0 / rank for rank in ranked) / len(ranked)) if ranked else 0.0
    rank_hist = Counter(rank if rank is not None else 0 for rank in ranks)
    out["oracle_rank_hist"] = dict(sorted(rank_hist.items()))
    return out


def format_pct(value: float) -> str:
    return f"{value:.1%}"


def print_metrics(title: str, metrics: dict[str, Any], ks: tuple[int, ...]) -> None:
    print(f"{title}: n={metrics['n']} ranked={metrics['n_ranked']} unranked={metrics['n_unranked']}")
    for k in ks:
        print(
            f"  Recall@{k}={format_pct(metrics[f'recall_at_{k}'])} "
            f"({metrics[f'recall_at_{k}_n']}/{metrics['n']})"
        )
    print(
        f"  MRR={metrics['mrr']:.4f} (unranked=0)  "
        f"MRR|ranked={metrics['mrr_ranked_only']:.4f}"
    )
    print(f"  oracle_rank_hist={metrics['oracle_rank_hist']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline R1-R4 analysis of V4 IR_RANK_ABSTAIN")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--min-score", type=float, default=0.30)
    parser.add_argument("--min-margin", type=float, default=0.08)
    parser.add_argument("--k", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    details = read_csv(args.details.resolve())
    ks = tuple(sorted({1, args.k, 5}))
    rank_rows: list[dict[str, Any]] = []
    r_counts: Counter[str] = Counter()
    r_by_type: dict[str, Counter[str]] = defaultdict(Counter)
    r_by_domain: dict[str, Counter[str]] = defaultdict(Counter)

    for row in details:
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
        top1_score = float(top1.get("score") or 0) if top1 else 0.0
        top2_score = float(top2.get("score") or 0) if top2 else 0.0
        margin = top1_score - top2_score
        subtype = ""
        if row.get("decision_path") == "IR_RANK_ABSTAIN":
            subtype = classify_rank_abstain(
                oracle_rank=oracle_rank,
                oracle_score=oracle_score,
                margin=margin,
                min_score=args.min_score,
                min_margin=args.min_margin,
                k=args.k,
            )
            r_counts[subtype] += 1
            r_by_type[row.get("semantic_type") or ""][subtype] += 1
            r_by_domain[row.get("domain") or ""][subtype] += 1
        rank_rows.append(
            {
                "event_id": row.get("event_id", ""),
                "run": row.get("run", ""),
                "semantic_type": row.get("semantic_type", ""),
                "domain": row.get("domain", ""),
                "ir_status": row.get("ir_status", ""),
                "decision_path": row.get("decision_path", ""),
                "selection_status": row.get("selection_status", ""),
                "selection_reason": row.get("selection_reason", ""),
                "oracle_candidate_id": oracle,
                "n_candidates": len(scores),
                "oracle_rank": oracle_rank if oracle_rank is not None else "",
                "oracle_score": "" if oracle_score is None else round(oracle_score, 4),
                "top1_candidate_id": str(top1.get("candidate_id") or "") if top1 else "",
                "top1_score": round(top1_score, 4),
                "top2_candidate_id": str(top2.get("candidate_id") or "") if top2 else "",
                "top2_score": round(top2_score, 4),
                "margin": round(margin, 4),
                "rank_abstain_subtype": subtype,
                "candidate_scores_json": row.get("candidate_scores_json", ""),
            }
        )

    abstain_rows = [row for row in rank_rows if row["decision_path"] == "IR_RANK_ABSTAIN"]
    ok_rows = [row for row in rank_rows if row["ir_status"] == "OK"]
    slices = {
        "ALL": rank_rows,
        "IR_OK": ok_rows,
        "IR_RANK_ABSTAIN": abstain_rows,
    }

    metric_rows: list[dict[str, Any]] = []
    for slice_name, slice_rows in slices.items():
        metrics = ranking_metrics(slice_rows, ks)
        metric_rows.append(
            {
                "slice": slice_name,
                "min_score": args.min_score,
                "min_margin": args.min_margin,
                "k": args.k,
                **{key: value for key, value in metrics.items() if key != "oracle_rank_hist"},
                "oracle_rank_hist": json.dumps(metrics["oracle_rank_hist"], sort_keys=True),
            }
        )

    subtype_rows = []
    n_abstain = len(abstain_rows)
    for key in (
        "R1_ORACLE_NOT_IN_TOPK",
        "R2_ORACLE_IN_TOPK_NOT_TOP1",
        "R3_ORACLE_TOP1_SCORE_TOO_LOW",
        "R4_ORACLE_TOP1_MARGIN_TOO_SMALL",
        "R0_OTHER_GATE_ABSTAIN",
    ):
        count = r_counts[key]
        subtype_rows.append(
            {
                "subtype": key,
                "label": R_LABELS[key],
                "n": count,
                "share_of_rank_abstain": (count / n_abstain) if n_abstain else 0.0,
                "share_of_all": (count / len(rank_rows)) if rank_rows else 0.0,
            }
        )

    breakdown_rows: list[dict[str, Any]] = []
    for axis, grouped in (("semantic_type", r_by_type), ("domain", r_by_domain)):
        for group, counts in sorted(grouped.items()):
            total = sum(counts.values())
            for subtype, count in counts.most_common():
                breakdown_rows.append(
                    {
                        "axis": axis,
                        "group": group,
                        "subtype": subtype,
                        "n": count,
                        "share_of_group": (count / total) if total else 0.0,
                    }
                )

    out_dir = args.output_dir.resolve()
    prefix = args.prefix
    write_csv(out_dir / f"{prefix}-ranked-details.csv", rank_rows)
    write_csv(out_dir / f"{prefix}-ir-rank-abstain.csv", abstain_rows)
    write_csv(out_dir / f"{prefix}-r-subtypes.csv", subtype_rows)
    write_csv(out_dir / f"{prefix}-recall-mrr.csv", metric_rows)
    write_csv(out_dir / f"{prefix}-r-subtypes-by-group.csv", breakdown_rows)

    print("AUTO_POLICY_V4 IR_RANK_ABSTAIN R1-R4")
    print(f"details={args.details.resolve()}")
    print(f"min_score={args.min_score} min_margin={args.min_margin} K={args.k}")
    print(f"n_all={len(rank_rows)} ir_ok={len(ok_rows)} ir_rank_abstain={n_abstain}")
    print()
    print("R-subtypes among IR_RANK_ABSTAIN:")
    for row in subtype_rows:
        print(f"  {row['subtype']:32s} {row['n']:3d}  {format_pct(row['share_of_rank_abstain'])}  {row['label']}")
    print()
    for slice_name, slice_rows in slices.items():
        print_metrics(slice_name, ranking_metrics(slice_rows, ks), ks)
        print()
    print("R-subtypes by semantic_type:")
    for semantic_type, counts in sorted(r_by_type.items()):
        total = sum(counts.values())
        parts = ", ".join(f"{key}={value}" for key, value in counts.most_common())
        print(f"  {semantic_type}: n={total}  {parts}")
    print()
    print(f"wrote {out_dir / prefix}-*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
