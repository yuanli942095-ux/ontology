from __future__ import annotations

"""Formal blind evaluation orchestrator for external-real-holdout-v1-expanded.

Steps:
  validate  — read-only freeze checks
  prepare   — build 236×5 manifest + public input stubs
  env       — record experiment environment
  run-main  — EVIDENCE_CONSTRAINED_SEMANTIC_IR_REPAIR (236×5)
  run-baselines — legacy ablation baselines (optional)
  analyze   — main table + paired significance + failure analysis
  all       — validate → prepare → env (does not auto-run model)
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from formal_holdout_eval_guard import validate_pre_run_gates
from semantic_v2_common import PROJECT_DIR

DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded" / "final-blind-eval-r5"
HOLDOUT_FREEZE = DEFAULT_OUTPUT.parent / "external-real-holdout-v1-freeze-manifest.json"

BASELINE_METHODS_AUTO = (
    "DIRECT_FREE",
    "OPTION_DESCRIPTION",
    "OPTION_VALUE_ONLY",
    "OPTION_FORMAL_OPERATION",
)
BASELINE_METHODS_PRIVILEGED = ("OPTION_FORMAL_POLICY",)
BASELINE_METHODS_UPPER = ("OPTION_FORMAL_POLICY_HARD_GATE",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step",
        choices=("validate", "prepare", "env", "run-main", "run-baselines", "analyze", "all"),
        default="validate",
    )
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--qwen-timeout", type=int, default=180)
    parser.add_argument("--top-n", type=int, default=4)
    parser.add_argument(
        "--baseline-methods",
        default=",".join(BASELINE_METHODS_AUTO),
        help="comma-separated baseline methods for run-baselines",
    )
    return parser.parse_args()


def run_py(script: str, extra: list[str]) -> None:
    cmd = [sys.executable, str(PROJECT_DIR / "src" / script), *extra]
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR)


def checkpoint_path(output_dir: Path) -> Path:
    return output_dir / "formal-eval-checkpoint.json"


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"completed_steps": [], "failures": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def step_validate(args: argparse.Namespace) -> dict[str, Any]:
    return validate_pre_run_gates()


def step_prepare(args: argparse.Namespace) -> None:
    run_py(
        "build_formal_holdout_manifest.py",
        ["--benchmark-dir", str(args.benchmark_dir), "--output-dir", str(args.output_dir)],
    )


def step_env(args: argparse.Namespace) -> None:
    run_py("record_experiment_environment.py", ["--output-dir", str(args.output_dir)])


def step_run_main(args: argparse.Namespace) -> None:
    manifest = args.output_dir / "formal-eval-manifest-r5.csv"
    if not manifest.is_file():
        raise SystemExit(f"missing manifest; run --step prepare first: {manifest}")
    m13_out = args.output_dir / "main-method" / "m13-pilot"
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_m13_rule_refinement_pilot.py"),
        "--benchmark-dir",
        str(args.benchmark_dir),
        "--output-dir",
        str(m13_out),
        "--manifest",
        str(manifest),
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
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = "src"
    env["ONTOLOGY_EVOLUTION_OFFICIAL_RUN"] = "1"
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR, env=env)


def step_run_baselines(args: argparse.Namespace) -> None:
    rules_dir = args.benchmark_dir / "rules"
    methods = [m.strip() for m in args.baseline_methods.split(",") if m.strip()]
    needs_rules = [m for m in methods if m in BASELINE_METHODS_PRIVILEGED or m in BASELINE_METHODS_UPPER]
    if needs_rules and not rules_dir.is_dir():
        raise SystemExit(
            f"hold-out has no rules/ directory; cannot run privileged/upper-bound baselines: {', '.join(needs_rules)}"
        )
    prefix = "external-real-holdout-v1-ablation-r5"
    out = args.output_dir / "baselines"
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_external_real_v1_candidate_ablation.py"),
        "--allow-legacy-experiment",
        "--benchmark-dir",
        str(args.benchmark_dir),
        "--benchmark-name",
        "external-real-holdout-v1-expanded",
        "--freeze-manifest",
        str(HOLDOUT_FREEZE),
        "--runs",
        "5",
        "--seed",
        "20260829",
        "--methods",
        ",".join(methods),
        "--prefix",
        prefix,
        "--output-dir",
        str(out),
    ]
    if args.resume:
        cmd.append("--resume")
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = "src"
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR, env=env)


def step_analyze(args: argparse.Namespace) -> None:
    run_py(
        "analyze_external_real_holdout_main_table.py",
        ["--benchmark-dir", str(args.benchmark_dir), "--output-dir", str(args.output_dir)],
    )


def main() -> int:
    args = parse_args()
    args.benchmark_dir = args.benchmark_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    ckpt = load_checkpoint(checkpoint_path(args.output_dir))
    steps = {
        "validate": step_validate,
        "prepare": step_prepare,
        "env": step_env,
        "run-main": step_run_main,
        "run-baselines": step_run_baselines,
        "analyze": step_analyze,
    }

    if args.step == "all":
        sequence = ("validate", "prepare", "env")
    else:
        sequence = (args.step,)

    log: list[dict[str, Any]] = []
    for name in sequence:
        if args.resume and name in ckpt.get("completed_steps", []):
            print(f"[resume] skip completed step: {name}", flush=True)
            continue
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = steps[name](args)
            log.append({"step": name, "status": "ok", "started_at_utc": started, "result": result})
            ckpt.setdefault("completed_steps", []).append(name)
        except subprocess.CalledProcessError as exc:
            failure = {
                "step": name,
                "type": "pipeline_crash",
                "returncode": exc.returncode,
                "started_at_utc": started,
            }
            ckpt.setdefault("failures", []).append(failure)
            save_checkpoint(checkpoint_path(args.output_dir), ckpt)
            raise
        save_checkpoint(checkpoint_path(args.output_dir), ckpt)

    summary = {
        "phase": "FINAL_BLIND_EVALUATION",
        "steps": sequence,
        "output_dir": str(args.output_dir.relative_to(PROJECT_DIR)),
        "log": log,
        "checkpoint": ckpt,
    }
    (args.output_dir / "formal-eval-orchestrator-log.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
