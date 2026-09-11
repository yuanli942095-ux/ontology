from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""7-slot Task Formulation Pilot: 5 GRE + 2 CSS M1/M2 slots."""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from auto_policy_m12_task_formulation import apply_task_formulated_extraction
from auto_policy_task_formulation import METHOD_NAME
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from run_m12_decomposed_extraction_pilot import (
    ARM_A_RAW,
    BENCHMARK_DIR,
    C5_ANALYSIS,
    IR_PREFIX,
    M12_SUBTYPES,
    SEED_BASE,
    load_evidence,
    run_ir,
)
from semantic_v2_common import PROJECT_DIR, write_csv

OUTPUT_ROOT = PROJECT_DIR / "output" / "m12-task-formulation-pilot"
FIXED_SCHEMA_PILOT = PROJECT_DIR / "output" / "m12-decomposed-extraction-pilot"
GRE_CSS_TYPES = frozenset({"GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"})


def load_manifest(path: Path) -> list[dict[str, str]]:
    return [
        row
        for row in csv.DictReader(path.open(encoding="utf-8-sig"))
        if row.get("missing_subtype", "") in M12_SUBTYPES
        and row.get("semantic_type", "") in GRE_CSS_TYPES
    ]


def main() -> int:
    block_legacy_entrypoint(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=C5_ANALYSIS)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--qwen-timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-extraction", action="store_true")
    parser.add_argument("--skip-ir", action="store_true")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    paths = configure_paths(BENCHMARK_DIR)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    event_ids = {row["event_id"] for row in manifest}

    arm_a_raw = args.output_dir / "arm-a-adaptive" / "raw_window_metadata_light" / "raw"
    arm_b_raw = args.output_dir / "arm-b-task-formulation" / "raw_window_metadata_light" / "raw"
    arm_a_raw.mkdir(parents=True, exist_ok=True)
    arm_b_raw.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    for row in manifest:
        event_id = row["event_id"]
        run = int(row["run"])
        seed = int(row.get("seed") or SEED_BASE + run - 1)
        event = events[event_id]
        source = ARM_A_RAW / f"{event_id}-run{run}-seed{seed}.json"
        record = json.loads(source.read_text(encoding="utf-8-sig"))
        evidence = load_evidence(record)

        arm_a_raw.joinpath(source.name).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        if args.skip_extraction and (arm_b_raw / source.name).is_file():
            out_record = json.loads((arm_b_raw / source.name).read_text(encoding="utf-8-sig"))
            result = None
        else:
            out_record, result = apply_task_formulated_extraction(
                record,
                event,
                evidence,
                subtype=row.get("missing_subtype", ""),
                seed=seed,
                qwen_timeout=args.qwen_timeout,
                dry_run=args.dry_run,
            )
        arm_b_raw.joinpath(source.name).write_text(json.dumps(out_record, ensure_ascii=False, indent=2), encoding="utf-8")

        formulation = result.formulation if result else {}
        summary_rows.append(
            {
                "event_id": event_id,
                "run": run,
                "seed": seed,
                "semantic_type": row.get("semantic_type", ""),
                "missing_subtype": row.get("missing_subtype", ""),
                "extraction_mode": formulation.extraction_mode.value if result and result.formulation else "",
                "route_reason": formulation.route_reason if result and result.formulation else "",
                "repair_dimension": formulation.repair_dimension if result and result.formulation else "",
                "triggered": bool(result and result.triggered) if result else out_record.get("m12_task_formulated_extraction", False),
                "extraction_status": result.extraction_status if result else "resumed",
                "atomic_complete": str(result.atomic_complete).lower() if result else "",
                "required_slot_count": len(result.required_slots) if result else 0,
                "filled_slot_count": len(result.filled_slots) if result else 0,
                "missing_slots": "|".join(result.missing_slots) if result else "",
                "failure_layer": result.derivation.failure_layer if result and result.derivation else "",
                "derived_result": result.derivation.derived_result if result and result.derivation else "",
                "runtime_ms": result.runtime_ms if result else 0,
            }
        )
        print(
            f"{event_id} run={run} mode={(formulation.extraction_mode.value if result and result.formulation else 'n/a')} "
            f"status={(result.extraction_status if result else 'resumed')}",
            flush=True,
        )

    write_csv(args.output_dir / "task-formulation-summary.csv", summary_rows)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "experiment": "7-slot Task Formulation Pilot (5 GRE + 2 CSS)",
                "slots": len(manifest),
                "method": METHOD_NAME,
                "qwen_called": not args.dry_run and not args.skip_extraction,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if not args.skip_ir and not args.dry_run:
        run_ir(arm_a_raw, args.output_dir / "arm-a-adaptive" / "ir", event_ids=event_ids, method_name="V4.5_Adaptive")
        run_ir(
            arm_b_raw,
            args.output_dir / "arm-b-task-formulation" / "ir",
            event_ids=event_ids,
            method_name="M12_Task_Formulation",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
