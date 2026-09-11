from __future__ import annotations

import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from method_experiment_guard import (
    FROZEN_V43_GATE,
    LEGACY_FLAG,
    block_legacy_entrypoint,
    guard_frozen_v43_config,
    legacy_allowed,
)

PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
PYTHON = sys.executable


def run_script(script: str, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["PYTHONPATH"] = str(SRC)
    if env:
        merged.update(env)
    return subprocess.run(
        [PYTHON, str(SRC / script), *extra],
        cwd=PROJECT,
        env=merged,
        capture_output=True,
        text=True,
    )


def test_legacy_baseline_blocked_by_default():
    proc = run_script("run_baseline_direct.py")
    assert proc.returncode == 2
    assert "Blocked legacy experiment script" in proc.stderr


def test_legacy_baseline_allowed_with_flag():
    proc = run_script("run_baseline_direct.py", LEGACY_FLAG, "--help")
    assert proc.returncode == 0


def test_v4_blocks_non_frozen_gate():
    args = Namespace(
        min_score=0.42,
        min_margin=0.08,
        reranker="lexical",
        temporal_unique_top1=False,
        robust_ir=True,
    )
    try:
        guard_frozen_v43_config(args, script="run_auto_policy_v4_ir_candidate_repair.py")
        raise AssertionError("expected SystemExit")
    except SystemExit as exc:
        assert exc.code == 2


def test_block_legacy_entrypoint_respects_flag():
    try:
        block_legacy_entrypoint("run_baseline_direct.py")
        raise AssertionError("expected SystemExit")
    except SystemExit as exc:
        assert exc.code == 2
    block_legacy_entrypoint("run_baseline_direct.py", argv=[LEGACY_FLAG])


def test_v4_allows_frozen_gate():
    guard_frozen_v43_config(Namespace(**FROZEN_V43_GATE), script="run_auto_policy_v4_ir_candidate_repair.py")


if __name__ == "__main__":
    test_legacy_baseline_blocked_by_default()
    test_legacy_baseline_allowed_with_flag()
    test_v4_blocks_non_frozen_gate()
    test_v4_allows_frozen_gate()
    test_block_legacy_entrypoint_respects_flag()
    test_official_runner_dry_run()
    print("ok")
