from __future__ import annotations

"""Analyze 7-slot task formulation pilot vs fixed-schema M12 baseline."""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv

OUTPUT_ROOT = PROJECT_DIR / "output" / "m12-task-formulation-pilot"
FIXED_SCHEMA_PILOT = PROJECT_DIR / "output" / "m12-decomposed-extraction-pilot"
IR_PREFIX = "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827"


def load_details(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    return {(row["event_id"], row["run"]): row for row in csv.DictReader(path.open(encoding="utf-8-sig"))}


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def summarize_ir(rows: list[dict[str, str]]) -> dict[str, Any]:
    selected = [row for row in rows if row.get("selection_status") == "SELECTED"]
    return {
        "atomic_fact_complete": "",
        "ir_ok": sum(row.get("ir_status") == "OK" for row in rows),
        "selected": len(selected),
        "oracle_correct": sum(truthy(row.get("selection_oracle_correct", "")) for row in rows),
        "closure_pass": sum(truthy(row.get("full_closure_success", "")) for row in rows),
        "precision_given_select": (
            sum(truthy(row.get("selection_oracle_correct", "")) for row in selected) / len(selected) if selected else 0.0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()

    summary = list(csv.DictReader((args.output_dir / "task-formulation-summary.csv").open(encoding="utf-8-sig")))
    keys = [(row["event_id"], row["run"]) for row in summary]
    task_atomic_complete = sum(truthy(row.get("atomic_complete", "")) for row in summary)

    fixed_summary_path = FIXED_SCHEMA_PILOT / "decomposition-summary.csv"
    fixed_rows = {
        (row["event_id"], row["run"]): row
        for row in csv.DictReader(fixed_summary_path.open(encoding="utf-8-sig"))
        if row.get("semantic_type") in {"GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}
    }
    fixed_atomic_complete = sum(truthy(row.get("atomic_complete", "")) for row in fixed_rows.values())

    adaptive_map = load_details(args.output_dir / "arm-a-adaptive" / "ir" / f"{IR_PREFIX}-details.csv")
    task_map = load_details(args.output_dir / "arm-b-task-formulation" / "ir" / f"{IR_PREFIX}-details.csv")
    fixed_map = load_details(FIXED_SCHEMA_PILOT / "arm-b-decomposed" / "ir" / f"{IR_PREFIX}-details.csv")

    adaptive_rows = [adaptive_map[k] for k in keys if k in adaptive_map]
    task_rows = [task_map[k] for k in keys if k in task_map]
    fixed_rows_ir = [fixed_map[k] for k in keys if k in fixed_map]

    adaptive_metrics = summarize_ir(adaptive_rows)
    fixed_metrics = summarize_ir(fixed_rows_ir)
    task_metrics = summarize_ir(task_rows)
    fixed_metrics["atomic_fact_complete"] = fixed_atomic_complete
    task_metrics["atomic_fact_complete"] = task_atomic_complete
    adaptive_metrics["atomic_fact_complete"] = 0

    paired = []
    for row in summary:
        key = (row["event_id"], row["run"])
        adaptive = adaptive_map.get(key, {})
        fixed = fixed_map.get(key, {})
        task = task_map.get(key, {})
        paired.append(
            {
                "event_id": key[0],
                "run": key[1],
                "semantic_type": row.get("semantic_type", ""),
                "extraction_mode": row.get("extraction_mode", ""),
                "missing_slots": row.get("missing_slots", ""),
                "fixed_atomic_complete": fixed_rows.get(key, {}).get("atomic_complete", ""),
                "task_atomic_complete": row.get("atomic_complete", ""),
                "fixed_ir": fixed.get("ir_status", ""),
                "task_ir": task.get("ir_status", ""),
                "fixed_closure": fixed.get("full_closure_success", ""),
                "task_closure": task.get("full_closure_success", ""),
                "task_derived_result": row.get("derived_result", ""),
            }
        )

    by_type: dict[str, dict[str, int]] = {}
    for row in paired:
        st = row["semantic_type"]
        bucket = by_type.setdefault(st, {"slots": 0, "fixed_closure": 0, "task_closure": 0, "task_atomic": 0})
        bucket["slots"] += 1
        bucket["fixed_closure"] += int(truthy(row["fixed_closure"]))
        bucket["task_closure"] += int(truthy(row["task_closure"]))
        bucket["task_atomic"] += int(truthy(row["task_atomic_complete"]))

    main_table = [
        {"metric": "Atomic Fact Complete", "adaptive": 0, "fixed_schema": fixed_atomic_complete, "task_formulation": task_atomic_complete},
        {"metric": "IR OK", "adaptive": adaptive_metrics["ir_ok"], "fixed_schema": fixed_metrics["ir_ok"], "task_formulation": task_metrics["ir_ok"]},
        {"metric": "SELECT", "adaptive": adaptive_metrics["selected"], "fixed_schema": fixed_metrics["selected"], "task_formulation": task_metrics["selected"]},
        {"metric": "Oracle Correct", "adaptive": adaptive_metrics["oracle_correct"], "fixed_schema": fixed_metrics["oracle_correct"], "task_formulation": task_metrics["oracle_correct"]},
        {"metric": "OWL Closure", "adaptive": adaptive_metrics["closure_pass"], "fixed_schema": fixed_metrics["closure_pass"], "task_formulation": task_metrics["closure_pass"]},
        {"metric": "P|select", "adaptive": adaptive_metrics["precision_given_select"], "fixed_schema": fixed_metrics["precision_given_select"], "task_formulation": task_metrics["precision_given_select"]},
    ]

    interpretation = "task_formulation_mismatch_confirmed"
    if task_metrics["closure_pass"] - fixed_metrics["closure_pass"] >= 2 or task_atomic_complete - fixed_atomic_complete >= 3:
        interpretation = "task_formulation_mismatch_confirmed"
    elif task_atomic_complete <= fixed_atomic_complete + 1 and task_metrics["closure_pass"] <= fixed_metrics["closure_pass"]:
        interpretation = "evidence_or_reasoning_still_blocking"

    analysis = {
        "attempts": len(keys),
        "adaptive": adaptive_metrics,
        "fixed_schema": fixed_metrics,
        "task_formulation": task_metrics,
        "delta_task_vs_fixed": {
            "atomic_fact_complete": task_atomic_complete - fixed_atomic_complete,
            "ir_ok": task_metrics["ir_ok"] - fixed_metrics["ir_ok"],
            "closure_pass": task_metrics["closure_pass"] - fixed_metrics["closure_pass"],
        },
        "by_semantic_type": by_type,
        "interpretation": interpretation,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "main-table.csv", main_table)
    write_csv(args.output_dir / "paired-comparison.csv", paired)
    (args.output_dir / "analysis-summary.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(analysis, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
