from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Summarize Auto Policy V3 component ablations.

The script creates one deterministic ablation output:

* METADATA_EVIDENCE_ONLY_NORMALIZER: no Qwen call, no candidates, no Oracle,
  no manual formal-policy.  It applies the V3 normalizer to public event
  metadata plus candidate-blind evidence only.

Other rows are read from already generated V2/V3/no-normalizer outputs.
"""

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3


ROOT = v3.ROOT
OUTPUT_DIR = ROOT / "output"
METADATA_OUTPUT_DIR = OUTPUT_DIR / "auto-policy-v3-metadata-evidence-only"
METADATA_RAW_DIR = METADATA_OUTPUT_DIR / "raw"
METADATA_CLEAN_DIR = METADATA_OUTPUT_DIR / "candidate-blind-evidence"
DEFAULT_PREFIX = "auto-policy-v3-component-ablation-r5-seed20260820"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Policy V3 component ablation")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--skip-repair", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def create_metadata_evidence_only_raw() -> None:
    events = v3.load_events()
    expected_event_ids = [f"EXT_E{i:03d}" for i in range(1, 31)]
    METADATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for event_id in expected_event_ids:
        event = events[event_id]
        evidence_path = v3.EXCERPT_DIR / f"{event_id}-evidence.md"
        clean_evidence = v3.remove_candidate_sections(evidence_path.read_text(encoding="utf-8"))
        remaining = v3.find_candidate_markers(clean_evidence)
        if remaining:
            raise RuntimeError(f"{event_id}: candidate marker remained: {remaining}")
        clean_path = METADATA_CLEAN_DIR / f"{event_id}-candidate-blind.md"
        clean_path.write_text(clean_evidence, encoding="utf-8")

        for run in range(1, v3.RUNS + 1):
            seed = v3.SEED_BASE + run - 1
            parsed: dict[str, Any] = {
                "semantic_type": event["semantic_type"],
                "facts": {},
                "rules": [{"priority": 300, "conditions": []}],
            }
            parsed, canonical_status, canonical_result, canonical_semantic_result = v3.normalize_generated_policy(
                parsed,
                event,
                clean_evidence,
            )
            schema_valid, validation_reason = v3.validate_generated_policy(parsed, event["semantic_type"])
            status = "GENERATED" if schema_valid and canonical_status == "ok" else "INVALID_SCHEMA"
            raw_record = {
                "event_id": event_id,
                "semantic_type": event["semantic_type"],
                "run": run,
                "seed": seed,
                "model": "NONE",
                "prompt_version": "METADATA_EVIDENCE_ONLY_NORMALIZER",
                "source_type": "CANDIDATE_BLIND_PUBLIC_EVIDENCE",
                "source_file": str(evidence_path.relative_to(ROOT)),
                "candidate_blind_file": str(clean_path.relative_to(ROOT)),
                "oracle_used": False,
                "candidate_used": False,
                "manual_formal_policy_used": False,
                "qwen_used": False,
                "deterministic_normalization": True,
                "status": status,
                "runtime_ms": 0,
                "prompt_eval_count": 0,
                "eval_count": 0,
                "forbidden_markers": [],
                "schema_valid": schema_valid,
                "validation_reason": validation_reason,
                "canonical_status": canonical_status,
                "canonical_result": canonical_result,
                "canonical_semantic_result": canonical_semantic_result,
                "response": parsed,
            }
            raw_path = METADATA_RAW_DIR / f"{event_id}-run{run}-seed{seed}.json"
            raw_path.write_text(json.dumps(raw_record, ensure_ascii=False, indent=2), encoding="utf-8")
            records.append(
                {
                    "event_id": event_id,
                    "semantic_type": event["semantic_type"],
                    "run": run,
                    "seed": seed,
                    "status": status,
                    "schema_valid": schema_valid,
                    "validation_reason": validation_reason,
                    "canonical_status": canonical_status,
                    "canonical_semantic_result": canonical_semantic_result,
                    "raw_output_file": str(raw_path.relative_to(ROOT)),
                }
            )
    write_csv(METADATA_OUTPUT_DIR / "auto-policy-v3-metadata-evidence-only-generation-details.csv", records)
    summary = {
        "experiment": "AUTO_POLICY_V3_METADATA_EVIDENCE_ONLY_NORMALIZER",
        "events": 30,
        "runs": v3.RUNS,
        "attempts": len(records),
        "generated": sum(row["status"] == "GENERATED" for row in records),
        "invalid_schema": sum(row["status"] != "GENERATED" for row in records),
        "qwen_used": False,
        "candidate_blind": True,
        "oracle_used": False,
        "candidate_used": False,
        "manual_formal_policy_used": False,
        "deterministic_normalization": True,
    }
    (METADATA_OUTPUT_DIR / "auto-policy-v3-metadata-evidence-only-generation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_command(args: list[str]) -> None:
    print(" ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def run_evaluations(skip_repair: bool) -> None:
    python = str(ROOT / ".venv" / "Scripts" / "python.exe")
    variants = [
        (
            "auto-policy-v3-no-normalizer",
            OUTPUT_DIR / "auto-policy-v3-no-normalizer" / "raw",
            OUTPUT_DIR / "auto-policy-v3-no-normalizer",
            "auto-policy-v3-no-normalizer-semantic-evaluation",
            "auto-policy-v3-no-normalizer-candidate-repair-r5-seed20260820",
        ),
        (
            "auto-policy-v3-metadata-evidence-only",
            METADATA_RAW_DIR,
            METADATA_OUTPUT_DIR,
            "auto-policy-v3-metadata-evidence-only-semantic-evaluation",
            "auto-policy-v3-metadata-evidence-only-candidate-repair-r5-seed20260820",
        ),
    ]
    for _, raw_dir, out_dir, semantic_prefix, repair_prefix in variants:
        if not raw_dir.exists():
            raise FileNotFoundError(raw_dir)
        run_command(
            [
                python,
                "src\\evaluate_auto_formal_policy_v2_semantic.py",
                "--raw-dir",
                str(raw_dir),
                "--output-dir",
                str(out_dir),
                "--prefix",
                semantic_prefix,
            ]
        )
        if not skip_repair:
            run_command(
                [
                    python,
                    "src\\run_auto_policy_v2_candidate_repair.py",
                    "--raw-dir",
                    str(raw_dir),
                    "--prefix",
                    repair_prefix,
                ]
            )


def semantic_summary(path: Path) -> dict[str, Any]:
    summary = load_json(path)["summary"][0]
    return {
        "semantic_correct": summary["semantic_correct"],
        "semantic_accuracy": summary["semantic_accuracy"],
        "strict_event_successes": summary["strict_event_successes"],
        "strict_event_accuracy": summary["strict_event_accuracy"],
        "generation_successes": summary["generation_successes"],
    }


def repair_summary(path: Path) -> dict[str, Any]:
    rows = read_csv(path)
    if not rows:
        return {}
    row = rows[0]
    return {
        "selected": row["selected"],
        "oracle_correct": row["oracle_correct"],
        "oracle_accuracy": row["oracle_accuracy"],
        "full_closure_success": row["full_closure_success"],
        "full_closure_accuracy": row["full_closure_accuracy"],
        "repair_strict_event_successes": row["strict_event_successes"],
        "repair_strict_event_accuracy": row["strict_event_accuracy"],
        "abstains": row["abstains"],
    }


def build_component_summary(prefix: str, skip_repair: bool) -> None:
    rows: list[dict[str, Any]] = []
    variants = [
        {
            "variant": "V2_FREE_TEXT",
            "qwen_used": True,
            "canonical_schema": False,
            "deterministic_normalizer": False,
            "metadata_evidence_only": False,
            "semantic": OUTPUT_DIR / "auto-policy-v2" / "auto-policy-v2-semantic-evaluation-v2-summary.json",
            "repair": OUTPUT_DIR / "auto-policy-v2-candidate-repair-r5-seed20260820-summary.csv",
        },
        {
            "variant": "V3_NO_NORMALIZER",
            "qwen_used": True,
            "canonical_schema": True,
            "deterministic_normalizer": False,
            "metadata_evidence_only": False,
            "semantic": OUTPUT_DIR / "auto-policy-v3-no-normalizer" / "auto-policy-v3-no-normalizer-semantic-evaluation-summary.json",
            "repair": OUTPUT_DIR / "auto-policy-v3-no-normalizer-candidate-repair-r5-seed20260820-summary.csv",
        },
        {
            "variant": "V3_FULL",
            "qwen_used": True,
            "canonical_schema": True,
            "deterministic_normalizer": True,
            "metadata_evidence_only": False,
            "semantic": OUTPUT_DIR / "auto-policy-v3" / "auto-policy-v3-semantic-evaluation-summary.json",
            "repair": OUTPUT_DIR / "auto-policy-v3-candidate-repair-r5-seed20260820-summary.csv",
        },
        {
            "variant": "METADATA_EVIDENCE_ONLY_NORMALIZER",
            "qwen_used": False,
            "canonical_schema": True,
            "deterministic_normalizer": True,
            "metadata_evidence_only": True,
            "semantic": METADATA_OUTPUT_DIR / "auto-policy-v3-metadata-evidence-only-semantic-evaluation-summary.json",
            "repair": OUTPUT_DIR / "auto-policy-v3-metadata-evidence-only-candidate-repair-r5-seed20260820-summary.csv",
        },
    ]
    for variant in variants:
        semantic = semantic_summary(variant["semantic"])
        repair = {} if skip_repair else repair_summary(variant["repair"])
        rows.append(
            {
                "variant": variant["variant"],
                "qwen_used": variant["qwen_used"],
                "canonical_schema": variant["canonical_schema"],
                "deterministic_normalizer": variant["deterministic_normalizer"],
                "metadata_evidence_only": variant["metadata_evidence_only"],
                "semantic_correct": semantic["semantic_correct"],
                "semantic_accuracy": semantic["semantic_accuracy"],
                "semantic_strict_event_successes": semantic["strict_event_successes"],
                "semantic_strict_event_accuracy": semantic["strict_event_accuracy"],
                "generation_successes": semantic["generation_successes"],
                **repair,
            }
        )
    summary_csv = OUTPUT_DIR / f"{prefix}-summary.csv"
    summary_json = OUTPUT_DIR / f"{prefix}.json"
    write_csv(summary_csv, rows)
    summary_json.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "summary_csv": str(summary_csv),
                "rows": rows,
                "boundary": (
                    "METADATA_EVIDENCE_ONLY_NORMALIZER uses public event metadata and "
                    "candidate-blind evidence only; it is a leakage probe for templated "
                    "evidence, not a deployable method."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"summary={summary_csv}")
    for row in rows:
        print(
            "{variant}: semantic={semantic_accuracy:.2%} strict={semantic_strict_event_successes}/30 "
            "closure={full_closure_accuracy}".format(
                **{
                    **row,
                    "semantic_accuracy": float(row["semantic_accuracy"]),
                    "full_closure_accuracy": row.get("full_closure_accuracy", ""),
                }
            )
        )


def main() -> int:
    block_legacy_entrypoint(__file__)
    args = parse_args()
    create_metadata_evidence_only_raw()
    run_evaluations(args.skip_repair)
    build_component_summary(args.prefix, args.skip_repair)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
