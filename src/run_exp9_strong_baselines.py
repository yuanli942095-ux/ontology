from __future__ import annotations

"""Orchestrate Exp9 strong baselines (B1/B2/B3) on candidate-id-permute."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from exp9_baseline_common import (
    BASELINE_METHODS,
    DEFAULT_BENCHMARK,
    EXP9_ROOT,
    import_ecr_control_rows,
    summarize_attempts,
)
from paper_final_validation_schema import UNIFIED_ATTEMPT_FIELDS
from semantic_v2_common import PROJECT_DIR, write_csv


RUNNERS = {
    "direct-llm": "src/run_exp9_baseline_direct.py",
    "m16-only": "src/run_exp9_baseline_m16_only.py",
    "compute-matched": "src/run_exp9_baseline_compute_matched.py",
}


def read_unified(path: Path) -> list[dict[str, Any]]:
    import csv

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def run_runner(name: str, output: Path, runs: int, event_limit: int, timeout: int, pilot: bool, resume: bool) -> None:
    py = str(PROJECT_DIR / ".venv" / "Scripts" / "python.exe")
    cmd = [
        py,
        RUNNERS[name],
        "--benchmark-dir",
        str(DEFAULT_BENCHMARK),
        "--output-dir",
        str(output),
        "--runs",
        str(runs),
        "--event-limit",
        str(event_limit),
        "--timeout",
        str(timeout),
    ]
    if pilot:
        cmd.append("--pilot")
    if resume:
        cmd.append("--resume")
    print("\n" + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=PROJECT_DIR, env=os.environ.copy(), check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baselines", default=",".join(BASELINE_METHODS))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--event-limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--skip-budget", action="store_true")
    args = parser.parse_args()

    if args.pilot:
        args.runs = 1

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["ONTOLOGY_EVOLUTION_OFFICIAL_RUN"] = "1"
    env["ONTOLOGY_EVOLUTION_MODEL_ABLATION"] = "1"
    os.environ.update(env)

    phase = "pilot" if args.pilot else "formal"
    selected = [item.strip() for item in args.baselines.split(",") if item.strip()]
    unknown = [item for item in selected if item not in RUNNERS]
    if unknown:
        raise SystemExit(f"unknown baselines: {unknown}")

    if not args.skip_budget and not args.export_only:
        subprocess.run(
            [str(PROJECT_DIR / ".venv" / "Scripts" / "python.exe"), "src/build_exp9_ecr_compute_budget.py"],
            cwd=PROJECT_DIR,
            env=os.environ.copy(),
            check=True,
        )

    for name in selected:
        run_dir = EXP9_ROOT / "runs" / phase / name
        if not args.export_only:
            run_runner(name, run_dir, args.runs, args.event_limit, args.timeout, args.pilot, args.resume)

    all_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for name in selected:
        run_dir = EXP9_ROOT / "runs" / phase / name
        unified = run_dir / "exp9-unified-attempts.csv"
        if not unified.is_file():
            raise FileNotFoundError(unified)
        rows = read_unified(unified)
        all_rows.extend(rows)
        summary = summarize_attempts(
            [
                {
                    "event_id": row["event_id"],
                    "selection_status": "SELECTED" if row["decision"] == "SELECT" else "ABSTAIN",
                    "selection_oracle_correct": row["oracle_correct"],
                    "full_closure_success": row["closure_pass"],
                }
                for row in rows
            ]
        )
        summary["condition"] = name
        summary["pilot"] = args.pilot
        summary_rows.append(summary)

    ecr_rows = import_ecr_control_rows(args.pilot)
    all_rows.extend(ecr_rows)
    ecr_summary = summarize_attempts(
        [
            {
                "event_id": row["event_id"],
                "selection_status": "SELECTED" if row["decision"] == "SELECT" else "ABSTAIN",
                "selection_oracle_correct": row["oracle_correct"],
                "full_closure_success": row["closure_pass"],
            }
            for row in ecr_rows
        ]
    )
    ecr_summary["condition"] = "ecr-control"
    ecr_summary["pilot"] = args.pilot
    summary_rows.append(ecr_summary)

    details_path = EXP9_ROOT / f"strong-baselines-details-{phase}.csv"
    summary_path = EXP9_ROOT / f"strong-baselines-summary-{phase}.csv"
    write_csv(details_path, all_rows)
    write_csv(summary_path, summary_rows)

    status = {
        "completed_at_utc": __import__("exp9_baseline_common", fromlist=["utc_now_iso"]).utc_now_iso(),
        "phase": phase,
        "baselines": selected,
        "runs": args.runs,
        "details_csv": str(details_path.relative_to(PROJECT_DIR)),
        "summary_csv": str(summary_path.relative_to(PROJECT_DIR)),
    }
    (EXP9_ROOT / f"strong-baselines-status-{phase}.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
