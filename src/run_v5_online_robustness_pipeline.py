from __future__ import annotations

"""Run the frozen M13->M16 pipeline on a v5 robustness benchmark variant."""

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large-robustness-variants" / "candidate-order-shuffle"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v5-blind-large" / "robustness-online"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def event_ids(benchmark: Path, limit: int) -> list[str]:
    rows = [
        row
        for row in read_csv(benchmark / "public" / "events" / "external-real-event-template.csv")
        if row.get("status") == "READY"
    ]
    rows.sort(key=lambda row: row["event_id"])
    if limit:
        rows = rows[:limit]
    return [row["event_id"] for row in rows]


def run(cmd: list[str], env: dict[str, str]) -> None:
    print("\n" + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=PROJECT_DIR, env=env, check=True)


def recovery_raw_empty(output_dir: Path) -> bool:
    raw_dir = output_dir / "raw"
    return raw_dir.is_dir() and not any(raw_dir.glob("*.json"))


def write_empty_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["event_id", "run", "seed"])
        writer.writeheader()


def run_optional_recovery(cmd: list[str], env: dict[str, str], output_dir: Path, details_path: Path) -> None:
    try:
        run(cmd, env)
    except subprocess.CalledProcessError:
        if recovery_raw_empty(output_dir):
            print(f"optional recovery produced no raw records; writing empty details: {details_path}", flush=True)
            write_empty_csv(details_path)
            return
        raise
    if not details_path.is_file():
        if recovery_raw_empty(output_dir):
            write_empty_csv(details_path)
        else:
            raise FileNotFoundError(details_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--event-limit", type=int, default=30)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--qwen-timeout", type=int, default=180)
    parser.add_argument("--llm-backend", choices=("ollama", "deepseek_api"), default="deepseek_api")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    benchmark = args.benchmark_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    only = ",".join(event_ids(benchmark, args.event_limit))
    if not only:
        raise SystemExit("no READY events selected")

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["ONTOLOGY_EVOLUTION_OFFICIAL_RUN"] = "1"
    env["ONTOLOGY_EVOLUTION_MODEL_ABLATION"] = "1"
    py = str(PROJECT_DIR / ".venv" / "Scripts" / "python.exe")

    m13_dir = output / "m13-pilot"
    m13_details = m13_dir / "arm-d-rule-refinement" / "ir" / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
    m14_dir = output / "m14-clause-level-gre-css-recovery"
    m14_details = m14_dir / "ir" / "m14-clause-level-gre-css-recovery-v4-ir-details.csv"
    m14_combined = output / "m14-full-holdout-combined"
    m15_dir = output / "m15-temporal-anchor-recovery"
    m15_details = m15_dir / "ir" / "m15-temporal-anchor-recovery-v4-ir-details.csv"
    m15_combined = output / "m15-full-holdout-combined"
    m16_dir = output / "m16-candidate-entailment"
    m16_details = m16_dir / "m16-candidate-entailment-details.csv"
    m16_combined = output / "m16-full-holdout-combined"

    resume_flag = ["--resume"] if args.resume else []
    run(
        [
            py,
            "src/run_m13_rule_refinement_pilot.py",
            "--benchmark-dir",
            str(benchmark),
            "--output-dir",
            str(m13_dir),
            "--manifest-mode",
            "all",
            "--only",
            only,
            "--runs",
            str(args.runs),
            "--qwen-timeout",
            str(args.qwen_timeout),
            "--main-method-only",
            "--llm-backend",
            args.llm_backend,
            *resume_flag,
        ],
        env,
    )
    run_optional_recovery(
        [
            py,
            "src/run_m14_clause_level_gre_css_recovery.py",
            "--benchmark-dir",
            str(benchmark),
            "--details",
            str(m13_details),
            "--output-dir",
            str(m14_dir),
            "--limit",
            "999999",
            "--only",
            only,
            "--timeout",
            str(args.qwen_timeout),
            "--llm-backend",
            args.llm_backend,
            *resume_flag,
        ],
        env,
        m14_dir,
        m14_details,
    )
    run(
        [
            py,
            "src/combine_holdout_pipeline_details.py",
            "--stage",
            "m14",
            "--output-dir",
            str(m14_combined),
            "--base-details",
            str(m13_details),
            "--recovery-details",
            str(m14_details),
        ],
        env,
    )
    run_optional_recovery(
        [
            py,
            "src/run_m15_temporal_anchor_recovery.py",
            "--benchmark-dir",
            str(benchmark),
            "--details",
            str(m14_combined / "m14-full-holdout-combined-full-details.csv"),
            "--output-dir",
            str(m15_dir),
            "--limit",
            "999999",
            "--only",
            only,
            *resume_flag,
        ],
        env,
        m15_dir,
        m15_details,
    )
    run(
        [
            py,
            "src/combine_holdout_pipeline_details.py",
            "--stage",
            "m15",
            "--output-dir",
            str(m15_combined),
            "--base-details",
            str(m13_details),
            "--prior-full-details",
            str(m14_combined / "m14-full-holdout-combined-full-details.csv"),
            "--prior-summary-details",
            str(m14_combined / "m14-full-holdout-combined-details.csv"),
            "--recovery-details",
            str(m15_details),
        ],
        env,
    )
    run(
        [
            py,
            "src/run_m16_candidate_entailment_verifier.py",
            "--benchmark-dir",
            str(benchmark),
            "--details",
            str(m15_combined / "m15-full-holdout-combined-details.csv"),
            "--m14-details",
            str(m14_details),
            "--output-dir",
            str(m16_dir),
            "--limit",
            "999999",
            "--only",
            only,
            "--timeout",
            str(args.qwen_timeout),
            "--llm-backend",
            args.llm_backend,
            *resume_flag,
        ],
        env,
    )
    if not m16_details.is_file():
        write_empty_csv(m16_details)
    run(
        [
            py,
            "src/combine_holdout_pipeline_details.py",
            "--stage",
            "m16",
            "--output-dir",
            str(m16_combined),
            "--base-details",
            str(m13_details),
            "--prior-full-details",
            str(m15_combined / "m15-full-holdout-combined-full-details.csv"),
            "--prior-summary-details",
            str(m15_combined / "m15-full-holdout-combined-details.csv"),
            "--recovery-details",
            str(m16_details),
        ],
        env,
    )

    summary_path = m16_combined / "m16-full-holdout-combined-summary.csv"
    summary = read_csv(summary_path)
    binding: dict[str, Any] = {
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": str(benchmark),
        "output": str(output),
        "event_limit": args.event_limit,
        "runs": args.runs,
        "only": only,
        "summary_csv": str(summary_path),
        "summary": summary,
    }
    (output / "v5-online-robustness-pipeline-summary.json").write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(binding, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
