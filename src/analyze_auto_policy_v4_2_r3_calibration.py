from __future__ import annotations

"""Archive the V4.2 R3 pool and event-grouped confidence calibration.

Does not call Qwen, change prompts, or lower min_score as a default gate.
Oracle IDs are used only for post-hoc labels. Newly admitted R3 rows are scored
as oracle-correct; OWL closure is not re-run here.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from analyze_auto_policy_v4_rank_abstain import parse_scores, read_csv, write_csv
from auto_policy_v4_calibration import (
    ece,
    event_grouped_folds,
    fit_isotonic,
    fit_platt,
    predict_isotonic,
    predict_platt,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DETAILS = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.2-ir"
    / "auto-policy-v4.2-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v4.2-ir"
DEFAULT_PREFIX = "auto-policy-v4.2-r3-calibration"
MIN_SCORE = 0.30
SCORE_BINS = (0.00, 0.10, 0.20, 0.30, 0.40, 0.55, 1.01)
MARGIN_BINS = (0.00, 0.02, 0.05, 0.10, 0.20, 1.01)


def parse_ranked(raw: str) -> list[dict[str, Any]]:
    rows = parse_scores(raw)
    return sorted(rows, key=lambda item: -float(item.get("score") or 0))


def ir_field_count(raw: str) -> int:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    count = 0
    for value in payload.values():
        if value not in (None, "", [], {}):
            count += 1
    return count


def structured_match_count(top: dict[str, Any]) -> int:
    keys = (
        "structured_value_match",
        "temporal_role_match",
        "relation_direction_match",
        "status_consistency",
        "ontology_constraint_score",
        "family_score",
        "token_score",
        "number_score",
        "code_score",
        "exact_score",
    )
    return sum(1 for key in keys if float(top.get(key) or 0) > 0)


def bin_label(value: float, edges: tuple[float, ...]) -> str:
    for low, high in zip(edges, edges[1:]):
        if low <= value < high:
            return f"[{low:.2f},{high:.2f})"
    return f"[{edges[-2]:.2f},]"


def extract_row(row: dict[str, str]) -> dict[str, Any]:
    ranked = parse_ranked(row.get("candidate_scores_json", ""))
    oracle = (row.get("oracle_candidate_id") or "").strip()
    top1 = ranked[0] if ranked else None
    top2 = ranked[1] if len(ranked) > 1 else None
    top1_score = float(top1.get("score") or 0) if top1 else 0.0
    top2_score = float(top2.get("score") or 0) if top2 else 0.0
    unique_top1 = bool(top1) and (top2 is None or top1_score > top2_score)
    top1_id = str(top1.get("candidate_id") or "") if top1 else ""
    oracle_rank = None
    for index, item in enumerate(ranked, start=1):
        if str(item.get("candidate_id") or "") == oracle:
            oracle_rank = index
            break
    selected = row.get("selection_status") == "SELECTED"
    top1_correct = unique_top1 and top1_id == oracle
    r3 = (
        row.get("ir_status") == "OK"
        and unique_top1
        and top1_correct
        and not selected
        and top1_score < MIN_SCORE
    )
    r2 = row.get("ir_status") == "OK" and unique_top1 and oracle_rank not in (None, 1) and not selected
    return {
        "event_id": row.get("event_id", ""),
        "run": row.get("run", ""),
        "semantic_type": row.get("semantic_type", ""),
        "domain": row.get("domain", ""),
        "ir_status": row.get("ir_status", ""),
        "decision_path": row.get("decision_path", ""),
        "selection_status": row.get("selection_status", ""),
        "selected_candidate_id": row.get("selected_candidate_id", ""),
        "oracle_candidate_id": oracle,
        "top1_candidate_id": top1_id,
        "top1_score": round(top1_score, 4),
        "top2_score": round(top2_score, 4),
        "score_margin": round(top1_score - top2_score, 4),
        "unique_top1": unique_top1,
        "top1_correct": top1_correct,
        "oracle_rank": oracle_rank if oracle_rank is not None else "",
        "structured_value_match": float(top1.get("structured_value_match") or 0) if top1 else 0.0,
        "temporal_role_match": float(top1.get("temporal_role_match") or 0) if top1 else 0.0,
        "contradiction_penalty": float(top1.get("contradiction_penalty") or 0) if top1 else 0.0,
        "family_score": float(top1.get("family_score") or 0) if top1 else 0.0,
        "token_score": float(top1.get("token_score") or 0) if top1 else 0.0,
        "structured_match_count": structured_match_count(top1 or {}),
        "ir_field_count": ir_field_count(row.get("ir_json", "")),
        "ir_complete": int(row.get("ir_status") == "OK"),
        "is_r3": r3,
        "is_r2": r2,
        "is_tie": row.get("decision_path") == "IR_RANK_TIE_ABSTAIN",
        "top1_display": top1.get("display_value", "") if top1 else "",
        "oracle_display": row.get("oracle_value", ""),
        "selection_oracle_correct": row.get("selection_oracle_correct", ""),
        "full_closure_success": row.get("full_closure_success", ""),
    }


def reliability_table(rows: list[dict[str, Any]], key: str, labeler) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(labeler(row))].append(row)
    out = []
    for label, items in sorted(groups.items()):
        n = len(items)
        correct = sum(1 for item in items if item["top1_correct"])
        out.append(
            {
                "feature": key,
                "bin": label,
                "n": n,
                "top1_accuracy": correct / n if n else 0.0,
                "r3_n": sum(1 for item in items if item["is_r3"]),
            }
        )
    return out


def simulate_policy(all_rows: list[dict[str, Any]], extracted: list[dict[str, Any]], *, select_if) -> dict[str, Any]:
    by_key = {(row["event_id"], str(row["run"])): row for row in extracted}
    n = len(all_rows)
    selected = 0
    correct = 0
    admitted_r3 = 0
    for raw in all_rows:
        item = by_key[(raw["event_id"], raw["run"])]
        choose = False
        if raw.get("decision_path") == "IR_FAIL_CLOSED":
            choose = False
        elif raw.get("decision_path") == "IR_EXACT_MATCH":
            choose = True
        elif item["is_tie"] or not item["unique_top1"]:
            choose = False
        else:
            choose = bool(select_if(item))
        if choose:
            selected += 1
            if item["top1_correct"] or str(raw.get("selection_oracle_correct")).lower() == "true":
                correct += 1
            if item["is_r3"]:
                admitted_r3 += 1
    return {
        "n": n,
        "selected": selected,
        "oracle_correct": correct,
        "coverage": selected / n if n else 0.0,
        "oracle_accuracy": correct / n if n else 0.0,
        "precision_given_select": correct / selected if selected else 0.0,
        "admitted_r3": admitted_r3,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V4.2 R3 archive and event-grouped calibration")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--folds", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_rows = read_csv(args.details.resolve())
    extracted = [extract_row(row) for row in raw_rows]
    r3_rows = [row for row in extracted if row["is_r3"]]
    ranking_rows = [
        row
        for row in extracted
        if row["ir_status"] == "OK" and row["unique_top1"] and row["decision_path"] != "IR_FAIL_CLOSED"
    ]

    summary = [
        {"metric": "n_all", "value": len(extracted)},
        {"metric": "n_ir_ok", "value": sum(1 for row in extracted if row["ir_status"] == "OK")},
        {"metric": "n_unique_top1_ranking", "value": len(ranking_rows)},
        {"metric": "n_r3_v42", "value": len(r3_rows)},
        {"metric": "n_r2_remaining", "value": sum(1 for row in extracted if row["is_r2"])},
        {"metric": "n_tie_abstain", "value": sum(1 for row in extracted if row["is_tie"])},
        {"metric": "v42_selected_oracle", "value": sum(1 for row in extracted if str(row["selection_oracle_correct"]).lower() == "true")},
    ]
    by_type = Counter(row["semantic_type"] for row in r3_rows)
    for semantic_type, count in sorted(by_type.items()):
        summary.append({"metric": f"r3_{semantic_type}", "value": count})

    feature_rows: list[dict[str, Any]] = []
    feature_rows.extend(reliability_table(ranking_rows, "top1_score", lambda row: bin_label(row["top1_score"], SCORE_BINS)))
    feature_rows.extend(reliability_table(ranking_rows, "score_margin", lambda row: bin_label(row["score_margin"], MARGIN_BINS)))
    feature_rows.extend(reliability_table(ranking_rows, "semantic_type", lambda row: row["semantic_type"]))
    feature_rows.extend(reliability_table(ranking_rows, "structured_match_count", lambda row: str(row["structured_match_count"])))
    feature_rows.extend(
        reliability_table(
            ranking_rows,
            "contradiction_penalty",
            lambda row: "zero" if row["contradiction_penalty"] <= 0 else "positive",
        )
    )
    feature_rows.extend(reliability_table(ranking_rows, "ir_field_count", lambda row: str(row["ir_field_count"])))
    feature_rows.extend(
        reliability_table(
            ranking_rows,
            "family_or_token_high",
            lambda row: "high" if max(row["family_score"], row["token_score"], row["structured_value_match"]) >= 0.5 else "low",
        )
    )

    cv_rows: list[dict[str, Any]] = []
    oof_platt: dict[tuple[str, str], float] = {}
    oof_iso: dict[tuple[str, str], float] = {}
    for fold, (train, val) in enumerate(event_grouped_folds(ranking_rows, folds=args.folds), start=1):
        y_train = [int(row["top1_correct"]) for row in train]
        x_train = [float(row["top1_score"]) for row in train]
        platt = fit_platt(x_train, y_train)
        iso = fit_isotonic(x_train, y_train)
        val_pairs_raw = [(float(row["top1_score"]), int(row["top1_correct"])) for row in val]
        val_pairs_platt = [(predict_platt(platt, float(row["top1_score"])), int(row["top1_correct"])) for row in val]
        val_pairs_iso = [(predict_isotonic(iso, float(row["top1_score"])), int(row["top1_correct"])) for row in val]
        cv_rows.append(
            {
                "fold": fold,
                "n_train": len(train),
                "n_val": len(val),
                "n_train_events": len({row["event_id"] for row in train}),
                "n_val_events": len({row["event_id"] for row in val}),
                "ece_raw_score": ece(val_pairs_raw),
                "ece_platt": ece(val_pairs_platt),
                "ece_isotonic": ece(val_pairs_iso),
                "val_top1_accuracy": sum(int(row["top1_correct"]) for row in val) / len(val) if val else 0.0,
            }
        )
        for row, p_platt, p_iso in zip(val, val_pairs_platt, val_pairs_iso):
            key = (row["event_id"], str(row["run"]))
            oof_platt[key] = p_platt[0]
            oof_iso[key] = p_iso[0]
            row["p_platt"] = p_platt[0]
            row["p_isotonic"] = p_iso[0]

    for row in extracted:
        key = (row["event_id"], str(row["run"]))
        row["p_platt"] = oof_platt.get(key, "")
        row["p_isotonic"] = oof_iso.get(key, "")

    curve_rows = []
    baseline = simulate_policy(raw_rows, extracted, select_if=lambda item: item["top1_score"] >= MIN_SCORE)
    baseline["policy"] = "v4.2_min_score_0.30"
    curve_rows.append(baseline)
    for tau in (0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95):
        point = simulate_policy(
            raw_rows,
            extracted,
            select_if=lambda item, threshold=tau: item.get("p_isotonic") != "" and float(item["p_isotonic"]) >= threshold,
        )
        point["policy"] = f"isotonic_p>={tau:.2f}"
        curve_rows.append(point)
        point_p = simulate_policy(
            raw_rows,
            extracted,
            select_if=lambda item, threshold=tau: item.get("p_platt") != "" and float(item["p_platt"]) >= threshold,
        )
        point_p["policy"] = f"platt_p>={tau:.2f}"
        curve_rows.append(point_p)
    for score in (0.10, 0.15, 0.20, 0.25, 0.30):
        point = simulate_policy(raw_rows, extracted, select_if=lambda item, gate=score: item["top1_score"] >= gate)
        point["policy"] = f"raw_min_score_{score:.2f}"
        curve_rows.append(point)
    interpretable = [
        ("unique_top1_margin>=0.05", lambda item: item["unique_top1"] and item["score_margin"] >= 0.05),
        (
            "v42_plus_temporal_r3",
            lambda item: item["top1_score"] >= MIN_SCORE or (item["semantic_type"] == "TEMPORAL_VERSION" and item["unique_top1"]),
        ),
        (
            "v42_plus_margin>=0.05",
            lambda item: item["top1_score"] >= MIN_SCORE or (item["unique_top1"] and item["score_margin"] >= 0.05),
        ),
    ]
    for name, rule in interpretable:
        point = simulate_policy(raw_rows, extracted, select_if=rule)
        point["policy"] = name
        curve_rows.append(point)

    out_dir = args.output_dir.resolve()
    prefix = args.prefix
    write_csv(out_dir / f"{prefix}-r3-pool.csv", r3_rows)
    write_csv(out_dir / f"{prefix}-ranking-universe.csv", ranking_rows)
    write_csv(out_dir / f"{prefix}-feature-reliability.csv", feature_rows)
    write_csv(out_dir / f"{prefix}-cv.csv", cv_rows)
    write_csv(out_dir / f"{prefix}-coverage-accuracy.csv", curve_rows)
    write_csv(out_dir / f"{prefix}-summary.csv", summary)

    print("AUTO_POLICY_V4.2 R3 archive + event-grouped calibration")
    print(f"details={args.details.resolve()}")
    print(f"n_r3={len(r3_rows)} by_type={dict(by_type)}")
    print(f"remaining_r2={sum(1 for row in extracted if row['is_r2'])} ties={sum(1 for row in extracted if row['is_tie'])}")
    print("feature reliability (unique Top-1 ranking universe):")
    for row in feature_rows:
        if row["feature"] in {"top1_score", "semantic_type", "contradiction_penalty"}:
            print(f"  {row['feature']} {row['bin']}: n={row['n']} acc={row['top1_accuracy']:.1%} r3={row['r3_n']}")
    print("event-grouped CV:")
    for row in cv_rows:
        print(
            f"  fold {row['fold']}: events {row['n_val_events']}  "
            f"ECE raw={row['ece_raw_score']:.3f} platt={row['ece_platt']:.3f} iso={row['ece_isotonic']:.3f}"
        )
    print("coverage-accuracy (oracle, closure not re-run for newly admitted R3):")
    for row in curve_rows:
        if row["policy"] in {
            "v4.2_min_score_0.30",
            "isotonic_p>=0.80",
            "isotonic_p>=0.90",
            "platt_p>=0.80",
            "raw_min_score_0.20",
            "raw_min_score_0.25",
            "unique_top1_margin>=0.05",
            "v42_plus_temporal_r3",
            "v42_plus_margin>=0.05",
        }:
            print(
                f"  {row['policy']:24s} cov={row['coverage']:.1%} oracle={row['oracle_accuracy']:.1%} "
                f"P|sel={row['precision_given_select']:.1%} r3+={row['admitted_r3']}"
            )
    print(f"wrote {out_dir / prefix}-*")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
