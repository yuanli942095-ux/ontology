from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-v3-naturalized"
INPUT = BENCHMARK / "input"
PRIVATE = BENCHMARK / "private"
DOCS = BENCHMARK / "documents"
RULES = BENCHMARK / "rules"
MUTANTS = BENCHMARK / "mutants"
BUILT = BENCHMARK / "built"
INTAKE = BENCHMARK / "source-intake"

EVENT_CSV = INPUT / "external-real-event-template.csv"
DOC_CSV = INPUT / "external-real-document-template.csv"
CAND_CSV = INPUT / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE / "external-real-oracle-template.csv"
SOURCE_CSV = INTAKE / "external-real-v3-naturalized-source-families.csv"
COVERAGE_CSV = INTAKE / "external-real-v3-naturalized-domain-source-coverage.csv"

ALLOWED_SEMANTIC_TYPES = {"TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}
PUBLIC_FORBIDDEN_HEADERS = re.compile(r"oracle|gold|ground.?truth|correct.?answer|answer", re.I)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="validate external-real-v3-naturalized")
    parser.add_argument("--min-ready-events", type=int, default=200)
    parser.add_argument("--min-domains", type=int, default=10)
    parser.add_argument("--min-domain-events", type=int, default=12)
    parser.add_argument("--min-per-type", type=int, default=60)
    parser.add_argument("--min-source-families-per-domain", type=int, default=2)
    parser.add_argument("--max-structured-excerpt-rate", type=float, default=0.25)
    parser.add_argument("--prefix", default="external-real-v3-naturalized-validation-213")
    return parser.parse_args()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), list(reader)


def write_audit(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["level", "code", "entity", "message"])
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_ids(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def validate_xml(audit: list[dict[str, str]], path: Path, entity: str, code: str) -> None:
    if not path.is_file():
        audit.append({"level": "ERROR", "code": code, "entity": entity, "message": str(path)})
        return
    try:
        ET.parse(path)
    except ET.ParseError as exc:
        audit.append({"level": "ERROR", "code": "INVALID_XML", "entity": entity, "message": str(exc)})


def main() -> int:
    args = parse_args()
    audit: list[dict[str, str]] = []

    event_headers, events = read_csv(EVENT_CSV)
    doc_headers, docs = read_csv(DOC_CSV)
    candidate_headers, candidates = read_csv(CAND_CSV)
    _, oracles = read_csv(ORACLE_CSV)
    _, sources = read_csv(SOURCE_CSV)
    _, coverage = read_csv(COVERAGE_CSV)

    for label, headers in (("events", event_headers), ("documents", doc_headers), ("candidates", candidate_headers)):
        leaked = [header for header in headers if PUBLIC_FORBIDDEN_HEADERS.search(header)]
        if leaked:
            audit.append({"level": "ERROR", "code": "PUBLIC_ANSWER_HEADER", "entity": label, "message": "|".join(leaked)})

    ready = [row for row in events if row.get("status", "").upper() == "READY"]
    domain_counts = Counter(row["domain"] for row in ready)
    type_counts = Counter(row["semantic_type"] for row in ready)
    source_counts = Counter(row["domain"] for row in sources)
    raw_docs = sum(row["source_type"] == "EXTERNAL_PUBLIC_RAW_EXCERPT_WINDOW" for row in docs)
    structured_docs = sum(row["source_type"] == "EXTERNAL_PUBLIC_STRUCTURED_EXCERPT" for row in docs)
    structured_rate = structured_docs / len(docs)

    if len(ready) < args.min_ready_events:
        audit.append({"level": "ERROR", "code": "TOO_FEW_READY_EVENTS", "entity": "events", "message": str(len(ready))})
    if len(domain_counts) < args.min_domains:
        audit.append({"level": "ERROR", "code": "TOO_FEW_DOMAINS", "entity": "events", "message": str(dict(domain_counts))})
    for domain, count in domain_counts.items():
        if count < args.min_domain_events:
            audit.append({"level": "ERROR", "code": "DOMAIN_TOO_SMALL", "entity": domain, "message": str(count)})
    for semantic_type in ALLOWED_SEMANTIC_TYPES:
        if type_counts[semantic_type] < args.min_per_type:
            audit.append({"level": "ERROR", "code": "TOO_FEW_EVENTS_PER_TYPE", "entity": semantic_type, "message": str(type_counts[semantic_type])})
    for domain in domain_counts:
        if source_counts[domain] < args.min_source_families_per_domain:
            audit.append({"level": "ERROR", "code": "TOO_FEW_SOURCE_FAMILIES", "entity": domain, "message": str(source_counts[domain])})
    if structured_rate > args.max_structured_excerpt_rate:
        audit.append(
            {
                "level": "ERROR",
                "code": "STRUCTURED_EXCERPT_RATE_TOO_HIGH",
                "entity": "documents",
                "message": f"{structured_docs}/{len(docs)} = {structured_rate:.6f}",
            }
        )

    docs_by_id = {row["document_id"]: row for row in docs}
    candidates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidates:
        candidates_by_event[row["event_id"]].append(row)
        try:
            operation = json.loads(row["operation_json"])
        except json.JSONDecodeError as exc:
            audit.append({"level": "ERROR", "code": "BAD_OPERATION_JSON", "entity": f"{row['event_id']}/{row['candidate_id']}", "message": str(exc)})
            continue
        if "external-real-v3-naturalized" not in json.dumps(operation, ensure_ascii=False):
            audit.append({"level": "ERROR", "code": "BAD_OPERATION_IRI", "entity": f"{row['event_id']}/{row['candidate_id']}", "message": "missing naturalized IRI"})

    oracle_by_event = {row["event_id"]: row for row in oracles}
    slot_counts = Counter(row["oracle_candidate_id"] for row in oracles)
    for event in ready:
        event_id = event["event_id"]
        if event_id not in oracle_by_event:
            audit.append({"level": "ERROR", "code": "MISSING_ORACLE", "entity": event_id, "message": ""})
        for doc_id in split_ids(event["document_ids"]):
            if doc_id not in docs_by_id:
                audit.append({"level": "ERROR", "code": "MISSING_DOC_ID", "entity": event_id, "message": doc_id})
        if len(candidates_by_event[event_id]) != 3:
            audit.append({"level": "ERROR", "code": "BAD_CANDIDATE_COUNT", "entity": event_id, "message": str(len(candidates_by_event[event_id]))})
        if not (RULES / f"{event_id}-formal-policy.json").is_file():
            audit.append({"level": "ERROR", "code": "MISSING_RULE", "entity": event_id, "message": ""})
        if not (DOCS / "excerpts" / f"{event_id}-evidence.md").is_file():
            audit.append({"level": "ERROR", "code": "MISSING_EVIDENCE", "entity": event_id, "message": ""})
        validate_xml(audit, MUTANTS / f"{event_id}.owl", event_id, "MISSING_MUTANT")
        for candidate in candidates_by_event[event_id]:
            validate_xml(
                audit,
                BUILT / "candidate-owls" / event_id / f"{candidate['candidate_id']}.owl",
                f"{event_id}/{candidate['candidate_id']}",
                "MISSING_CANDIDATE_OWL",
            )

    hash_mismatches = 0
    for doc in docs:
        path = DOCS / doc["file_name"]
        if not path.is_file() or sha256_file(path) != doc["sha256"].lower():
            hash_mismatches += 1
    if hash_mismatches:
        audit.append({"level": "ERROR", "code": "DOCUMENT_HASH_MISMATCH", "entity": "documents", "message": str(hash_mismatches)})

    summary = {
        "benchmark": "external-real-v3-naturalized",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ready_events": len(ready),
        "domains": len(domain_counts),
        "type_counts": dict(sorted(type_counts.items())),
        "domain_counts": dict(sorted(domain_counts.items())),
        "documents": len(docs),
        "raw_excerpt_window_documents": raw_docs,
        "structured_excerpt_documents": structured_docs,
        "structured_excerpt_rate": structured_rate,
        "candidates": len(candidates),
        "oracle_rows": len(oracles),
        "source_families": len(sources),
        "source_families_by_domain": dict(sorted(source_counts.items())),
        "domain_source_coverage_rows": len(coverage),
        "correct_slot_counts": dict(sorted(slot_counts.items())),
        "errors": sum(row["level"] == "ERROR" for row in audit),
        "warnings": sum(row["level"] == "WARNING" for row in audit),
    }

    details_path = OUTPUT_DIR / f"{args.prefix}-details.csv"
    summary_path = OUTPUT_DIR / f"{args.prefix}-summary.json"
    report_path = OUTPUT_DIR / f"{args.prefix}-report.md"
    write_audit(details_path, audit)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(
        "\n".join(
            [
                "# External Real V3 Naturalized Validation Report",
                "",
                f"Generated at {summary['generated_at_utc']} UTC.",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| READY events | {summary['ready_events']} |",
                f"| Domains | {summary['domains']} |",
                f"| Source families | {summary['source_families']} |",
                f"| Documents | {summary['documents']} |",
                f"| Raw excerpt window documents | {summary['raw_excerpt_window_documents']} |",
                f"| Structured excerpt documents | {summary['structured_excerpt_documents']} |",
                f"| Structured excerpt rate | {summary['structured_excerpt_rate']:.2%} |",
                f"| Candidates | {summary['candidates']} |",
                f"| Private Oracle rows | {summary['oracle_rows']} |",
                f"| Errors | {summary['errors']} |",
                f"| Warnings | {summary['warnings']} |",
                "",
                "## Source Families By Domain",
                "",
                "| Domain | Source families |",
                "|---|---:|",
                *[f"| {domain} | {count} |" for domain, count in sorted(source_counts.items())],
                "",
                "## Boundary",
                "",
                "- This validation checks naturalized source coverage and raw excerpt-window conversion.",
                "- It does not rerun model experiments.",
                "- Formal policy and private Oracle files remain evaluation-only artifacts.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print("external-real-v3-naturalized validation")
    print(f"ready_events={summary['ready_events']}")
    print(f"domains={summary['domains']}")
    print(f"source_families={summary['source_families']}")
    print(f"source_families_by_domain={summary['source_families_by_domain']}")
    print(f"structured_excerpt_rate={summary['structured_excerpt_rate']:.2%}")
    print(f"errors={summary['errors']} warnings={summary['warnings']}")
    print(f"report={report_path}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
