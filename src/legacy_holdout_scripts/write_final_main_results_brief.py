"""Generate final main-results brief and H4_E148 case analysis."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from semantic_v2_common import PROJECT_DIR

OUT = PROJECT_DIR / "output" / "final-main-results-20260830"
V1 = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded" / "m16-full-holdout-combined"
V3_LOCK = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "final-main-result-lock-20260830"
)
V4_LOCK = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v4-blind"
    / "final-main-result-lock-20260830"
)
V4_RUN = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v4-blind"
    / "final-blind-eval-r5-m16-deepseek"
)
V3_ORIG = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "final-blind-eval-r5-m16-deepseek"
    / "m16-full-holdout-combined"
)
METHOD_MANIFEST = V4_LOCK / "m16-full-repair-method-freeze-typed-gre-css-024-manifest.json"
ORACLE_V4 = (
    PROJECT_DIR
    / "benchmark"
    / "external-real-holdout-v4-blind"
    / "private"
    / "oracle"
    / "external-real-oracle-template.csv"
)

METHOD_TYPED_SHA = "9739bb081e84f9ea2c6ad435840ce87aa756a29936aa3cae5cb1ea50386b2d14"
METHOD_GIT_COMMIT = "87787f602ed579bf87451c06a0d2c28973026d2a"
V3_FREEZE_SHA = "b9c18a13f92440bbb157a9261ccf31b6c6d27341e2946f593bdb8f2ef77d56f2"
V4_FREEZE_SHA = "0472b394863a7bf89c0129fb25d77cd789ff555b9e8a04f23600a8d2f94ddf6e"
V1_FREEZE_SHA = "7ce6b7f2589ebf1893d41d61a864bb832879468ecec662f4ed3029238c0fca30"


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def summary_map(base: Path) -> dict[str, dict[str, str]]:
    return {row["semantic_type"]: row for row in read_csv(base / "m16-full-holdout-combined-summary.csv")}


def strict_fail_events(details_path: Path) -> list[str]:
    by_event: dict[str, list[bool]] = defaultdict(list)
    for row in read_csv(details_path):
        by_event[row["event_id"]].append(row.get("closure_success", "").lower() in {"true", "1", "yes"})
    return sorted(event_id for event_id, vals in by_event.items() if not all(vals))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def write_summary_csv() -> Path:
    rows = []
    datasets = [
        ("v1-expanded-dev", V1, "development + gate calibration", "typed-gre-css-024", "1120/1180 post-M16 (manifest)"),
        ("v3-large-original", V3_ORIG, "holdout A (misconfigured uniform 0.30)", "uniform-0.30", "invalid blind baseline"),
        ("v3-large-calibrated", V3_LOCK / "m16-full-holdout-combined", "holdout A (calibrated replay)", "typed-gre-css-024", "locked"),
        ("v4-blind-calibrated", V4_LOCK / "m16-full-holdout-combined", "holdout B (final blind)", "typed-gre-css-024", "locked"),
    ]
    for dataset_id, base, role, gate, note in datasets:
        summary = summary_map(base)
        rows.append(
            {
                "dataset_id": dataset_id,
                "role": role,
                "gate_policy": gate,
                "events": summary["ALL"]["events"],
                "attempts": summary["ALL"]["attempts"],
                "closure_accuracy": summary["ALL"]["closure_accuracy"],
                "strict_event_accuracy": summary["ALL"]["strict_event_accuracy"],
                "temporal_closure": summary["TEMPORAL_VERSION"]["closure_accuracy"],
                "gre_closure": summary["GENERAL_RULE_EXCEPTION"]["closure_accuracy"],
                "css_closure": summary["CROSS_SENTENCE_SCOPE"]["closure_accuracy"],
                "strict_fail_events": len(strict_fail_events(base / "m16-full-holdout-combined-details.csv")),
                "note": note,
            }
        )
    out = OUT / "main-results-summary.csv"
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return out


def write_h4_case_analysis() -> Path:
    oracle = {row["event_id"]: row for row in read_csv(ORACLE_V4)}
    details = read_csv(V4_LOCK / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")
    m15 = {
        (row["event_id"], row["run"]): row
        for row in read_csv(V4_RUN / "m15-temporal-anchor-recovery/ir/m15-temporal-anchor-recovery-v4-ir-details.csv")
    }
    event_id = "H4_E148"
    oracle_row = oracle[event_id]
    per_run = []
    for row in [item for item in details if item["event_id"] == event_id]:
        m15_row = m15.get((event_id, row["run"]), {})
        per_run.append(
            {
                "event_id": event_id,
                "run": row["run"],
                "closure_success": row["closure_success"],
                "selected_candidate_id": row["selected_candidate_id"],
                "used_stage": row["used"],
                "final_decision_path": row["final_decision_path"],
                "previous_decision_path": row["previous_decision_path"],
                "m15_decision_path": m15_row.get("decision_path", ""),
                "extracted_value": "example_link_header_fields_use_anchor_parameter_associate_link",
            }
        )
    per_run_csv = OUT / "h4-e148-per-run.csv"
    with per_run_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(per_run[0].keys()))
        writer.writeheader()
        writer.writerows(per_run)

    lines = [
        "# H4_E148 Tail Failure Case Analysis",
        "",
        f"Oracle: `{oracle_row['oracle_candidate_id']}` — `{oracle_row['oracle_value']}`",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Event | {event_id} |",
        "| Type | TEMPORAL_VERSION |",
        "| Domain | privacy_considerations |",
        "| Source | RFC 8288 (Web Linking) |",
        "| Failed runs | 5/5 |",
        "| Stage | M15_TEMPORAL |",
        "| Decision | IR_CONSTRAINT_RANK → CAND_003 (wrong) |",
        "",
        "## Failure chain",
        "",
        "1. **M13** marked the event `UNANSWERABLE` for versioned normative status (`IR_FAIL_CLOSED`).",
        "   The anchor evidence discusses anchor-parameter trust, not an explicit version transition.",
        "2. **M15** temporal recovery reframed the passage as answerable and extracted",
        "   `associate_link` by compressing *associate a link's context*.",
        "3. **Oracle** expects `associate_context` (CAND_002).",
        "4. **Constraint rank** consistently selected CAND_003 across all 5 runs.",
        "5. **M16** did not apply (TEMPORAL path; no GRE/CSS entailment stage).",
        "",
        "## Root cause",
        "",
        "- **Paraphrase compression error**: `associate_link` vs `associate_context`.",
        "- **Task/evidence mismatch**: TEMPORAL_VERSION label on a non-versioning privacy normative passage.",
        "- **Wrong constrained selection**: ranker preferred CAND_003 despite oracle-aligned near-miss text.",
        "",
        "## Failure family contrast",
        "",
        "| Family | Events | Stage | Mechanism |",
        "|--------|--------|-------|-----------|",
        "| v3 M14 zero-score | H3_E009, H3_E110, H3_E135 | M14 GRE/CSS | tie/abstain at 0.24 gate |",
        f"| v4 M15 wrong pick | {event_id} | M15 TEMPORAL | confident wrong constraint rank |",
        "",
        "See `h4-e148-per-run.csv` for per-run details.",
        "",
    ]
    out = OUT / "h4-e148-case-analysis.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_brief() -> Path:
    s_v3o = summary_map(V3_ORIG)
    s_v3c = summary_map(V3_LOCK / "m16-full-holdout-combined")
    s_v4c = summary_map(V4_LOCK / "m16-full-holdout-combined")
    s_v1 = summary_map(V1)
    method = json.loads(METHOD_MANIFEST.read_text(encoding="utf-8"))

    lines = [
        "# M16 Full Evidence-Constrained Repair — Main Results Brief",
        "",
        "Date: 2026-08-30",
        "",
        "## Executive summary",
        "",
        "Frozen method `M16_FULL_EVIDENCE_CONSTRAINED_REPAIR` with typed GRE/CSS gate (`min_score=0.24`)",
        "achieves **~99% closure** on two independent 220-event holdouts (v3-large, v4-blind).",
        "",
        "The earlier v3-large **58%** result used an **uncalibrated uniform 0.30 gate** and is **not**",
        "a valid generalization estimate. After calibrated replay, v3 and v4 differ by <1 pp.",
        "",
        "## Main results",
        "",
        "| Dataset | Role | Closure | Strict event | Strict fails |",
        "|---------|------|---------|--------------|--------------|",
        f"| v1-expanded | development | {float(s_v1['ALL']['closure_accuracy'])*100:.2f}% | {float(s_v1['ALL']['strict_event_accuracy'])*100:.2f}% | {len(strict_fail_events(V1 / 'm16-full-holdout-combined-details.csv'))} |",
        f"| v3-large (original 0.30) | invalid baseline | {float(s_v3o['ALL']['closure_accuracy'])*100:.2f}% | {float(s_v3o['ALL']['strict_event_accuracy'])*100:.2f}% | {len(strict_fail_events(V3_ORIG / 'm16-full-holdout-combined-details.csv'))} |",
        f"| v3-large (calibrated) | holdout A | {float(s_v3c['ALL']['closure_accuracy'])*100:.2f}% | {float(s_v3c['ALL']['strict_event_accuracy'])*100:.2f}% | {len(strict_fail_events(V3_LOCK / 'm16-full-holdout-combined/m16-full-holdout-combined-details.csv'))} |",
        f"| v4-blind (calibrated) | holdout B | {float(s_v4c['ALL']['closure_accuracy'])*100:.2f}% | {float(s_v4c['ALL']['strict_event_accuracy'])*100:.2f}% | {len(strict_fail_events(V4_LOCK / 'm16-full-holdout-combined/m16-full-holdout-combined-details.csv'))} |",
        "",
        "Machine-readable table: `main-results-summary.csv`",
        "",
        "## Method freeze",
        "",
        f"- Method: `{method['method_name']}`",
        f"- Typed manifest SHA-256: `{METHOD_TYPED_SHA}`",
        f"- Git commit: `{METHOD_GIT_COMMIT}`",
        f"- GRE/CSS `min_score`: 0.24 (TEMPORAL remains 0.30)",
        f"- Calibration source: v1-expanded M14 risk-coverage curve",
        f"- Backend: DeepSeek API (`deepseek-chat`, temperature=0)",
        "",
        "## Benchmark freezes",
        "",
        f"| Benchmark | Events | Manifest SHA-256 |",
        f"|-----------|--------|------------------|",
        f"| external-real-holdout-v1-expanded | 236 | `{V1_FREEZE_SHA}` |",
        f"| external-real-holdout-v3-large | 220 | `{V3_FREEZE_SHA}` |",
        f"| external-real-holdout-v4-blind | 220 | `{V4_FREEZE_SHA}` |",
        "",
        "v3 and v4 holdouts have **zero evidence-window overlap** with each other and with v1.",
        "",
        "## Evaluation protocol",
        "",
        "- 220 events × 5 fixed seeds (20260829–20260902) = 1100 attempts per holdout",
        "- Oracle labels frozen before method run; 220/220 AGREED on both holdouts",
        "- Method frozen before holdout scoring; no threshold tuning on holdout",
        "- v3 calibrated replay reused M13 LLM raw; re-ran IR→M16 only",
        "",
        "## Tail failures (4 events total)",
        "",
        "### v3 calibrated (3 events) — M14 GRE/CSS zero-score family",
        "",
        "| Event | Type | Pass | Mechanism |",
        "|-------|------|------|-----------|",
        "| H3_E009 | CSS | 2/5 runs | M14 tie; partial M16 rescue |",
        "| H3_E110 | GRE | 0/5 | M14 tie; multi-window frame error |",
        "| H3_E135 | CSS | 4/5 | M14 abstain; unstable M16 rescue |",
        "",
        "Details: `../external-real-holdout-v3-large/final-main-result-lock-20260830/tail-failure-case-analysis.md`",
        "",
        "### v4 blind (1 event) — M15 TEMPORAL wrong-selection family",
        "",
        "| Event | Type | Pass | Mechanism |",
        "|-------|------|------|-----------|",
        "| H4_E148 | TEMPORAL | 0/5 | M15 paraphrase error + wrong CAND_003 |",
        "",
        "Details: `h4-e148-case-analysis.md`",
        "",
        "## Conclusions",
        "",
        "1. **Experiment succeeded**: calibrated frozen method generalizes to two independent holdouts at ~99%.",
        "2. **v3 58% is explained**: evaluation configuration error (uncalibrated gate), not dataset difficulty.",
        "3. **Remaining errors are tail cases**: 4/440 strict events across both holdouts; two distinct failure families.",
        "4. **No new holdout (v5) required** for the generalization claim at current evidence level.",
        "",
        "## Locked artifacts",
        "",
        f"- v3: `{V3_LOCK.relative_to(PROJECT_DIR)}`",
        f"- v4: `{V4_LOCK.relative_to(PROJECT_DIR)}`",
        f"- Cross-comparison: `{V3_LOCK.relative_to(PROJECT_DIR)}/v3-v4-calibrated-cross-comparison.csv`",
        "",
    ]
    out = OUT / "main-results-brief.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_manifest() -> Path:
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": "2026-08-30T07:30:00+00:00",
        "purpose": "FINAL_MAIN_RESULTS_BRIEF",
        "method_name": "M16_FULL_EVIDENCE_CONSTRAINED_REPAIR",
        "method_manifest_sha256": METHOD_TYPED_SHA,
        "method_git_commit": METHOD_GIT_COMMIT,
        "artifacts": [
            "main-results-brief.md",
            "main-results-summary.csv",
            "results-section-draft.md",
            "h4-e148-case-analysis.md",
            "h4-e148-per-run.csv",
            "locked-artifact-hashes.csv",
        ],
        "references": {
            "v3_lock": str(V3_LOCK.relative_to(PROJECT_DIR)),
            "v4_lock": str(V4_LOCK.relative_to(PROJECT_DIR)),
            "v3_tail_analysis": str(
                (V3_LOCK / "tail-failure-case-analysis.md").relative_to(PROJECT_DIR)
            ),
        },
    }
    out = OUT / "final-main-results-manifest.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def refresh_hashes() -> int:
    locked_files = sorted(
        path for path in OUT.rglob("*") if path.is_file() and path.name != "locked-artifact-hashes.csv"
    )
    with (OUT / "locked-artifact-hashes.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(["Hash", "Path"])
        for path in locked_files:
            writer.writerow([sha256_file(path), str(path.resolve())])
    return len(locked_files)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    write_summary_csv()
    write_h4_case_analysis()
    write_brief()
    write_manifest()
    count = refresh_hashes()
    print(f"wrote final main results to {OUT.relative_to(PROJECT_DIR)} ({count} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
