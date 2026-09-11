from __future__ import annotations

"""Freeze metadata for secondary post-freeze model substitution ablation."""

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from formal_holdout_eval_guard import FORMAL_HOLDOUT_SEEDS, validate_pre_run_gates
from semantic_v2_common import PROJECT_DIR, sha256_file, sha256_text, write_csv

OUTPUT = PROJECT_DIR / "output" / "final-method-freeze"
PRIMARY_FREEZE = OUTPUT / "final-method-freeze-manifest.json"

LOCKED_SOURCES = [
    ("src/m13_llm_backends.py", "m13_llm_backend_router"),
    ("src/auto_policy_m12_deepseek_client.py", "deepseek_api_client"),
    ("src/run_formal_holdout_model_ablation.py", "model_ablation_runner"),
    ("src/freeze_model_ablation_secondary.py", "model_ablation_freeze_script"),
]


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


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()

    validate_pre_run_gates()
    primary = json.loads(PRIMARY_FREEZE.read_text(encoding="utf-8")) if PRIMARY_FREEZE.is_file() else {}

    files = []
    for rel, role in LOCKED_SOURCES:
        path = PROJECT_DIR / rel
        stat = path.stat()
        files.append(
            {
                "role": role,
                "path": rel.replace("\\", "/"),
                "sha256": sha256_file(path),
                "bytes": stat.st_size,
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "freeze_kind": "SECONDARY_MODEL_SUBSTITUTION_ABLATION",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "experiment_role": "SECONDARY_MODEL_SUBSTITUTION_ABLATION",
        "primary_method_changed": False,
        "only_changed_component": "base_language_model",
        "created_after_primary_execution_started": True,
        "primary_holdout_results_used_for_design": False,
        "paper_label": "secondary post-freeze model ablation",
        "primary_frozen_method": {
            "method_name": primary.get("method_name", "EVIDENCE_CONSTRAINED_SEMANTIC_IR_REPAIR"),
            "base_model": "qwen3.5:9b",
            "backend": "ollama",
            "manifest_sha256": primary.get("manifest_sha256", ""),
        },
        "secondary_arm": {
            "method_pipeline": "EVIDENCE_CONSTRAINED_SEMANTIC_IR_REPAIR",
            "backend": "deepseek_api",
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "temperature": 0,
            "max_tokens": 900,
            "note": "Only the LLM backend differs; all symbolic stages remain frozen.",
        },
        "unchanged_components": [
            "public_evidence",
            "predicate_aware_evidence_focus",
            "m13_prompts",
            "semantic_ir",
            "robust_ir",
            "v43_ranking",
            "min_score_0.30",
            "min_margin_0.00",
            "safety_gate",
            "owl_materialization",
            "reasoner",
            "cq",
            "minimal_edit",
        ],
        "holdout_eval": {
            "events": 236,
            "runs": 5,
            "seeds": list(FORMAL_HOLDOUT_SEEDS),
            "attempts": 1180,
        },
        "output_layout": {
            "primary_arm": "output/external-real-holdout-v1-expanded/final-blind-eval-r5/main-method/",
            "secondary_arm": "output/external-real-holdout-v1-expanded/final-blind-eval-r5-model-ablation-deepseek/main-method/",
        },
        "do_not": [
            "replace_primary_method_with_deepseek",
            "tune_pipeline_based_on_deepseek_results",
            "share_output_directories_with_primary_arm",
        ],
        "git_commit": git_commit(),
        "files": files,
    }
    payload["manifest_sha256"] = sha256_text(
        json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "model-ablation-freeze-manifest.json"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(args.output_dir / "model-ablation-freeze-files.csv", files)
    summary = {
        "experiment_role": payload["experiment_role"],
        "manifest_sha256": payload["manifest_sha256"],
        "secondary_model": payload["secondary_arm"]["model"],
        "path": relpath(manifest_path),
    }
    (args.output_dir / "model-ablation-freeze-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
