from __future__ import annotations

"""Scaffold Phase 2 v5 Qwen backend evaluation (do not execute)."""

import argparse
from pathlib import Path

from paper_final_validation_common import (
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_DEEPSEEK_PRED_DIR,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    load_freeze_sha256,
    load_method_freeze_sha256,
    verify_deepseek_prediction_dir,
    write_summary_json,
    utc_now_iso,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PAPER_VALIDATION_ROOT / "06-backend-qwen-v5")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_sha256, _ = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_freeze_sha256, _ = load_method_freeze_sha256(DEFAULT_METHOD_FREEZE_DIR)
    binding_check = verify_deepseek_prediction_dir(
        DEFAULT_DEEPSEEK_PRED_DIR,
        benchmark_sha256=benchmark_sha256,
        method_freeze_sha256=method_freeze_sha256,
    )
    scaffold = {
        "phase": 2,
        "status": "SCAFFOLD_ONLY_NOT_EXECUTED",
        "deepseek_baseline_check": binding_check,
        "planned_command": (
            "python src/run_formal_holdout_v3_m16_eval.py "
            "--benchmark-dir benchmark/external-real-holdout-v5-blind-large "
            "--calibrated --llm-backend ollama --qwen-timeout 300"
        ),
        "allowed_change": {"llm_backend": "deepseek-chat -> qwen3.5:9b"},
        "frozen": [
            "EvidenceFocus",
            "M14 logic",
            "M15 rules",
            "V4.3 scorer/thresholds",
            "M16 rules",
            "OWL closure",
            "candidate set",
        ],
        "attempts": "264 events x 5 runs = 1320",
        "generated_at_utc": utc_now_iso(),
    }
    write_summary_json(args.output_dir / "scaffold.json", scaffold)
    print(f"[phase2-qwen-scaffold] deepseek baseline verified: {binding_check['attempts_ok']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
