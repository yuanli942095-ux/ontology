from __future__ import annotations

"""Generate the final method freeze after smoke-test engineering validation.

Output:
  output/final-method-freeze/final-method-freeze-manifest.json
  output/final-method-freeze/final-method-freeze-files.csv
  output/final-method-freeze/final-method-freeze-summary.json
"""

import argparse
import inspect
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
import run_m13_rule_refinement_pilot as m13
from method_experiment_guard import FROZEN_V43_GATE, HOLDOUT_BENCHMARK
from run_auto_policy_v4_ir_candidate_repair import parse_semantic_ir
from run_external_real_v1_symbolic_closure import repair_checks
from run_m12_predicate_focus_pilot import build_focused_evidence
from semantic_v2_common import PROJECT_DIR, sha256_file, sha256_text, write_csv

OUTPUT = PROJECT_DIR / "output" / "final-method-freeze"
METHOD_NAME = "EVIDENCE_CONSTRAINED_SEMANTIC_IR_REPAIR"
HOLDOUT_MANIFEST = (
    PROJECT_DIR / "output" / "external-real-holdout-v1-expanded" / "external-real-holdout-v1-freeze-manifest.json"
)
SMOKE_STAGE1_REPORT = PROJECT_DIR / "output" / "evidence-constrained-smoke-test" / "stage1" / "engineering-smoke-report.json"
SMOKE_STAGE2_REPORT = PROJECT_DIR / "output" / "evidence-constrained-smoke-test" / "stage2" / "engineering-smoke-report.json"
PRIOR_METHOD_FREEZE = PROJECT_DIR / "output" / "evidence-constrained-semantic-ir-repair" / "method-spec-freeze-manifest.json"

LOCKED_SOURCES = [
    ("src/run_m12_predicate_focus_pilot.py", "predicate_aware_evidence_focus"),
    ("src/run_m13_rule_refinement_pilot.py", "m13_rule_refinement"),
    ("src/auto_policy_task_formulation.py", "task_formulation_router"),
    ("src/run_auto_policy_v4_ir_candidate_repair.py", "semantic_ir_and_safety_gate"),
    ("src/auto_policy_v4_constraint_rerank.py", "constraint_reranker"),
    ("src/auto_policy_v4_css_slot.py", "robust_ir_css_completion"),
    ("src/run_auto_policy_v2_candidate_repair.py", "owl_materialization"),
    ("src/run_external_real_v1_symbolic_closure.py", "cq_closure"),
    ("src/validate_benchmark.py", "reasoner_validation"),
    ("src/method_experiment_guard.py", "experiment_guard"),
    ("src/run_evidence_constrained_semantic_ir_repair.py", "official_experiment_runner"),
    ("src/run_evidence_constrained_smoke_test.py", "smoke_test_runner"),
    ("src/freeze_evidence_constrained_semantic_ir_repair.py", "prior_method_freeze_script"),
    ("src/freeze_final_method.py", "final_method_freeze_script"),
]


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return ""


def file_row(path: Path, role: str) -> dict[str, Any]:
    stat = path.stat()
    return {"role": role, "path": relpath(path), "sha256": sha256_file(path), "bytes": stat.st_size}


def fn_source_hash(fn: Any) -> str:
    return sha256_text(inspect.getsource(fn))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def smoke_audit_summary() -> dict[str, Any]:
    out: dict[str, Any] = {"stages": {}}
    for stage, path in ((1, SMOKE_STAGE1_REPORT), (2, SMOKE_STAGE2_REPORT)):
        if not path.is_file():
            out["stages"][str(stage)] = {"present": False, "path": relpath(path)}
            continue
        report = load_json(path)
        out["stages"][str(stage)] = {
            "present": True,
            "path": relpath(path),
            "pass": report.get("pass"),
            "event_count": report.get("event_count"),
            "accuracy_evaluated": report.get("accuracy_evaluated"),
            "closure_used_as_pass_criterion": report.get("closure_used_as_pass_criterion"),
            "historical_failures": report.get("historical_failures", []),
            "unresolved_failures": report.get("unresolved_failures", []),
            "closure_observed": report.get("closure_observed"),
        }
    out["engineering_validation_complete"] = all(
        stage.get("pass") is True for stage in out["stages"].values() if stage.get("present")
    )
    return out


def candidate_isolation_policy() -> dict[str, Any]:
    return {
        "policy_id": "PUBLIC_EVIDENCE_ONLY_BEFORE_RANKING",
        "generation_and_refinement_may_read": [
            "public/evidence excerpts",
            "public/event metadata (subject_label, predicate_label, case_context, semantic_type, domain)",
            "public/documents and retrieval manifests",
        ],
        "generation_and_refinement_must_not_read": [
            "repair-stage/candidates",
            "private/oracle",
            "manual formal-policy artifacts",
            "candidate display values or oracle spans during generation/refinement",
        ],
        "candidate_seen_stage": "candidate_ranking_after_ir_fixed",
        "record_flags_required": {
            "candidate_used": False,
            "oracle_used": False,
            "manual_policy_used": False,
        },
        "enforced_in": [
            "src/run_m13_rule_refinement_pilot.py",
            "src/run_m12_predicate_focus_pilot.py",
            "src/auto_policy_public_window_handoff.py",
            "src/method_experiment_guard.py",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()

    hashed: list[dict[str, Any]] = []
    for relative, role in LOCKED_SOURCES:
        path = PROJECT_DIR / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        hashed.append(file_row(path, role))

    holdout_sha = ""
    if HOLDOUT_MANIFEST.is_file():
        holdout_sha = load_json(HOLDOUT_MANIFEST).get("manifest_sha256", sha256_file(HOLDOUT_MANIFEST))

    prior_sha = ""
    if PRIOR_METHOD_FREEZE.is_file():
        prior_sha = load_json(PRIOR_METHOD_FREEZE).get("manifest_sha256", sha256_file(PRIOR_METHOD_FREEZE))

    m13_config = {
        "draft_prompt_source_hash": fn_source_hash(m13.build_draft_prompt),
        "refine_prompt_source_hash": fn_source_hash(m13.build_refine_prompt),
        "verify_rule_source_hash": fn_source_hash(m13.verify_rule),
        "build_record_source_hash": fn_source_hash(m13.build_record),
        "qwen_timeout_default": 180,
        "refine_seed_offset": 1009,
        "method_flag": "m13_rule_refinement",
    }
    evidence_focus = {
        "implementation_source_hash": fn_source_hash(build_focused_evidence),
        "top_n_default": 4,
        "max_spans": 4,
        "inputs": ["subject_label", "predicate_label", "case_context"],
        "llm_called": False,
    }
    model_config = {
        "model": v3.MODEL,
        "ollama_url": v3.OLLAMA_URL,
        "temperature": 0,
        "num_predict_draft": 900,
        "num_predict_refine": 900,
        "num_ctx": 16384,
        "think": False,
        "stream": False,
    }
    seed_policy = {
        "smoke_test_seed": 20260827,
        "formal_holdout_seed_base": 20260827,
        "formal_holdout_runs": 5,
        "formal_holdout_seed_formula": "seed_base + run - 1",
        "m13_refine_seed_formula": "draft_seed + 1009",
    }
    semantic_ir = {
        "robust_ir": True,
        "parser_source_hash": fn_source_hash(parse_semantic_ir),
        "css_slot_completion_module": "src/auto_policy_v4_css_slot.py",
    }
    reasoner_config = {
        "engine": "owlready2.sync_reasoner (HermiT via Owlready2)",
        "infer_property_values": True,
        "consistent_status": "CONSISTENT",
        "implementation_source_hash": fn_source_hash(__import__("validate_benchmark", fromlist=["run_reasoner"]).run_reasoner),
    }
    cq_policy = {
        "implementation_source_hash": fn_source_hash(repair_checks),
        "fields": ["source_triggers_repair_cq", "candidate_satisfies_repair_cq"],
    }
    minimal_edit_policy = {
        "rule": "exactly_one_triple_removed_and_one_added",
        "gate_field": "minimal_edit_gate",
        "implementation": "graph_delta in src/run_auto_policy_v2_candidate_repair.py",
    }

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "freeze_kind": "FINAL_METHOD_FREEZE",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": METHOD_NAME,
        "method_display": {
            "english_primary": "Evidence-Constrained Semantic IR Repair",
            "english_short": "M13 + V4 Evidence-Constrained Repair",
            "chinese": "证据约束的语义中间表示本体修复方法",
        },
        "git_commit": git_commit(),
        "freeze_status": "FROZEN",
        "post_freeze_policy": {
            "holdout_tuning_forbidden": True,
            "method_changes_forbidden": True,
            "next_allowed_experiment": "236-event hold-out × 5 runs (evaluation only)",
        },
        "evidence_focus": evidence_focus,
        "m13_rule_refinement": m13_config,
        "semantic_ir": semantic_ir,
        "v43_gate": dict(FROZEN_V43_GATE),
        "candidate_isolation_policy": candidate_isolation_policy(),
        "model": model_config,
        "seed_policy": seed_policy,
        "owl_materialization": {
            "module": "src/run_auto_policy_v2_candidate_repair.py",
            "functions": ["materialize_candidate_owl", "resolve_candidate_owl_path", "resolve_source_owl"],
            "module_sha256": sha256_file(PROJECT_DIR / "src/run_auto_policy_v2_candidate_repair.py"),
        },
        "reasoner": reasoner_config,
        "cq": cq_policy,
        "minimal_edit": minimal_edit_policy,
        "benchmarks": {
            "development": "benchmark/external-real-v8-grounded",
            "holdout_eval_only": f"benchmark/{HOLDOUT_BENCHMARK}",
            "benchmark_holdout_manifest_sha256": holdout_sha,
        },
        "smoke_test_validation": smoke_audit_summary(),
        "linked_freezes": {
            "prior_method_spec_manifest_sha256": prior_sha,
            "holdout_manifest_sha256": holdout_sha,
            "paths": [
                "output/evidence-constrained-semantic-ir-repair/method-spec-freeze-manifest.json",
                "output/external-real-holdout-v1-expanded/external-real-holdout-v1-freeze-manifest.json",
                "output/auto-policy-v4.3-repair-decision-freeze-manifest.json",
            ],
        },
        "official_entrypoints": [
            "src/run_evidence_constrained_semantic_ir_repair.py",
            "src/run_evidence_constrained_smoke_test.py",
        ],
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
    manifest_path = args.output_dir / "final-method-freeze-manifest.json"
    files_csv = args.output_dir / "final-method-freeze-files.csv"
    summary_path = args.output_dir / "final-method-freeze-summary.json"

    write_csv(files_csv, hashed)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "method_name": METHOD_NAME,
        "freeze_status": "FROZEN",
        "git_commit": payload["git_commit"],
        "manifest_sha256": payload["manifest_sha256"],
        "benchmark_holdout_manifest_sha256": holdout_sha,
        "v43_gate": payload["v43_gate"],
        "robust_ir": True,
        "smoke_test_engineering_pass": payload["smoke_test_validation"].get("engineering_validation_complete"),
        "manifest": relpath(manifest_path),
        "files_csv": relpath(files_csv),
        "generated_at_utc": payload["generated_at_utc"],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
