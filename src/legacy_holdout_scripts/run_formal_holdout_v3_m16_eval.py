from __future__ import annotations

"""Formal blind evaluation for external-real-holdout-v3-large using frozen M16 pipeline.

Steps:
  validate     — hold-out + M16 method freeze checks
  prepare      — build 220×5 manifest + public input stubs
  env          — record experiment environment
  run-m13      — M13 rule refinement + V4 IR (DeepSeek API)
  run-m14      — M14 GRE/CSS recovery on base failures
  combine-m14  — merge M13 base + M14 recovery
  run-m15      — M15 temporal anchor recovery
  combine-m15  — merge temporal recovery
  run-m16      — M16 candidate entailment verifier
  combine-m16  — final combined metrics
  all          — validate → prepare → env (does not auto-run model)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from combine_holdout_pipeline_details import M13_IR_PREFIX, M14_IR_PREFIX, M15_IR_PREFIX
from formal_holdout_v3_eval_guard import (
    EXPECTED_M16_METHOD_TYPED_MANIFEST_SHA256,
    FORMAL_HOLDOUT_EVENTS,
    M16_METHOD_FREEZE_TYPED_MANIFEST,
    validate_pre_run_gates,
)
from semantic_v2_common import PROJECT_DIR

DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v3-large"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v3-large" / "final-blind-eval-r5-m16-deepseek"
DEFAULT_TYPED_OUTPUT = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "final-blind-eval-r5-m16-deepseek-typed-gre-css-024"
)
DEFAULT_OLLAMA_TYPED_OUTPUT = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "final-blind-eval-r5-m16-ollama-typed-gre-css-024"
)
DEFAULT_TYPED_METHOD_MANIFEST = M16_METHOD_FREEZE_TYPED_MANIFEST
M13_IR_DETAILS = f"{M13_IR_PREFIX}-details.csv"
M14_IR_DETAILS = f"{M14_IR_PREFIX}-details.csv"
M15_IR_DETAILS = f"{M15_IR_PREFIX}-details.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step",
        choices=(
            "validate",
            "prepare",
            "env",
            "run-m13",
            "run-m14",
            "combine-m14",
            "run-m15",
            "combine-m15",
            "run-m16",
            "combine-m16",
            "seed-m13-raw",
            "replay-m13-ir",
            "all",
        ),
        default="validate",
    )
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--method-freeze-manifest", type=Path, default=None)
    parser.add_argument(
        "--expected-method-manifest-sha256",
        default="",
        help="optional override; defaults from --calibrated preset when set",
    )
    parser.add_argument(
        "--calibrated",
        action="store_true",
        help="use typed GRE/CSS 0.24 method freeze and default typed output dir",
    )
    parser.add_argument(
        "--reuse-m13-from",
        type=Path,
        default=None,
        help="copy frozen M13 arm-d raw/focus artifacts from a prior v3 run before IR replay",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--qwen-timeout", type=int, default=180)
    parser.add_argument("--top-n", type=int, default=4)
    parser.add_argument(
        "--llm-backend",
        choices=("ollama", "deepseek_api"),
        default="deepseek_api",
        help="ollama uses local qwen3.5:9b; deepseek_api is the frozen M16 method backend",
    )
    parser.add_argument("--only", default="", help="comma-separated event ids for smoke/subset runs")
    return parser.parse_args()


def run_py(script: str, extra: list[str]) -> None:
    cmd = [sys.executable, str(PROJECT_DIR / "src" / script), *extra]
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR, env=official_env())


def official_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env["ONTOLOGY_EVOLUTION_OFFICIAL_RUN"] = "1"
    return env


def checkpoint_path(output_dir: Path) -> Path:
    return output_dir / "formal-eval-v3-checkpoint.json"


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"completed_steps": [], "failures": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def m13_root(output_dir: Path) -> Path:
    return output_dir / "main-method" / "m13-pilot"


def m13_details(output_dir: Path) -> Path:
    return m13_root(output_dir) / "arm-d-rule-refinement" / "ir" / M13_IR_DETAILS


def m14_root(output_dir: Path) -> Path:
    return output_dir / "m14-clause-level-gre-css-recovery"


def m14_details(output_dir: Path) -> Path:
    return m14_root(output_dir) / "ir" / M14_IR_DETAILS


def m15_root(output_dir: Path) -> Path:
    return output_dir / "m15-temporal-anchor-recovery"


def m15_details(output_dir: Path) -> Path:
    return m15_root(output_dir) / "ir" / M15_IR_DETAILS


def m16_root(output_dir: Path) -> Path:
    return output_dir / "m16-candidate-entailment"


def combine_root(output_dir: Path, stage: str) -> Path:
    return output_dir / f"{stage}-full-holdout-combined"


def only_clause(args: argparse.Namespace) -> list[str]:
    if not args.only.strip():
        return []
    return ["--only", args.only.strip()]


def llm_backend_args(args: argparse.Namespace) -> list[str]:
    return ["--llm-backend", args.llm_backend]


def step_validate(args: argparse.Namespace) -> dict[str, Any]:
    if args.llm_backend == "ollama":
        import run_auto_formal_policy_batch_v3 as v3
        from formal_holdout_v3_eval_guard import validate_holdout_freeze_manifest
        from semantic_v2_common import check_ollama

        check_ollama(v3.OLLAMA_URL.replace("/api/generate", ""), v3.MODEL, args.qwen_timeout)
        return {
            "backend": "ollama",
            "model": v3.MODEL,
            "holdout": validate_holdout_freeze_manifest(),
            "note": "backend substitution; method freeze not enforced for ollama runs",
        }
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise SystemExit("DEEPSEEK_API_KEY is not set")
    return validate_pre_run_gates(
        method_manifest=args.method_freeze_manifest,
        expected_method_manifest_sha256=args.expected_method_manifest_sha256 or None,
        phase="FINAL_BLIND_EVALUATION_V3_LARGE_TYPED_REPLAY"
        if args.calibrated
        else "FINAL_BLIND_EVALUATION_V3_LARGE",
    )


def seed_m13_raw(source_run: Path, output_dir: Path) -> dict[str, Any]:
    pairs = (
        (
            source_run / "main-method" / "m13-pilot" / "arm-d-rule-refinement" / "raw_window_metadata_light" / "raw",
            m13_root(output_dir) / "arm-d-rule-refinement" / "raw_window_metadata_light" / "raw",
        ),
        (
            source_run / "main-method" / "m13-pilot" / "focused-evidence",
            m13_root(output_dir) / "focused-evidence",
        ),
        (
            source_run / "main-method" / "m13-pilot" / "rule-drafts",
            m13_root(output_dir) / "rule-drafts",
        ),
        (
            source_run / "main-method" / "m13-pilot" / "rule-refined",
            m13_root(output_dir) / "rule-refined",
        ),
        (
            source_run / "main-method" / "m13-pilot" / "rule-refinement-summary.csv",
            m13_root(output_dir) / "rule-refinement-summary.csv",
        ),
    )
    copied = 0
    for src, dst in pairs:
        if not src.exists():
            if src.name.endswith(".csv"):
                continue
            raise SystemExit(f"missing reuse source path: {src}")
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst)
            copied += sum(1 for path in dst.rglob("*") if path.is_file())
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
    return {"source_run": str(source_run), "files_copied": copied}


def step_replay_m13_ir(args: argparse.Namespace) -> None:
    if args.reuse_m13_from:
        seed_m13_raw(args.reuse_m13_from.resolve(), args.output_dir)
    manifest = args.output_dir / "formal-eval-manifest-r5.csv"
    if not manifest.is_file():
        raise SystemExit(f"missing manifest: {manifest}")
    import csv

    event_ids = sorted({row["event_id"] for row in csv.DictReader(manifest.open(encoding="utf-8-sig"))})
    if args.only.strip():
        allowed = {token.strip().upper() for token in args.only.split(",") if token.strip()}
        event_ids = [event_id for event_id in event_ids if event_id.upper() in allowed]
    raw_dir = m13_root(args.output_dir) / "arm-d-rule-refinement" / "raw_window_metadata_light" / "raw"
    ir_dir = m13_root(args.output_dir) / "arm-d-rule-refinement" / "ir"
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_auto_policy_v4_ir_candidate_repair.py"),
        "--benchmark-dir",
        str(args.benchmark_dir),
        "--raw-dir",
        str(raw_dir),
        "--output-dir",
        str(ir_dir),
        "--prefix",
        M13_IR_PREFIX,
        "--method-name",
        "M13_Regulatory_Rule_Refinement",
        "--min-score",
        "0.30",
        "--gre-css-min-score",
        "0.24",
        "--min-margin",
        "0.00",
        "--reranker",
        "constraint",
        "--temporal-unique-top1",
        "--robust-ir",
        "--skip-missing-raw",
        "--discover-raw",
        "--only",
        ",".join(event_ids),
        "--allow-legacy-experiment",
    ]
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR, env=official_env())


def step_seed_m13_raw(args: argparse.Namespace) -> dict[str, Any]:
    if not args.reuse_m13_from:
        raise SystemExit("--reuse-m13-from is required for seed-m13-raw")
    return seed_m13_raw(args.reuse_m13_from.resolve(), args.output_dir)


def step_prepare(args: argparse.Namespace) -> None:
    run_py(
        "build_formal_holdout_manifest.py",
        [
            "--benchmark-dir",
            str(args.benchmark_dir),
            "--output-dir",
            str(args.output_dir),
            "--expected-events",
            str(FORMAL_HOLDOUT_EVENTS),
            "--skip-guard",
        ],
    )


def step_env(args: argparse.Namespace) -> None:
    run_py("record_experiment_environment.py", ["--output-dir", str(args.output_dir)])


def step_run_m13(args: argparse.Namespace) -> None:
    if args.reuse_m13_from:
        seed_m13_raw(args.reuse_m13_from.resolve(), args.output_dir)
    manifest = args.output_dir / "formal-eval-manifest-r5.csv"
    if not manifest.is_file():
        raise SystemExit(f"missing manifest; run --step prepare first: {manifest}")
    out = m13_root(args.output_dir)
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_m13_rule_refinement_pilot.py"),
        "--benchmark-dir",
        str(args.benchmark_dir),
        "--output-dir",
        str(out),
        "--manifest",
        str(manifest),
        "--manifest-mode",
        "all",
        "--top-n",
        str(args.top_n),
        "--qwen-timeout",
        str(args.qwen_timeout),
        "--main-method-only",
        "--llm-backend",
        args.llm_backend,
    ]
    cmd.extend(only_clause(args))
    if args.resume:
        cmd.append("--resume")
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR, env=official_env())


def step_run_m14(args: argparse.Namespace) -> None:
    base = m13_details(args.output_dir)
    if not base.is_file():
        raise SystemExit(f"missing M13 details: {base}")
    run_py(
        "run_m14_clause_level_gre_css_recovery.py",
        [
            "--benchmark-dir",
            str(args.benchmark_dir),
            "--details",
            str(base),
            "--output-dir",
            str(m14_root(args.output_dir)),
            "--limit",
            "0",
            "--top-n",
            str(args.top_n),
            "--timeout",
            str(args.qwen_timeout),
            *llm_backend_args(args),
            *only_clause(args),
            *(["--resume"] if args.resume else []),
        ],
    )


def step_combine_m14(args: argparse.Namespace) -> None:
    out = combine_root(args.output_dir, "m14")
    run_py(
        "combine_holdout_pipeline_details.py",
        [
            "--stage",
            "m14",
            "--output-dir",
            str(out),
            "--base-details",
            str(m13_details(args.output_dir)),
            "--recovery-details",
            str(m14_details(args.output_dir)),
        ],
    )


def step_run_m15(args: argparse.Namespace) -> None:
    combined = combine_root(args.output_dir, "m14") / "m14-full-holdout-combined-full-details.csv"
    if not combined.is_file():
        raise SystemExit(f"missing M14 combined full details: {combined}")
    run_py(
        "run_m15_temporal_anchor_recovery.py",
        [
            "--benchmark-dir",
            str(args.benchmark_dir),
            "--details",
            str(combined),
            "--output-dir",
            str(m15_root(args.output_dir)),
            "--limit",
            "0",
            *only_clause(args),
            *(["--resume"] if args.resume else []),
        ],
    )


def step_combine_m15(args: argparse.Namespace) -> None:
    out = combine_root(args.output_dir, "m15")
    run_py(
        "combine_holdout_pipeline_details.py",
        [
            "--stage",
            "m15",
            "--output-dir",
            str(out),
            "--base-details",
            str(m13_details(args.output_dir)),
            "--prior-full-details",
            str(combine_root(args.output_dir, "m14") / "m14-full-holdout-combined-full-details.csv"),
            "--prior-summary-details",
            str(combine_root(args.output_dir, "m14") / "m14-full-holdout-combined-details.csv"),
            "--recovery-details",
            str(m15_details(args.output_dir)),
        ],
    )


def step_run_m16(args: argparse.Namespace) -> None:
    combined = combine_root(args.output_dir, "m15") / "m15-full-holdout-combined-details.csv"
    if not combined.is_file():
        raise SystemExit(f"missing M15 combined summary details: {combined}")
    run_py(
        "run_m16_candidate_entailment_verifier.py",
        [
            "--benchmark-dir",
            str(args.benchmark_dir),
            "--details",
            str(combined),
            "--output-dir",
            str(m16_root(args.output_dir)),
            "--m14-details",
            str(m14_details(args.output_dir)),
            "--limit",
            "0",
            "--timeout",
            str(args.qwen_timeout),
            *llm_backend_args(args),
            *only_clause(args),
            *(["--resume"] if args.resume else []),
        ],
    )


def step_combine_m16(args: argparse.Namespace) -> None:
    m16_details_path = m16_root(args.output_dir) / "m16-candidate-entailment-details.csv"
    if not m16_details_path.is_file():
        m16_details_path = m16_root(args.output_dir) / "m16-candidate-entailment-details.csv"
    out = combine_root(args.output_dir, "m16")
    run_py(
        "combine_holdout_pipeline_details.py",
        [
            "--stage",
            "m16",
            "--output-dir",
            str(out),
            "--base-details",
            str(m13_details(args.output_dir)),
            "--prior-full-details",
            str(combine_root(args.output_dir, "m15") / "m15-full-holdout-combined-full-details.csv"),
            "--prior-summary-details",
            str(combine_root(args.output_dir, "m15") / "m15-full-holdout-combined-details.csv"),
            "--recovery-details",
            str(m16_details_path),
        ],
    )


def main() -> int:
    args = parse_args()
    args.benchmark_dir = args.benchmark_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    if args.calibrated:
        if args.output_dir == DEFAULT_OUTPUT.resolve():
            args.output_dir = (
                DEFAULT_OLLAMA_TYPED_OUTPUT.resolve()
                if args.llm_backend == "ollama"
                else DEFAULT_TYPED_OUTPUT.resolve()
            )
        if args.method_freeze_manifest is None:
            args.method_freeze_manifest = DEFAULT_TYPED_METHOD_MANIFEST.resolve()
        if not args.expected_method_manifest_sha256:
            args.expected_method_manifest_sha256 = EXPECTED_M16_METHOD_TYPED_MANIFEST_SHA256
    elif args.method_freeze_manifest is not None:
        args.method_freeze_manifest = args.method_freeze_manifest.resolve()
    if args.reuse_m13_from is not None:
        args.reuse_m13_from = args.reuse_m13_from.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    ckpt = load_checkpoint(checkpoint_path(args.output_dir))
    steps = {
        "validate": step_validate,
        "prepare": step_prepare,
        "env": step_env,
        "seed-m13-raw": step_seed_m13_raw,
        "replay-m13-ir": step_replay_m13_ir,
        "run-m13": step_run_m13,
        "run-m14": step_run_m14,
        "combine-m14": step_combine_m14,
        "run-m15": step_run_m15,
        "combine-m15": step_combine_m15,
        "run-m16": step_run_m16,
        "combine-m16": step_combine_m16,
    }

    if args.step == "all":
        sequence = (
            ("validate", "prepare", "env", "seed-m13-raw", "replay-m13-ir")
            if args.reuse_m13_from
            else ("validate", "prepare", "env")
        )
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
        "phase": "FINAL_BLIND_EVALUATION_V3_LARGE",
        "method": "M16_FULL_EVIDENCE_CONSTRAINED_REPAIR",
        "llm_backend": args.llm_backend,
        "steps": sequence,
        "output_dir": str(args.output_dir.relative_to(PROJECT_DIR)),
        "log": log,
        "checkpoint": ckpt,
    }
    (args.output_dir / "formal-eval-v3-orchestrator-log.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
