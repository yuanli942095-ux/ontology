from __future__ import annotations

"""Freeze the M14+M15+M16 full repair method before a new benchmark run."""

import argparse
import inspect
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_m14_clause_level_gre_css_recovery as m14
import run_m15_temporal_anchor_recovery as m15
import run_m16_candidate_entailment_verifier as m16
from semantic_v2_common import PROJECT_DIR, sha256_file, sha256_text, write_csv


OUTPUT = PROJECT_DIR / "output" / "m16-full-repair-method-freeze"
HOLDOUT_V1_MANIFEST = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v1-expanded"
    / "external-real-holdout-v1-freeze-manifest.json"
)

LOCKED_SOURCES = [
    ("src/method_experiment_guard.py", "experiment_guard_and_frozen_gate"),
    ("src/run_evidence_constrained_semantic_ir_repair.py", "official_experiment_entrypoint"),
    ("src/run_m13_rule_refinement_pilot.py", "m13_rule_refinement_base"),
    ("src/run_m14_clause_level_gre_css_recovery.py", "m14_clause_level_gre_css_recovery"),
    ("src/run_m15_temporal_anchor_recovery.py", "m15_temporal_anchor_recovery"),
    ("src/run_m16_candidate_entailment_verifier.py", "m16_candidate_entailment_verifier"),
    ("src/run_auto_policy_v4_ir_candidate_repair.py", "v4_semantic_ir_candidate_repair"),
    ("src/auto_policy_v4_constraint_rerank.py", "constraint_aware_candidate_reranker"),
    ("src/auto_policy_task_formulation.py", "task_formulation_router"),
    ("src/run_m12_predicate_focus_pilot.py", "predicate_aware_evidence_focus"),
    ("src/auto_policy_m12_deepseek_client.py", "deepseek_api_client"),
    ("src/m13_llm_backends.py", "llm_backend_router"),
]


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return ""


def fn_hash(fn: Any) -> str:
    return sha256_text(inspect.getsource(fn))


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()

    files = []
    for relative, role in LOCKED_SOURCES:
        path = PROJECT_DIR / relative
        files.append(
            {
                "role": role,
                "path": relative,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )

    holdout_v1 = load_json(HOLDOUT_V1_MANIFEST)
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "freeze_kind": "M16_FULL_REPAIR_METHOD_FREEZE",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": "M16_FULL_EVIDENCE_CONSTRAINED_REPAIR",
        "freeze_status": "FROZEN_FOR_NEW_HOLDOUT_EVALUATION",
        "git_commit": git_commit(),
        "base_model_backend": "deepseek_api",
        "base_model_env": {
            "DEEPSEEK_BASE_URL": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "DEEPSEEK_MODEL": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "temperature": 0,
            "api_key_recorded": False,
        },
        "development_data_used": {
            "benchmark": "external-real-holdout-v1-expanded",
            "benchmark_manifest_sha256": holdout_v1.get("manifest_sha256"),
            "method_iteration": [
                "M14 developed for GRE/CSS clause-level recovery",
                "M15 developed for TEMPORAL anchor recovery",
                "M16 developed for GRE/CSS candidate entailment verification",
                "GRE/CSS type-specific gate calibrated on v1-expanded M14 recovery risk-coverage curve",
            ],
            "best_diagnostic_result": "1120/1180 closure on v1-expanded after M16",
            "gate_calibration": {
                "source": "output/threshold-calibration/v1-expanded-m14-gre-css-combined/risk-coverage-curve.csv",
                "policy": "Set GRE/CSS min_score to 0.24 because it recovered additional coverage over 0.30 with zero wrong selections on the development calibration details; TEMPORAL keeps min_score 0.30.",
            },
        },
        "new_holdout_policy": {
            "method_changes_forbidden": True,
            "allowed_before_scoring": [
                "build/freeze a new benchmark",
                "run frozen M16 pipeline",
                "fix infrastructure-only path/API errors without changing method logic",
            ],
            "forbidden_before_scoring": [
                "modify prompts",
                "modify thresholds",
                "modify candidate ranking semantics",
                "inspect oracle to tune method",
            ],
        },
        "pipeline": [
            "M13 base rule refinement",
            "V4 semantic-IR candidate repair",
            "M14 GRE/CSS clause-level recovery for base failures",
            "M15 TEMPORAL anchor recovery for base failures",
            "M16 candidate entailment verifier for remaining GRE/CSS rank/tie abstains",
            "OWL materialization + reasoner + CQ closure",
        ],
        "fixed_parameters": {
            "runs": 5,
            "seeds": [20260829, 20260830, 20260831, 20260901, 20260902],
            "m13_top_n": 4,
            "m14_top_n": 4,
            "m14_clause_max_tokens": 700,
            "m14_frame_max_tokens": 700,
            "m16_max_tokens": 500,
            "v4_min_score": 0.30,
            "v4_gre_css_min_score": 0.24,
            "v4_min_margin": 0.00,
            "v4_reranker": "constraint",
            "v4_temporal_unique_top1": True,
            "v4_robust_ir": True,
        },
        "candidate_isolation": {
            "m13_m14_m15_generation_reads": [
                "public event metadata",
                "public evidence windows",
                "predicate-aware focused evidence",
            ],
            "m13_m14_m15_generation_must_not_read": [
                "repair-stage candidates",
                "private oracle",
                "manual formal policy",
            ],
            "candidate_seen_stage": "V4 ranking and M16 verifier after candidate-blind frame/IR is fixed",
            "oracle_seen_stage": "evaluation only after selection",
        },
        "function_hashes": {
            "m14_clause_prompt": fn_hash(m14.clause_prompt),
            "m14_frame_prompt": fn_hash(m14.frame_prompt),
            "m14_valid_frame": fn_hash(m14.valid_frame),
            "m15_find_anchor_unit": fn_hash(m15.find_anchor_unit),
            "m15_build_record": fn_hash(m15.build_record),
            "m16_verifier_prompt": fn_hash(m16.verifier_prompt),
            "m16_supported": fn_hash(m16.supported),
        },
        "files": files,
    }
    payload["manifest_sha256"] = sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = args.output_dir / "m16-full-repair-method-freeze-manifest.json"
    files_csv = args.output_dir / "m16-full-repair-method-freeze-files.csv"
    summary = args.output_dir / "m16-full-repair-method-freeze-summary.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(files_csv, files)
    summary_payload = {
        "method_name": payload["method_name"],
        "freeze_status": payload["freeze_status"],
        "manifest_sha256": payload["manifest_sha256"],
        "development_benchmark_manifest_sha256": payload["development_data_used"]["benchmark_manifest_sha256"],
        "manifest": relpath(manifest),
        "generated_at_utc": payload["generated_at_utc"],
    }
    summary.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
