from __future__ import annotations

"""Stage runner for the frozen RFC-213 Phase-3 confirmatory experiment.

This file only orchestrates frozen M13--M16 components. It does not change
Gamma, prompts, thresholds, or candidate construction. Model-bearing stages
must be selected explicitly with --step; there is no implicit full run.
"""

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from combine_holdout_pipeline_details import M13_IR_PREFIX, M14_IR_PREFIX, M15_IR_PREFIX
from m13_llm_backends import TRANSPORT_BACKOFF_SECONDS, TRANSPORT_MAX_ATTEMPTS
from semantic_v2_common import PROJECT_DIR


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "rfc-213-confirmatory-core"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "rfc-213-confirmatory-core" / "phase3"
SEEDS = (20260829, 20260830, 20260831, 20260901, 20260902)
SMOKE_IDS = ("H5_E001", "H5_E002", "H5_E003", "H5_E004", "H5_E005")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["event_id", "run", "seed", "semantic_type", "domain", "missing_subtype", "raw_output_file"]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def profile_manifest(benchmark: Path, profile: str) -> list[dict[str, str]]:
    events = read_csv(benchmark / "public" / "events" / "external-real-event-template.csv")
    events = [row for row in events if row.get("status") == "READY"]
    if profile == "smoke":
        wanted = set(SMOKE_IDS)
        events = [row for row in events if row["event_id"] in wanted]
        runs = 1
    elif profile == "engineering":
        runs = 1
    else:
        runs = 5
    return [
        {
            "event_id": event["event_id"],
            "run": str(index),
            "seed": str(SEEDS[index - 1]),
            "semantic_type": event["semantic_type"],
            "domain": event["domain"],
            "missing_subtype": "RFC213_CONFIRMATORY_EVAL",
            "raw_output_file": "",
        }
        for event in events
        for index in range(1, runs + 1)
    ]


def run_py(script: str, args: list[str], *, env: dict[str, str]) -> None:
    command = [sys.executable, str(PROJECT_DIR / "src" / script), *args]
    print("COMMAND:", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=PROJECT_DIR, env=env, check=True)


def paths(root: Path) -> dict[str, Path]:
    m13 = root / "m13"
    m14 = root / "m14"
    m15 = root / "m15"
    m16 = root / "m16"
    return {
        "manifest": root / "rfc213-phase3-manifest.csv",
        "m13_root": m13,
        "m13": m13 / "arm-d-rule-refinement" / "ir" / f"{M13_IR_PREFIX}-details.csv",
        "m14_root": m14,
        "m14": m14 / "ir" / f"{M14_IR_PREFIX}-details.csv",
        "m14_full_root": root / "m14-combined",
        "m14_full": root / "m14-combined" / "m14-full-holdout-combined-full-details.csv",
        "m14_summary": root / "m14-combined" / "m14-full-holdout-combined-details.csv",
        "m15_root": m15,
        "m15": m15 / "ir" / f"{M15_IR_PREFIX}-details.csv",
        "m15_full_root": root / "m15-combined",
        "m15_full": root / "m15-combined" / "m15-full-holdout-combined-full-details.csv",
        "m15_summary": root / "m15-combined" / "m15-full-holdout-combined-details.csv",
        "m16_root": m16,
        "m16": m16 / "m16-candidate-entailment-details.csv",
        "m16_full_root": root / "m16-combined",
        "m16_full": root / "m16-combined" / "m16-full-holdout-combined-full-details.csv",
    }


def require(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"required prior-stage artifact is missing: {path}")


def ensure_empty_recovery(path: Path) -> Path:
    """Create a header-only recovery artifact when a stage has no eligible rows."""
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "event_id", "semantic_type", "domain", "run", "seed",
                "selected_candidate_id", "selection_oracle_correct",
                "full_closure_success", "decision_path",
            ],
        )
        writer.writeheader()
    print(f"NO-OP RECOVERY: wrote header-only artifact {path}", flush=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("smoke", "engineering", "confirmatory"), required=True)
    parser.add_argument(
        "--step",
        choices=("prepare", "m13", "m14", "combine-m14", "m15", "combine-m15", "m16", "combine-m16", "gamma-eval"),
        required=True,
    )
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--llm-backend", choices=("ollama", "deepseek_api"), default="deepseek_api")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--top-n", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    benchmark = args.benchmark_dir.resolve()
    root = (args.output_dir / args.profile).resolve()
    root.mkdir(parents=True, exist_ok=True)
    p = paths(root)
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env["ONTOLOGY_EVOLUTION_OFFICIAL_RUN"] = "1"

    if args.step == "prepare":
        rows = profile_manifest(benchmark, args.profile)
        write_manifest(p["manifest"], rows)
        metadata = {
            "benchmark": benchmark.name,
            "profile": args.profile,
            "attempts": len(rows),
            "events": len({row["event_id"] for row in rows}),
            "runs_per_event": max(int(row["run"]) for row in rows),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "model_experiment_started": False,
            "transport_retry_policy": {
                "max_attempts": TRANSPORT_MAX_ATTEMPTS,
                "backoff_seconds": list(TRANSPORT_BACKOFF_SECONDS),
                "semantic_or_schema_retry_allowed": False,
            },
        }
        (root / "rfc213-phase3-run-plan.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0

    require(p["manifest"])
    resume = ["--resume"] if args.resume else []
    common_backend = ["--llm-backend", args.llm_backend]
    if args.step == "m13":
        run_py("run_m13_rule_refinement_pilot.py", [
            "--benchmark-dir", str(benchmark), "--output-dir", str(p["m13_root"]),
            "--manifest", str(p["manifest"]), "--manifest-mode", "all",
            "--top-n", str(args.top_n), "--qwen-timeout", str(args.timeout),
            "--main-method-only", *common_backend, *resume,
        ], env=env)
    elif args.step == "m14":
        require(p["m13"])
        run_py("run_m14_clause_level_gre_css_recovery.py", [
            "--benchmark-dir", str(benchmark), "--details", str(p["m13"]),
            "--output-dir", str(p["m14_root"]), "--limit", "0", "--top-n", str(args.top_n),
            "--timeout", str(args.timeout), *common_backend, *resume,
        ], env=env)
    elif args.step == "combine-m14":
        require(p["m13"])
        ensure_empty_recovery(p["m14"])
        run_py("combine_holdout_pipeline_details.py", [
            "--stage", "m14", "--output-dir", str(p["m14_full_root"]),
            "--base-details", str(p["m13"]), "--recovery-details", str(p["m14"]),
        ], env=env)
    elif args.step == "m15":
        require(p["m14_full"])
        run_py("run_m15_temporal_anchor_recovery.py", [
            "--benchmark-dir", str(benchmark), "--details", str(p["m14_full"]),
            "--output-dir", str(p["m15_root"]), "--limit", "0", *resume,
        ], env=env)
    elif args.step == "combine-m15":
        for needed in (p["m13"], p["m14_full"], p["m14_summary"]): require(needed)
        ensure_empty_recovery(p["m15"])
        run_py("combine_holdout_pipeline_details.py", [
            "--stage", "m15", "--output-dir", str(p["m15_full_root"]),
            "--base-details", str(p["m13"]), "--prior-full-details", str(p["m14_full"]),
            "--prior-summary-details", str(p["m14_summary"]), "--recovery-details", str(p["m15"]),
        ], env=env)
    elif args.step == "m16":
        require(p["m15_summary"]); require(p["m14"])
        run_py("run_m16_candidate_entailment_verifier.py", [
            "--benchmark-dir", str(benchmark), "--details", str(p["m15_summary"]),
            "--output-dir", str(p["m16_root"]), "--m14-details", str(p["m14"]),
            "--limit", "0", "--timeout", str(args.timeout), *common_backend, *resume,
        ], env=env)
    elif args.step == "combine-m16":
        for needed in (p["m13"], p["m15_full"], p["m15_summary"]): require(needed)
        recovery_args = ["--recovery-details", str(p["m16"])] if p["m16"].is_file() else []
        run_py("combine_holdout_pipeline_details.py", [
            "--stage", "m16", "--output-dir", str(p["m16_full_root"]),
            "--base-details", str(p["m13"]), "--prior-full-details", str(p["m15_full"]),
            "--prior-summary-details", str(p["m15_summary"]), *recovery_args,
        ], env=env)
    else:
        require(p["m16_full"])
        run_py("evaluate_rfc213_phase3_gamma_closure.py", [
            "--benchmark-dir", str(benchmark), "--predictions", str(p["m16_full"]),
            "--output-dir", str(root / "gamma-evaluation"),
        ], env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
