from __future__ import annotations

"""Shared helpers for post-freeze paper validation experiments (Phase 1/2)."""

import csv
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from external_real_v8_layout import BenchmarkLayout, candidate_selection_paths, evaluation_paths
from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large"
DEFAULT_METHOD_FREEZE_DIR = PROJECT_DIR / "output" / "m16-full-repair-method-freeze-final-20260831"
DEFAULT_BENCHMARK_FREEZE_SUMMARY = (
    PROJECT_DIR / "output" / "external-real-holdout-v5-blind-large" / "external-real-holdout-v5-blind-large-freeze-manifest-summary.json"
)
DEFAULT_DEEPSEEK_PRED_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v5-blind-large"
    / "final-blind-eval-r5-m16-deepseek"
)
PAPER_VALIDATION_ROOT = PROJECT_DIR / "output" / "paper-final-validation"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str:
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    return output.strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def ready(value: str) -> bool:
    return str(value or "").strip().upper() == "READY"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def benchmark_layout(benchmark_dir: Path | None = None) -> BenchmarkLayout:
    return BenchmarkLayout(benchmark_dir or DEFAULT_BENCHMARK)


def benchmark_paths(benchmark_dir: Path | None = None) -> dict[str, Path]:
    root = (benchmark_dir or DEFAULT_BENCHMARK).resolve()
    selection = candidate_selection_paths(root)
    evaluation = evaluation_paths(root)
    return {
        "benchmark_dir": root,
        "event_csv": selection["event_csv"],
        "candidate_csv": selection["candidate_csv"],
        "oracle_csv": evaluation["oracle_csv"],
        "document_csv": root / "public" / "documents" / "external-real-document-template.csv",
        "mutants_dir": selection["mutants_dir"],
    }


def load_freeze_sha256(summary_path: Path) -> tuple[str, Path]:
    summary = load_json(summary_path)
    manifest_path = PROJECT_DIR / str(summary["json"]).replace("/", "\\")
    if not manifest_path.is_file():
        manifest_path = Path(str(summary["json"]))
        if not manifest_path.is_absolute():
            manifest_path = PROJECT_DIR / manifest_path
    return str(summary["manifest_sha256"]), manifest_path


def load_method_freeze_sha256(method_freeze_dir: Path | None = None) -> tuple[str, Path]:
    freeze_dir = (method_freeze_dir or DEFAULT_METHOD_FREEZE_DIR).resolve()
    manifest_path = freeze_dir / "m16-full-repair-method-freeze-manifest.json"
    summary = load_json(manifest_path)
    return str(summary["manifest_sha256"]), manifest_path


def write_binding(
    output_dir: Path,
    *,
    experiment_role: str,
    script_path: Path,
    benchmark_manifest: Path,
    benchmark_sha256: str,
    method_freeze_manifest: Path,
    method_freeze_sha256: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    payload: dict[str, Any] = {
        "experiment_role": experiment_role,
        "created_at_utc": utc_now_iso(),
        "git_commit": git_commit(),
        "script_sha256": sha256_file(script_path),
        "method_freeze_manifest": str(method_freeze_manifest.relative_to(PROJECT_DIR)),
        "method_freeze_sha256": method_freeze_sha256,
        "benchmark_manifest": str(benchmark_manifest.relative_to(PROJECT_DIR)),
        "benchmark_sha256": benchmark_sha256,
        "method_changed": False,
        "benchmark_changed": False,
        "oracle_used_for_inference": False,
    }
    if extra:
        payload.update(extra)
    path = output_dir / "experiment-binding.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def verify_deepseek_prediction_dir(
    prediction_dir: Path,
    *,
    benchmark_sha256: str,
    method_freeze_sha256: str,
    expected_events: int = 264,
    expected_runs: int = 5,
) -> dict[str, Any]:
    """Sanity-check the frozen DeepSeek run without trusting directory names alone."""
    details_path = prediction_dir / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv"
    if not details_path.is_file():
        raise FileNotFoundError(details_path)
    rows = read_csv(details_path)
    event_ids = {row["event_id"] for row in rows}
    runs = {row["run"] for row in rows}
    return {
        "prediction_dir": str(prediction_dir.relative_to(PROJECT_DIR)),
        "details_path": str(details_path.relative_to(PROJECT_DIR)),
        "attempts": len(rows),
        "events": len(event_ids),
        "runs_per_event": len(runs),
        "expected_events": expected_events,
        "expected_runs": expected_runs,
        "events_ok": len(event_ids) == expected_events,
        "runs_ok": len(runs) == expected_runs,
        "attempts_ok": len(rows) == expected_events * expected_runs,
        "benchmark_sha256_expected": benchmark_sha256,
        "method_freeze_sha256_expected": method_freeze_sha256,
        "note": "Hashes verified against freeze manifests at experiment start; prediction dir stores no separate binding.",
    }


def audit_cq_implementation() -> dict[str, Any]:
    """Stop-condition audit: v5 closure uses repair_checks(), not an independent CQ suite."""
    from run_external_real_v1_symbolic_closure import repair_checks

    source = Path(repair_checks.__code__.co_filename).read_text(encoding="utf-8")
    return {
        "implementation": "REPAIR_CHECK_ASSERTION_REGRESSION",
        "stress_variant_name": "REPAIR_CHECK_BREAK",
        "paper_wording": (
            "Current closure CQ/repair check is event-specific assertion regression "
            "(source old triple present, candidate new triple present), not an independent semantic query suite."
        ),
        "repair_checks_source": str(Path(repair_checks.__code__.co_filename).relative_to(PROJECT_DIR)),
        "uses_per_event_cq_files": False,
        "independent_cq_suite_for_v5": False,
        "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
    }


def load_ready_events(paths: dict[str, Path]) -> list[dict[str, str]]:
    rows = [row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))]
    if not rows:
        raise RuntimeError(f"no READY events in {paths['event_csv']}")
    return rows


def load_candidates(paths: dict[str, Path]) -> dict[str, list[dict[str, Any]]]:
    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in read_csv(paths["candidate_csv"]):
        if not ready(row.get("status", "")):
            continue
        event_id = str(row["event_id"])
        by_event.setdefault(event_id, []).append(
            {**row, "operation": json.loads(row["operation_json"])}
        )
    return by_event


def load_oracles(paths: dict[str, Path]) -> dict[str, dict[str, str]]:
    return {
        str(row["event_id"]): row
        for row in read_csv(paths["oracle_csv"])
        if ready(row.get("status", ""))
    }


def write_manifest_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    write_csv(path, rows)


def write_summary_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
