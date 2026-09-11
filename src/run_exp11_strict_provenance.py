from __future__ import annotations

"""Orchestrate Exp11 strict historical provenance ECR evaluation."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from build_exp11_strict_provenance_benchmark import DEFAULT_OUT, EXP11_ID_PERMUTE_SEED
from paper_final_validation_common import (
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    load_freeze_sha256,
    load_method_freeze_sha256,
    utc_now_iso,
    write_binding,
)
from run_exp8_evidence_dependence import export_unified_attempts, summarize_attempts, truth
from semantic_v2_common import PROJECT_DIR, write_csv


EXP11_ROOT = PAPER_VALIDATION_ROOT / "11-strict-provenance"
FROZEN_CSV = EXP11_ROOT / "strict-subset-frozen.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--output-root", type=Path, default=EXP11_ROOT)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--event-limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--target-events", type=int, default=60)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args()

    if args.pilot:
        args.runs = 1

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["ONTOLOGY_EVOLUTION_OFFICIAL_RUN"] = "1"
    env["ONTOLOGY_EVOLUTION_MODEL_ABLATION"] = "1"
    os.environ.update(env)

    phase = "pilot" if args.pilot else "formal"
    py = str(PROJECT_DIR / ".venv" / "Scripts" / "python.exe")

    if not args.skip_build and not args.export_only:
        subprocess.run([py, "src/build_exp11_strict_provenance_gold.py", "--target-events", str(args.target_events)], check=True)
        subprocess.run(
            [
                py,
                "src/build_exp11_strict_provenance_benchmark.py",
                "--frozen-csv",
                str(FROZEN_CSV),
                "--output-dir",
                str(args.benchmark_dir),
                "--seed",
                str(EXP11_ID_PERMUTE_SEED),
                "--force",
            ],
            check=True,
        )

    benchmark = args.benchmark_dir.resolve()
    run_output = args.output_root / "runs" / phase
    method_sha256, method_manifest = load_method_freeze_sha256(DEFAULT_METHOD_FREEZE_DIR)
    benchmark_sha256, benchmark_manifest = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)

    if not args.export_only:
        cmd = [
            py,
            "src/run_v5_online_robustness_pipeline.py",
            "--benchmark-dir",
            str(benchmark),
            "--output-dir",
            str(run_output),
            "--event-limit",
            str(args.event_limit),
            "--runs",
            str(args.runs),
            "--qwen-timeout",
            str(args.timeout),
            "--llm-backend",
            "deepseek_api",
        ]
        if args.resume:
            cmd.append("--resume")
        print("\n" + " ".join(cmd), flush=True)
        subprocess.run(cmd, cwd=PROJECT_DIR, env=os.environ.copy(), check=True)

    unified = export_unified_attempts(
        condition="strict-provenance",
        benchmark=benchmark,
        run_output=run_output,
        method_sha256=method_sha256,
        pilot=args.pilot,
    )
    for row in unified:
        row["experiment_id"] = "exp11"
        row["condition"] = "strict-provenance"

    summary = summarize_attempts(unified)
    summary["condition"] = "strict-provenance"
    summary["pilot"] = args.pilot
    summary["coverage"] = summary["attempts"] / (summary["events"] * args.runs) if summary["events"] else 0.0

    details_path = args.output_root / f"strict-provenance-details-{phase}.csv"
    summary_path = args.output_root / f"strict-provenance-summary-{phase}.csv"
    write_csv(details_path, unified)
    write_csv(summary_path, [summary])

    status = {
        "completed_at_utc": utc_now_iso(),
        "phase": phase,
        "events": summary["events"],
        "attempts": summary["attempts"],
        "runs": args.runs,
        "benchmark_dir": str(benchmark.relative_to(PROJECT_DIR)),
        "details_csv": str(details_path.relative_to(PROJECT_DIR)),
        "summary_csv": str(summary_path.relative_to(PROJECT_DIR)),
        "frozen_subset_csv": str(FROZEN_CSV.relative_to(PROJECT_DIR)),
        "id_permute_seed": EXP11_ID_PERMUTE_SEED,
    }
    (args.output_root / f"strict-provenance-status-{phase}.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_binding(
        args.output_root,
        experiment_role=f"exp11-strict-provenance-{phase}",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_manifest,
        method_freeze_sha256=method_sha256,
        extra={"phase": phase, "summary": summary},
    )
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
