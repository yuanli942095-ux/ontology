from __future__ import annotations

"""Exp15: export deterministic end-to-end audit traces (3 success + 2 failure cases)."""

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from exp9_baseline_common import ECR_CONTROL_DIR
from paper_final_validation_common import (
    DEFAULT_BENCHMARK,
    PAPER_VALIDATION_ROOT,
    benchmark_paths,
    read_csv,
    utc_now_iso,
    write_summary_json,
)
from semantic_v2_common import PROJECT_DIR


EXP15_ROOT = PAPER_VALIDATION_ROOT / "15-audit-trace"
RUN_ROOT = ECR_CONTROL_DIR
M13_DETAILS = (
    RUN_ROOT
    / "m13-pilot"
    / "arm-d-rule-refinement"
    / "ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
M16_DETAILS = RUN_ROOT / "m16-candidate-entailment" / "m16-candidate-entailment-details.csv"
M16_COMBINED = RUN_ROOT / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv"

TYPE_ORDER = (
    "TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE",
)
FAILURE_CASES = ("H5_E017", "H5_E003")  # GRE abstention chain; permuted-ID failure exemplar


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def pick_success_cases(rows: list[dict[str, str]], runs_expected: int = 5) -> dict[str, str]:
    by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_type[row["semantic_type"]].append(row)
    chosen: dict[str, str] = {}
    for semantic_type in TYPE_ORDER:
        event_ids = sorted({r["event_id"] for r in by_type.get(semantic_type, [])})
        for event_id in event_ids:
            attempt_rows = [r for r in by_type[semantic_type] if r["event_id"] == event_id]
            if len(attempt_rows) < runs_expected:
                continue
            if all(truth(r.get("closure_success", r.get("full_closure_success", ""))) for r in attempt_rows):
                chosen[semantic_type] = event_id
                break
    return chosen


def load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def copy_if_exists(src: Path, dst: Path) -> str:
    if not src.is_file():
        return ""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return str(dst.relative_to(PROJECT_DIR))


def export_case(
    event_id: str,
    run: str,
    seed: str,
    benchmark: Path,
    out_dir: Path,
    label: str,
) -> dict[str, Any]:
    case_dir = out_dir / label
    case_dir.mkdir(parents=True, exist_ok=True)
    paths = benchmark_paths(benchmark)
    excerpt = paths["benchmark_dir"] / "public" / "excerpts" / f"{event_id}-evidence.md"
    focus = RUN_ROOT / "m13-pilot" / "focused-evidence" / f"{event_id}-run{run}-seed{seed}-focused.md"
    m13_raw = (
        RUN_ROOT
        / "m13-pilot"
        / "arm-d-rule-refinement"
        / "raw_window_metadata_light"
        / "raw"
        / f"{event_id}-run{run}-seed{seed}.json"
    )
    m14_raw = RUN_ROOT / "m14-clause-level-gre-css-recovery" / "raw" / f"{event_id}-run{run}-seed{seed}.json"
    m15_raw = RUN_ROOT / "m15-temporal-anchor-recovery" / "raw" / f"{event_id}-run{run}-seed{seed}.json"
    m16_row = None
    for row in read_csv(M16_DETAILS):
        if row["event_id"] == event_id and str(row.get("run", "")) == run:
            m16_row = row
            break
    m13_row = None
    for row in read_csv(M13_DETAILS):
        if row["event_id"] == event_id and str(row.get("run", "")) == run:
            m13_row = row
            break

    stages = {
        "stage2_raw_evidence": copy_if_exists(excerpt, case_dir / "stage2-raw-evidence.md"),
        "stage3_evidence_focus": copy_if_exists(focus, case_dir / "stage3-evidence-focus.md"),
        "stage4_m13_raw": copy_if_exists(m13_raw, case_dir / "stage4-m13-raw.json"),
        "stage5_m14_raw": copy_if_exists(m14_raw, case_dir / "stage5-m14-raw.json"),
        "stage5_m15_raw": copy_if_exists(m15_raw, case_dir / "stage5-m15-raw.json"),
    }
    if m13_row:
        (case_dir / "stage4-m13-ir.json").write_text(
            json.dumps(
                {
                    "ir_json": m13_row.get("ir_json", ""),
                    "candidate_scores_json": m13_row.get("candidate_scores_json", ""),
                    "decision_path": m13_row.get("decision_path", ""),
                    "selected_candidate_id": m13_row.get("selected_candidate_id", ""),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        stages["stage4_m13_ir"] = str((case_dir / "stage4-m13-ir.json").relative_to(PROJECT_DIR))
    if m16_row:
        (case_dir / "stage7-m16-details.json").write_text(
            json.dumps(m16_row, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        stages["stage7_m16"] = str((case_dir / "stage7-m16-details.json").relative_to(PROJECT_DIR))
        if m16_row.get("verdicts_json"):
            try:
                verdicts = json.loads(m16_row["verdicts_json"])
            except json.JSONDecodeError:
                verdicts = m16_row["verdicts_json"]
            (case_dir / "stage7-candidate-verdicts.json").write_text(
                json.dumps(verdicts, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    manifest = {
        "event_id": event_id,
        "run": run,
        "seed": seed,
        "label": label,
        "stages": stages,
    }
    (case_dir / "audit-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=EXP15_ROOT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    combined = read_csv(M16_COMBINED)
    success_map = pick_success_cases(combined)
    manifests: list[dict[str, Any]] = []

    for semantic_type, event_id in success_map.items():
        runs = [r for r in combined if r["event_id"] == event_id and truth(r.get("closure_success", ""))]
        run = str(runs[0]["run"])
        seed = str(runs[0]["seed"])
        label = f"success-{semantic_type}-run{run}"
        manifests.append(export_case(event_id, run, seed, args.benchmark_dir, args.output_dir, label))

    for event_id in FAILURE_CASES:
        rows = [r for r in combined if r["event_id"] == event_id]
        if not rows:
            continue
        failed = [r for r in rows if not truth(r.get("closure_success", r.get("full_closure_success", "")))]
        row = failed[0] if failed else rows[0]
        label = f"failure-{event_id}-run{row['run']}"
        manifests.append(
            export_case(event_id, str(row["run"]), str(row["seed"]), args.benchmark_dir, args.output_dir, label)
        )

    summary = {
        "completed_at_utc": utc_now_iso(),
        "run_root": str(RUN_ROOT.relative_to(PROJECT_DIR)),
        "success_cases": success_map,
        "failure_cases": list(FAILURE_CASES),
        "exported": len(manifests),
    }
    write_summary_json(args.output_dir / "audit-trace-summary.json", summary)
    (args.output_dir / "audit-trace-report.md").write_text(
        "# Exp15 Audit Trace Export\n\n"
        f"- Success cases: {json.dumps(success_map, ensure_ascii=False)}\n"
        f"- Failure cases: {', '.join(FAILURE_CASES)}\n"
        f"- Exported bundles: {len(manifests)}\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
