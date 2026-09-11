from __future__ import annotations

"""Scaffold Phase 2 candidate-pool k=5/k=10 benchmark variants (do not run pipeline)."""

import argparse
import json
from pathlib import Path

from paper_final_validation_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    load_freeze_sha256,
    load_method_freeze_sha256,
    write_summary_json,
    utc_now_iso,
)
from semantic_v2_common import PROJECT_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PAPER_VALIDATION_ROOT / "05-candidate-pool")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_sha256, _ = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_freeze_sha256, _ = load_method_freeze_sha256(DEFAULT_METHOD_FREEZE_DIR)
    scaffold = {
        "phase": 2,
        "status": "EVAL_IN_PROGRESS",
        "eval_runner": "src/run_phase2_candidate_pool_eval.py",
        "eval_launcher": "scripts/run_phase2_pool_eval.ps1",
        "built_variants": {
            "k5": "benchmark/external-real-holdout-v5-blind-large-pool-k5",
            "k10": "benchmark/external-real-holdout-v5-blind-large-pool-k10",
        },
        "next_commands": [
            "python src/run_v5_online_robustness_pipeline.py --benchmark-dir benchmark/external-real-holdout-v5-blind-large-pool-k5 ...",
            "python src/run_v5_online_robustness_pipeline.py --benchmark-dir benchmark/external-real-holdout-v5-blind-large-pool-k10 ...",
        ],
        "discipline": [
            "Do not retune min_score/min_margin/M16 prompts",
            "Use frozen method parameters only",
            "Run audits: manifest, hash, oracle-preservation, duplicate, leakage",
        ],
        "benchmark_sha256": benchmark_sha256,
        "method_freeze_sha256": method_freeze_sha256,
        "generated_at_utc": utc_now_iso(),
    }
    for pool in ("k5", "k10"):
        (args.output_dir / pool).mkdir(parents=True, exist_ok=True)
    write_summary_json(args.output_dir / "scaffold.json", scaffold)
    (args.output_dir / "README.txt").write_text(
        "Phase 2 candidate-pool experiment scaffold. Execute only after Phase 1 QC passes.\n",
        encoding="utf-8",
    )
    print(f"[phase2-pool-scaffold] wrote {args.output_dir / 'scaffold.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
