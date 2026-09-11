from __future__ import annotations

"""Five-consistency + manual-review gate for external-real-holdout-v1-expanded."""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audit_external_real_holdout_v1_quality import BOILERPLATE, first_window, tokens
from semantic_v2_common import PROJECT_DIR, write_csv

BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded"

LEGACY_TEMPLATE_CASE_CONTEXT = (
    "Determine the evidence-grounded normative claim for this hold-out event "
    "using only the cited public source window."
)

GENERIC_PREDICATES = {
    "versioned normative claim",
    "rule exception or constraint",
    "scope of normative claim",
    "normative claim",
    "normative requirement",
    "regulatory text status",
    "rule",
    "scope",
    "status",
}


@dataclass
class RowReview:
    event_id: str
    domain: str
    semantic_type: str
    predicate_label: str
    subject_label: str
    cand2: str
    cand3: str
    oracle_span_in_w1: bool
    oracle_span_in_evidence: bool
    cand2_token_overlap_w1: int
    public_leak: bool
    generic_predicate: bool
    template_case_context: bool
    distractor_is_not_prefix: bool
    issues: str
    review_status: str


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def parse_value(display: str) -> tuple[str, str]:
    if "=" not in display:
        return "", display
    left, right = display.split("=", 1)
    return left.strip(), right.strip()


def review_event(
    event: dict[str, str],
    candidates: list[dict[str, str]],
    oracle: dict[str, str],
    evidence: str,
) -> RowReview:
    event_id = event["event_id"]
    window1 = first_window(evidence)
    cands = sorted(candidates, key=lambda row: row["candidate_id"])
    c2 = next(row for row in cands if row["candidate_id"] == "CAND_002")
    c3 = next(row for row in cands if row["candidate_id"] == "CAND_003")
    _, val2 = parse_value(c2["display_value"])
    _, val3 = parse_value(c3["display_value"])
    spans = json.loads(oracle.get("evidence_spans_json", "[]") or "[]")
    quote = str(spans[0].get("quote", "")) if spans else ""
    issues: list[str] = []

    oracle_in_evidence = bool(quote and quote in evidence)
    oracle_in_w1 = bool(quote and quote in window1)
    overlap = len(tokens(val2.replace("_", " ")) & tokens(window1))
    public_leak = any(
        needle and needle in evidence
        for needle in (c2["display_value"], c3["display_value"], oracle.get("oracle_value", ""), "CAND_002", "CAND_003")
    )
    generic_predicate = event.get("predicate_label", "").strip().lower() in GENERIC_PREDICATES
    template_case_context = event.get("case_context", "").strip() == LEGACY_TEMPLATE_CASE_CONTEXT
    distractor_is_not_prefix = val3 == f"not_{val2}"

    if not oracle_in_evidence:
        issues.append("ORACLE_SPAN_NOT_IN_EVIDENCE")
    if not oracle_in_w1:
        issues.append("ORACLE_SPAN_NOT_IN_WINDOW1")
    if overlap < 2:
        issues.append(f"LOW_CAND2_WINDOW1_OVERLAP:{overlap}")
    if public_leak:
        issues.append("PUBLIC_LEAK")
    if BOILERPLATE.search(window1):
        issues.append("BOILERPLATE_WINDOW1")
    if len(window1) < 70:
        issues.append("SHORT_WINDOW1")
    if oracle.get("oracle_value") != c2.get("display_value"):
        issues.append("ORACLE_CAND2_MISMATCH")
    if generic_predicate:
        issues.append("GENERIC_PREDICATE")
    if template_case_context:
        issues.append("TEMPLATE_CASE_CONTEXT")
    if not distractor_is_not_prefix:
        issues.append("DISTRACTOR_NOT_NOT_PREFIX")

    hard_fail = any(
        code in issues
        for code in (
            "ORACLE_SPAN_NOT_IN_EVIDENCE",
            "ORACLE_SPAN_NOT_IN_WINDOW1",
            "PUBLIC_LEAK",
            "ORACLE_CAND2_MISMATCH",
            "BOILERPLATE_WINDOW1",
        )
    )
    review_status = "FAIL" if hard_fail else ("WARN" if issues else "PASS")
    return RowReview(
        event_id=event_id,
        domain=event["domain"],
        semantic_type=event["semantic_type"],
        predicate_label=event["predicate_label"],
        subject_label=event["subject_label"],
        cand2=c2["display_value"],
        cand3=c3["display_value"],
        oracle_span_in_w1=oracle_in_w1,
        oracle_span_in_evidence=oracle_in_evidence,
        cand2_token_overlap_w1=overlap,
        public_leak=public_leak,
        generic_predicate=generic_predicate,
        template_case_context=template_case_context,
        distractor_is_not_prefix=distractor_is_not_prefix,
        issues="|".join(issues),
        review_status=review_status,
    )


def sample_event_ids(events: list[dict[str, str]], per_domain: int) -> list[str]:
    by_domain: dict[str, list[str]] = defaultdict(list)
    for row in events:
        by_domain[row["domain"]].append(row["event_id"])
    picked: list[str] = []
    for domain in sorted(by_domain):
        picked.extend(sorted(by_domain[domain])[:per_domain])
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--sample-per-domain", type=int, default=3)
    args = parser.parse_args()

    event_csv = args.benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    candidate_csv = args.benchmark_dir / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    oracle_csv = args.benchmark_dir / "private" / "oracle" / "external-real-oracle-template.csv"
    events = [row for row in read_csv(event_csv) if row.get("status") == "READY"]
    candidates = read_csv(candidate_csv)
    oracles = {row["event_id"]: row for row in read_csv(oracle_csv)}
    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidates:
        by_event[row["event_id"]].append(row)

    reviews: list[RowReview] = []
    for event in events:
        excerpt_path = args.benchmark_dir / "public" / "excerpts" / f"{event['event_id']}-evidence.md"
        evidence = excerpt_path.read_text(encoding="utf-8", errors="replace") if excerpt_path.is_file() else ""
        reviews.append(
            review_event(
                event,
                by_event[event["event_id"]],
                oracles[event["event_id"]],
                evidence,
            )
        )

    status_counts = Counter(row.review_status for row in reviews)
    issue_counts = Counter()
    for row in reviews:
        for issue in filter(None, row.issues.split("|")):
            issue_counts[issue.split(":", 1)[0]] += 1

    sample_ids = set(sample_event_ids(events, args.sample_per_domain))
    sample_rows = [row for row in reviews if row.event_id in sample_ids]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_rows = [row.__dict__ for row in reviews]
    write_csv(args.output_dir / "manual-five-consistency-review-all.csv", all_rows)
    write_csv(args.output_dir / "manual-five-consistency-review-sample.csv", [row.__dict__ for row in sample_rows])

    fails = [row for row in reviews if row.review_status == "FAIL"]
    warns = [row for row in reviews if row.review_status == "WARN"]
    summary = {
        "benchmark": args.benchmark_dir.name,
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "events": len(reviews),
        "pass": status_counts["PASS"],
        "warn": status_counts["WARN"],
        "fail": status_counts["FAIL"],
        "issue_counts": dict(sorted(issue_counts.items())),
        "hard_fail_events": [row.event_id for row in fails],
        "sample_per_domain": args.sample_per_domain,
        "sample_pass": sum(1 for row in sample_rows if row.review_status == "PASS"),
        "sample_warn": sum(1 for row in sample_rows if row.review_status == "WARN"),
        "sample_fail": sum(1 for row in sample_rows if row.review_status == "FAIL"),
        "freeze_recommendation": "BLOCK" if fails else ("REVIEW_WARNINGS" if warns else "READY"),
    }
    (args.output_dir / "manual-five-consistency-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
