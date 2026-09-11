from __future__ import annotations

"""Run frozen M13->M16 pipeline for Phase 2 candidate-pool variants (k=5 / k=10)."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from paper_final_validation_common import (
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    load_freeze_sha256,
    load_method_freeze_sha256,
    read_csv,
    write_binding,
    write_summary_json,
    utc_now_iso,
)
from semantic_v2_common import PROJECT_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-size", type=int, required=True, choices=(5, 10))
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=None,
        help="Defaults to benchmark/external-real-holdout-v5-blind-large-pool-k{size}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Defaults to output/paper-final-validation/05-candidate-pool/k{size}/eval",
    )
    parser.add_argument("--event-limit", type=int, default=0, help="0 means all READY events")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--qwen-timeout", type=int, default=180)
    parser.add_argument("--llm-backend", choices=("deepseek_api", "ollama"), default="deepseek_api")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def count_attempts(output_dir: Path) -> dict[str, Any]:
    details = output_dir / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv"
    if not details.is_file():
        return {"attempts": 0, "events": 0, "details_path": str(details)}
    rows = read_csv(details)
    return {
        "attempts": len(rows),
        "events": len({row["event_id"] for row in rows}),
        "details_path": str(details.relative_to(PROJECT_DIR)),
    }


def main() -> int:
    args = parse_args()
    benchmark_dir = (
        args.benchmark_dir
        or PROJECT_DIR / "benchmark" / f"external-real-holdout-v5-blind-large-pool-k{args.pool_size}"
    ).resolve()
    output_dir = (
        args.output_dir or PAPER_VALIDATION_ROOT / "05-candidate-pool" / f"k{args.pool_size}" / "eval"
    ).resolve()
    validation_dir = PAPER_VALIDATION_ROOT / "05-candidate-pool" / f"k{args.pool_size}"
    validation_dir.mkdir(parents=True, exist_ok=True)

    if not benchmark_dir.is_dir():
        raise SystemExit(f"missing benchmark variant: {benchmark_dir}")

    parent_sha256, parent_manifest = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_freeze_sha256, method_freeze_manifest = load_method_freeze_sha256(DEFAULT_METHOD_FREEZE_DIR)

    py = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = Path(sys.executable)

    cmd = [
        str(py),
        "src/run_v5_online_robustness_pipeline.py",
        "--benchmark-dir",
        str(benchmark_dir),
        "--output-dir",
        str(output_dir),
        "--event-limit",
        str(args.event_limit),
        "--runs",
        str(args.runs),
        "--qwen-timeout",
        str(args.qwen_timeout),
        "--llm-backend",
        args.llm_backend,
    ]
    if args.resume:
        cmd.append("--resume")

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    print("[phase2-pool-eval] " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=PROJECT_DIR, env=env, check=True)

    attempt_stats = count_attempts(output_dir)
    summary_path = output_dir / "m16-full-holdout-combined" / "m16-full-holdout-combined-summary.csv"
    summary_rows = read_csv(summary_path) if summary_path.is_file() else []
    summary = {
        "generated_at_utc": utc_now_iso(),
        "status": "COMPLETE",
        "phase": 2,
        "experiment": f"candidate-pool-k{args.pool_size}",
        "pool_size": args.pool_size,
        "benchmark_dir": str(benchmark_dir.relative_to(PROJECT_DIR)),
        "output_dir": str(output_dir.relative_to(PROJECT_DIR)),
        "parent_benchmark_sha256": parent_sha256,
        "method_freeze_sha256": method_freeze_sha256,
        "llm_backend": args.llm_backend,
        "runs": args.runs,
        "attempt_stats": attempt_stats,
        "m16_summary": summary_rows,
    }
    write_summary_json(validation_dir / "eval-summary.json", summary)
    from paper_final_validation_common import write_manifest_csv

    if summary_rows:
        write_manifest_csv(validation_dir / "details.csv", summary_rows)
    write_binding(
        validation_dir,
        experiment_role=f"05-candidate-pool-k{args.pool_size}",
        script_path=Path(__file__),
        benchmark_manifest=parent_manifest,
        benchmark_sha256=parent_sha256,
        method_freeze_manifest=method_freeze_manifest,
        method_freeze_sha256=method_freeze_sha256,
        extra={
            "derived_variant": True,
            "derived_benchmark": str(benchmark_dir.relative_to(PROJECT_DIR)),
            "benchmark_changed": False,
            "pool_size": args.pool_size,
            "input_prediction_dir": str(output_dir.relative_to(PROJECT_DIR)),
            "llm_backend": args.llm_backend,
            "model": "deepseek-chat" if args.llm_backend == "deepseek_api" else "qwen3.5:9b",
            "method_changed": False,
            "oracle_used_for_inference": False,
        },
    )
    print(
        f"[phase2-pool-eval] complete pool=k{args.pool_size} attempts={attempt_stats['attempts']} "
        f"events={attempt_stats['events']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
