from __future__ import annotations

"""Generate a reproducibility runbook for the current experiment package."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(r"G:\LearnAI\ontology-evolution")
OUT = ROOT / "output"
PY = r"G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe"
PREFIX = "reproducibility-package"


EVENTS_30 = ",".join(f"EXT_E{i:03d}" for i in range(1, 31))


FULL_COMMANDS = [
    {
        "step": "Validate external-real-v1 public/private benchmark state",
        "calls_qwen": False,
        "command": f"{PY} src\\validate_external_real_v1.py --prefix external-real-v1-validation-30-private-oracle-fixed",
        "primary_outputs": [
            "output/external-real-v1-validation-30-private-oracle-fixed-summary.csv",
        ],
    },
    {
        "step": "Generate benchmark freeze manifest",
        "calls_qwen": False,
        "command": f"{PY} src\\generate_external_real_v1_freeze_manifest.py --prefix external-real-v1-freeze-manifest-30-private-oracle-fixed --include-built",
        "primary_outputs": [
            "output/external-real-v1-freeze-manifest-30-private-oracle-fixed.json",
        ],
    },
    {
        "step": "Run external symbolic closure smoke validation",
        "calls_qwen": False,
        "command": f"{PY} src\\run_external_real_v1_symbolic_closure.py --only {EVENTS_30} --prefix external-real-v1-symbolic-closure-30-fixed",
        "primary_outputs": [
            "output/external-real-v1-symbolic-closure-30-fixed-summary.csv",
            "output/external-real-v1-symbolic-closure-30-fixed-details.csv",
        ],
    },
    {
        "step": "Run external candidate-information ablation",
        "calls_qwen": True,
        "qwen_calls_expected": 600,
        "command": f"{PY} src\\run_external_real_v1_candidate_ablation.py --runs 5 --seed 20260820 --methods DIRECT_FREE,OPTION_VALUE_ONLY,OPTION_FORMAL_OPERATION,OPTION_FORMAL_POLICY,OPTION_FORMAL_POLICY_HARD_GATE --prefix external-real-v1-candidate-ablation-r5-seed20260820",
        "primary_outputs": [
            "output/external-real-v1-candidate-ablation-r5-seed20260820-summary.csv",
            "output/external-real-v1-candidate-ablation-r5-seed20260820-runtime-policy-costs-summary.csv",
        ],
    },
    {
        "step": "Generate Auto Policy V2 candidate-blind outputs",
        "calls_qwen": True,
        "qwen_calls_expected": 150,
        "command": f"{PY} src\\run_auto_formal_policy_batch_v2.py",
        "primary_outputs": [
            "output/auto-policy-v2/auto-policy-v2-generation-summary.json",
            "output/auto-policy-v2/raw/",
        ],
    },
    {
        "step": "Evaluate Auto Policy V2 semantic outputs",
        "calls_qwen": False,
        "command": f"{PY} src\\evaluate_auto_formal_policy_v2_semantic.py --raw-dir output\\auto-policy-v2\\raw --output-dir output\\auto-policy-v2 --prefix auto-policy-v2-semantic-evaluation-v2",
        "primary_outputs": [
            "output/auto-policy-v2/auto-policy-v2-semantic-evaluation-v2-summary.json",
        ],
    },
    {
        "step": "Run Auto Policy V2 candidate repair closure",
        "calls_qwen": False,
        "command": f"{PY} src\\run_auto_policy_v2_candidate_repair.py --raw-dir output\\auto-policy-v2\\raw --prefix auto-policy-v2-candidate-repair-r5-seed20260820 --runs 5 --seed 20260820",
        "primary_outputs": [
            "output/auto-policy-v2-candidate-repair-r5-seed20260820-summary.csv",
        ],
    },
    {
        "step": "Generate Auto Policy V3 candidate-blind canonical outputs",
        "calls_qwen": True,
        "qwen_calls_expected": 150,
        "command": f"{PY} src\\run_auto_formal_policy_batch_v3.py",
        "primary_outputs": [
            "output/auto-policy-v3/auto-policy-v3-generation-summary.json",
            "output/auto-policy-v3/raw/",
        ],
    },
    {
        "step": "Evaluate Auto Policy V3 semantic outputs",
        "calls_qwen": False,
        "command": f"{PY} src\\evaluate_auto_formal_policy_v2_semantic.py --raw-dir output\\auto-policy-v3\\raw --output-dir output\\auto-policy-v3 --prefix auto-policy-v3-semantic-evaluation",
        "primary_outputs": [
            "output/auto-policy-v3/auto-policy-v3-semantic-evaluation-summary.json",
        ],
    },
    {
        "step": "Run Auto Policy V3 candidate repair closure",
        "calls_qwen": False,
        "command": f"{PY} src\\run_auto_policy_v2_candidate_repair.py --raw-dir output\\auto-policy-v3\\raw --prefix auto-policy-v3-candidate-repair-r5-seed20260820 --runs 5 --seed 20260820",
        "primary_outputs": [
            "output/auto-policy-v3-candidate-repair-r5-seed20260820-summary.csv",
        ],
    },
    {
        "step": "Generate V3 no-normalizer ablation outputs",
        "calls_qwen": True,
        "qwen_calls_expected": 150,
        "command": f"{PY} src\\run_auto_formal_policy_batch_v3_no_normalizer.py",
        "primary_outputs": [
            "output/auto-policy-v3-no-normalizer/auto-policy-v3-no-normalizer-generation-summary.json",
        ],
    },
    {
        "step": "Build V3 component ablation summary",
        "calls_qwen": False,
        "command": f"{PY} src\\run_auto_policy_v3_component_ablation.py --prefix auto-policy-v3-component-ablation-r5-seed20260820",
        "primary_outputs": [
            "output/auto-policy-v3-component-ablation-r5-seed20260820-summary.csv",
        ],
    },
    {
        "step": "Run V3 robustness experiment",
        "calls_qwen": True,
        "qwen_calls_expected": 270,
        "command": f"{PY} src\\run_auto_policy_v3_robustness.py --prefix auto-policy-v3-robustness-r3-seed20260827",
        "primary_outputs": [
            "output/auto-policy-v3-robustness-r3-seed20260827-summary.csv",
        ],
    },
    {
        "step": "Run V3 negative safety experiment",
        "calls_qwen": True,
        "qwen_calls_expected": 120,
        "command": f"{PY} src\\run_auto_policy_v3_negative_safety.py --prefix auto-policy-v3-negative-safety-r1-seed20260827",
        "primary_outputs": [
            "output/auto-policy-v3-negative-safety-r1-seed20260827-summary.csv",
            "output/auto-policy-v3-negative-safety-r1-seed20260827-by-variant.csv",
        ],
    },
    {
        "step": "Run V3 conflict/uncertainty gate",
        "calls_qwen": False,
        "command": f"{PY} src\\run_auto_policy_v3_conflict_gate.py --prefix auto-policy-v3-conflict-gate-r1-seed20260827",
        "primary_outputs": [
            "output/auto-policy-v3-conflict-gate-r1-seed20260827-by-dataset.csv",
            "output/auto-policy-v3-conflict-gate-r1-seed20260827-by-variant.csv",
        ],
    },
    {
        "step": "Run natural evidence robustness",
        "calls_qwen": True,
        "qwen_calls_expected": 120,
        "command": f"{PY} src\\run_auto_policy_v3_natural_evidence_robustness.py --variants NATURAL_PARAGRAPH,RAW_SHORT_CONTEXT,RAW_PROVENANCE_CONTEXT,RAW_PROVENANCE_METADATA_LIGHT --runs 1 --seed 20260827 --prefix auto-policy-v3-natural-evidence-robustness-r1-seed20260827",
        "primary_outputs": [
            "output/auto-policy-v3-natural-evidence-robustness-r1-seed20260827-summary.csv",
            "output/auto-policy-v3-natural-evidence-robustness-r1-seed20260827.json",
        ],
    },
    {
        "step": "Run leave-one-domain-out schema transfer",
        "calls_qwen": False,
        "command": f"{PY} src\\run_auto_policy_v3_schema_transfer.py --prefix auto-policy-v3-schema-transfer-r5-seed20260820-semantic-only --skip-repair",
        "primary_outputs": [
            "output/auto-policy-v3-schema-transfer-r5-seed20260820-semantic-only-summary.csv",
            "output/auto-policy-v3-schema-transfer-r5-seed20260820-semantic-only-by-domain.csv",
        ],
    },
    {
        "step": "Run natural conflict safety benchmark",
        "calls_qwen": True,
        "qwen_calls_expected": 60,
        "command": f"{PY} src\\run_auto_policy_v3_natural_conflict_safety.py --runs 1 --seed 20260827 --prefix auto-policy-v3-natural-conflict-safety-r1-seed20260827",
        "primary_outputs": [
            "output/auto-policy-v3-natural-conflict-safety-r1-seed20260827-summary.csv",
            "output/auto-policy-v3-natural-conflict-safety-r1-seed20260827-by-variant.csv",
        ],
    },
    {
        "step": "Run enhanced natural-conflict gate",
        "calls_qwen": False,
        "command": f"{PY} src\\run_auto_policy_v3_natural_conflict_gate.py --prefix auto-policy-v3-natural-conflict-gate-r1-seed20260827",
        "primary_outputs": [
            "output/auto-policy-v3-natural-conflict-gate-r1-seed20260827-by-dataset.csv",
            "output/auto-policy-v3-natural-conflict-gate-r1-seed20260827-by-variant.csv",
        ],
    },
    {
        "step": "Run real-public-excerpt natural conflict benchmark",
        "calls_qwen": False,
        "command": f"{PY} src\\run_natural_conflict_real_v1.py --runs 5 --seed 20260820 --prefix natural-conflict-real-v1-gate-r5-seed20260820",
        "primary_outputs": [
            "output/natural-conflict-real-v1-gate-r5-seed20260820-summary.csv",
            "output/natural-conflict-real-v1-gate-r5-seed20260820-by-domain.csv",
            "output/natural-conflict-real-v1-gate-r5-seed20260820.md",
        ],
    },
    {
        "step": "Compute extended confidence intervals and paired tests",
        "calls_qwen": False,
        "command": f"{PY} src\\analyze_auto_policy_v3_extended_statistics.py",
        "primary_outputs": [
            "output/auto-policy-v3-extended-statistical-analysis-metrics.csv",
            "output/auto-policy-v3-extended-statistical-analysis-paired-tests.csv",
        ],
    },
    {
        "step": "Build final method comparison table",
        "calls_qwen": False,
        "command": f"{PY} src\\build_final_method_comparison_table.py",
        "primary_outputs": [
            "output/final-method-comparison-table.md",
            "output/final-method-comparison-table.csv",
        ],
    },
    {
        "step": "Build schema adapter inventory",
        "calls_qwen": False,
        "command": f"{PY} src\\build_schema_adapter_inventory.py",
        "primary_outputs": [
            "output/auto-policy-v3-schema-adapter-inventory.md",
            "output/auto-policy-v3-schema-adapter-inventory.csv",
        ],
    },
    {
        "step": "Build schema adapter onboarding evidence",
        "calls_qwen": False,
        "command": f"{PY} src\\build_schema_adapter_onboarding_evidence.py",
        "primary_outputs": [
            "output/auto-policy-v3-schema-adapter-onboarding-evidence.md",
            "output/auto-policy-v3-schema-adapter-onboarding-by-domain.csv",
        ],
    },
    {
        "step": "Build external validity register",
        "calls_qwen": False,
        "command": f"{PY} src\\build_external_validity_register.py",
        "primary_outputs": [
            "output/auto-policy-v3-external-validity-register.md",
            "output/auto-policy-v3-external-validity-register.csv",
        ],
    },
    {
        "step": "Build external-real-v2 expansion plan",
        "calls_qwen": False,
        "command": f"{PY} src\\build_external_real_v2_expansion_plan.py",
        "primary_outputs": [
            "output/external-real-v2-expansion-plan.md",
            "benchmark/external-real-v2/source-intake/external-real-v2-source-intake-plan.csv",
        ],
    },
    {
        "step": "Build completed external-real-v2 68-event benchmark",
        "calls_qwen": False,
        "command": f"{PY} src\\build_external_real_v2_complete.py",
        "primary_outputs": [
            "benchmark/external-real-v2/input/external-real-event-template.csv",
            "output/external-real-v2-freeze-manifest-68.json",
        ],
    },
    {
        "step": "Validate completed external-real-v2 benchmark",
        "calls_qwen": False,
        "command": f"{PY} src\\validate_external_real_v2.py --min-ready-events 60 --min-domains 5 --min-per-type 15 --prefix external-real-v2-validation-68",
        "primary_outputs": [
            "output/external-real-v2-validation-68-summary.json",
            "output/external-real-v2-validation-68-report.md",
        ],
    },
    {
        "step": "Build paper-level main experiment table",
        "calls_qwen": False,
        "command": f"{PY} src\\build_paper_experiment_main_table.py",
        "primary_outputs": [
            "output/paper-experiment-main-table.md",
            "output/paper-experiment-main-table.csv",
        ],
    },
    {
        "step": "Build V3 failure-case analysis tables",
        "calls_qwen": False,
        "command": f"{PY} src\\build_auto_policy_v3_failure_case_analysis.py",
        "primary_outputs": [
            "output/auto-policy-v3-failure-case-analysis.md",
        ],
    },
    {
        "step": "Build method-flow and input-isolation diagrams",
        "calls_qwen": False,
        "command": f"{PY} src\\build_method_flow_input_isolation_diagrams.py",
        "primary_outputs": [
            "output/method-flow-and-input-isolation-diagrams.md",
            "output/auto-policy-v3-method-flow.mmd",
            "output/auto-policy-v3-input-isolation.mmd",
            "output/auto-policy-v3-policy-boundary.mmd",
        ],
    },
    {
        "step": "Verify expected Auto Policy V3 artifacts and headline values",
        "calls_qwen": False,
        "command": f"{PY} src\\verify_auto_policy_v3_artifacts.py",
        "primary_outputs": [
            "terminal PASS/FAIL",
        ],
    },
]


OFFLINE_COMMANDS = [
    {
        "step": "Rebuild V2 candidate repair from existing raw",
        "command": f"{PY} src\\run_auto_policy_v2_candidate_repair.py --raw-dir output\\auto-policy-v2\\raw --prefix auto-policy-v2-candidate-repair-r5-seed20260820 --runs 5 --seed 20260820",
    },
    {
        "step": "Rebuild V3 candidate repair from existing raw",
        "command": f"{PY} src\\run_auto_policy_v2_candidate_repair.py --raw-dir output\\auto-policy-v3\\raw --prefix auto-policy-v3-candidate-repair-r5-seed20260820 --runs 5 --seed 20260820",
    },
    {
        "step": "Rebuild component ablation from existing/generated raw",
        "command": f"{PY} src\\run_auto_policy_v3_component_ablation.py --prefix auto-policy-v3-component-ablation-r5-seed20260820",
    },
    {
        "step": "Rebuild robustness summaries from existing raw",
        "command": f"{PY} src\\run_auto_policy_v3_robustness.py --skip-generation --prefix auto-policy-v3-robustness-r3-seed20260827",
    },
    {
        "step": "Rebuild negative safety from existing raw",
        "command": f"{PY} src\\run_auto_policy_v3_negative_safety.py --skip-generation --prefix auto-policy-v3-negative-safety-r1-seed20260827",
    },
    {
        "step": "Rebuild naturalness, transfer, conflict-gate, statistics, tables, failure analysis, and diagrams",
        "command": f"{PY} src\\run_auto_policy_v3_conflict_gate.py --prefix auto-policy-v3-conflict-gate-r1-seed20260827 && {PY} src\\run_auto_policy_v3_natural_evidence_robustness.py --variants NATURAL_PARAGRAPH,RAW_SHORT_CONTEXT,RAW_PROVENANCE_CONTEXT,RAW_PROVENANCE_METADATA_LIGHT --runs 1 --seed 20260827 --prefix auto-policy-v3-natural-evidence-robustness-r1-seed20260827 --skip-generation && {PY} src\\run_auto_policy_v3_schema_transfer.py --prefix auto-policy-v3-schema-transfer-r5-seed20260820-semantic-only --skip-repair && {PY} src\\run_auto_policy_v3_natural_conflict_safety.py --runs 1 --seed 20260827 --prefix auto-policy-v3-natural-conflict-safety-r1-seed20260827 --skip-generation && {PY} src\\run_auto_policy_v3_natural_conflict_gate.py --prefix auto-policy-v3-natural-conflict-gate-r1-seed20260827 && {PY} src\\run_natural_conflict_real_v1.py --runs 5 --seed 20260820 --prefix natural-conflict-real-v1-gate-r5-seed20260820 && {PY} src\\analyze_auto_policy_v3_extended_statistics.py && {PY} src\\build_final_method_comparison_table.py && {PY} src\\build_schema_adapter_inventory.py && {PY} src\\build_schema_adapter_onboarding_evidence.py && {PY} src\\build_external_validity_register.py && {PY} src\\build_external_real_v2_expansion_plan.py && {PY} src\\build_external_real_v2_complete.py && {PY} src\\validate_external_real_v2.py --min-ready-events 60 --min-domains 5 --min-per-type 15 --prefix external-real-v2-validation-68 && {PY} src\\build_paper_experiment_main_table.py && {PY} src\\build_auto_policy_v3_failure_case_analysis.py && {PY} src\\build_method_flow_input_isolation_diagrams.py && {PY} src\\verify_auto_policy_v3_artifacts.py",
    },
]


EXPECTED_CHECKS = [
    "external-real-v1 validation: events_ready=30, errors=0, warnings=0",
    "symbolic closure: selected=30/30, Oracle correct=30/30, full closure=30/30",
    "candidate ablation: DIRECT_FREE=91.33%, OPTION_FORMAL_POLICY=100.00%, HARD_GATE=100.00%",
    "Auto Policy V2 candidate repair: Oracle/closure=82.67%, strict=13/30",
    "Auto Policy V3 candidate repair: Oracle/closure=100.00%, strict=30/30",
    "V3 robustness distractor metadata-light: closure=61.11%",
    "V3 negative safety before gate: safe abstain=57.33%, unsafe selection=42.67%",
    "V3+gate negative safety: safe abstain=98.67%, unsafe selection=1.33%",
    "natural evidence RAW_SHORT_CONTEXT: semantic/closure=50.00%, strict=15/30",
    "natural evidence RAW_PROVENANCE_CONTEXT: semantic/closure=100.00%, strict=30/30",
    "natural evidence RAW_PROVENANCE_METADATA_LIGHT: semantic/closure=80.00%, strict=24/30",
    "schema transfer held-out-domain canonical rate: 0.00% for all held-out domains",
    "schema onboarding: web_accessibility=21 events / 3 families; digital_identity and insurance remain weak reuse",
    "enhanced natural-conflict gate: safe abstain=100.00% on 60/60 conflict attempts",
    "natural-conflict-real-v1: safe abstain=100.00% on 300/300 real-public-excerpt mixture attempts",
    "external-real-v2 data scale: 68 READY events, 5 domains, type counts 21/25/22, validation errors=0 warnings=0",
    "extended statistics: V3 vs V2 p=2.98e-08; raw provenance vs raw short p=6.10e-05; enhanced natural gate vs original p=1.82e-12",
]


def git_value(args: list[str], fallback: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return fallback
    return completed.stdout.strip() or fallback


def git_status() -> dict[str, object]:
    short = git_value(["status", "--short"], "")
    return {
        "head": git_value(["rev-parse", "HEAD"], "unknown"),
        "dirty": bool(short),
        "status_short_line_count": len(short.splitlines()) if short else 0,
    }


def code_block(command: str) -> str:
    return f"```powershell\n{command}\n```"


def command_table(commands: list[dict[str, object]], include_qwen: bool = True) -> str:
    columns = ["Step", "Calls Qwen", "Expected Qwen Calls", "Command", "Primary Outputs"] if include_qwen else ["Step", "Command"]
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for item in commands:
        if include_qwen:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(item["step"]),
                        "Yes" if item.get("calls_qwen") else "No",
                        str(item.get("qwen_calls_expected", 0) or 0),
                        f"`{item['command']}`",
                        "<br/>".join(f"`{output}`" for output in item.get("primary_outputs", [])),
                    ]
                )
                + " |"
            )
        else:
            lines.append(f"| {item['step']} | `{item['command']}` |")
    return "\n".join(lines)


def main() -> int:
    total_qwen = sum(int(item.get("qwen_calls_expected", 0) or 0) for item in FULL_COMMANDS)
    git = git_status()
    md = f"""# Reproducibility Package

This runbook reproduces the current external-real-v1 Auto Policy V3 experiment package from `G:\\LearnAI\\ontology-evolution`.

## Environment

- Repository root: `G:\\LearnAI\\ontology-evolution`
- Python: `{PY}`
- Current Git HEAD when this package was generated: `{git['head']}`
- Current Git dirty state when this package was generated: `{git['dirty']}` ({git['status_short_line_count']} changed/untracked entries)
- Main model for Qwen steps: `qwen3.5:9b` through local Ollama `http://localhost:11434/api/generate`
- Fixed seeds: candidate ablation and V2/V3 main runs use `20260820`; robustness and negative safety use `20260827`.
- Private Oracle files are used only by evaluation scripts after selections/gate decisions are fixed.

Before running Qwen-dependent steps, verify Ollama is running and the model is available:

```powershell
ollama list
```

## Full Reproduction Order

Expected Qwen calls for the full reproduction path: approximately `{total_qwen}`. Runtime depends on local Ollama speed.

{command_table(FULL_COMMANDS, include_qwen=True)}

## Faster Offline Rebuild

Use this path when the raw Qwen outputs already exist and only summaries, repair closure, statistics, tables, and diagrams need to be regenerated. These commands do not intentionally call Qwen.

{command_table(OFFLINE_COMMANDS, include_qwen=False)}

## Expected Headline Checks

{chr(10).join(f"- {item}" for item in EXPECTED_CHECKS)}

## Artifact Map

- Main report: `output/semantic-v2-methodology-corrected-report.md`
- Main table: `output/paper-experiment-main-table.md`
- Final method comparison table: `output/final-method-comparison-table.md`
- Failure analysis: `output/auto-policy-v3-failure-case-analysis.md`
- Method/input-isolation diagrams: `output/method-flow-and-input-isolation-diagrams.md`
- Naturalness and transfer report: `output/auto-policy-v3-natural-and-transfer-experiments.md`
- Real-source natural conflict report: `output/natural-conflict-real-v1-gate-r5-seed20260820.md`
- Schema adapter inventory: `output/auto-policy-v3-schema-adapter-inventory.md`
- Schema adapter onboarding evidence: `output/auto-policy-v3-schema-adapter-onboarding-evidence.md`
- External-validity register: `output/auto-policy-v3-external-validity-register.md`
- External-real-v2 expansion plan: `output/external-real-v2-expansion-plan.md`
- External-real-v2 validation report: `output/external-real-v2-validation-68-report.md`
- External-real-v2 freeze manifest: `output/external-real-v2-freeze-manifest-68.json`
- Statistical analysis: `output/auto-policy-v3-extended-statistical-analysis-metrics.csv`, `output/auto-policy-v3-extended-statistical-analysis-paired-tests.csv`
- Freeze manifest: `output/external-real-v1-freeze-manifest-30-private-oracle-fixed.json`

## Methodology Boundaries

- `OPTION_FORMAL_POLICY` and `OPTION_FORMAL_POLICY_HARD_GATE` are policy-available upper bounds, not fully automatic methods.
- `AUTO_POLICY_V3` and `AUTO_POLICY_V3_PLUS_GATE` are the candidate-blind automatic line.
- The current 30-event `external-real-v1` set is a public-source validation benchmark, not a large independently annotated real-world corpus.
- `policy_cost` is a heuristic complexity score unless explicitly replaced by measured annotation time.
- The enhanced conflict gate is validated on constructed mixed-evidence natural-conflict variants, not on an independent real conflict corpus.
- The freeze manifest hashes the benchmark state for final reruns. It does not prove that all earlier exploratory experiments happened after a clean freeze.
"""
    md_path = OUT / f"{PREFIX}.md"
    json_path = OUT / f"{PREFIX}.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "root": str(ROOT),
                "python": PY,
                "git": git,
                "full_commands": FULL_COMMANDS,
                "offline_commands": OFFLINE_COMMANDS,
                "expected_checks": EXPECTED_CHECKS,
                "expected_full_qwen_calls": total_qwen,
                "markdown": str(md_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"markdown={md_path}")
    print(f"json={json_path}")
    print(f"expected_full_qwen_calls={total_qwen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
