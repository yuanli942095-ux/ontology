from __future__ import annotations

"""Record experiment environment snapshot for formal blind evaluation."""

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from formal_holdout_eval_guard import current_git_commit, validate_pre_run_gates
from semantic_v2_common import PROJECT_DIR

DEFAULT_OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded" / "final-blind-eval-r5"


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def try_command(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, cwd=PROJECT_DIR).strip()
    except Exception as exc:
        return f"ERROR:{type(exc).__name__}:{exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-guard", action="store_true")
    args = parser.parse_args()

    freeze_report = None if args.skip_guard else validate_pre_run_gates(exit_on_fail=False)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "phase": "FINAL_BLIND_EVALUATION",
        "python_version": sys.version,
        "platform": platform.platform(),
        "git_commit": current_git_commit(),
        "ollama_version": try_command(["ollama", "--version"]),
        "ollama_models": try_command(["ollama", "list"]),
        "configured_model": v3.MODEL,
        "ollama_url": v3.OLLAMA_URL,
        "freeze_validation": freeze_report,
    }
    out = args.output_dir / "experiment-environment.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": relpath(out), "git_commit": payload["git_commit"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
