from __future__ import annotations

"""Guardrails to keep paper experiments on the frozen main method.

Default policy:
- Legacy baselines / ablations / old pilots are blocked unless explicitly opted in.
- V4 IR repair must use frozen V4.3 gate settings unless legacy opt-in.
- Hold-out benchmark is evaluation-only (no tuning runners).
"""

import os
import sys
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR

ALLOW_LEGACY_ENV = "ONTOLOGY_EVOLUTION_ALLOW_LEGACY"
OFFICIAL_RUN_ENV = "ONTOLOGY_EVOLUTION_OFFICIAL_RUN"
LEGACY_FLAG = "--allow-legacy-experiment"

HOLDOUT_BENCHMARK = "external-real-holdout-v1-expanded"

FROZEN_V43_GATE = {
    "min_score": 0.30,
    "gre_css_min_score": 0.24,
    "min_margin": 0.00,
    "reranker": "constraint",
    "temporal_unique_top1": True,
    "robust_ir": True,
}

OFFICIAL_ENTRYPOINTS = {
    "run_evidence_constrained_semantic_ir_repair.py",
    "run_evidence_constrained_smoke_test.py",
}

# Read-only / infrastructure scripts are always allowed.
ALLOWLIST_SUFFIXES = (
    "validate_",
    "audit_",
    "review_",
    "analyze_",
    "freeze_",
    "enrich_",
    "apply_holdout_",
    "generate_",
    "build_external_real_holdout",
    "build_external_real_v8",
)

LEGACY_BLOCKED: dict[str, str] = {
    "run_external_real_v1_candidate_ablation.py": "baseline/ablation (DIRECT_FREE, HARD_GATE, etc.)",
    "run_schema_contract_repair_ablation.py": "rejected V4.5 contract-repair ablation",
    "run_generation_strategy_ablation.py": "generation strategy ablation",
    "run_m12_model_capacity_ablation.py": "model capacity ablation",
    "run_m12_decomposed_extraction_pilot.py": "M12 decomposed extraction pilot",
    "run_m12_task_formulation_pilot.py": "M12 task-formulation pilot (not full method)",
    "run_m12_predicate_focus_pilot.py": "standalone predicate-focus pilot; use official M13 runner",
    "run_m14_llm_candidate_alignment_pilot.py": "M14 alignment pilot",
    "run_m13_boundary_semantic_reading_pilot.py": "M13 boundary-reading pilot",
    "run_semantic_completion_pilot.py": "semantic completion pilot",
    "run_auto_policy_v45_reliability_rerun.py": "V4.5 reliability rerun",
    "run_auto_policy_v3_component_ablation.py": "V3 component ablation",
    "run_auto_policy_v3_conflict_gate.py": "V3 conflict gate (not V4.3 safety gate)",
    "run_auto_policy_v3_natural_conflict_gate.py": "V3 natural conflict gate",
    "run_auto_policy_v3_natural_conflict_safety.py": "V3 natural conflict safety",
    "run_auto_policy_v3_negative_safety.py": "V3 negative safety",
    "run_auto_policy_v3_schema_transfer.py": "V3 schema transfer",
    "run_hard_gate_survivor_stress.py": "hard gate stress (upper bound)",
    "run_template_policy_hard_gate.py": "template hard gate (upper bound)",
    "run_baseline_direct.py": "direct baseline",
    "run_candidate_information_ablation.py": "candidate-information ablation",
    "run_no_model_baselines_semantic_v2.py": "no-model baselines",
    "run_auto_formal_policy_batch.py": "deprecated formal-policy batch v1",
    "run_auto_formal_policy_batch_v2.py": "deprecated formal-policy batch v2",
    "run_auto_formal_policy_batch_v3_no_normalizer.py": "ablation without normalizer",
    "run_natural_conflict_real_v1.py": "legacy natural-conflict v1 runner",
}

HOLDOUT_TUNING_BLOCKED = set(LEGACY_BLOCKED.keys()) | {
    "run_m13_rule_refinement_pilot.py",
    "run_auto_policy_v4_ir_candidate_repair.py",
    "run_auto_policy_v3_natural_evidence_robustness.py",
    "run_auto_formal_policy_batch_v3.py",
    "run_auto_policy_v2_candidate_repair.py",
}

HOLDOUT_EVAL_ALLOWED = {
    "run_evidence_constrained_semantic_ir_repair.py",
    "run_evidence_constrained_smoke_test.py",
    "run_formal_holdout_eval.py",
    "run_formal_holdout_model_ablation.py",
    "build_formal_holdout_manifest.py",
    "record_experiment_environment.py",
    "analyze_external_real_holdout_main_table.py",
    "freeze_model_ablation_secondary.py",
    "validate_external_real_holdout_v1.py",
    "audit_external_real_holdout_v1_quality.py",
    "review_holdout_five_consistency.py",
}


def script_basename(path: str | Path) -> str:
    return Path(path).name


def legacy_allowed(argv: list[str] | None = None) -> bool:
    if os.environ.get(ALLOW_LEGACY_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return True
    args = argv if argv is not None else sys.argv[1:]
    return LEGACY_FLAG in args


def official_run_active() -> bool:
    return os.environ.get(OFFICIAL_RUN_ENV, "").strip().lower() in {"1", "true", "yes"}


def is_allowlisted_script(name: str) -> bool:
    if name in OFFICIAL_ENTRYPOINTS:
        return True
    return any(name.startswith(prefix) for prefix in ALLOWLIST_SUFFIXES)


def holdout_benchmark_path(benchmark_dir: Path | str | None) -> bool:
    if benchmark_dir is None:
        return False
    return HOLDOUT_BENCHMARK in Path(benchmark_dir).as_posix()


def block_legacy_entrypoint(
    script: str | Path,
    *,
    argv: list[str] | None = None,
    category: str = "legacy/ablation",
) -> None:
    name = script_basename(script)
    if is_allowlisted_script(name) or official_run_active() or legacy_allowed(argv):
        return
    reason = LEGACY_BLOCKED.get(name, category)
    _exit_blocked(
        title=f"Blocked legacy experiment script: {name}",
        reason=reason,
        hint=(
            f"Use src/run_evidence_constrained_semantic_ir_repair.py for paper experiments, "
            f"or pass {LEGACY_FLAG} / set {ALLOW_LEGACY_ENV}=1 to run this script intentionally."
        ),
    )


def guard_holdout_benchmark(
    benchmark_dir: Path | str | None,
    script: str | Path,
    *,
    argv: list[str] | None = None,
) -> None:
    if not holdout_benchmark_path(benchmark_dir):
        return
    name = script_basename(script)
    if name in HOLDOUT_EVAL_ALLOWED or is_allowlisted_script(name):
        return
    if official_run_active() and name in {
        "run_m13_rule_refinement_pilot.py",
        "run_auto_policy_v4_ir_candidate_repair.py",
    }:
        return
    if legacy_allowed(argv):
        return
    _exit_blocked(
        title=f"Blocked tuning runner on hold-out benchmark: {name}",
        reason="external-real-holdout-v1-expanded is evaluation-only after freeze",
        hint=(
            "Run hold-out evaluation only through src/run_evidence_constrained_semantic_ir_repair.py "
            f"--benchmark-dir benchmark/{HOLDOUT_BENCHMARK}, or opt in with {LEGACY_FLAG}."
        ),
    )


def guard_frozen_v43_config(args: Any, *, script: str | Path) -> None:
    if legacy_allowed() or official_run_active():
        return
    mismatches: list[str] = []
    if float(getattr(args, "min_score", -1)) != FROZEN_V43_GATE["min_score"]:
        mismatches.append(f"min_score={args.min_score} (frozen={FROZEN_V43_GATE['min_score']})")
    if float(getattr(args, "gre_css_min_score", -1)) != FROZEN_V43_GATE["gre_css_min_score"]:
        mismatches.append(
            f"gre_css_min_score={getattr(args, 'gre_css_min_score', None)} "
            f"(frozen={FROZEN_V43_GATE['gre_css_min_score']})"
        )
    if float(getattr(args, "min_margin", -1)) != FROZEN_V43_GATE["min_margin"]:
        mismatches.append(f"min_margin={args.min_margin} (frozen={FROZEN_V43_GATE['min_margin']})")
    if str(getattr(args, "reranker", "")) != FROZEN_V43_GATE["reranker"]:
        mismatches.append(f"reranker={args.reranker} (frozen={FROZEN_V43_GATE['reranker']})")
    if bool(getattr(args, "temporal_unique_top1", False)) != FROZEN_V43_GATE["temporal_unique_top1"]:
        mismatches.append(
            f"temporal_unique_top1={getattr(args, 'temporal_unique_top1', False)} "
            f"(frozen={FROZEN_V43_GATE['temporal_unique_top1']})"
        )
    if bool(getattr(args, "robust_ir", True)) != FROZEN_V43_GATE["robust_ir"]:
        mismatches.append(f"robust_ir={getattr(args, 'robust_ir', True)} (frozen={FROZEN_V43_GATE['robust_ir']})")
    if not mismatches:
        return
    _exit_blocked(
        title=f"Blocked non-frozen V4.3 config in {script_basename(script)}",
        reason="; ".join(mismatches),
        hint=(
            "Use frozen gate flags or src/run_evidence_constrained_semantic_ir_repair.py. "
            f"For ablations, pass {LEGACY_FLAG}."
        ),
    )


def add_legacy_opt_in_arg(parser: Any) -> None:
    parser.add_argument(
        LEGACY_FLAG,
        action="store_true",
        help="Allow this legacy/ablation script to run (not for paper main results).",
    )


def activate_official_run_env() -> dict[str, str]:
    env = os.environ.copy()
    env[OFFICIAL_RUN_ENV] = "1"
    return env


def require_official_runner_or_legacy(script: str | Path) -> None:
    if official_run_active() or legacy_allowed():
        return
    _exit_blocked(
        title=f"Blocked direct launch: {script_basename(script)}",
        reason="Use src/run_evidence_constrained_semantic_ir_repair.py for paper experiments",
        hint=f"Pass {LEGACY_FLAG} to run this script directly.",
    )


def _exit_blocked(*, title: str, reason: str, hint: str) -> None:
    message = "\n".join(
        [
            title,
            f"Reason: {reason}",
            hint,
            f"Frozen method spec: output/evidence-constrained-semantic-ir-repair/method-spec.md",
        ]
    )
    print(message, file=sys.stderr)
    raise SystemExit(2)


def assert_not_holdout_tuning_script(script: str | Path) -> None:
    name = script_basename(script)
    if name in HOLDOUT_TUNING_BLOCKED and not legacy_allowed() and not official_run_active():
        guard_holdout_benchmark(Path(f"benchmark/{HOLDOUT_BENCHMARK}"), script)
