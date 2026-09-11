from __future__ import annotations

"""Enrich hold-out event metadata: specific predicate_label and case_context."""

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from holdout_metadata_enrichment import (
    display_source_title,
    enrich_event_row,
    first_window,
    load_source_family_by_event,
    source_title_from_event,
)
from semantic_v2_common import PROJECT_DIR, write_csv

BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    event_csv = args.benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    doc_csv = args.benchmark_dir / "public" / "documents" / "external-real-document-template.csv"
    events = read_csv(event_csv)
    documents = {row["document_id"]: row for row in read_csv(doc_csv)}
    families_by_event = load_source_family_by_event(args.benchmark_dir)

    enriched_rows: list[dict[str, str]] = []
    audit_rows: list[dict[str, Any]] = []
    for event in events:
        excerpt_path = args.benchmark_dir / "public" / "excerpts" / f"{event['event_id']}-evidence.md"
        evidence = excerpt_path.read_text(encoding="utf-8", errors="replace") if excerpt_path.is_file() else ""
        source_title = source_title_from_event(event, documents)
        subject_source_title = display_source_title(event, documents)
        source_family = families_by_event.get(event["event_id"], event.get("domain", ""))
        enriched = enrich_event_row(
            event,
            evidence=evidence,
            source_title=source_title,
            source_family=source_family,
            subject_source_title=subject_source_title,
        )
        enriched_rows.append(enriched)
        audit_rows.append(
            {
                "event_id": event["event_id"],
                "old_predicate_label": event.get("predicate_label", ""),
                "new_predicate_label": enriched["predicate_label"],
                "old_case_context": event.get("case_context", ""),
                "new_case_context": enriched["case_context"],
                "source_window_1_preview": first_window(evidence)[:180],
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "holdout-metadata-enrichment-audit.csv", audit_rows)
    summary = {
        "benchmark": args.benchmark_dir.name,
        "enriched_at_utc": datetime.now(timezone.utc).isoformat(),
        "events": len(enriched_rows),
        "unique_predicate_labels": len({row["predicate_label"] for row in enriched_rows}),
        "unique_case_contexts": len({row["case_context"] for row in enriched_rows}),
        "dry_run": args.dry_run,
    }
    (args.output_dir / "holdout-metadata-enrichment-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not args.dry_run:
        write_csv(event_csv, enriched_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
