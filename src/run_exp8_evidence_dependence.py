from __future__ import annotations

"""Run Exp8 evidence-dependence frozen ECR pipeline across benchmark conditions."""

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_final_validation_common import (
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    load_freeze_sha256,
    load_json,
    load_method_freeze_sha256,
    sha256_file,
    utc_now_iso,
    write_binding,
)
from paper_final_validation_schema import UNIFIED_ATTEMPT_FIELDS
from semantic_v2_common import PROJECT_DIR, write_csv


EXP8_ROOT = PAPER_VALIDATION_ROOT / "08-evidence-dependence"
VARIANT_ROOT = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large-evidence-variants"
def truth(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


CONDITIONS = (
    "normal",
    "no-evidence",
    "no-evidence-masked-source",
    "shuffled-evidence",
    "irrelevant-same-source",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def run_pipeline(
    *,
    benchmark: Path,
    output: Path,
    runs: int,
    timeout: int,
    resume: bool,
    event_limit: int,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    py = str(PROJECT_DIR / ".venv" / "Scripts" / "python.exe")
    cmd = [
        py,
        "src/run_v5_online_robustness_pipeline.py",
        "--benchmark-dir",
        str(benchmark),
        "--output-dir",
        str(output),
        "--event-limit",
        str(event_limit),
        "--runs",
        str(runs),
        "--qwen-timeout",
        str(timeout),
        "--llm-backend",
        "deepseek_api",
    ]
    if resume:
        cmd.append("--resume")
    print("\n" + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=PROJECT_DIR, env=os.environ.copy(), check=True)


def export_unified_attempts(
    *,
    condition: str,
    benchmark: Path,
    run_output: Path,
    method_sha256: str,
    pilot: bool,
) -> list[dict[str, Any]]:
    details = run_output / "m16-full-holdout-combined" / "m16-full-holdout-combined-full-details.csv"
    if not details.is_file():
        raise FileNotFoundError(details)
    event_csv = benchmark / "public" / "events" / "external-real-event-template.csv"
    events = {row["event_id"]: row for row in read_csv(event_csv)}
    retrieval = benchmark / "public" / "retrieval" / "external-real-v8-grounded-source-families.csv"
    source_by_event: dict[str, str] = {}
    if retrieval.is_file():
        for row in read_csv(benchmark / "public" / "documents" / "external-real-document-template.csv"):
            source_by_event[row["event_id"]] = row.get("issuer", "")

    benchmark_sha = sha256_file(event_csv)
    rows_out: list[dict[str, Any]] = []
    for row in read_csv(details):
        event = events[row["event_id"]]
        selected = str(row.get("selection_status", "")).upper()
        decision = "SELECT" if selected == "SELECTED" else "ABSTAIN"
        closure = truth(row.get("full_closure_success"))
        oracle_ok = truth(row.get("selection_oracle_correct"))
        wrong_select = decision == "SELECT" and not (oracle_ok and closure)
        rows_out.append(
            {
                "experiment_id": "exp8",
                "condition": condition,
                "method": "ECR",
                "event_id": row["event_id"],
                "run_id": row["run"],
                "semantic_type": row.get("semantic_type", event.get("semantic_type", "")),
                "source_id": event.get("document_ids", ""),
                "source_family": source_by_event.get(row["event_id"], event.get("domain", "")),
                "benchmark_sha256": benchmark_sha,
                "method_sha256": method_sha256,
                "prompt_sha256": "",
                "oracle_candidate_id": row.get("oracle_candidate_id", ""),
                "selected_candidate_id": row.get("selected_candidate_id", ""),
                "decision": decision,
                "oracle_correct": str(oracle_ok),
                "closure_pass": str(closure),
                "strict_success": "",
                "wrong_select": str(wrong_select),
                "abstain": str(decision == "ABSTAIN"),
                "failure_stage": row.get("decision_path", ""),
                "failure_reason": row.get("ir_reason", ""),
                "m13_status": row.get("generation_status", ""),
                "m14_status": row.get("source_m14_raw_output_file", ""),
                "m15_status": "",
                "ranking_status": row.get("decision_path", ""),
                "m16_status": row.get("supported_count", ""),
                "closure_status": row.get("reasoner_result", ""),
                "api_error": "",
                "timeout": "",
                "llm_calls": "",
                "input_tokens": "",
                "output_tokens": "",
                "total_tokens": "",
                "latency_ms": row.get("runtime_ms", ""),
                "random_seed": row.get("seed", ""),
                "timestamp": utc_now_iso(),
                "pilot": str(pilot),
            }
        )
    return rows_out


def summarize_attempts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = len(rows)
    closure = sum(truth(row["closure_pass"]) for row in rows)
    oracle = sum(truth(row["oracle_correct"]) for row in rows)
    abstain = sum(truth(row["abstain"]) for row in rows)
    wrong = sum(truth(row["wrong_select"]) for row in rows)
    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_event.setdefault(row["event_id"], []).append(row)
    strict = sum(all(truth(item["closure_pass"]) for item in items) for items in by_event.values())
    return {
        "attempts": attempts,
        "events": len(by_event),
        "closure_success": closure,
        "closure_accuracy": closure / attempts if attempts else 0.0,
        "oracle_success": oracle,
        "oracle_accuracy": oracle / attempts if attempts else 0.0,
        "strict_event_successes": strict,
        "strict_event_accuracy": strict / len(by_event) if by_event else 0.0,
        "abstain_count": abstain,
        "wrong_select_count": wrong,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant-root", type=Path, default=VARIANT_ROOT)
    parser.add_argument("--output-root", type=Path, default=EXP8_ROOT)
    parser.add_argument("--conditions", default=",".join(CONDITIONS))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--event-limit", type=int, default=0)
    parser.add_argument("--qwen-timeout", type=int, default=180)
    parser.add_argument("--pilot", action="store_true", help="Use runs=1 for QA; excluded from formal stats")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args()

    if args.pilot:
        args.runs = 1

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["ONTOLOGY_EVOLUTION_OFFICIAL_RUN"] = "1"
    env["ONTOLOGY_EVOLUTION_MODEL_ABLATION"] = "1"
    os.environ.update(env)

    method_sha256, method_manifest_path = load_method_freeze_sha256()
    benchmark_sha256, benchmark_manifest_path = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)

    selected = [item.strip() for item in args.conditions.split(",") if item.strip()]
    all_attempt_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for condition in selected:
        benchmark = args.variant_root / condition
        if not benchmark.is_dir():
            raise SystemExit(f"missing benchmark variant: {benchmark}")
        run_output = args.output_root / "runs" / ("pilot" if args.pilot else "formal") / condition
        if not args.export_only:
            run_pipeline(
                benchmark=benchmark,
                output=run_output,
                runs=args.runs,
                timeout=args.qwen_timeout,
                resume=args.resume,
                event_limit=args.event_limit,
            )
        attempts = export_unified_attempts(
            condition=condition,
            benchmark=benchmark,
            run_output=run_output,
            method_sha256=method_sha256,
            pilot=args.pilot,
        )
        all_attempt_rows.extend(attempts)
        summary = summarize_attempts(attempts)
        summary["condition"] = condition
        summary["pilot"] = args.pilot
        summary_rows.append(summary)
        write_csv(run_output / "exp8-unified-attempts.csv", attempts)
        write_binding(
            run_output,
            experiment_role=f"exp8-{condition}",
            script_path=Path(__file__),
            benchmark_manifest=benchmark_manifest_path,
            benchmark_sha256=benchmark_sha256,
            method_freeze_manifest=method_manifest_path,
            method_freeze_sha256=method_sha256,
            extra={
                "condition": condition,
                "pilot": args.pilot,
                "runs": args.runs,
                "summary": summary,
            },
        )

    phase = "pilot" if args.pilot else "formal"
    details_path = args.output_root / f"evidence-dependence-details-{phase}.csv"
    summary_path = args.output_root / f"evidence-dependence-summary-{phase}.csv"
    write_csv(details_path, all_attempt_rows)
    write_csv(summary_path, summary_rows)

    status = {
        "completed_at_utc": utc_now_iso(),
        "phase": phase,
        "conditions": selected,
        "runs": args.runs,
        "details_csv": str(details_path.relative_to(PROJECT_DIR)),
        "summary_csv": str(summary_path.relative_to(PROJECT_DIR)),
    }
    (args.output_root / f"evidence-dependence-status-{phase}.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
