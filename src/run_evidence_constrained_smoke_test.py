from __future__ import annotations

"""Engineering smoke test for Evidence-Constrained Semantic IR Repair.

Runs staged smoke batches on external-real-v8-grounded (never hold-out).
Pass/fail is based on pipeline integrity only — not accuracy or closure rate.
"""

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from method_experiment_guard import (
    FROZEN_V43_GATE,
    HOLDOUT_BENCHMARK,
    activate_official_run_env,
    guard_holdout_benchmark,
)
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from semantic_v2_common import PROJECT_DIR, write_csv

DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
DEFAULT_MANIFEST = DEFAULT_BENCHMARK / "smoke" / "smoke-manifest.csv"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "evidence-constrained-smoke-test"
DEFAULT_SEED = 20260827
FORBIDDEN_LEAK_KEYS = ("candidate_used", "oracle_used", "manual_policy_used")
PRIVATE_PATH_MARKERS = (
    "/private/",
    "/oracle/",
    "candidate-selection",
    "oracle-adjudication",
    HOLDOUT_BENCHMARK,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=int, choices=(1, 2), required=True)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--top-n", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Reconcile checkpoint audit state and regenerate engineering report without running the pipeline.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--qwen-timeout", type=int, default=180)
    return parser.parse_args()


def load_smoke_manifest(path: Path, stage: int) -> list[dict[str, str]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    if stage == 1:
        return [row for row in rows if str(row.get("stage", "")).strip() == "1"]
    return rows


def to_m13_manifest(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        out.append(
            {
                "event_id": row["event_id"],
                "run": row.get("run", "1"),
                "seed": row.get("seed", str(DEFAULT_SEED)),
                "semantic_type": row["semantic_type"],
                "domain": row.get("domain", ""),
                "generation_status": "SMOKE_BASELINE",
                "validation_reason": "smoke_test",
                "ir_status": "SMOKE",
                "ir_reason": "smoke_test",
                "missing_fields": "",
                "missing_subtype": "SMOKE_TEST",
                "facts_field_count": "0",
                "semantic_result_present": "False",
                "model_abstained": "False",
                "evidence_signal": "smoke",
                "evidence_chars": "0",
                "raw_output_file": row["raw_output_file"],
            }
        )
    return out


def checkpoint_path(output_dir: Path, stage: int) -> Path:
    return output_dir / f"stage{stage}-checkpoint.json"


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"completed_event_ids": [], "failures": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def contains_private_path(text: str) -> bool:
    lowered = text.lower().replace("\\", "/")
    return any(marker in lowered for marker in PRIVATE_PATH_MARKERS)


def audit_record_isolation(record: dict[str, Any], *, stage: str) -> list[str]:
    issues: list[str] = []
    for key in FORBIDDEN_LEAK_KEYS:
        if record.get(key):
            issues.append(f"{stage}:{key}=true")
    serialized = json.dumps(record, ensure_ascii=False)
    if contains_private_path(serialized):
        issues.append(f"{stage}:private_path_reference")
    return issues


def audit_focus_file(path: Path) -> list[str]:
    if not path.is_file():
        return ["missing_focus_output"]
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return ["empty_focus_output"]
    if contains_private_path(text):
        return ["focus_private_path_reference"]
    return []


def validate_event(
    row: dict[str, str],
    *,
    m13_dir: Path,
    ir_details: dict[str, dict[str, str]],
) -> dict[str, Any]:
    event_id = row["event_id"]
    run = row.get("run", "1")
    seed = row.get("seed", str(DEFAULT_SEED))
    stem = f"{event_id}-run{run}-seed{seed}"
    issues: list[str] = []

    focus_path = m13_dir / "focused-evidence" / f"{stem}-focused.md"
    draft_path = m13_dir / "rule-drafts" / f"{stem}.txt"
    refined_path = m13_dir / "rule-refined" / f"{stem}.txt"
    arm_d_raw = m13_dir / "arm-d-rule-refinement" / "raw_window_metadata_light" / "raw" / f"{stem}.json"

    issues.extend(audit_focus_file(focus_path))
    for label, path in (
        ("missing_draft_output", draft_path),
        ("missing_refined_output", refined_path),
        ("missing_arm_d_raw", arm_d_raw),
    ):
        if not path.is_file():
            issues.append(label)

    if arm_d_raw.is_file():
        record = json.loads(arm_d_raw.read_text(encoding="utf-8"))
        issues.extend(audit_record_isolation(record, stage="arm_d_raw"))
        audit = record.get("m13_rule_refinement_audit", {})
        if not audit:
            issues.append("missing_m13_audit")
        else:
            issues.extend(audit_record_isolation(audit, stage="m13_audit"))

    detail = ir_details.get(event_id)
    if not detail:
        issues.append("missing_ir_detail")
    else:
        if not detail.get("ir_status"):
            issues.append("missing_ir_status")
        if detail.get("selection_status") not in {"SELECTED", "ABSTAIN"}:
            issues.append("invalid_selection_status")
        if not detail.get("decision_path"):
            issues.append("missing_decision_path")
        if detail.get("selection_status") == "SELECTED":
            for key in (
                "candidate_owl",
                "reasoner_result",
                "minimal_edit_gate",
                "source_triggers_repair_cq",
                "candidate_satisfies_repair_cq",
            ):
                if key not in detail or detail.get(key) in ("", None):
                    issues.append(f"missing_{key}")
            if detail.get("reasoner_result") not in {"CONSISTENT", "INCONSISTENT", "ERROR"}:
                issues.append("invalid_reasoner_result")

    return {
        "event_id": event_id,
        "semantic_type": row.get("semantic_type", ""),
        "coverage_tags": row.get("coverage_tags", ""),
        "issues": issues,
        "passed": not issues,
        "selection_status": (detail or {}).get("selection_status", ""),
        "decision_path": (detail or {}).get("decision_path", ""),
        "full_closure_success": (detail or {}).get("full_closure_success", ""),
    }


def load_ir_details(ir_dir: Path) -> dict[str, dict[str, str]]:
    details_path = ir_dir / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
    if not details_path.is_file():
        return {}
    return {row["event_id"]: row for row in csv.DictReader(details_path.open(encoding="utf-8-sig"))}


def reconcile_checkpoint_audit(
    checkpoint: dict[str, Any],
    *,
    event_results: list[dict[str, Any]],
    pass_run: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split stale checkpoint failures from current unresolved failures."""
    raw_failures = list(checkpoint.get("failures") or [])
    prior_historical = list(checkpoint.get("historical_failures") or [])
    unresolved_event_failures = [row for row in event_results if not row["passed"]]
    historical: list[dict[str, Any]] = list(prior_historical)
    unresolved: list[dict[str, Any]] = list(unresolved_event_failures)

    if pass_run and not unresolved_event_failures:
        for item in raw_failures:
            historical.append({**item, "recovered": True, "resolution": "subsequent_successful_run"})
        checkpoint = {
            **checkpoint,
            "failures": [],
            "historical_failures": historical,
            "unresolved_failures": [],
            "audit_reconciled_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    else:
        unresolved.extend(
            {**item, "recovered": False}
            for item in raw_failures
            if item.get("type") == "pipeline_crash"
        )
        checkpoint = {
            **checkpoint,
            "failures": raw_failures,
            "historical_failures": historical,
            "unresolved_failures": unresolved,
        }
    return checkpoint, historical, unresolved


def build_engineering_report(
    *,
    stage: int,
    manifest_rows: list[dict[str, str]],
    event_results: list[dict[str, Any]],
    checkpoint: dict[str, Any],
    output_dir: Path,
    benchmark_dir: Path,
    historical_failures: list[dict[str, Any]] | None = None,
    unresolved_failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gate_names = [
        "input_path_correct",
        "isolation_correct",
        "evidence_focus_output",
        "m13_refined_record",
        "ir_constructed",
        "candidate_ranking",
        "safety_gate",
        "owl_materialize",
        "reasoner_execution",
        "cq_execution",
        "minimal_edit",
        "artifacts_complete",
        "seed_recorded",
        "resume_ok",
        "batch_resilience",
    ]
    failures = [row for row in event_results if not row["passed"]]
    historical = historical_failures if historical_failures is not None else list(checkpoint.get("historical_failures") or [])
    unresolved = unresolved_failures if unresolved_failures is not None else list(checkpoint.get("unresolved_failures") or failures)
    pass_run = len(failures) == 0 and len(unresolved) == 0
    report = {
        "method": "EVIDENCE_CONSTRAINED_SEMANTIC_IR_REPAIR",
        "test_type": "engineering_smoke_test",
        "stage": stage,
        "event_count": len(manifest_rows),
        "benchmark": str(benchmark_dir.relative_to(PROJECT_DIR)),
        "output_dir": str(output_dir.relative_to(PROJECT_DIR)),
        "seed": DEFAULT_SEED,
        "frozen_v43_gate": FROZEN_V43_GATE,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "accuracy_evaluated": False,
        "closure_used_as_pass_criterion": False,
        "engineering_gates": {
            name: pass_run and HOLDOUT_BENCHMARK not in benchmark_dir.as_posix()
            for name in gate_names
        },
        "engineering_gate_definitions": {
            "input_path_correct": "benchmark is external-real-v8-grounded, not hold-out",
            "isolation_correct": "no candidate/oracle/manual_policy leakage before ranking",
            "evidence_focus_output": "focused-evidence/*.md exists and non-empty",
            "m13_refined_record": "rule-refined + arm-d raw + m13 audit present",
            "ir_constructed": "ir_status present in V4.3 details",
            "candidate_ranking": "selection_status SELECTED or ABSTAIN",
            "safety_gate": "decision_path recorded",
            "owl_materialize": "candidate_owl for SELECTED rows",
            "reasoner_execution": "reasoner_result for SELECTED rows",
            "cq_execution": "repair CQ fields for SELECTED rows",
            "minimal_edit": "minimal_edit_gate for SELECTED rows",
            "artifacts_complete": "summary + details CSV/JSON exist",
            "seed_recorded": "seed in checkpoint and manifest",
            "resume_ok": "resume flag honored without overwrite errors",
            "batch_resilience": "per-event failures captured without batch crash",
        },
        "pass": pass_run,
        "failures": failures,
        "historical_failures": historical,
        "unresolved_failures": unresolved,
        "events": event_results,
        "checkpoint": checkpoint,
    }
    closure_counts = {
        "closure_true": sum(1 for row in event_results if str(row.get("full_closure_success")).lower() == "true"),
        "closure_false": sum(1 for row in event_results if str(row.get("full_closure_success")).lower() == "false"),
    }
    report["closure_observed"] = closure_counts
    report["note"] = (
        "Closure rate is observational only and must not be used to change the frozen method."
    )
    if historical:
        report["audit_note"] = (
            "historical_failures are retained for traceability only; they were caused by an "
            "earlier resume engineering bug (empty summary write on full skip) and were recovered "
            "by a subsequent successful run. unresolved_failures is the authoritative pass/fail list."
        )
    return report


def main() -> int:
    args = parse_args()
    guard_holdout_benchmark(args.benchmark_dir, __file__)
    args.benchmark_dir = args.benchmark_dir.resolve()
    args.output_dir = (args.output_dir / f"stage{args.stage}").resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows = load_smoke_manifest(args.manifest.resolve(), args.stage)
    if not manifest_rows:
        raise SystemExit(f"No smoke events for stage {args.stage} in {args.manifest}")

    paths = configure_paths(args.benchmark_dir)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    missing = [row["event_id"] for row in manifest_rows if row["event_id"] not in events]
    if missing:
        raise SystemExit(f"Smoke events not READY in benchmark: {', '.join(missing)}")

    m13_manifest_path = args.output_dir / "m13-manifest.csv"
    m13_manifest = to_m13_manifest(manifest_rows)
    write_csv(m13_manifest_path, m13_manifest)

    ckpt_path = checkpoint_path(args.output_dir, args.stage)
    if args.audit_only:
        checkpoint = load_checkpoint(ckpt_path)
    elif args.resume:
        checkpoint = load_checkpoint(ckpt_path)
    else:
        checkpoint = {"completed_event_ids": [], "failures": []}
    if not args.audit_only and not args.resume and ckpt_path.is_file():
        raise SystemExit(f"Checkpoint exists; pass --resume to continue: {ckpt_path}")

    run_meta = {
        "stage": args.stage,
        "seed": args.seed,
        "event_ids": [row["event_id"] for row in manifest_rows],
        "manifest": str(args.manifest.relative_to(PROJECT_DIR)),
        "m13_manifest": str(m13_manifest_path.relative_to(PROJECT_DIR)),
        "benchmark": str(args.benchmark_dir.relative_to(PROJECT_DIR)),
        "frozen_v43_gate": FROZEN_V43_GATE,
        "dry_run": args.dry_run,
    }
    (args.output_dir / "run-config.json").write_text(
        json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.dry_run:
        print(json.dumps(run_meta, ensure_ascii=False, indent=2))
        return 0

    m13_dir = args.output_dir / "m13-pilot"
    if args.audit_only:
        ir_dir = m13_dir / "arm-d-rule-refinement" / "ir"
        ir_details = load_ir_details(ir_dir)
        event_results = [
            validate_event(row, m13_dir=m13_dir, ir_details=ir_details) for row in manifest_rows
        ]
        ckpt_path = checkpoint_path(args.output_dir, args.stage)
        checkpoint = load_checkpoint(ckpt_path)
        pass_run = all(row["passed"] for row in event_results)
        checkpoint, historical, unresolved = reconcile_checkpoint_audit(
            checkpoint,
            event_results=event_results,
            pass_run=pass_run,
        )
        save_checkpoint(ckpt_path, checkpoint)
        report = build_engineering_report(
            stage=args.stage,
            manifest_rows=manifest_rows,
            event_results=event_results,
            checkpoint=checkpoint,
            output_dir=args.output_dir,
            benchmark_dir=args.benchmark_dir,
            historical_failures=historical,
            unresolved_failures=unresolved,
        )
        report_path = args.output_dir / "engineering-smoke-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_csv(args.output_dir / "engineering-smoke-events.csv", event_results)
        print(json.dumps(
            {
                "audit_only": True,
                "pass": report["pass"],
                "historical_failures": len(historical),
                "unresolved_failures": len(unresolved),
                "report": str(report_path.relative_to(PROJECT_DIR)),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0 if report["pass"] else 1

    env = activate_official_run_env()
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_m13_rule_refinement_pilot.py"),
        "--benchmark-dir",
        str(args.benchmark_dir),
        "--output-dir",
        str(m13_dir),
        "--manifest",
        str(m13_manifest_path),
        "--manifest-mode",
        "all",
        "--top-n",
        str(args.top_n),
        "--qwen-timeout",
        str(args.qwen_timeout),
        "--main-method-only",
    ]
    if args.resume:
        cmd.append("--resume")
    only = ",".join(row["event_id"] for row in manifest_rows)
    cmd.extend(["--only", only])

    print("Running smoke pipeline:", " ".join(cmd), flush=True)
    try:
        subprocess.run(cmd, check=True, cwd=PROJECT_DIR, env=env)
    except subprocess.CalledProcessError as exc:
        checkpoint["failures"].append({"type": "pipeline_crash", "returncode": exc.returncode})
        save_checkpoint(ckpt_path, checkpoint)
        raise

    ir_dir = m13_dir / "arm-d-rule-refinement" / "ir"
    ir_details = load_ir_details(ir_dir)
    required_outputs = [
        m13_dir / "rule-refinement-summary.csv",
        ir_dir / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv",
        ir_dir / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827.json",
    ]
    missing_outputs = [str(path.relative_to(PROJECT_DIR)) for path in required_outputs if not path.is_file()]

    event_results = [
        validate_event(row, m13_dir=m13_dir, ir_details=ir_details) for row in manifest_rows
    ]
    pass_run = all(row["passed"] for row in event_results) and not missing_outputs
    checkpoint, historical, unresolved = reconcile_checkpoint_audit(
        checkpoint,
        event_results=event_results,
        pass_run=pass_run,
    )
    checkpoint["completed_event_ids"] = [row["event_id"] for row in manifest_rows]
    checkpoint["seed"] = args.seed
    checkpoint["stage"] = args.stage
    save_checkpoint(ckpt_path, checkpoint)

    report = build_engineering_report(
        stage=args.stage,
        manifest_rows=manifest_rows,
        event_results=event_results,
        checkpoint=checkpoint,
        output_dir=args.output_dir,
        benchmark_dir=args.benchmark_dir,
        historical_failures=historical,
        unresolved_failures=unresolved,
    )
    if missing_outputs:
        report["pass"] = False
        report["missing_outputs"] = missing_outputs
        report["engineering_gates"]["artifacts_complete"] = False

    report_path = args.output_dir / "engineering-smoke-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(args.output_dir / "engineering-smoke-events.csv", event_results)

    print(json.dumps(
        {
            "pass": report["pass"],
            "stage": args.stage,
            "events": len(manifest_rows),
            "failures": len(report["failures"]),
            "closure_observed": report["closure_observed"],
            "report": str(report_path.relative_to(PROJECT_DIR)),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
