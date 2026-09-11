from __future__ import annotations

"""Orchestrate Exp10 candidate-only shortcut audit."""

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from exp10_candidate_shortcut_common import (
    DEFAULT_BENCHMARK,
    EXP10_ROOT,
    compute_always_candidate_baseline,
    summarize_attempts,
    write_method_outputs,
)
from exp9_baseline_common import import_ecr_control_rows
from semantic_v2_common import PROJECT_DIR, write_csv


COMPONENTS = ("candidate-only-llm", "c1-with-id", "c2-without-id")
RUNNERS = {
    "candidate-only-llm": "src/run_exp10_candidate_only_llm.py",
    "c1-with-id": "src/run_exp10_statistical_classifier.py",
    "c2-without-id": "src/run_exp10_statistical_classifier.py",
}


def read_unified(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def run_component(name: str, output: Path, runs: int, event_limit: int, timeout: int, pilot: bool, resume: bool) -> None:
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
    if name in {"c1-with-id", "c2-without-id"}:
        cmd.extend(["--model", name])
    if pilot:
        cmd.append("--pilot")
    if resume:
        cmd.append("--resume")
    print("\n" + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=PROJECT_DIR, env=os.environ.copy(), check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--components", default=",".join(COMPONENTS))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--event-limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--skip-freeze", action="store_true")
    args = parser.parse_args()

    if args.pilot:
        args.runs = 1

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["ONTOLOGY_EVOLUTION_OFFICIAL_RUN"] = "1"
    env["ONTOLOGY_EVOLUTION_MODEL_ABLATION"] = "1"
    os.environ.update(env)

    phase = "pilot" if args.pilot else "formal"
    selected = [item.strip() for item in args.components.split(",") if item.strip()]
    unknown = [item for item in selected if item not in COMPONENTS]
    if unknown:
        raise SystemExit(f"unknown components: {unknown}")

    py = str(PROJECT_DIR / ".venv" / "Scripts" / "python.exe")
    if not args.skip_freeze and not args.export_only:
        subprocess.run([py, "src/build_exp10_candidate_feature_freeze.py"], cwd=PROJECT_DIR, check=True)

    for name in selected:
        run_dir = EXP10_ROOT / "runs" / phase / name
        if not args.export_only:
            run_component(name, run_dir, args.runs, args.event_limit, args.timeout, args.pilot, args.resume)

    all_rows: list[dict[str, str]] = []
    summary_rows: list[dict[str, Any]] = []

    always_dir = EXP10_ROOT / "runs" / phase / "always-cand-002"
    if not args.export_only:
        always_rows = compute_always_candidate_baseline(
            benchmark=DEFAULT_BENCHMARK,
            candidate_id="CAND_002",
            runs=args.runs,
            event_limit=args.event_limit,
            timeout=args.timeout,
            output_dir=always_dir,
            condition="always-cand-002",
            decision_path="ALWAYS_CAND_002",
        )
        write_method_outputs(
            condition="always-cand-002",
            method="ALWAYS_CAND_002",
            rows=always_rows,
            output_dir=always_dir,
            benchmark=DEFAULT_BENCHMARK,
            script_path=Path(__file__),
            prompt_file=None,
            pilot=args.pilot,
            extra={"note": "Exp10-A analytical baseline on permuted benchmark"},
        )

    for name in ("always-cand-002", *selected):
        run_dir = EXP10_ROOT / "runs" / phase / name
        unified = run_dir / "exp10-unified-attempts.csv"
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

    details_path = EXP10_ROOT / f"candidate-shortcut-details-{phase}.csv"
    summary_path = EXP10_ROOT / f"candidate-shortcut-summary-{phase}.csv"
    write_csv(details_path, all_rows)
    write_csv(summary_path, summary_rows)

    analytical = {
        "always_cand_002_expected_oracle_accuracy": 0.3333,
        "chance_baseline_oracle_accuracy": 0.3333,
        "note": "On candidate-id-permute, oracle is uniformly distributed across CAND_001/002/003.",
    }
    (EXP10_ROOT / f"candidate-shortcut-analytical-{phase}.json").write_text(
        json.dumps(analytical, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    status = {
        "completed_at_utc": __import__("paper_final_validation_common", fromlist=["utc_now_iso"]).utc_now_iso(),
        "phase": phase,
        "components": ["always-cand-002", *selected],
        "runs": args.runs,
        "details_csv": str(details_path.relative_to(PROJECT_DIR)),
        "summary_csv": str(summary_path.relative_to(PROJECT_DIR)),
        "analytical_json": f"candidate-shortcut-analytical-{phase}.json",
    }
    (EXP10_ROOT / f"candidate-shortcut-status-{phase}.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
