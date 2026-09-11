from __future__ import annotations

"""Exp13 IR fidelity package: GRE/CSS gold sheets + temporal gold link + scaffold report."""

import argparse
import json
import random
from pathlib import Path

from generate_m15_temporal_gold_sheet import document_metadata, first_evidence_window
from paper_final_validation_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    benchmark_paths,
    load_freeze_sha256,
    load_method_freeze_sha256,
    load_ready_events,
    read_csv,
    utc_now_iso,
    write_binding,
    write_manifest_csv,
    write_summary_json,
)
from semantic_v2_common import PROJECT_DIR, write_csv


EXP13_ROOT = PAPER_VALIDATION_ROOT / "13-ir-fidelity"
GRE_SEED = 20260908
CSS_SEED = 20260908
GRE_TARGET = 30
CSS_TARGET = 30
TEMPORAL_GOLD = PAPER_VALIDATION_ROOT / "02-m15-semantic-audit" / "gold-temporal-anchors.csv"


GRE_FIELDS = [
    "subject",
    "general_rule",
    "exception_trigger",
    "exception_rule",
    "priority_relation",
    "result",
    "supporting_span",
]

CSS_FIELDS = [
    "subject",
    "statement",
    "qualifier",
    "scope_target",
    "scope_relation",
    "result",
    "supporting_span",
]


def sample_type(events: list[dict[str, str]], semantic_type: str, n: int, seed: int) -> list[dict[str, str]]:
    pool = sorted([e for e in events if e.get("semantic_type") == semantic_type], key=lambda r: r["event_id"])
    if len(pool) < n:
        raise RuntimeError(f"need {n} {semantic_type}, have {len(pool)}")
    rng = random.Random(seed)
    indices = list(range(len(pool)))
    rng.shuffle(indices)
    return sorted([pool[i] for i in indices[:n]], key=lambda r: r["event_id"])


def gold_row(event: dict[str, str], meta: dict[str, str], evidence: str, fields: list[str]) -> dict[str, str]:
    row = {
        "event_id": event["event_id"],
        "domain": event.get("domain", ""),
        "subject_label": event.get("subject_label", ""),
        "predicate_label": event.get("predicate_label", ""),
        "document_id": meta.get("document_id", ""),
        "issuer": meta.get("issuer", ""),
        "source_url": meta.get("source_url", ""),
        "public_evidence_window": evidence,
    }
    for field in fields:
        row[field] = ""
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=EXP13_ROOT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    benchmark_sha256, benchmark_manifest = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_sha256, method_manifest = load_method_freeze_sha256(DEFAULT_METHOD_FREEZE_DIR)
    paths = benchmark_paths(args.benchmark_dir)
    events = load_ready_events(paths)
    documents = read_csv(paths["document_csv"])
    excerpts_dir = paths["benchmark_dir"] / "public" / "excerpts"

    gre_events = sample_type(events, "GENERAL_RULE_EXCEPTION", GRE_TARGET, GRE_SEED)
    css_events = sample_type(events, "CROSS_SENTENCE_SCOPE", CSS_TARGET, CSS_SEED)

    gre_rows = [
        gold_row(
            event,
            document_metadata(event["event_id"], documents),
            first_evidence_window(event["event_id"], excerpts_dir),
            GRE_FIELDS,
        )
        for event in gre_events
    ]
    css_rows = [
        gold_row(
            event,
            document_metadata(event["event_id"], documents),
            first_evidence_window(event["event_id"], excerpts_dir),
            CSS_FIELDS,
        )
        for event in css_events
    ]

    write_csv(args.output_dir / "gold-gre-ir-sheet.csv", gre_rows)
    write_csv(args.output_dir / "gold-cross-sentence-ir-sheet.csv", css_rows)

    temporal_link = str(TEMPORAL_GOLD.relative_to(PROJECT_DIR)) if TEMPORAL_GOLD.is_file() else ""
    if TEMPORAL_GOLD.is_file():
        (args.output_dir / "gold-temporal-anchors-link.txt").write_text(temporal_link + "\n", encoding="utf-8")

    report = [
        "# Exp13 Semantic IR Fidelity Audit (package)",
        "",
        "## Gold sheets",
        f"- GRE: **{len(gre_rows)}** events → `gold-gre-ir-sheet.csv`",
        f"- Cross-Sentence: **{len(css_rows)}** events → `gold-cross-sentence-ir-sheet.csv`",
        f"- Temporal: reuse **{temporal_link or 'MISSING'}** (88 events)",
        "",
        "## Comparisons (after gold fill)",
        "- GRE/CSS: M13 vs M13+M14",
        "- TEMP: M13 vs M13+M15",
        "",
        "## Metrics",
        "Slot accuracy, complete-frame accuracy, ESR, HFR, CMR — human gold + deterministic matching.",
        "Do **not** use LLM-as-judge.",
        "",
        "## Existing temporal audit",
        "See `output/paper-final-validation/02-m15-semantic-audit/` for M13 vs M15 temporal scoring.",
    ]
    (args.output_dir / "ir-fidelity-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    summary = {
        "created_at_utc": utc_now_iso(),
        "gre_events": len(gre_rows),
        "css_events": len(css_rows),
        "temporal_gold": temporal_link,
        "gre_seed": GRE_SEED,
        "css_seed": CSS_SEED,
        "status": "gold_sheets_ready_pending_human_fill",
    }
    write_summary_json(args.output_dir / "ir-fidelity-status.json", summary)
    write_manifest_csv(
        args.output_dir / "manifest.csv",
        [
            {"artifact": "gold-gre-ir-sheet.csv", "rows": len(gre_rows)},
            {"artifact": "gold-cross-sentence-ir-sheet.csv", "rows": len(css_rows)},
            {"artifact": "gold-temporal-anchors-link.txt", "rows": 1 if temporal_link else 0},
        ],
    )
    write_binding(
        args.output_dir,
        experiment_role="exp13-ir-fidelity-package",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_manifest,
        method_freeze_sha256=method_sha256,
        extra=summary,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
