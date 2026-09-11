from __future__ import annotations

"""Run all Phase 1 paper-validation artifacts."""

import subprocess
import sys
from pathlib import Path

from paper_final_validation_common import (
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_DEEPSEEK_PRED_DIR,
    DEFAULT_METHOD_FREEZE_DIR,
    audit_cq_implementation,
    load_freeze_sha256,
    load_method_freeze_sha256,
    verify_deepseek_prediction_dir,
    write_summary_json,
    utc_now_iso,
    PAPER_VALIDATION_ROOT,
)


STEPS = [
    ("closure-stress", ["python", "src/run_closure_stress_test_v5.py"]),
    ("m15-gold-sheet", ["python", "src/generate_m15_temporal_gold_sheet.py"]),
    ("m15-audit-framework", ["python", "src/run_m15_semantic_audit.py"]),
    ("independent-annotation-sheet", ["python", "src/generate_independent_annotation_sheet.py"]),
    ("independent-agreement-stub", ["python", "src/compute_independent_annotation_agreement.py"]),
    ("strict-drift-draft", ["python", "src/generate_strict_drift_subset_draft.py"]),
    ("strict-drift-results-stub", ["python", "src/apply_strict_drift_subset_results.py"]),
    ("phase2-pool-scaffold", ["python", "src/scaffold_phase2_candidate_pool_eval.py"]),
    ("phase2-qwen-scaffold", ["python", "src/scaffold_phase2_qwen_v5_eval.py"]),
]


def main() -> int:
    PAPER_VALIDATION_ROOT.mkdir(parents=True, exist_ok=True)
    benchmark_sha256, _ = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_freeze_sha256, _ = load_method_freeze_sha256(DEFAULT_METHOD_FREEZE_DIR)
    cq_audit = audit_cq_implementation()
    deepseek_check = verify_deepseek_prediction_dir(
        DEFAULT_DEEPSEEK_PRED_DIR,
        benchmark_sha256=benchmark_sha256,
        method_freeze_sha256=method_freeze_sha256,
    )
    for name, command in STEPS:
        print(f"[phase1] running {name}")
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1])
        if result.returncode != 0:
            print(f"[phase1] failed at {name}")
            return result.returncode
    write_summary_json(
        PAPER_VALIDATION_ROOT / "phase1-qc.json",
        {
            "generated_at_utc": utc_now_iso(),
            "benchmark_sha256": benchmark_sha256,
            "method_freeze_sha256": method_freeze_sha256,
            "deepseek_prediction_check": deepseek_check,
            "cq_audit": cq_audit,
            "status": "PHASE1_ARTIFACTS_GENERATED",
        },
    )
    print(f"[phase1] complete output={PAPER_VALIDATION_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
