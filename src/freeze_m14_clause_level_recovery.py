from __future__ import annotations

"""Freeze the M14 clause-level GRE/CSS recovery method for second-half testing."""

import argparse
import inspect
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_m14_clause_level_gre_css_recovery as m14
from semantic_v2_common import PROJECT_DIR, sha256_file, sha256_text, write_csv

OUTPUT = PROJECT_DIR / "output" / "m14-clause-level-method-freeze"
HOLDOUT_MANIFEST = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v1-expanded"
    / "external-real-holdout-v1-freeze-manifest.json"
)

LOCKED_SOURCES = [
    ("src/run_m14_clause_level_gre_css_recovery.py", "m14_clause_level_gre_css_recovery"),
    ("src/run_auto_policy_v4_ir_candidate_repair.py", "v4_semantic_ir_candidate_repair"),
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

    holdout = load_json(HOLDOUT_MANIFEST)
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "freeze_kind": "M14_CLAUSE_LEVEL_GRE_CSS_RECOVERY_METHOD_FREEZE",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": "M14_CLAUSE_LEVEL_GRE_CSS_RECOVERY",
        "freeze_status": "FROZEN_FOR_SECOND_HALF_EVALUATION",
        "git_commit": git_commit(),
        "base_model_backend": "deepseek_api",
        "base_model_env": {
            "DEEPSEEK_BASE_URL": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "DEEPSEEK_MODEL": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "temperature": 0,
            "api_key_recorded": False,
        },
        "benchmark": {
            "name": "external-real-holdout-v1-expanded",
            "events": holdout.get("events_ready"),
            "manifest_sha256": holdout.get("manifest_sha256"),
        },
        "development_data_used": {
            "base_half": "first 118 events x 5 runs from repaired DeepSeek half run",
            "m14_diagnostic_failure_set": "GRE/CSS failures from the first half only",
            "result_used_before_freeze": "370 failure attempts; 84.86% closure after M14 recovery",
        },
        "evaluation_data_reserved": {
            "second_half": "remaining 118 events x 5 runs",
            "policy": "do not modify M14 code or thresholds based on second-half results before reporting",
        },
        "candidate_isolation": {
            "generation_reads": [
                "public event metadata",
                "public evidence windows",
                "predicate-aware focused evidence",
            ],
            "generation_must_not_read": [
                "repair-stage candidates",
                "private oracle",
                "manual formal policy",
            ],
            "candidate_seen_stage": "V4 ranking after M14 frame is fixed",
        },
        "fixed_parameters": {
            "m14_limit": 0,
            "m14_top_n": 4,
            "clause_max_tokens": 700,
            "frame_max_tokens": 700,
            "v4_min_score": 0.30,
            "v4_min_margin": 0.00,
            "v4_reranker": "constraint",
            "v4_temporal_unique_top1": True,
            "v4_robust_ir": True,
        },
        "function_hashes": {
            "clause_prompt": fn_hash(m14.clause_prompt),
            "frame_prompt": fn_hash(m14.frame_prompt),
            "valid_frame": fn_hash(m14.valid_frame),
            "canonical_result": fn_hash(m14.canonical_result),
            "build_recovery_record": fn_hash(m14.build_recovery_record),
        },
        "files": files,
    }
    payload["manifest_sha256"] = sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = args.output_dir / "m14-method-freeze-manifest.json"
    files_csv = args.output_dir / "m14-method-freeze-files.csv"
    summary = args.output_dir / "m14-method-freeze-summary.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(files_csv, files)
    summary_payload = {
        "method_name": payload["method_name"],
        "freeze_status": payload["freeze_status"],
        "manifest_sha256": payload["manifest_sha256"],
        "holdout_manifest_sha256": payload["benchmark"]["manifest_sha256"],
        "manifest": relpath(manifest),
        "generated_at_utc": payload["generated_at_utc"],
    }
    summary.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
