from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-v3"
EVENT_CSV = BENCHMARK / "input" / "external-real-event-template.csv"
DOC_CSV = BENCHMARK / "input" / "external-real-document-template.csv"
CAND_CSV = BENCHMARK / "input" / "external-real-candidate-template.csv"
ORACLE_CSV = BENCHMARK / "private" / "external-real-oracle-template.csv"
SOURCE_CSV = BENCHMARK / "source-intake" / "external-real-v3-source-families.csv"
VALIDATION_JSON = OUTPUT_DIR / "external-real-v3-validation-213-summary.json"
MANIFEST_JSON = OUTPUT_DIR / "external-real-v3-freeze-manifest-213.json"

REPORT_MD = OUTPUT_DIR / "external-real-v3-dataset-report.md"
REPORT_JSON = OUTPUT_DIR / "external-real-v3-dataset-report.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def main() -> int:
    events = read_csv(EVENT_CSV)
    docs = read_csv(DOC_CSV)
    candidates = read_csv(CAND_CSV)
    oracles = read_csv(ORACLE_CSV)
    sources = read_csv(SOURCE_CSV)
    validation = json.loads(VALIDATION_JSON.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))

    domain_counts = Counter(row["domain"] for row in events)
    type_counts = Counter(row["semantic_type"] for row in events)
    source_type_counts = Counter(row["source_type"] for row in docs)
    candidate_slot_counts = validation.get("correct_slot_counts", {})

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "external-real-v3",
        "ready_events": len(events),
        "domains": dict(sorted(domain_counts.items())),
        "semantic_types": dict(sorted(type_counts.items())),
        "documents": len(docs),
        "document_source_types": dict(sorted(source_type_counts.items())),
        "candidates": len(candidates),
        "oracle_rows_private": len(oracles),
        "source_families": len(sources),
        "correct_slot_counts": candidate_slot_counts,
        "validation_errors": validation.get("errors"),
        "validation_warnings": validation.get("warnings"),
        "manifest": str(MANIFEST_JSON.relative_to(PROJECT_DIR)),
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# External Real V3 Dataset Report",
        "",
        f"Generated at {payload['generated_at_utc']} UTC.",
        "",
        "## Scale",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| READY events | {len(events)} |",
        f"| Domains | {len(domain_counts)} |",
        f"| Source families | {len(sources)} |",
        f"| Documents | {len(docs)} |",
        f"| Candidates | {len(candidates)} |",
        f"| Private Oracle rows | {len(oracles)} |",
        f"| Freeze manifest public files | {manifest.get('public_file_count')} |",
        f"| Validation errors | {validation.get('errors')} |",
        f"| Validation warnings | {validation.get('warnings')} |",
        "",
        "## Domain Coverage",
        "",
        "| Domain | READY events |",
        "|---|---:|",
        *[f"| {domain} | {count} |" for domain, count in sorted(domain_counts.items())],
        "",
        "## Semantic Type Balance",
        "",
        "| Semantic type | READY events |",
        "|---|---:|",
        *[f"| {semantic_type} | {count} |" for semantic_type, count in sorted(type_counts.items())],
        "",
        "## Source Families",
        "",
        "| Domain | Source family | Publisher | Source URL |",
        "|---|---|---|---|",
        *[
            f"| {row['domain']} | {row['source_title']} | {row['publisher']} | {row['source_url']} |"
            for row in sources
        ],
        "",
        "## Candidate-Slot Check",
        "",
        "| Oracle candidate slot | Rows |",
        "|---|---:|",
        *[f"| {slot} | {count} |" for slot, count in sorted(candidate_slot_counts.items())],
        "",
        "The v3 added events rotate the correct candidate slot; inherited v2 events retain their original adjudication.",
        "",
        "## Boundary",
        "",
        "- This is a real public-source diagnostic benchmark: source URLs and publishers are retained, and every event has local evidence files and hashes.",
        "- The added events are structured extractions from official/public normative sources, not random synthetic rows.",
        "- It is still not a naturally occurring production incident log; paper wording should say controlled public-source benchmark.",
        "- This report validates the dataset only. Full model/runtime/robustness experiments have not yet been rerun on external-real-v3.",
        "",
        "## Files",
        "",
        f"- Benchmark: `{BENCHMARK}`",
        f"- Validation report: `{OUTPUT_DIR / 'external-real-v3-validation-213-report.md'}`",
        f"- Freeze manifest: `{MANIFEST_JSON}`",
        f"- Manifest files CSV: `{OUTPUT_DIR / 'external-real-v3-freeze-manifest-213-files.csv'}`",
    ]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report={REPORT_MD}")
    print(f"json={REPORT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
