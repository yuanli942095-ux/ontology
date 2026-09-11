from __future__ import annotations

"""Freeze the paper main method: Evidence-Constrained Semantic IR Repair.

Pipeline:
  Evidence Retrieval
  → Predicate-aware Evidence Focus
  → M13 Regulatory Rule Refinement
  → Semantic IR / Robust IR
  → V4.3 Constraint-aware Candidate Ranking
  → V4.3 Safety Gate / Selective Repair
  → OWL Materialization → Reasoner + CQ Closure
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, sha256_file, sha256_text, write_csv

OUTPUT = PROJECT_DIR / "output" / "evidence-constrained-semantic-ir-repair"
BENCHMARK_V8 = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
BENCHMARK_HOLDOUT = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
PREFIX = "method-spec-freeze-manifest"

METHOD_NAMES = {
    "english_primary": "Evidence-Constrained Semantic IR Repair",
    "english_short": "M13 + V4 Evidence-Constrained Repair",
    "chinese": "证据约束的语义中间表示本体修复方法",
    "freeze_label": "EVIDENCE_CONSTRAINED_SEMANTIC_IR_REPAIR",
}

ONE_LINER = {
    "english": (
        "Use predicate-aware evidence focus and evidence-grounded rule refinement to build Semantic IR "
        "from public evidence, then apply V4.3 constraint-aware ranking with a selective safety gate to "
        "choose a candidate repair, and finally validate via OWL materialization, reasoner checks, and CQ closure."
    ),
    "chinese": (
        "使用 predicate-aware evidence focus 和 evidence-grounded rule refinement，从 public evidence 中构造 "
        "Semantic IR，再通过 V4.3 constraint-aware ranking 与 selective safety gate 选择候选修复，"
        "最后执行 OWL materialization、reasoner 与 CQ closure 验证。"
    ),
}

PIPELINE_STAGES = [
    {
        "id": "evidence_retrieval",
        "order": 1,
        "title": "Evidence Retrieval",
        "description": "Frozen public source windows per event; no oracle/candidate leakage at construction.",
        "sources": [
            "src/build_external_real_v8_grounded.py",
            "src/auto_policy_public_window_handoff.py",
            "src/run_auto_policy_v3_natural_evidence_robustness.py",
        ],
        "artifacts": [
            "benchmark/external-real-v8-grounded/public/retrieval/external-real-v8-event-retrieval.csv",
            "output/external-real-v8-grounded/preformal-r3/raw_window_metadata_light/raw/",
        ],
    },
    {
        "id": "predicate_aware_evidence_focus",
        "order": 2,
        "title": "Predicate-aware Evidence Focus",
        "description": "Select 1-4 spans from frozen public evidence aligned to subject_label, predicate_label, case_context.",
        "sources": ["src/run_m12_predicate_focus_pilot.py"],
        "config": {"top_n": 4, "max_spans": 4, "inputs": ["subject_label", "predicate_label", "case_context"]},
    },
    {
        "id": "m13_rule_refinement",
        "order": 3,
        "title": "M13 Regulatory Rule Refinement",
        "description": "draft extraction → evidence-grounded critique/refinement → policy-like structured record.",
        "sources": ["src/run_m13_rule_refinement_pilot.py", "src/auto_policy_task_formulation.py"],
        "config": {"method_flag": "m13_rule_refinement"},
    },
    {
        "id": "semantic_ir",
        "order": 4,
        "title": "Semantic IR / Robust IR",
        "description": "Convert refined rule to unified Semantic IR with robust completion paths.",
        "sources": [
            "src/run_auto_policy_v4_ir_candidate_repair.py",
            "src/auto_policy_v4_css_slot.py",
            "src/run_auto_formal_policy_batch_v3.py",
        ],
        "config": {"robust_ir": True},
    },
    {
        "id": "v43_candidate_ranking",
        "order": 5,
        "title": "V4.3 Constraint-aware Candidate Ranking",
        "description": "Constraint reranker with temporal-unique-top1 bypass for TEMPORAL_VERSION.",
        "sources": ["src/auto_policy_v4_constraint_rerank.py"],
        "config": {
            "min_score": 0.30,
            "min_margin": 0.00,
            "reranker": "constraint",
            "temporal_unique_top1": True,
            "robust_ir": True,
        },
    },
    {
        "id": "safety_gate",
        "order": 6,
        "title": "V4.3 Safety Gate / Selective Repair",
        "description": "ABSTAIN when IR, score, evidence, or ranking conditions fail.",
        "sources": ["src/run_auto_policy_v4_ir_candidate_repair.py"],
        "config": {"ties": "ABSTAIN", "decision_paths": ["IR_FAIL_CLOSED", "IR_RANK_ABSTAIN", "IR_RANK_TIE_ABSTAIN", "NO_CANDIDATES"]},
    },
    {
        "id": "owl_closure",
        "order": 7,
        "title": "OWL Materialization → Reasoner + CQ Closure",
        "description": "Materialize candidate OWL, run reasoner consistency, CQ/repair checks, minimal-edit gate.",
        "sources": [
            "src/run_auto_policy_v2_candidate_repair.py",
            "src/run_external_real_v1_symbolic_closure.py",
            "src/validate_benchmark.py",
        ],
    },
]

INPUT_ISOLATION = {
    "auto_policy_and_rule_refinement_may_read": [
        "public/evidence excerpts",
        "public/event metadata (subject_label, predicate_label, case_context, semantic_type, domain)",
        "public/documents and retrieval manifests",
    ],
    "auto_policy_and_rule_refinement_must_not_read": [
        "repair-stage/candidates",
        "private/oracle",
        "manual formal-policy artifacts",
        "candidate display values or oracle spans during generation/refinement",
    ],
    "enforced_in": [
        "src/run_m13_rule_refinement_pilot.py",
        "src/run_m12_predicate_focus_pilot.py",
        "src/auto_policy_public_window_handoff.py",
    ],
}

EXCLUDED_FROM_MAIN_METHOD = [
    {
        "id": "OPTION_FORMAL_POLICY_HARD_GATE",
        "role": "symbolic_upper_bound",
        "source": "src/run_external_real_v1_candidate_ablation.py",
    },
    {
        "id": "DIRECT_FREE",
        "role": "baseline",
        "source": "src/run_external_real_v1_candidate_ablation.py",
    },
    {
        "id": "OPTION_VALUE_ONLY",
        "role": "baseline_ablation",
        "source": "src/run_external_real_v1_candidate_ablation.py",
    },
    {
        "id": "OPTION_FORMAL_OPERATION",
        "role": "baseline_ablation",
        "source": "src/run_external_real_v1_candidate_ablation.py",
    },
    {
        "id": "V4_ORIGINAL_ONLY",
        "role": "unstable_on_complex_m7",
        "source": "src/run_m13_rule_refinement_pilot.py (arm-a-v4-original)",
    },
    {
        "id": "M12_TASK_ROUTER_ONLY",
        "role": "intermediate_module_not_full_method",
        "source": "src/run_m12_predicate_focus_pilot.py (arm-a-task-router)",
    },
    {
        "id": "V45_SCHEMA_CONTRACT_REPAIR",
        "role": "rejected_overlap_with_robust_ir",
        "source": "output/schema-contract-repair-ablation/contract-repair-decision.json",
    },
]

LOCKED_SOURCES = [
    ("src/run_m12_predicate_focus_pilot.py", "predicate_aware_evidence_focus"),
    ("src/run_m13_rule_refinement_pilot.py", "m13_rule_refinement"),
    ("src/run_auto_policy_v4_ir_candidate_repair.py", "semantic_ir_and_safety_gate"),
    ("src/auto_policy_v4_constraint_rerank.py", "constraint_reranker"),
    ("src/auto_policy_v4_css_slot.py", "robust_ir_css_completion"),
    ("src/run_auto_policy_v2_candidate_repair.py", "owl_materialization"),
    ("src/run_external_real_v1_symbolic_closure.py", "cq_closure"),
    ("src/validate_benchmark.py", "reasoner_validation"),
    ("src/auto_policy_public_window_handoff.py", "public_evidence_handoff"),
    ("src/run_auto_formal_policy_batch_v3.py", "formal_policy_schema_prompt"),
    ("src/run_auto_policy_v3_natural_evidence_robustness.py", "light_evidence_generation"),
    ("src/freeze_auto_policy_v4_3.py", "v43_repair_decision_subfreeze"),
    ("src/freeze_evidence_constrained_semantic_ir_repair.py", "method_freeze_script"),
    ("src/method_experiment_guard.py", "experiment_guard"),
    ("src/run_evidence_constrained_semantic_ir_repair.py", "official_experiment_runner"),
]

LINKED_FREEZES = [
    "output/auto-policy-v4.3-repair-decision-freeze-manifest.json",
    "output/external-real-v8-grounded-freeze-manifest.json",
    "output/external-real-holdout-v1-expanded/external-real-holdout-v1-freeze-manifest.json",
]

REPRODUCTION_COMMAND = (
    "PYTHONPATH=src .venv/bin/python src/run_m13_rule_refinement_pilot.py "
    "(predicate focus + M13 refinement + V4.3 IR repair on configured event subset); "
    "for full-benchmark V4.3 replay use run_auto_policy_v4_ir_candidate_repair.py with "
    "--min-score 0.30 --min-margin 0.00 --reranker constraint --temporal-unique-top1 --robust-ir"
)


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def file_row(path: Path, role: str) -> dict[str, Any]:
    stat = path.stat()
    return {"role": role, "path": relpath(path), "sha256": sha256_file(path), "bytes": stat.st_size}


def git_parent() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return {"available": True, "parent_commit": commit}
    except Exception as exc:
        return {"available": False, "parent_commit": "", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=PREFIX)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()

    hashed: list[dict[str, Any]] = []
    for relative, role in LOCKED_SOURCES:
        path = PROJECT_DIR / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        hashed.append(file_row(path, role))

    for relative in LINKED_FREEZES:
        path = PROJECT_DIR / relative
        if path.is_file():
            hashed.append(file_row(path, f"linked_freeze:{path.stem}"))

    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": METHOD_NAMES,
        "one_liner": ONE_LINER,
        "pipeline_stages": PIPELINE_STAGES,
        "input_isolation": INPUT_ISOLATION,
        "excluded_from_main_method": EXCLUDED_FROM_MAIN_METHOD,
        "v43_gate": PIPELINE_STAGES[4]["config"],
        "benchmarks": {
            "development_eval": relpath(BENCHMARK_V8),
            "holdout_eval_only": relpath(BENCHMARK_HOLDOUT),
        },
        "linked_freezes": LINKED_FREEZES,
        "reproduction_command": REPRODUCTION_COMMAND,
        "official_entrypoint": "src/run_evidence_constrained_semantic_ir_repair.py",
        "legacy_opt_in": {
            "cli_flag": "--allow-legacy-experiment",
            "env_var": "ONTOLOGY_EVOLUTION_ALLOW_LEGACY",
        },
        "experiment_guard": "src/method_experiment_guard.py",
        "do_not": [
            "use_hard_gate_as_main_method",
            "tune_on_holdout_after_freeze",
            "lower_min_score_for_paper_numbers",
            "read_candidates_or_oracle_during_refinement",
            "replace_m13_with_v4_original_only_on_complex_m7",
        ],
        "git": git_parent(),
        "files": hashed,
    }
    payload["manifest_sha256"] = sha256_text(
        json.dumps(
            [{"path": row["path"], "sha256": row["sha256"], "role": row["role"]} for row in hashed],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"{args.prefix}.json"
    csv_path = args.output_dir / f"{args.prefix}-files.csv"
    md_path = args.output_dir / "method-spec.md"
    benchmark_md = BENCHMARK_V8 / "METHOD.md"

    write_csv(csv_path, hashed)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        f"# {METHOD_NAMES['english_primary']}",
        "",
        f"**中文名：** {METHOD_NAMES['chinese']}",
        "",
        f"**Freeze label:** `{METHOD_NAMES['freeze_label']}`",
        "",
        "## One-liner",
        "",
        METHOD_NAMES["english_short"] + ":",
        "",
        ONE_LINER["english"],
        "",
        ONE_LINER["chinese"],
        "",
        "## Pipeline",
        "",
    ]
    for stage in PIPELINE_STAGES:
        md_lines.append(f"{stage['order']}. **{stage['title']}** — {stage['description']}")
    md_lines.extend(
        [
            "",
            "## Input isolation (Auto Policy / Rule Refinement)",
            "",
            "**May read:**",
            "",
        ]
    )
    for item in INPUT_ISOLATION["auto_policy_and_rule_refinement_may_read"]:
        md_lines.append(f"- {item}")
    md_lines.extend(["", "**Must NOT read:**", ""])
    for item in INPUT_ISOLATION["auto_policy_and_rule_refinement_must_not_read"]:
        md_lines.append(f"- {item}")
    md_lines.extend(["", "## V4.3 gate (frozen)", "", "```json", json.dumps(PIPELINE_STAGES[4]["config"], indent=2), "```", "", "## Excluded from main method", ""])
    for item in EXCLUDED_FROM_MAIN_METHOD:
        md_lines.append(f"- `{item['id']}` — {item['role']}")
    md_lines.extend(
        [
            "",
            "## Experiment guardrails",
            "",
            "- Official entrypoint: `src/run_evidence_constrained_semantic_ir_repair.py`",
            "- Legacy/ablation scripts blocked unless `--allow-legacy-experiment` or `ONTOLOGY_EVOLUTION_ALLOW_LEGACY=1`",
            "- `run_auto_policy_v4_ir_candidate_repair.py` enforces frozen V4.3 gate settings by default",
            "- Hold-out `external-real-holdout-v1-expanded` is evaluation-only",
            "",
            f"Manifest: `{relpath(json_path)}`",
            f"manifest_sha256: `{payload['manifest_sha256']}`",
            "",
        ]
    )
    md_text = "\n".join(md_lines)
    md_path.write_text(md_text, encoding="utf-8")
    benchmark_md.write_text(md_text, encoding="utf-8")

    summary = {
        "method": METHOD_NAMES["freeze_label"],
        "manifest_sha256": payload["manifest_sha256"],
        "json": relpath(json_path),
        "markdown": relpath(md_path),
        "benchmark_method_card": relpath(benchmark_md),
    }
    (args.output_dir / "method-spec-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
