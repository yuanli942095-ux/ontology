from __future__ import annotations

"""Score M13 vs M15 temporal IR against manually audited event-level gold."""

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from m15_semantic_audit_normalize import (
    ROLE_FIELDS,
    gold_frame,
    parse_ir_payload,
    rate,
    role_micro_macro_f1,
    score_attempt,
    unresolved_pr_recall,
)
from paper_final_validation_common import (
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_DEEPSEEK_PRED_DIR,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    load_freeze_sha256,
    load_method_freeze_sha256,
    read_csv,
    verify_deepseek_prediction_dir,
    write_binding,
    write_manifest_csv,
    write_summary_json,
    utc_now_iso,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold-csv",
        type=Path,
        default=PAPER_VALIDATION_ROOT / "02-m15-semantic-audit" / "gold-temporal-anchors.csv",
    )
    parser.add_argument(
        "--m13-details",
        type=Path,
        default=DEFAULT_DEEPSEEK_PRED_DIR
        / "m13-pilot"
        / "arm-d-rule-refinement"
        / "ir"
        / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv",
    )
    parser.add_argument(
        "--m15-details",
        type=Path,
        default=DEFAULT_DEEPSEEK_PRED_DIR
        / "m15-temporal-anchor-recovery"
        / "ir"
        / "m15-temporal-anchor-recovery-v4-ir-details.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=PAPER_VALIDATION_ROOT / "02-m15-semantic-audit")
    parser.add_argument("--benchmark-freeze-summary", type=Path, default=DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    parser.add_argument("--method-freeze-dir", type=Path, default=DEFAULT_METHOD_FREEZE_DIR)
    parser.add_argument("--prediction-dir", type=Path, default=DEFAULT_DEEPSEEK_PRED_DIR)
    return parser.parse_args()


def gold_complete(row: dict[str, str]) -> bool:
    required = [
        "entities",
        "resolution",
        *[f"role_{name}" for name in ROLE_FIELDS],
        "relation_supersedes",
        "relation_updates",
        "relation_updatedBy",
    ]
    return all(str(row.get(field, "")).strip() for field in required)


def stage_metrics(attempts: list[dict[str, Any]], stage: str) -> dict[str, Any]:
    role_f1 = role_micro_macro_f1(attempts, stage)
    return {
        "role_micro_f1": role_f1["micro"],
        "role_macro_f1": role_f1["macro"],
        "role_exact_frame": rate(attempts, f"{stage}_role_exact_frame"),
        "relation_accuracy": rate(attempts, f"{stage}_relation_type"),
        "relation_direction_accuracy": rate(attempts, f"{stage}_relation_direction"),
        "current_anchor_accuracy": rate(attempts, f"{stage}_current_anchor"),
        "effective_anchor_accuracy": rate(attempts, f"{stage}_effective_anchor"),
        "complete_temporal_frame_accuracy": rate(attempts, f"{stage}_complete_temporal_frame"),
        "unresolved_precision_recall": unresolved_pr_recall(attempts, stage),
    }


def event_level_summary(attempts: list[dict[str, Any]], stage: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        groups[row["event_id"]].append(row)
    out: list[dict[str, Any]] = []
    for event_id, rows in sorted(groups.items()):
        out.append(
            {
                "event_id": event_id,
                "attempts": len(rows),
                f"{stage}_role_exact_rate": rate(rows, f"{stage}_role_exact_frame"),
                f"{stage}_relation_type_rate": rate(rows, f"{stage}_relation_type"),
                f"{stage}_complete_frame_rate": rate(rows, f"{stage}_complete_temporal_frame"),
            }
        )
    return out


def clustered_event_mean(attempts: list[dict[str, Any]], key: str) -> dict[str, float]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        groups[row["event_id"]].append(row)
    per_event = [rate(rows, key) for rows in groups.values() if rows]
    if not per_event:
        return {"mean": 0.0, "stdev": 0.0, "events": 0}
    return {
        "mean": statistics.mean(per_event),
        "stdev": statistics.pstdev(per_event) if len(per_event) > 1 else 0.0,
        "events": len(per_event),
    }


def build_attempt_row(
    *,
    m13: dict[str, str],
    m15: dict[str, str],
    gold_row: dict[str, str],
) -> dict[str, Any]:
    gold = gold_frame(gold_row)
    m13_score = score_attempt(gold_row, m13, parse_ir_payload(m13.get("ir_json", "")))
    m15_score = score_attempt(gold_row, m15, parse_ir_payload(m15.get("ir_json", "")))
    row: dict[str, Any] = {
        "event_id": m13["event_id"],
        "run": m13["run"],
        "seed": m13["seed"],
        "gold_resolution": gold_row.get("resolution", ""),
        "gold_primary_rfc": m13_score["gold_primary_rfc"],
        "gold_unresolved": m13_score["gold_unresolved"],
        "m13_ir_relation": m13_score["ir_relation"],
        "m15_ir_relation": m15_score["ir_relation"],
    }
    for role in ROLE_FIELDS:
        row[f"gold_role_active_{role}"] = gold["role_active"][role]
    for stage, score in (("m13", m13_score), ("m15", m15_score)):
        row[f"{stage}_role_exact_frame"] = score["role_exact_frame"]
        row[f"{stage}_complete_temporal_frame"] = score["complete_temporal_frame"]
        row[f"{stage}_relation_type"] = score["relations"]["relation_type"]
        row[f"{stage}_relation_direction"] = score["relations"]["relation_direction"]
        row[f"{stage}_current_anchor"] = score["current_anchor"]
        row[f"{stage}_effective_anchor"] = score["effective_anchor"]
        row[f"{stage}_pred_unresolved"] = score["pred_unresolved"]
        row[f"{stage}_unresolved_match"] = score["unresolved_match"]
        for role in ROLE_FIELDS:
            row[f"{stage}_role_{role}"] = score["roles"][role]
    row["m15_improves_role_exact_frame"] = (not row["m13_role_exact_frame"]) and row["m15_role_exact_frame"]
    row["m15_improves_complete_frame"] = (not row["m13_complete_temporal_frame"]) and row[
        "m15_complete_temporal_frame"
    ]
    return row


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_sha256, benchmark_manifest = load_freeze_sha256(args.benchmark_freeze_summary)
    method_freeze_sha256, method_freeze_manifest = load_method_freeze_sha256(args.method_freeze_dir)
    verify_deepseek_prediction_dir(
        args.prediction_dir,
        benchmark_sha256=benchmark_sha256,
        method_freeze_sha256=method_freeze_sha256,
    )

    gold_rows = read_csv(args.gold_csv)
    incomplete = [row["event_id"] for row in gold_rows if not gold_complete(row)]
    if incomplete:
        write_summary_json(
            args.output_dir / "summary.json",
            {
                "generated_at_utc": utc_now_iso(),
                "status": "GOLD_INCOMPLETE",
                "incomplete_events": len(incomplete),
                "message": "Fill gold-temporal-anchors.csv before running audit metrics.",
            },
        )
        print(f"[m15-audit] gold incomplete ({len(incomplete)} events); metrics skipped")
        return 0

    gold_by_event = {row["event_id"]: row for row in gold_rows}
    m13_rows = [row for row in read_csv(args.m13_details) if row.get("semantic_type") == "TEMPORAL_VERSION"]
    m15_rows = [row for row in read_csv(args.m15_details) if row.get("semantic_type") == "TEMPORAL_VERSION"]
    m15_by_key = {f"{row['event_id']}|{row['run']}|{row['seed']}": row for row in m15_rows}

    attempt_details: list[dict[str, Any]] = []
    for m13 in m13_rows:
        key = f"{m13['event_id']}|{m13['run']}|{m13['seed']}"
        m15 = m15_by_key.get(key)
        if m15 is None:
            continue
        attempt_details.append(
            build_attempt_row(
                m13=m13,
                m15=m15,
                gold_row=gold_by_event[m13["event_id"]],
            )
        )

    m13_metrics = stage_metrics(attempt_details, "m13")
    m15_metrics = stage_metrics(attempt_details, "m15")
    event_level = event_level_summary(attempt_details, "m13")
    for stage in ("m13", "m15"):
        stage_rows = event_level_summary(attempt_details, stage)
        by_event = {row["event_id"]: row for row in stage_rows}
        for row in event_level:
            event_id = row["event_id"]
            if event_id in by_event:
                row[f"{stage}_role_exact_rate"] = by_event[event_id][f"{stage}_role_exact_rate"]
                row[f"{stage}_complete_frame_rate"] = by_event[event_id][f"{stage}_complete_frame_rate"]

    summary = {
        "generated_at_utc": utc_now_iso(),
        "status": "COMPLETE",
        "attempts": len(attempt_details),
        "events": len({row["event_id"] for row in attempt_details}),
        "scoring": "normalized_gold_vs_ir",
        "attempt_level": {
            "m13": m13_metrics,
            "m15": m15_metrics,
            "m15_minus_m13": {
                "role_exact_frame_delta": m15_metrics["role_exact_frame"] - m13_metrics["role_exact_frame"],
                "complete_temporal_frame_delta": m15_metrics["complete_temporal_frame_accuracy"]
                - m13_metrics["complete_temporal_frame_accuracy"],
                "current_anchor_delta": m15_metrics["current_anchor_accuracy"]
                - m13_metrics["current_anchor_accuracy"],
                "effective_anchor_delta": m15_metrics["effective_anchor_accuracy"]
                - m13_metrics["effective_anchor_accuracy"],
                "attempts_improved_role_exact": sum(row["m15_improves_role_exact_frame"] for row in attempt_details),
                "attempts_improved_complete_frame": sum(row["m15_improves_complete_frame"] for row in attempt_details),
            },
        },
        "event_level_clustered": {
            "m13_role_exact_frame": clustered_event_mean(attempt_details, "m13_role_exact_frame"),
            "m15_role_exact_frame": clustered_event_mean(attempt_details, "m15_role_exact_frame"),
            "m13_complete_temporal_frame": clustered_event_mean(attempt_details, "m13_complete_temporal_frame"),
            "m15_complete_temporal_frame": clustered_event_mean(attempt_details, "m15_complete_temporal_frame"),
        },
        "note": (
            "Gold labels are document-centric (RFC/date anchors); IR outputs are claim-centric. "
            "Metrics use normalized RFC/date/relation overlap rather than raw string equality. "
            "Attempt-level metrics are descriptive; event-level clustered means should be used for inference."
        ),
    }
    write_manifest_csv(args.output_dir / "m13-vs-m15-details.csv", attempt_details)
    write_manifest_csv(args.output_dir / "event-level-summary.csv", event_level)
    write_summary_json(args.output_dir / "summary.json", summary)
    write_binding(
        args.output_dir,
        experiment_role="02-m15-semantic-audit",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_freeze_manifest,
        method_freeze_sha256=method_freeze_sha256,
        extra={
            "input_prediction_dir": str(args.prediction_dir),
            "llm_backend": "deepseek-chat",
            "model": "deepseek-chat",
            "scoring": "normalized_gold_vs_ir",
        },
    )
    print(
        f"[m15-audit] attempts={len(attempt_details)} "
        f"m13_role_exact={m13_metrics['role_exact_frame']:.3f} "
        f"m15_role_exact={m15_metrics['role_exact_frame']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
