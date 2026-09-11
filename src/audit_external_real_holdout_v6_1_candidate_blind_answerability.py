from __future__ import annotations

"""Audit whether v6.1 Gold targets are identifiable from candidate-blind public input.

The audit is post-hoc and diagnostic. Its heuristic findings do not overwrite
human Gold labels and do not retroactively change the frozen blind result.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from rfc213_direct_repair_ir import ontology_literal_assertions, source_windows
from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark/external-real-holdout-v6-1-direct-ir-blind"
OUTPUT = PROJECT_DIR / "output/external-real-holdout-v6-1-direct-ir-blind/final-blind-r2b-v2-r5/posthoc-failure-analysis"
GENERIC = {
    "evidence", "grounded", "value", "normative", "subject", "repair", "determine",
    "whether", "requires", "using", "only", "five", "frozen", "public", "source",
    "windows", "return", "under", "contract", "security", "semantics", "transport",
    "identity", "structured", "data", "network", "routing", "certificates", "email",
    "authorization", "http", "dns", "iot", "quic", "pki", "evidence-grounded",
}
NORMATIVE = re.compile(r"\b(MUST(?: NOT)?|SHOULD(?: NOT)?|MAY|REQUIRED|RECOMMENDED|SHALL(?: NOT)?)\b", re.I)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text)
        if token.lower() not in GENERIC
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    events = {row["event_id"]: row for row in read_jsonl(args.benchmark_dir / "public/events/events.jsonl")}
    golds = {row["event_id"]: row for row in read_jsonl(args.benchmark_dir / "private/oracle/gold-repair-ir.jsonl")}
    rows: list[dict[str, Any]] = []
    for event_id, event in sorted(events.items()):
        gold = golds[event_id]
        evidence = (args.benchmark_dir / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
        windows = source_windows(evidence)
        source_path = args.benchmark_dir / event["source_owl"]
        assertions = ontology_literal_assertions(source_path)
        target_tokens = tokens(event.get("predicate_label", ""))
        # case_context is a benchmark-wide instruction template that repeats the
        # subject and predicate; it does not add a claim-level target descriptor.
        discriminators = target_tokens
        scores = [(window_id, len(discriminators & tokens(text))) for window_id, text in windows]
        max_score = max((score for _, score in scores), default=0)
        top_windows = [window_id for window_id, score in scores if score == max_score]
        gold_window = str(gold.get("gold_source_window", "")).removeprefix("SOURCE_WINDOW_")
        normative_windows = [window_id for window_id, text in windows if NORMATIVE.search(text)]
        current_literals = [row["lexical"] for row in assertions]
        current_is_placeholder = any(value == "unmodeled" or value.endswith("=unmodeled") for value in current_literals)
        no_public_target_discriminator = not discriminators
        multiple_plausible = len(normative_windows) >= 2
        if gold["decision"] == "REPAIR" and no_public_target_discriminator and current_is_placeholder and multiple_plausible:
            status = "UNDER_SPECIFIED_PUBLIC_TARGET"
        elif gold["decision"] == "REPAIR" and (len(top_windows) != 1 or gold_window not in top_windows):
            status = "MANUAL_ANSWERABILITY_REVIEW"
        else:
            status = "NO_AUTOMATIC_BLOCKER"
        rows.append({
            "event_id": event_id,
            "gold_decision": gold["decision"],
            "adjudicated_semantic_type": gold["semantic_type"],
            "domain": event["domain"],
            "subject_label": event["subject_label"],
            "predicate_label": event["predicate_label"],
            "gold_source_window": gold.get("gold_source_window", ""),
            "public_window_count": len(windows),
            "normative_window_count": len(normative_windows),
            "normative_windows": json.dumps(normative_windows),
            "target_discriminator_tokens": json.dumps(sorted(discriminators)),
            "metadata_window_scores": json.dumps(dict(scores), sort_keys=True),
            "metadata_top_windows": json.dumps(top_windows),
            "gold_is_unique_metadata_top": len(top_windows) == 1 and gold_window in top_windows,
            "current_literal_is_placeholder": current_is_placeholder,
            "answerability_status": status,
            "manual_gold_uniquely_identifiable": "PENDING_MANUAL_REVIEW",
            "manual_alternative_plausible_windows": "",
            "manual_notes": "",
        })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "candidate-blind-answerability-audit.csv", rows)
    counts = Counter(row["answerability_status"] for row in rows)
    repair_rows = [row for row in rows if row["gold_decision"] == "REPAIR"]
    summary = {
        "status": "POST_HOC_DIAGNOSTIC_ONLY",
        "events": len(rows),
        "repair_events": len(repair_rows),
        "unique_subject_labels": len({row["subject_label"] for row in rows}),
        "unique_predicate_labels": len({row["predicate_label"] for row in rows}),
        "repair_placeholder_current_literal": sum(bool(row["current_literal_is_placeholder"]) for row in repair_rows),
        "repair_with_multiple_normative_windows": sum(int(row["normative_window_count"]) >= 2 for row in repair_rows),
        "automatic_status_counts": dict(counts),
        "interpretation": (
            "Automatic flags are protocol-risk indicators, not replacement Gold labels. "
            "Events flagged UNDER_SPECIFIED_PUBLIC_TARGET require independent manual review "
            "before model changes are attributed solely to extraction quality."
        ),
    }
    (args.output_dir / "candidate-blind-answerability-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = [
        "# Candidate-blind Target Answerability Audit",
        "",
        "> This is a post-hoc protocol audit, not a relabeling of the frozen blind result. Automatic flags require independent human confirmation.",
        "",
        "## Findings",
        "",
        f"- Public events: {len(rows)}; Repair events: {len(repair_rows)}.",
        f"- Unique subject labels: {summary['unique_subject_labels']}; unique predicate labels: {summary['unique_predicate_labels']}.",
        f"- Repair events whose current target literal is a placeholder: {summary['repair_placeholder_current_literal']}/{len(repair_rows)}.",
        f"- Repair events with at least two normative evidence windows: {summary['repair_with_multiple_normative_windows']}/{len(repair_rows)}.",
        f"- Automatically flagged under-specified Repair targets: {counts.get('UNDER_SPECIFIED_PUBLIC_TARGET', 0)}/{len(repair_rows)}.",
        "",
        "The public predicate labels identify only a domain-level `evidence-grounded value`, not the claim that must be repaired. The case context repeats that generic predicate and does not add a claim-level discriminator. For Repair events, the current ontology stores an `unmodeled` placeholder while multiple windows contain genuine normative statements. Under this contract, selecting the adjudicated Gold window is generally not uniquely determined by candidate-blind public input.",
        "",
        "## Required action before V3",
        "",
        "1. Independently review the generated CSV and record whether the Gold claim/window is uniquely identifiable without candidates or Oracle fields.",
        "2. Treat v6.1 as a diagnostic dataset for method development after this analysis; do not reuse it as a new blind test.",
        "3. For the next protocol, expose a claim-level target description or ontology assertion gloss, while keeping the replacement value and Gold window private.",
        "4. Construct NO_CHANGE and ABSTAIN cases for the same target contract: unchanged evidence, missing evidence, and conflicting evidence must all concern an explicitly identified claim.",
        "5. Balance decision labels by domain and evidence position, then freeze a new independent hold-out after V3 is frozen.",
    ]
    (args.output_dir / "candidate-blind-answerability-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
