from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""M1/M2 Decomposed Semantic Extraction Pilot."""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from auto_policy_m12_decomposed_extraction import METHOD_NAME, apply_decomposed_extraction
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from semantic_v2_common import PROJECT_DIR, write_csv

OUTPUT_ROOT = PROJECT_DIR / "output" / "m12-decomposed-extraction-pilot"
C5_ANALYSIS = PROJECT_DIR / "output" / "schema-contract-repair-ablation" / "c5-semantic-missing-analysis.csv"
ARM_A_RAW = PROJECT_DIR / "output" / "schema-contract-repair-ablation" / "arm-a-adaptive" / "raw_window_metadata_light" / "raw"
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
IR_PREFIX = "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827"
SEED_BASE = 20260827
M12_SUBTYPES = frozenset({"M1_ABSTAIN_OR_EMPTY", "M2_MISSING_RESULT"})


def load_manifest(path: Path) -> list[dict[str, str]]:
    return [
        row
        for row in csv.DictReader(path.open(encoding="utf-8-sig"))
        if row.get("missing_subtype", "") in M12_SUBTYPES
    ]


def load_evidence(record: dict[str, Any]) -> str:
    if record.get("candidate_blind_file"):
        evidence_path = PROJECT_DIR / str(record["candidate_blind_file"])
        if evidence_path.is_file():
            return evidence_path.read_text(encoding="utf-8")
    return ""


def run_ir(raw_dir: Path, output_dir: Path, *, event_ids: set[str], method_name: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_auto_policy_v4_ir_candidate_repair.py"),
        "--benchmark-dir",
        str(BENCHMARK_DIR),
        "--raw-dir",
        str(raw_dir),
        "--output-dir",
        str(output_dir),
        "--prefix",
        IR_PREFIX,
        "--method-name",
        method_name,
        "--min-score",
        "0.30",
        "--min-margin",
        "0.00",
        "--reranker",
        "constraint",
        "--temporal-unique-top1",
        "--robust-ir",
        "--skip-missing-raw",
        "--only",
        ",".join(sorted(event_ids)),
    ]
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR)
    return output_dir / f"{IR_PREFIX}-details.csv"


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
    arm_b_raw = args.output_dir / "arm-b-decomposed" / "raw_window_metadata_light" / "raw"
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

        arm_a_dest = arm_a_raw / source.name
        arm_a_dest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        if args.skip_extraction and (arm_b_raw / source.name).is_file():
            out_record = json.loads((arm_b_raw / source.name).read_text(encoding="utf-8-sig"))
            result = None
        else:
            out_record, result = apply_decomposed_extraction(
                record,
                event,
                evidence,
                subtype=row.get("missing_subtype", ""),
                seed=seed,
                qwen_timeout=args.qwen_timeout,
                dry_run=args.dry_run,
            )
        (arm_b_raw / source.name).write_text(json.dumps(out_record, ensure_ascii=False, indent=2), encoding="utf-8")

        summary_rows.append(
            {
                "event_id": event_id,
                "run": run,
                "seed": seed,
                "semantic_type": row.get("semantic_type", ""),
                "missing_subtype": row.get("missing_subtype", ""),
                "decomposition_triggered": bool(result and result.triggered) if result else out_record.get("m12_decomposed_extraction", False),
                "extraction_status": result.extraction_status if result else out_record.get("m12_derivation", {}).get("derivation_status", "resumed"),
                "atomic_complete": result.atomic_complete if result else "",
                "derivation_status": result.derivation.derivation_status if result and result.derivation else "",
                "failure_layer": result.derivation.failure_layer if result and result.derivation else "",
                "derived_result": result.derivation.derived_result if result and result.derivation else "",
                "runtime_ms": result.runtime_ms if result else 0,
                "generation_call_count": result.generation_call_count if result else 0,
            }
        )
        print(
            f"{event_id} run={run} subtype={row.get('missing_subtype')} "
            f"status={(result.extraction_status if result else 'resumed')}",
            flush=True,
        )

    write_csv(args.output_dir / "decomposition-summary.csv", summary_rows)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "experiment": "M1/M2 Decomposed Semantic Extraction Pilot",
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
            args.output_dir / "arm-b-decomposed" / "ir",
            event_ids=event_ids,
            method_name="M12_Decomposed_Semantic_Extraction",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
