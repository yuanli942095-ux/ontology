from __future__ import annotations

"""Run Phase 2 Qwen v5 backend evaluation (frozen method, only LLM backend changes)."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from paper_final_validation_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_DEEPSEEK_PRED_DIR,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    load_freeze_sha256,
    load_method_freeze_sha256,
    read_csv,
    verify_deepseek_prediction_dir,
    write_binding,
    write_summary_json,
    utc_now_iso,
)
from semantic_v2_common import PROJECT_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PAPER_VALIDATION_ROOT / "06-backend-qwen-v5",
    )
    parser.add_argument("--event-limit", type=int, default=0, help="0 means all READY events")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--qwen-timeout", type=int, default=300)
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
    benchmark_dir = args.benchmark_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    benchmark_sha256, benchmark_manifest = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_freeze_sha256, method_freeze_manifest = load_method_freeze_sha256(DEFAULT_METHOD_FREEZE_DIR)
    baseline_check = verify_deepseek_prediction_dir(
        DEFAULT_DEEPSEEK_PRED_DIR,
        benchmark_sha256=benchmark_sha256,
        method_freeze_sha256=method_freeze_sha256,
    )

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
        "ollama",
    ]
    if args.resume:
        cmd.append("--resume")

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    print("[phase2-qwen-eval] " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=PROJECT_DIR, env=env, check=True)

    attempt_stats = count_attempts(output_dir)
    summary_path = output_dir / "m16-full-holdout-combined" / "m16-full-holdout-combined-summary.csv"
    summary_rows = read_csv(summary_path) if summary_path.is_file() else []
    summary = {
        "generated_at_utc": utc_now_iso(),
        "status": "COMPLETE",
        "phase": 2,
        "experiment": "backend-qwen-v5",
        "benchmark_dir": str(benchmark_dir.relative_to(PROJECT_DIR)),
        "output_dir": str(output_dir.relative_to(PROJECT_DIR)),
        "benchmark_sha256": benchmark_sha256,
        "method_freeze_sha256": method_freeze_sha256,
        "deepseek_baseline_check": baseline_check,
        "llm_backend": "ollama",
        "model": "qwen3.5:9b",
        "runs": args.runs,
        "attempt_stats": attempt_stats,
        "m16_summary": summary_rows,
        "allowed_change": {"llm_backend": "deepseek-chat -> qwen3.5:9b"},
        "method_changed": False,
    }
    write_summary_json(output_dir / "eval-summary.json", summary)
    write_binding(
        output_dir,
        experiment_role="06-backend-qwen-v5",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_freeze_manifest,
        method_freeze_sha256=method_freeze_sha256,
        extra={
            "derived_variant": False,
            "benchmark_changed": False,
            "llm_backend": "ollama",
            "model": "qwen3.5:9b",
            "deepseek_baseline_dir": str(DEFAULT_DEEPSEEK_PRED_DIR.relative_to(PROJECT_DIR)),
            "method_changed": False,
            "oracle_used_for_inference": False,
        },
    )
    print(
        f"[phase2-qwen-eval] complete attempts={attempt_stats['attempts']} "
        f"events={attempt_stats['events']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
