"""Write tail-failure case analysis and v3/v4 calibrated cross-comparison artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from semantic_v2_common import PROJECT_DIR

LOCK = (
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
V3_ORIG = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "final-blind-eval-r5-m16-deepseek"
    / "m16-full-holdout-combined"
)
ROOT = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "final-blind-eval-r5-m16-deepseek-typed-gre-css-024"
)
ORACLE = (
    PROJECT_DIR
    / "benchmark"
    / "external-real-holdout-v3-large"
    / "private"
    / "oracle"
    / "external-real-oracle-template.csv"
)


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def summary_rows(base: Path) -> dict[str, dict[str, str]]:
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


def write_cross_comparison() -> Path:
    s_v3o = summary_rows(V3_ORIG)
    s_v3c = summary_rows(LOCK / "m16-full-holdout-combined")
    s_v4c = summary_rows(V4_LOCK / "m16-full-holdout-combined")
    types = ["ALL", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"]
    out = LOCK / "v3-v4-calibrated-cross-comparison.csv"
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "semantic_type",
                "metric",
                "v3_original_uniform_030",
                "v3_calibrated_typed_024",
                "v4_calibrated_typed_024",
                "v3_cal_minus_v3_orig",
                "v3_cal_minus_v4_cal",
            ]
        )
        for semantic_type in types:
            for metric in ("closure_accuracy", "strict_event_accuracy"):
                v3o = float(s_v3o[semantic_type][metric])
                v3c = float(s_v3c[semantic_type][metric])
                v4c = float(s_v4c[semantic_type][metric])
                writer.writerow(
                    [
                        semantic_type,
                        metric,
                        f"{v3o:.4f}",
                        f"{v3c:.4f}",
                        f"{v4c:.4f}",
                        f"{v3c - v3o:+.4f}",
                        f"{v3c - v4c:+.4f}",
                    ]
                )
        writer.writerow([])
        writer.writerow(
            [
                "notes",
                "v3_original_strict_fail_events",
                len(strict_fail_events(V3_ORIG / "m16-full-holdout-combined-details.csv")),
            ]
        )
        writer.writerow(
            [
                "notes",
                "v3_calibrated_strict_fail_events",
                len(strict_fail_events(LOCK / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")),
            ]
        )
        writer.writerow(
            [
                "notes",
                "v4_calibrated_strict_fail_events",
                len(strict_fail_events(V4_LOCK / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")),
            ]
        )
        writer.writerow(
            [
                "notes",
                "v3_calibrated_fail_event_ids",
                ";".join(strict_fail_events(LOCK / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")),
            ]
        )
        writer.writerow(
            [
                "notes",
                "v4_calibrated_fail_event_ids",
                ";".join(strict_fail_events(V4_LOCK / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")),
            ]
        )
    return out


def write_tail_per_run() -> Path:
    details = read_csv(LOCK / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")
    m14 = {
        (row["event_id"], row["run"]): row
        for row in read_csv(
            ROOT / "m14-clause-level-gre-css-recovery/ir/m14-clause-level-gre-css-recovery-v4-ir-details.csv"
        )
    }
    oracle = {row["event_id"]: row for row in read_csv(ORACLE)}
    tail_rows: list[dict[str, str]] = []
    for event_id in ("H3_E009", "H3_E110", "H3_E135"):
        oracle_row = oracle[event_id]
        for row in [item for item in details if item["event_id"] == event_id]:
            m14_row = m14.get((event_id, row["run"]), {})
            scores = json.loads(m14_row.get("candidate_scores_json") or "[]")
            tops = [
                f"{score['candidate_id']}:{score.get('total_score', 0):.3f}/{score.get('constraint_score', 0):.3f}"
                for score in scores
            ]
            tail_rows.append(
                {
                    "event_id": event_id,
                    "semantic_type": row["semantic_type"],
                    "domain": row["domain"],
                    "run": row["run"],
                    "closure_success": row["closure_success"],
                    "used_stage": row["used"],
                    "final_decision_path": row["final_decision_path"],
                    "m14_decision_path": m14_row.get("decision_path", ""),
                    "oracle_candidate_id": oracle_row["oracle_candidate_id"],
                    "oracle_claim": oracle_row["oracle_value"],
                    "m14_top_scores": ";".join(tops),
                    "m14_frame_answerable": m14_row.get("frame_answerable", ""),
                }
            )
    out = LOCK / "tail-failure-per-run.csv"
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(tail_rows[0].keys()))
        writer.writeheader()
        writer.writerows(tail_rows)
    return out


def write_markdown() -> Path:
    details = read_csv(LOCK / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")
    oracle = {row["event_id"]: row for row in read_csv(ORACLE)}
    s_v3o = summary_rows(V3_ORIG)
    s_v3c = summary_rows(LOCK / "m16-full-holdout-combined")
    s_v4c = summary_rows(V4_LOCK / "m16-full-holdout-combined")
    cases = {
        "H3_E009": {
            "type": "CROSS_SENTENCE_SCOPE",
            "pattern": "M14 IR: IR_RANK_TIE_ABSTAIN on all 5 runs (all candidates score 0)",
            "rescue": "M16 rescued runs 3 and 5 via entailment; runs 1/2/4 failed",
            "root_cause": "M14 frame over-structures trust conditions; constraint reranker cannot separate CAND_002 from CAND_003",
            "evidence_note": "Oracle anchors a single normative sentence; CAND_003 is a trust-condition distractor",
        },
        "H3_E110": {
            "type": "GENERAL_RULE_EXCEPTION",
            "pattern": "M14 IR: IR_RANK_TIE_ABSTAIN on all 5 runs; M16 never rescued",
            "rescue": "none",
            "root_cause": "Evidence contains two adjacent rules; M14 merges them into a GRE frame so CAND_002 and CAND_003 tie at zero",
            "evidence_note": "Oracle anchors null/true/false serialization; second window supports CAND_003 distractor",
        },
        "H3_E135": {
            "type": "CROSS_SENTENCE_SCOPE",
            "pattern": "M14 IR: IR_RANK_ABSTAIN on all 5 runs (top score 0 < gate 0.24)",
            "rescue": "M16 rescued runs 1/3/4/5; only run 2 failed",
            "root_cause": "M14 frame yields zero constraint scores; M16 entailment is unstable across runs",
            "evidence_note": "Oracle anchors authorization-endpoint interaction with resource owner",
        },
    }
    lines = [
        "# v3 Calibrated Tail Failure Case Analysis",
        "",
        f"Generated from `{LOCK.relative_to(PROJECT_DIR)}`.",
        "",
        "## Summary",
        "",
        "| Event | Type | Pass runs | Failed runs | Primary blocker | M16 rescue |",
        "|-------|------|-----------|-------------|-----------------|------------|",
    ]
    for event_id, case in cases.items():
        event_rows = [row for row in details if row["event_id"] == event_id]
        failed_runs = [row["run"] for row in event_rows if row["closure_success"].lower() not in {"true", "1", "yes"}]
        passed_runs = 5 - len(failed_runs)
        rescue = "partial" if event_id in {"H3_E009", "H3_E135"} else "none"
        failed_label = ",".join(failed_runs) if failed_runs else "-"
        lines.append(
            f"| {event_id} | {case['type']} | {passed_runs}/5 | {failed_label} | M14 zero-score tie/abstain | {rescue} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- All three strict-fail events have AGREED oracle labels on CAND_002.",
            "- Shared mechanism: M14 constraint reranker assigns zero scores under the recovery frame.",
            "- M16 entailment rescues some runs for H3_E009 and H3_E135, but not H3_E110.",
            "- v4 sole failure H4_E148 is a different family: M15 TEMPORAL wrong selection.",
            "",
        ]
    )
    for event_id, case in cases.items():
        lines.extend(
            [
                f"## {event_id}",
                "",
                f"- **Oracle:** `{oracle[event_id]['oracle_candidate_id']}` — `{oracle[event_id]['oracle_value']}`",
                f"- **Pattern:** {case['pattern']}",
                f"- **M16 rescue:** {case['rescue']}",
                f"- **Root cause:** {case['root_cause']}",
                f"- **Evidence:** {case['evidence_note']}",
                "",
            ]
        )
    lines.extend(
        [
            "## v3 vs v4 Calibrated Comparison",
            "",
            "| Type | v3 original | v3 calibrated | v4 calibrated |",
            "|------|-------------|---------------|---------------|",
        ]
    )
    for semantic_type in ("ALL", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"):
        lines.append(
            f"| {semantic_type} closure | "
            f"{float(s_v3o[semantic_type]['closure_accuracy']) * 100:.2f}% | "
            f"{float(s_v3c[semantic_type]['closure_accuracy']) * 100:.2f}% | "
            f"{float(s_v4c[semantic_type]['closure_accuracy']) * 100:.2f}% |"
        )
    lines.extend(
        [
            "",
            "See `v3-v4-calibrated-cross-comparison.csv` and `tail-failure-per-run.csv` for machine-readable tables.",
            "",
        ]
    )
    out = LOCK / "tail-failure-case-analysis.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def refresh_hashes() -> int:
    locked_files = sorted(
        path
        for path in LOCK.rglob("*")
        if path.is_file() and path.name != "locked-main-result-file-hashes.csv"
    )
    with (LOCK / "locked-main-result-file-hashes.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(["Hash", "Path"])
        for path in locked_files:
            writer.writerow([sha256_file(path), str(path.resolve())])
    return len(locked_files)


def main() -> int:
    write_cross_comparison()
    write_tail_per_run()
    write_markdown()
    count = refresh_hashes()
    print(f"wrote analysis artifacts; locked files now {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
