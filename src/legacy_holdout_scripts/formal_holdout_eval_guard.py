from __future__ import annotations

"""Read-only pre-run guards for final blind hold-out evaluation."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, sha256_file

HOLDOUT_BENCHMARK = "external-real-holdout-v1-expanded"
HOLDOUT_FREEZE_MANIFEST = (
    PROJECT_DIR / "output" / "external-real-holdout-v1-expanded" / "external-real-holdout-v1-freeze-manifest.json"
)
FINAL_METHOD_FREEZE_MANIFEST = PROJECT_DIR / "output" / "final-method-freeze" / "final-method-freeze-manifest.json"

EXPECTED_HOLDOUT_MANIFEST_SHA256 = "7ce6b7f2589ebf1893d41d61a864bb832879468ecec662f4ed3029238c0fca30"
EXPECTED_METHOD_GIT_COMMIT = "87787f602ed579bf87451c06a0d2c28973026d2a"

FORMAL_HOLDOUT_SEEDS = (20260829, 20260830, 20260831, 20260901, 20260902)
FORMAL_HOLDOUT_RUNS = 5
FORMAL_HOLDOUT_EVENTS = 236


def current_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return ""


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_holdout_freeze_manifest(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or HOLDOUT_FREEZE_MANIFEST
    if not manifest_path.is_file():
        raise SystemExit(f"hold-out freeze manifest missing: {manifest_path}")
    payload = load_json(manifest_path)
    actual = str(payload.get("manifest_sha256", "")).strip()
    if not actual:
        actual = sha256_file(manifest_path)
    ok = actual == EXPECTED_HOLDOUT_MANIFEST_SHA256
    return {
        "check": "benchmark_manifest_sha256",
        "path": str(manifest_path.relative_to(PROJECT_DIR)),
        "expected": EXPECTED_HOLDOUT_MANIFEST_SHA256,
        "actual": actual,
        "pass": ok,
        "events_ready": payload.get("events_ready"),
    }


def validate_final_method_freeze(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or FINAL_METHOD_FREEZE_MANIFEST
    if not manifest_path.is_file():
        raise SystemExit(f"final method freeze manifest missing: {manifest_path}")
    payload = load_json(manifest_path)
    freeze_status = str(payload.get("freeze_status", "")).strip().upper()
    changes_forbidden = bool(payload.get("post_freeze_policy", {}).get("method_changes_forbidden"))
    git_expected = str(payload.get("git_commit", EXPECTED_METHOD_GIT_COMMIT)).strip()
    git_actual = current_git_commit()
    holdout_sha = str(payload.get("benchmarks", {}).get("benchmark_holdout_manifest_sha256", "")).strip()
    return {
        "checks": [
            {
                "check": "method_freeze_status",
                "expected": "FROZEN",
                "actual": freeze_status,
                "pass": freeze_status == "FROZEN",
            },
            {
                "check": "method_changes_forbidden",
                "expected": True,
                "actual": changes_forbidden,
                "pass": changes_forbidden is True,
            },
            {
                "check": "git_commit",
                "expected": git_expected,
                "actual": git_actual,
                "pass": git_actual == git_expected,
            },
            {
                "check": "benchmark_holdout_manifest_sha256",
                "expected": EXPECTED_HOLDOUT_MANIFEST_SHA256,
                "actual": holdout_sha,
                "pass": holdout_sha == EXPECTED_HOLDOUT_MANIFEST_SHA256,
            },
        ],
        "path": str(manifest_path.relative_to(PROJECT_DIR)),
        "method_name": payload.get("method_name"),
        "pass": freeze_status == "FROZEN"
        and changes_forbidden is True
        and git_actual == git_expected
        and holdout_sha == EXPECTED_HOLDOUT_MANIFEST_SHA256,
    }


def validate_pre_run_gates(*, exit_on_fail: bool = True) -> dict[str, Any]:
    holdout = validate_holdout_freeze_manifest()
    method = validate_final_method_freeze()
    report = {
        "phase": "FINAL_BLIND_EVALUATION",
        "holdout_freeze": holdout,
        "method_freeze": method,
        "pass": holdout["pass"] and method["pass"],
    }
    if exit_on_fail and not report["pass"]:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit("pre-run freeze validation failed; aborting blind evaluation")
    return report
