from __future__ import annotations

"""Official paper experiment runner: Evidence-Constrained Semantic IR Repair.

This is the only supported entrypoint for main-method experiments without
passing --allow-legacy-experiment on lower-level scripts.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from method_experiment_guard import (
    FROZEN_V43_GATE,
    HOLDOUT_BENCHMARK,
    activate_official_run_env,
    guard_holdout_benchmark,
)
from semantic_v2_common import PROJECT_DIR

DEFAULT_DEV_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
DEFAULT_HOLDOUT_BENCHMARK = PROJECT_DIR / "benchmark" / HOLDOUT_BENCHMARK
DEFAULT_RAW_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-v8-grounded"
    / "preformal-r3"
    / "raw_window_metadata_light"
    / "raw"
)
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "evidence-constrained-semantic-ir-repair" / "runs"


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("m13-pilot", "v43-replay"),
        default="m13-pilot",
        help="m13-pilot: predicate focus + M13 refinement + V4.3 on configured subset; "
        "v43-replay: frozen V4.3 IR repair on existing raw records",
    )
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_DEV_BENCHMARK)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=PROJECT_DIR / "output" / "m12-predicate-focus-pilot-v3-expanded-manifest.csv")
    parser.add_argument("--top-n", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    guard_holdout_benchmark(args.benchmark_dir, __file__)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    env = activate_official_run_env()

    if args.mode == "m13-pilot":
        cmd = [
            sys.executable,
            str(PROJECT_DIR / "src" / "run_m13_rule_refinement_pilot.py"),
            "--benchmark-dir",
            str(args.benchmark_dir),
            "--output-dir",
            str(args.output_dir / "m13-pilot"),
            "--manifest",
            str(args.manifest),
            "--top-n",
            str(args.top_n),
        ]
    else:
        cmd = [
            sys.executable,
            str(PROJECT_DIR / "src" / "run_auto_policy_v4_ir_candidate_repair.py"),
            "--benchmark-dir",
            str(args.benchmark_dir),
            "--raw-dir",
            str(args.raw_dir),
            "--output-dir",
            str(args.output_dir / "v43-replay"),
            "--min-score",
            str(FROZEN_V43_GATE["min_score"]),
            "--gre-css-min-score",
            str(FROZEN_V43_GATE["gre_css_min_score"]),
            "--min-margin",
            str(FROZEN_V43_GATE["min_margin"]),
            "--reranker",
            FROZEN_V43_GATE["reranker"],
            "--temporal-unique-top1",
        ]
        if FROZEN_V43_GATE["robust_ir"]:
            cmd.append("--robust-ir")
        else:
            cmd.append("--no-robust-ir")

    summary = {
        "method": "EVIDENCE_CONSTRAINED_SEMANTIC_IR_REPAIR",
        "mode": args.mode,
        "benchmark": relpath(args.benchmark_dir),
        "output_dir": relpath(args.output_dir),
        "frozen_v43_gate": FROZEN_V43_GATE,
        "command": cmd,
        "dry_run": args.dry_run,
    }
    summary_path = args.output_dir / "official-run-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
