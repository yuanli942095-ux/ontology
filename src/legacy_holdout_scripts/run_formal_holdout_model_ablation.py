from __future__ import annotations

"""Secondary model substitution ablation on frozen hold-out (DeepSeek API arm).

Arm B only. Primary Qwen arm must remain in final-blind-eval-r5/.
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

from formal_holdout_eval_guard import validate_pre_run_gates
from semantic_v2_common import PROJECT_DIR

PRIMARY_ROOT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded" / "final-blind-eval-r5"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded" / "final-blind-eval-r5-model-ablation-deepseek"
BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
MODEL_ABLATION_FREEZE = PROJECT_DIR / "output" / "final-method-freeze" / "model-ablation-freeze-manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=("validate", "prepare", "run-main"), default="run-main")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--qwen-timeout", type=int, default=180)
    parser.add_argument("--top-n", type=int, default=4)
    return parser.parse_args()


def checkpoint_path(output_dir: Path) -> Path:
    return output_dir / "model-ablation-checkpoint.json"


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"completed_steps": [], "failures": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_model_ablation_freeze() -> dict[str, Any]:
    if not MODEL_ABLATION_FREEZE.is_file():
        raise SystemExit(
            f"missing model ablation freeze manifest; run: python src/freeze_model_ablation_secondary.py"
        )
    payload = json.loads(MODEL_ABLATION_FREEZE.read_text(encoding="utf-8"))
    if payload.get("experiment_role") != "SECONDARY_MODEL_SUBSTITUTION_ABLATION":
        raise SystemExit("invalid model ablation freeze manifest")
    if payload.get("primary_method_changed"):
        raise SystemExit("model ablation freeze must not change primary method")
    return {"path": str(MODEL_ABLATION_FREEZE.relative_to(PROJECT_DIR)), "pass": True}


def step_validate() -> dict[str, Any]:
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise SystemExit("DEEPSEEK_API_KEY is not set")
    return {
        "pre_run_gates": validate_pre_run_gates(),
        "model_ablation_freeze": validate_model_ablation_freeze(),
    }


def step_prepare(args: argparse.Namespace) -> None:
    primary_manifest = PRIMARY_ROOT / "formal-eval-manifest-r5.csv"
    if not primary_manifest.is_file():
        raise SystemExit(f"primary manifest missing: {primary_manifest}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(primary_manifest, args.output_dir / "formal-eval-manifest-r5.csv")
    binding = {
        "experiment_role": "SECONDARY_MODEL_SUBSTITUTION_ABLATION",
        "benchmark": str(BENCHMARK.relative_to(PROJECT_DIR)),
        "primary_reference": str(PRIMARY_ROOT.relative_to(PROJECT_DIR)),
        "shared_public_input": str((PRIMARY_ROOT / "public-input").relative_to(PROJECT_DIR)),
        "manifest": str((args.output_dir / "formal-eval-manifest-r5.csv").relative_to(PROJECT_DIR)),
        "llm_backend": "deepseek_api",
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (args.output_dir / "run-binding.json").write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def step_run_main(args: argparse.Namespace) -> None:
    manifest = args.output_dir / "formal-eval-manifest-r5.csv"
    if not manifest.is_file():
        step_prepare(args)
    m13_out = args.output_dir / "main-method" / "m13-pilot"
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_m13_rule_refinement_pilot.py"),
        "--benchmark-dir",
        str(BENCHMARK),
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
        "--llm-backend",
        "deepseek_api",
    ]
    if args.resume:
        cmd.append("--resume")
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env["ONTOLOGY_EVOLUTION_OFFICIAL_RUN"] = "1"
    env["ONTOLOGY_EVOLUTION_MODEL_ABLATION"] = "1"
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR, env=env)


def main() -> int:
    args = parse_args()
    args.output_dir = args.output_dir.resolve()
    ckpt = load_checkpoint(checkpoint_path(args.output_dir))
    steps = {
        "validate": step_validate,
        "prepare": lambda: step_prepare(args),
        "run-main": lambda: step_run_main(args),
    }
    sequence = ("validate", "prepare", "run-main") if args.step == "run-main" else (args.step,)
    for name in sequence:
        if args.resume and name in ckpt.get("completed_steps", []):
            print(f"[resume] skip {name}", flush=True)
            continue
        steps[name]()
        ckpt.setdefault("completed_steps", []).append(name)
        save_checkpoint(checkpoint_path(args.output_dir), ckpt)
    print(json.dumps({"output_dir": str(args.output_dir.relative_to(PROJECT_DIR)), "step": args.step}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
