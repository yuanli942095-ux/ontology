from __future__ import annotations

"""Fill hold-out oracle review sheet from evidence/candidate/oracle consistency checks."""

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from audit_external_real_holdout_v1_quality import BOILERPLATE, first_window, tokens
from generate_holdout_oracle_review_sheet import REVIEW_COLUMNS
from holdout_metadata_enrichment import parse_display_value, predicate_label_zh
from semantic_v2_common import PROJECT_DIR

BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded"

HEADER_BY_KEY = {key: header for key, header in REVIEW_COLUMNS}
KEY_BY_HEADER = {header: key for key, header in REVIEW_COLUMNS}


@dataclass
class ReviewOutcome:
    event_id: str
    predicate_ok: str
    case_context_ok: str
    five_ok: str
    span_ok: str
    cand003_ok: str
    notes: str
    agreement: str
    issues: list[str]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def row_get(row: dict[str, str], key: str) -> str:
    header = HEADER_BY_KEY[key]
    return str(row.get(header, "") or row.get(key, "")).strip()


def row_set(row: dict[str, str], key: str, value: str) -> None:
    header = HEADER_BY_KEY[key]
    row[header] = value


def subject_in_claim(subject_label: str, claim_key: str) -> bool:
    subject_tokens = tokens(subject_label.replace("Semantics", " semantics "))
    claim_tokens = tokens(claim_key.replace("_", " "))
    if not subject_tokens:
        return True
    overlap = subject_tokens & claim_tokens
    return len(overlap) >= max(1, min(2, len(subject_tokens) // 2))


def value_supported(value: str, window1: str, *, min_overlap: int = 2) -> tuple[bool, int]:
    overlap = len(tokens(value.replace("_", " ")) & tokens(window1))
    return overlap >= min_overlap, overlap


def review_row(row: dict[str, str], evidence: str) -> ReviewOutcome:
    event_id = row_get(row, "event_id")
    issues: list[str] = []

    predicate_current = row_get(row, "predicate_label_current")
    predicate_proposed = row_get(row, "predicate_label_proposed")
    case_current = row_get(row, "case_context_current")
    case_proposed = row_get(row, "case_context_proposed")
    window1 = row_get(row, "source_window_1")
    c2_display = row_get(row, "cand_002_display_value")
    c2_value = row_get(row, "cand_002_value_only")
    c3_display = row_get(row, "cand_003_display_value")
    oracle_id = row_get(row, "oracle_candidate_id")
    oracle_value = row_get(row, "oracle_value")
    oracle_span = row_get(row, "oracle_evidence_span")
    subject_label = row_get(row, "subject_label")

    c2_key, c2_val = parse_display_value(c2_display)
    c3_key, c3_val = parse_display_value(c3_display)

    predicate_ok = bool(predicate_current) and (
        predicate_current == predicate_proposed
        or predicate_current in case_current
        or predicate_label_zh(predicate_current) in case_current
    )
    if predicate_current != predicate_proposed and predicate_ok:
        issues.append("PREDICATE_PROPOSED_DIFFERS_USE_CURRENT")

    case_ok = case_current == case_proposed and bool(case_proposed)
    if not case_ok:
        issues.append("CASE_CONTEXT_CURRENT_PROPOSED_MISMATCH")

    span_in_evidence = bool(oracle_span and oracle_span in evidence)
    span_in_window = bool(oracle_span and oracle_span in window1)
    span_sheet_ok = row_get(row, "oracle_span_in_evidence") == "是" and row_get(row, "oracle_span_matches_window1") == "是"
    span_ok = span_in_evidence and span_in_window and span_sheet_ok
    if not span_in_evidence:
        issues.append("ORACLE_SPAN_NOT_IN_EVIDENCE")
    if not span_in_window:
        issues.append("ORACLE_SPAN_NOT_IN_WINDOW1")

    if oracle_id != "CAND_002":
        issues.append("ORACLE_ID_NOT_CAND_002")
    if oracle_value != c2_display:
        issues.append("ORACLE_VALUE_NOT_CAND_002_DISPLAY")
    if not subject_in_claim(subject_label, c2_key):
        issues.append("SUBJECT_CLAIM_FAMILY_MISMATCH")

    c2_supported, c2_overlap = value_supported(c2_value, window1)
    c3_supported, c3_overlap = value_supported(c3_val, window1)
    if not c2_supported:
        issues.append(f"CAND_002_LOW_WINDOW_OVERLAP:{c2_overlap}")
    if c3_overlap > c2_overlap:
        issues.append("CAND_003_BETTER_SUPPORTED_THAN_002")

    if c2_key != c3_key:
        issues.append("CAND_002_003_FAMILY_MISMATCH")
    if c2_val == c3_val:
        issues.append("CAND_003_SAME_VALUE_AS_002")
    if not c3_val:
        issues.append("CAND_003_EMPTY")

    if BOILERPLATE.search(window1):
        issues.append("BOILERPLATE_WINDOW1")
    if len(window1) < 70:
        issues.append("SHORT_WINDOW1")

    hard_fail = {
        "ORACLE_SPAN_NOT_IN_EVIDENCE",
        "ORACLE_SPAN_NOT_IN_WINDOW1",
        "ORACLE_ID_NOT_CAND_002",
        "ORACLE_VALUE_NOT_CAND_002_DISPLAY",
        "CAND_002_LOW_WINDOW_OVERLAP",
        "CAND_003_BETTER_SUPPORTED_THAN_002",
        "CAND_002_003_FAMILY_MISMATCH",
        "CAND_003_SAME_VALUE_AS_002",
        "BOILERPLATE_WINDOW1",
    }
    hard_issues = [issue for issue in issues if issue.split(":", 1)[0] in hard_fail]

    five_ok = not hard_issues and c2_supported and span_ok and oracle_id == "CAND_002" and oracle_value == c2_display
    cand003_ok = (
        c2_key == c3_key
        and c2_val != c3_val
        and c2_overlap > c3_overlap
    )
    if not cand003_ok:
        issues.append("CAND_003_NOT_PLAUSIBLE_DISTRACTOR")

    all_ok = predicate_ok and case_ok and five_ok and span_ok and cand003_ok and not hard_issues

    if all_ok:
        notes = "证据直接支持CAND_002；CAND_003为同领域真实取值干扰项，且不被当前证据窗口更好支持"
        if "PREDICATE_PROPOSED_DIFFERS_USE_CURRENT" in issues:
            notes += "；建议谓词列含主体前缀冗余，以当前谓词为准"
        agreement = "一致"
    else:
        notes = "；".join(issues)
        agreement = "不一致"

    def yn(ok: bool) -> str:
        return "是" if ok else "否"

    return ReviewOutcome(
        event_id=event_id,
        predicate_ok=yn(predicate_ok),
        case_context_ok=yn(case_ok),
        five_ok=yn(five_ok),
        span_ok=yn(span_ok),
        cand003_ok=yn(cand003_ok),
        notes=notes,
        agreement=agreement,
        issues=issues,
    )


def apply_outcome(row: dict[str, str], outcome: ReviewOutcome, *, reviewer: str, reviewed_at: str) -> None:
    row_set(row, "review_predicate_ok", outcome.predicate_ok)
    row_set(row, "review_case_context_ok", outcome.case_context_ok)
    row_set(row, "review_five_consistency_ok", outcome.five_ok)
    row_set(row, "review_oracle_span_ok", outcome.span_ok)
    row_set(row, "review_cand003_plausible", outcome.cand003_ok)
    row_set(row, "reviewer_notes", outcome.notes)
    row_set(row, "reviewer_name", reviewer)
    row_set(row, "reviewed_at", reviewed_at)
    row_set(row, "agreement_status_after_review", outcome.agreement)


def write_sheet(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [header for _, header in REVIEW_COLUMNS]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--input-sheet", type=Path, default=OUTPUT / "oracle-review-sheet.csv")
    parser.add_argument("--output-sheet", type=Path, default=OUTPUT / "oracle-review-sheet-zh.csv")
    parser.add_argument("--reviewer", default="Composer")
    parser.add_argument("--review-date", default=date.today().isoformat())
    args = parser.parse_args()

    rows = read_csv(args.input_sheet)
    outcomes: list[ReviewOutcome] = []
    for row in rows:
        event_id = row_get(row, "event_id")
        excerpt = args.benchmark_dir / "public" / "excerpts" / f"{event_id}-evidence.md"
        evidence = excerpt.read_text(encoding="utf-8", errors="replace") if excerpt.is_file() else ""
        outcome = review_row(row, evidence)
        apply_outcome(row, outcome, reviewer=args.reviewer, reviewed_at=args.review_date)
        outcomes.append(outcome)

    args.output_sheet.parent.mkdir(parents=True, exist_ok=True)
    write_sheet(args.output_sheet, rows)
    write_sheet(args.input_sheet, rows)

    agreed = sum(1 for item in outcomes if item.agreement == "一致")
    disagreed = [item.event_id for item in outcomes if item.agreement != "一致"]
    summary = {
        "events": len(outcomes),
        "agreed": agreed,
        "disagreed": len(disagreed),
        "disagreed_event_ids": disagreed,
        "output_sheet": str(args.output_sheet.resolve().relative_to(PROJECT_DIR.resolve())),
    }
    (args.output_sheet.parent / "oracle-manual-review-filled-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if disagreed else 0


if __name__ == "__main__":
    raise SystemExit(main())
