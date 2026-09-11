from __future__ import annotations

"""Main-table statistics for formal hold-out blind evaluation."""

import argparse
import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analyze_external_real_v8_main_table import (
    binom_two_sided_p,
    bootstrap_mean_diff_ci,
    load_event_meta,
    paired_mcnemar,
    rate_with_ci,
    read_csv,
    summarize_attempts,
    write_csv_rows,
    wilson,
)

ROOT = Path(__file__).resolve().parents[1]
MAIN_METHOD = "EVIDENCE_CONSTRAINED_SEMANTIC_IR_REPAIR"

METHOD_POSITION = {
    "DIRECT_FREE": "fully automatic",
    "OPTION_DESCRIPTION": "fully automatic",
    "OPTION_VALUE_ONLY": "fully automatic",
    "OPTION_FORMAL_OPERATION": "fully automatic",
    "EVIDENCE_CONSTRAINED_SEMANTIC_IR_REPAIR": "fully automatic",
    "OPTION_FORMAL_POLICY": "privileged / aided",
    "OPTION_FORMAL_POLICY_HARD_GATE": "symbolic upper bound",
}

PAIRED_BASELINES = (
    "DIRECT_FREE",
    "OPTION_DESCRIPTION",
    "OPTION_VALUE_ONLY",
    "OPTION_FORMAL_OPERATION",
)


def bool_value(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def normalize_ablation_row(row: dict[str, str], meta: dict[str, dict[str, str]]) -> dict[str, Any]:
    event_id = row["event_id"]
    info = meta.get(event_id, {})
    status = str(row.get("status", ""))
    return {
        "method": row.get("method", ""),
        "event_id": event_id,
        "domain": info.get("domain", ""),
        "semantic_type": row.get("semantic_type") or info.get("semantic_type", ""),
        "run": int(row.get("run", 1) or 1),
        "oracle_correct": bool_value(row.get("oracle_correct")),
        "full_closure_success": False,
        "abstain": status == "ABSTAIN",
        "invalid": status.startswith("REJECTED") or status in {"INVALID_JSON", "INVALID_SCHEMA", "ERROR"},
        "selected": status == "SELECTED",
        "runtime_ms": int(float(row.get("runtime_ms", 0) or 0)),
    }


def normalize_main_method_row(row: dict[str, str], meta: dict[str, dict[str, str]]) -> dict[str, Any]:
    event_id = row["event_id"]
    info = meta.get(event_id, {})
    selection = str(row.get("selection_status", ""))
    selected = selection == "SELECTED"
    oracle_correct = bool_value(row.get("selection_oracle_correct"))
    return {
        "method": MAIN_METHOD,
        "event_id": event_id,
        "domain": info.get("domain", ""),
        "semantic_type": row.get("semantic_type") or info.get("semantic_type", ""),
        "run": int(row.get("run", 1) or 1),
        "oracle_correct": oracle_correct,
        "full_closure_success": bool_value(row.get("full_closure_success")),
        "abstain": selection == "ABSTAIN",
        "invalid": str(row.get("generation_status", "")) in {"INVALID_JSON", "INVALID_SCHEMA", "ERROR"},
        "selected": selected,
        "reasoner_gate": bool_value(row.get("reasoner_gate")) if selected else False,
        "minimal_edit_gate": bool_value(row.get("minimal_edit_gate")) if selected else False,
        "source_triggers_repair_cq": bool_value(row.get("source_triggers_repair_cq")) if selected else False,
        "candidate_satisfies_repair_cq": bool_value(row.get("candidate_satisfies_repair_cq")) if selected else False,
        "runtime_ms": int(float(row.get("runtime_ms", 0) or 0)),
    }


def main_method_extended_summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    base = summarize_attempts(attempts)
    selected = [row for row in attempts if row.get("selected")]
    abstained = [row for row in attempts if row.get("abstain")]
    closure = rate_with_ci(sum(bool(r.get("full_closure_success")) for r in attempts), len(attempts))
    coverage = rate_with_ci(len(selected), len(attempts))
    p_select = rate_with_ci(sum(bool(r.get("oracle_correct")) for r in selected), len(selected) or 1)
    reasoner = rate_with_ci(sum(bool(r.get("reasoner_gate")) for r in selected), len(selected) or 1)
    cq = rate_with_ci(
        sum(bool(r.get("source_triggers_repair_cq")) and bool(r.get("candidate_satisfies_repair_cq")) for r in selected),
        len(selected) or 1,
    )
    minimal = rate_with_ci(sum(bool(r.get("minimal_edit_gate")) for r in selected), len(selected) or 1)
    base.update(
        {
            "owl_closure_accuracy": closure["estimate"],
            "owl_closure_ci95_low": closure["ci95_low"],
            "owl_closure_ci95_high": closure["ci95_high"],
            "coverage_rate": coverage["estimate"],
            "p_select_given_selected": p_select["estimate"] if selected else 0.0,
            "selected_attempts": len(selected),
            "abstained_attempts": len(abstained),
            "reasoner_consistency_rate": reasoner["estimate"] if selected else 0.0,
            "cq_pass_rate": cq["estimate"] if selected else 0.0,
            "minimal_edit_pass_rate": minimal["estimate"] if selected else 0.0,
        }
    )
    return base


def run_variance(attempts: list[dict[str, Any]], metric: str = "oracle_correct") -> dict[str, float]:
    by_event: dict[str, list[float]] = defaultdict(list)
    for row in attempts:
        by_event[str(row["event_id"])].append(float(bool(row.get(metric))))
    per_event = [sum(vals) / len(vals) for vals in by_event.values() if vals]
    if not per_event:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": statistics.mean(per_event),
        "std": statistics.pstdev(per_event) if len(per_event) > 1 else 0.0,
        "min": min(per_event),
        "max": max(per_event),
    }


def failure_analysis(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in attempts:
        if row.get("oracle_correct") and row.get("full_closure_success"):
            continue
        reason = []
        if row.get("abstain"):
            reason.append("abstain")
        elif not row.get("oracle_correct"):
            reason.append("oracle_mismatch")
        if row.get("selected") and not row.get("full_closure_success"):
            if not row.get("reasoner_gate"):
                reason.append("reasoner_fail")
            if not row.get("minimal_edit_gate"):
                reason.append("minimal_edit_fail")
            if not (row.get("source_triggers_repair_cq") and row.get("candidate_satisfies_repair_cq")):
                reason.append("cq_fail")
        rows.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "semantic_type": row.get("semantic_type", ""),
                "selected": row.get("selected"),
                "oracle_correct": row.get("oracle_correct"),
                "full_closure_success": row.get("full_closure_success"),
                "failure_tags": "|".join(reason) or "unknown",
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=ROOT / "benchmark" / "external-real-holdout-v1-expanded")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "external-real-holdout-v1-expanded" / "final-blind-eval-r5")
    parser.add_argument("--prefix", default="external-real-holdout-main-table")
    return parser.parse_args()


def find_main_details(output_dir: Path) -> Path:
    ir_dir = output_dir / "main-method" / "m13-pilot" / "arm-d-rule-refinement" / "ir"
    matches = sorted(ir_dir.glob("*-details.csv"))
    if not matches:
        raise FileNotFoundError(f"main-method IR details not found under {ir_dir}")
    return matches[-1]


def find_baseline_details(output_dir: Path) -> Path | None:
    base = output_dir / "baselines"
    matches = sorted(base.glob("*-details.csv"))
    return matches[-1] if matches else None


def main() -> int:
    args = parse_args()
    meta = load_event_meta(args.benchmark_dir)
    main_details = find_main_details(args.output_dir)
    main_rows = [normalize_main_method_row(row, meta) for row in read_csv(main_details)]

    method_attempts: dict[str, list[dict[str, Any]]] = {MAIN_METHOD: main_rows}
    baseline_details = find_baseline_details(args.output_dir)
    if baseline_details and baseline_details.is_file():
        for row in read_csv(baseline_details):
            method = str(row.get("method", "")).strip()
            if not method:
                continue
            method_attempts.setdefault(method, []).append(normalize_ablation_row(row, meta))

    main_table: list[dict[str, Any]] = []
    type_breakdown: list[dict[str, Any]] = []
    for method, attempts in sorted(method_attempts.items()):
        if method == MAIN_METHOD:
            summary = main_method_extended_summary(attempts)
        else:
            summary = summarize_attempts(attempts)
        main_table.append(
            {
                "method": method,
                "position": METHOD_POSITION.get(method, "unspecified"),
                "oracle_accuracy": summary.get("oracle_accuracy", 0),
                "owl_closure_accuracy": summary.get("owl_closure_accuracy", summary.get("oracle_accuracy", 0) if method != MAIN_METHOD else summary.get("owl_closure_accuracy", 0)),
                "strict_event_success": summary.get("strict_event_success", 0),
                "coverage_rate": summary.get("coverage_rate", 1.0 - summary.get("abstain_rate", 0)),
                "p_select_given_selected": summary.get("p_select_given_selected", summary.get("oracle_accuracy", 0)),
                "abstain_rate": summary.get("abstain_rate", 0),
                "selected_attempts": summary.get("selected_attempts", summary.get("attempts", 0) - int(summary.get("abstain_rate", 0) * summary.get("attempts", 0))),
                "reasoner_consistency_rate": summary.get("reasoner_consistency_rate", 0),
                "cq_pass_rate": summary.get("cq_pass_rate", 0),
                "minimal_edit_pass_rate": summary.get("minimal_edit_pass_rate", 0),
                "attempts": summary.get("attempts", 0),
                "events": summary.get("events", 0),
            }
        )
        for semantic_type in ("TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"):
            subset = [row for row in attempts if row.get("semantic_type") == semantic_type]
            if not subset:
                continue
            sub_summary = main_method_extended_summary(subset) if method == MAIN_METHOD else summarize_attempts(subset)
            type_breakdown.append(
                {
                    "method": method,
                    "semantic_type": semantic_type,
                    "oracle_accuracy": sub_summary.get("oracle_accuracy", 0),
                    "owl_closure_accuracy": sub_summary.get("owl_closure_accuracy", 0),
                    "strict_event_success": sub_summary.get("strict_event_success", 0),
                    "abstain_rate": sub_summary.get("abstain_rate", 0),
                    "attempts": sub_summary.get("attempts", 0),
                }
            )

    paired_rows: list[dict[str, Any]] = []
    main_attempts = method_attempts.get(MAIN_METHOD, [])
    for baseline in PAIRED_BASELINES:
        base_attempts = method_attempts.get(baseline, [])
        if not base_attempts:
            continue
        for metric in ("oracle_correct", "full_closure_success"):
            mcnemar = paired_mcnemar(MAIN_METHOD, baseline, main_attempts, base_attempts, metric=metric)
            boot = bootstrap_mean_diff_ci(MAIN_METHOD, baseline, main_attempts, base_attempts, metric=metric)
            paired_rows.append(
                {
                    **mcnemar,
                    "delta_pp": boot["mean_diff"] * 100,
                    "delta_ci95_low_pp": boot["boot_ci95_low"] * 100,
                    "delta_ci95_high_pp": boot["boot_ci95_high"] * 100,
                }
            )

    failures = failure_analysis(main_attempts)
    run_stats = run_variance(main_attempts, "oracle_correct")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix
    write_csv_rows(args.output_dir / f"{prefix}.csv", main_table)
    write_csv_rows(args.output_dir / f"{prefix}-by-semantic-type.csv", type_breakdown)
    write_csv_rows(args.output_dir / f"{prefix}-paired-significance.csv", paired_rows)
    write_csv_rows(args.output_dir / f"{prefix}-failure-analysis.csv", failures)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "phase": "FINAL_BLIND_EVALUATION",
        "main_method_details": str(main_details.relative_to(ROOT)),
        "main_table": main_table,
        "run_variance_oracle_per_event": run_stats,
        "paired_significance": paired_rows,
        "failure_count": len(failures),
    }
    (args.output_dir / f"{prefix}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"main_table": str(args.output_dir / f"{prefix}.csv"), "failures": len(failures)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
