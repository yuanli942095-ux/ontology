from __future__ import annotations

"""Read-only pre-run guards for external-real-holdout-v3-large M16 blind evaluation."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, sha256_file

HOLDOUT_BENCHMARK = "external-real-holdout-v3-large"
HOLDOUT_FREEZE_MANIFEST = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "external-real-holdout-v3-large-freeze-manifest.json"
)
M16_METHOD_FREEZE_MANIFEST = (
    PROJECT_DIR / "output" / "m16-full-repair-method-freeze" / "m16-full-repair-method-freeze-manifest.json"
)
M16_METHOD_FREEZE_TYPED_MANIFEST = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v4-blind"
    / "final-main-result-lock-20260830"
    / "m16-full-repair-method-freeze-typed-gre-css-024-manifest.json"
)

EXPECTED_HOLDOUT_MANIFEST_SHA256 = "b9c18a13f92440bbb157a9261ccf31b6c6d27341e2946f593bdb8f2ef77d56f2"
EXPECTED_M16_METHOD_MANIFEST_SHA256 = "6843c041f93d7143729c99a6462ee1c0082e47b869e87a2d24a977b01932e5ff"
EXPECTED_M16_METHOD_TYPED_MANIFEST_SHA256 = "9739bb081e84f9ea2c6ad435840ce87aa756a29936aa3cae5cb1ea50386b2d14"
EXPECTED_METHOD_GIT_COMMIT = "87787f602ed579bf87451c06a0d2c28973026d2a"

FORMAL_HOLDOUT_SEEDS = (20260829, 20260830, 20260831, 20260901, 20260902)
FORMAL_HOLDOUT_RUNS = 5
FORMAL_HOLDOUT_EVENTS = 220


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
        "benchmark": payload.get("benchmark"),
    }


def validate_m16_method_freeze(
    path: Path | None = None,
    *,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    manifest_path = path or M16_METHOD_FREEZE_MANIFEST
    expected_sha = expected_manifest_sha256 or EXPECTED_M16_METHOD_MANIFEST_SHA256
    if not manifest_path.is_file():
        raise SystemExit(f"M16 method freeze manifest missing: {manifest_path}")
    payload = load_json(manifest_path)
    freeze_status = str(payload.get("freeze_status", "")).strip().upper()
    changes_forbidden = bool(payload.get("new_holdout_policy", {}).get("method_changes_forbidden"))
    git_expected = str(payload.get("git_commit", EXPECTED_METHOD_GIT_COMMIT)).strip()
    git_actual = current_git_commit()
    manifest_sha = str(payload.get("manifest_sha256", "")).strip()
    if not manifest_sha:
        manifest_sha = sha256_file(manifest_path)
    return {
        "checks": [
            {
                "check": "m16_method_freeze_status",
                "expected": "FROZEN_FOR_NEW_HOLDOUT_EVALUATION",
                "actual": freeze_status,
                "pass": freeze_status == "FROZEN_FOR_NEW_HOLDOUT_EVALUATION",
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
                "check": "m16_method_manifest_sha256",
                "expected": expected_sha,
                "actual": manifest_sha,
                "pass": manifest_sha == expected_sha,
            },
        ],
        "path": str(manifest_path.relative_to(PROJECT_DIR)),
        "method_name": payload.get("method_name"),
        "pass": freeze_status == "FROZEN_FOR_NEW_HOLDOUT_EVALUATION"
        and changes_forbidden is True
        and git_actual == git_expected
        and manifest_sha == expected_sha,
    }


def validate_pre_run_gates(
    *,
    exit_on_fail: bool = True,
    holdout_manifest: Path | None = None,
    method_manifest: Path | None = None,
    expected_method_manifest_sha256: str | None = None,
    phase: str = "FINAL_BLIND_EVALUATION_V3_LARGE",
) -> dict[str, Any]:
    holdout = validate_holdout_freeze_manifest(holdout_manifest)
    method = validate_m16_method_freeze(
        method_manifest,
        expected_manifest_sha256=expected_method_manifest_sha256,
    )
    report = {
        "phase": phase,
        "holdout_freeze": holdout,
        "m16_method_freeze": method,
        "pass": holdout["pass"] and method["pass"],
        "events": FORMAL_HOLDOUT_EVENTS,
        "runs": FORMAL_HOLDOUT_RUNS,
        "seeds": list(FORMAL_HOLDOUT_SEEDS),
    }
    if exit_on_fail and not report["pass"]:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit("pre-run freeze validation failed; aborting v3 blind evaluation")
    return report
