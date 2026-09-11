from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Offline paired ablation: V4.5 Adaptive vs + Schema Contract Repair on 600 LIGHT attempts."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_v4_schema_contract import prepare_policy_response
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from semantic_v2_common import PROJECT_DIR, write_csv

BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
PREFORMAL_RAW = (
    PROJECT_DIR
    / "output"
    / "external-real-v8-grounded"
    / "preformal-r3"
    / "raw_window_metadata_light"
)
V45_RELIABILITY_RAW = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.5-reliability"
    / "preformal-r3"
    / "raw_window_metadata_light"
)
OUTPUT_ROOT = PROJECT_DIR / "output" / "schema-contract-repair-ablation"
FROZEN_ROOT = OUTPUT_ROOT / "frozen-v45-adaptive-raw"
RUNS = 3
SEED_BASE = 20260827
IR_PREFIX = "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827"


def copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def assemble_frozen_v45_raw(*, overwrite: bool = False) -> Path:
    frozen_raw = FROZEN_ROOT / "raw_window_metadata_light" / "raw"
    frozen_evidence = FROZEN_ROOT / "raw_window_metadata_light" / "candidate-blind-evidence"
    if frozen_raw.is_dir() and any(frozen_raw.glob("*.json")) and not overwrite:
        return frozen_raw

    frozen_raw.parent.mkdir(parents=True, exist_ok=True)
    copy_tree(PREFORMAL_RAW, FROZEN_ROOT / "raw_window_metadata_light")

    overlay_count = 0
    reliability_raw = V45_RELIABILITY_RAW / "raw"
    if reliability_raw.is_dir():
        for path in reliability_raw.glob("*.json"):
            shutil.copy2(path, frozen_raw / path.name)
            overlay_count += 1

    manifest = {
        "benchmark": "external-real-v8-grounded",
        "attempts": RUNS * 200,
        "base_raw": str(PREFORMAL_RAW.relative_to(PROJECT_DIR)),
        "overlay_raw": str(reliability_raw.relative_to(PROJECT_DIR)) if reliability_raw.is_dir() else "",
        "overlay_count": overlay_count,
        "qwen_called": False,
    }
    (FROZEN_ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return frozen_raw


def load_evidence_map(evidence_dir: Path) -> dict[tuple[str, int, int], str]:
    mapping: dict[tuple[str, int, int], str] = {}
    for path in evidence_dir.glob("*-candidate-blind.md"):
        name = path.name
        event_id = name.split("-run", 1)[0]
        tail = name.split("-run", 1)[1]
        run = int(tail.split("-seed", 1)[0])
        seed = int(tail.split("-seed", 1)[1].split("-candidate-blind", 1)[0])
        mapping[(event_id, run, seed)] = path.read_text(encoding="utf-8")
    return mapping


def generation_status(record: dict[str, Any], schema_valid: bool, parsed: Any) -> str:
    if record.get("status") == "RETRIEVAL_FAILED":
        return "RETRIEVAL_FAILED"
    if record.get("status") == "INPUT_CONSTRUCTION_ERROR":
        return "INPUT_CONSTRUCTION_ERROR"
    if parsed is None:
        return "INVALID_JSON"
    if isinstance(parsed, dict) and parsed.get("abstain") is True:
        return "ABSTAIN"
    if schema_valid:
        return "GENERATED"
    return "INVALID_SCHEMA"


def process_arm_raw(
    *,
    frozen_raw_dir: Path,
    evidence_dir: Path,
    output_raw_dir: Path,
    events: dict[str, dict[str, str]],
    apply_contract_repair: bool,
    arm_name: str,
) -> list[dict[str, Any]]:
    output_raw_dir.mkdir(parents=True, exist_ok=True)
    evidence_map = load_evidence_map(evidence_dir)
    summary_rows: list[dict[str, Any]] = []

    for event_id, event in sorted(events.items()):
        for run in range(1, RUNS + 1):
            seed = SEED_BASE + run - 1
            source_path = frozen_raw_dir / f"{event_id}-run{run}-seed{seed}.json"
            if not source_path.is_file():
                raise FileNotFoundError(source_path)
            record = json.loads(source_path.read_text(encoding="utf-8-sig"))
            evidence = evidence_map.get((event_id, run, seed), "")
            if not evidence and record.get("candidate_blind_file"):
                evidence_path = PROJECT_DIR / str(record["candidate_blind_file"])
                if evidence_path.is_file():
                    evidence = evidence_path.read_text(encoding="utf-8")

            response = record.get("response")
            parsed, canonical_status, canonical_result, schema_valid, validation_reason, repair_meta = prepare_policy_response(
                response if isinstance(response, dict) else None,
                event,
                evidence,
                apply_contract_repair=apply_contract_repair,
                candidate_used=bool(record.get("candidate_used")),
                oracle_used=bool(record.get("oracle_used")),
                manual_policy_used=bool(record.get("manual_formal_policy_used")),
            )
            status = generation_status(record, schema_valid, parsed)
            out_record = {
                **record,
                "status": status,
                "schema_valid": schema_valid,
                "validation_reason": validation_reason,
                "canonical_status": canonical_status,
                "canonical_result": canonical_result,
                "canonical_semantic_result": str((canonical_result or {}).get("semantic_result") or (canonical_result or {}).get("change_value") or ""),
                "response": parsed,
                "schema_contract_arm": arm_name,
                "contract_repair_applied": bool(repair_meta and repair_meta.repair_applied),
                "contract_repair_meta": repair_meta.as_dict() if repair_meta else {},
                "qwen_called": False,
            }
            dest_path = output_raw_dir / source_path.name
            dest_path.write_text(json.dumps(out_record, ensure_ascii=False, indent=2), encoding="utf-8")
            summary_rows.append(
                {
                    "event_id": event_id,
                    "run": run,
                    "seed": seed,
                    "semantic_type": event["semantic_type"],
                    "arm": arm_name,
                    "generation_status": status,
                    "schema_valid": schema_valid,
                    "validation_reason": validation_reason,
                    "contract_repair_applied": out_record["contract_repair_applied"],
                    "failure_class": (repair_meta.failure_class if repair_meta else ""),
                    "repair_audit_count": len(repair_meta.audit) if repair_meta else 0,
                    "raw_output_file": str(dest_path.relative_to(PROJECT_DIR)),
                }
            )
    return summary_rows


def run_ir_pipeline(*, raw_dir: Path, output_dir: Path, method_name: str, robust_ir: bool) -> Path:
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
    ]
    if robust_ir:
        cmd.append("--robust-ir")
    else:
        cmd.append("--no-robust-ir")
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR)
    return output_dir / f"{IR_PREFIX}-details.csv"


def main() -> int:
    block_legacy_entrypoint(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite-frozen", action="store_true")
    parser.add_argument("--skip-raw", action="store_true", help="reuse processed arm raw dirs")
    parser.add_argument("--skip-ir", action="store_true", help="reuse IR outputs")
    parser.add_argument(
        "--robust-ir",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="match V4.4 robust_ir bypass for INVALID_SCHEMA payloads",
    )
    parser.add_argument("--ir-only", action="store_true", help="only rerun IR (implies --skip-raw)")
    args = parser.parse_args()
    if args.ir_only:
        args.skip_raw = True

    frozen_raw_dir = assemble_frozen_v45_raw(overwrite=args.overwrite_frozen)
    evidence_dir = frozen_raw_dir.parent.parent / "candidate-blind-evidence"
    paths = configure_paths(BENCHMARK_DIR)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}

    arm_a_raw = OUTPUT_ROOT / "arm-a-adaptive" / "raw_window_metadata_light" / "raw"
    arm_b_raw = OUTPUT_ROOT / "arm-b-contract-repair" / "raw_window_metadata_light" / "raw"
    if not args.skip_raw:
        arm_a_summary = process_arm_raw(
            frozen_raw_dir=frozen_raw_dir,
            evidence_dir=evidence_dir,
            output_raw_dir=arm_a_raw,
            events=events,
            apply_contract_repair=False,
            arm_name="V4.5_Adaptive",
        )
        arm_b_summary = process_arm_raw(
            frozen_raw_dir=frozen_raw_dir,
            evidence_dir=evidence_dir,
            output_raw_dir=arm_b_raw,
            events=events,
            apply_contract_repair=True,
            arm_name="V4.5_Adaptive_plus_Contract_Repair",
        )
        write_csv(OUTPUT_ROOT / "arm-a-raw-summary.csv", arm_a_summary)
        write_csv(OUTPUT_ROOT / "arm-b-raw-summary.csv", arm_b_summary)

    ir_subdir = "ir" if args.robust_ir else "ir-no-robust"
    if not args.skip_ir:
        run_ir_pipeline(
            raw_dir=arm_a_raw,
            output_dir=OUTPUT_ROOT / "arm-a-adaptive" / ir_subdir,
            method_name="V4.5_Adaptive" + ("" if args.robust_ir else "_strict_ir"),
            robust_ir=args.robust_ir,
        )
        run_ir_pipeline(
            raw_dir=arm_b_raw,
            output_dir=OUTPUT_ROOT / "arm-b-contract-repair" / ir_subdir,
            method_name="V4.5_Adaptive_plus_Contract_Repair" + ("" if args.robust_ir else "_strict_ir"),
            robust_ir=args.robust_ir,
        )

    print(f"frozen_raw={frozen_raw_dir}")
    print(f"robust_ir={args.robust_ir}")
    print(f"arm_a_details={OUTPUT_ROOT / 'arm-a-adaptive' / ir_subdir / (IR_PREFIX + '-details.csv')}")
    print(f"arm_b_details={OUTPUT_ROOT / 'arm-b-contract-repair' / ir_subdir / (IR_PREFIX + '-details.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
