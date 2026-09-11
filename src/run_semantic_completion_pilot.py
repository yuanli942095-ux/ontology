from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Pilot: V4.5 Adaptive vs + Adaptive Semantic Completion on 20 true semantic-missing slots."""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from auto_policy_v45_semantic_completion import METHOD_NAME, apply_semantic_completion
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from semantic_v2_common import PROJECT_DIR, write_csv

OUTPUT_ROOT = PROJECT_DIR / "output" / "semantic-completion-pilot"
C5_ANALYSIS = PROJECT_DIR / "output" / "schema-contract-repair-ablation" / "c5-semantic-missing-analysis.csv"
ARM_A_RAW = PROJECT_DIR / "output" / "schema-contract-repair-ablation" / "arm-a-adaptive" / "raw_window_metadata_light" / "raw"
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
IR_PREFIX = "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827"
SEED_BASE = 20260827


def load_manifest(path: Path) -> list[dict[str, str]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    return [row for row in rows if row.get("missing_subtype") != "M7_INVALID_JSON"]


def load_evidence(record: dict[str, Any]) -> str:
    if record.get("candidate_blind_file"):
        evidence_path = PROJECT_DIR / str(record["candidate_blind_file"])
        if evidence_path.is_file():
            return evidence_path.read_text(encoding="utf-8")
    return ""


def run_ir(raw_dir: Path, output_dir: Path, *, only_events: set[str], method_name: str) -> Path:
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
        ",".join(sorted(only_events)),
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
    parser.add_argument("--skip-completion", action="store_true", help="reuse saved raw outputs, only rerun IR")
    parser.add_argument("--skip-ir", action="store_true")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    paths = configure_paths(BENCHMARK_DIR)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    event_ids = {row["event_id"] for row in manifest}

    arm_a_raw_out = args.output_dir / "arm-a-adaptive" / "raw_window_metadata_light" / "raw"
    arm_b_raw_out = args.output_dir / "arm-b-semantic-completion" / "raw_window_metadata_light" / "raw"
    arm_a_raw_out.mkdir(parents=True, exist_ok=True)
    arm_b_raw_out.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    for row in manifest:
        event_id = row["event_id"]
        run = int(row["run"])
        seed = int(row.get("seed") or SEED_BASE + run - 1)
        event = events[event_id]
        source_path = ARM_A_RAW / f"{event_id}-run{run}-seed{seed}.json"
        arm_a_dest = arm_a_raw_out / source_path.name
        arm_b_dest = arm_b_raw_out / source_path.name

        if args.skip_completion and arm_a_dest.is_file() and arm_b_dest.is_file():
            record = json.loads(arm_a_dest.read_text(encoding="utf-8-sig"))
            attempt_summary = next(
                (
                    item
                    for item in csv.DictReader((args.output_dir / "completion-summary.csv").open(encoding="utf-8-sig"))
                    if item["event_id"] == event_id and int(item["run"]) == run
                ),
                {},
            )
            summary_rows.append(attempt_summary)
            continue

        record = json.loads(source_path.read_text(encoding="utf-8-sig"))
        evidence = load_evidence(record)

        arm_a_dest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        completed_record, attempt = apply_semantic_completion(
            record,
            event,
            evidence,
            subtype=row.get("missing_subtype", ""),
            candidate_used=bool(record.get("candidate_used")),
            oracle_used=bool(record.get("oracle_used")),
            manual_policy_used=bool(record.get("manual_formal_policy_used") or record.get("manual_policy_used")),
            seed=seed,
            qwen_timeout=args.qwen_timeout,
            dry_run=args.dry_run,
        )
        arm_b_dest.write_text(json.dumps(completed_record, ensure_ascii=False, indent=2), encoding="utf-8")
        summary_rows.append(
            {
                "event_id": event_id,
                "run": run,
                "seed": seed,
                "semantic_type": row.get("semantic_type", ""),
                "missing_subtype": row.get("missing_subtype", ""),
                "completion_triggered": attempt.triggered,
                "completion_status": attempt.completion_status,
                "completion_reason": attempt.completion_reason,
                "missing_fields": "|".join(attempt.missing_fields),
                "ir_status_before": attempt.ir_status_before,
                "ir_status_after": attempt.ir_status_after,
                "runtime_ms": attempt.runtime_ms,
                "eval_count": attempt.eval_count,
            }
        )
        print(
            f"{event_id} run={run} triggered={attempt.triggered} "
            f"before={attempt.ir_status_before} after={attempt.ir_status_after} status={attempt.completion_status}",
            flush=True,
        )

    if not args.skip_completion:
        write_csv(args.output_dir / "completion-summary.csv", summary_rows)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "benchmark": "external-real-v8-grounded",
                "slots": len(manifest),
                "method": METHOD_NAME,
                "candidate_blind": True,
                "qwen_called": not args.dry_run,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if not args.skip_ir and not args.dry_run:
        run_ir(
            arm_a_raw_out,
            args.output_dir / "arm-a-adaptive" / "ir",
            only_events=event_ids,
            method_name="V4.5_Adaptive",
        )
        run_ir(
            arm_b_raw_out,
            args.output_dir / "arm-b-semantic-completion" / "ir",
            only_events=event_ids,
            method_name="V4.5_Adaptive_plus_Semantic_Completion",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
