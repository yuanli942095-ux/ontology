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


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v3"
INPUT_DIR = BENCHMARK_DIR / "input"
PRIVATE_DIR = BENCHMARK_DIR / "private"
DOCUMENT_DIR = BENCHMARK_DIR / "documents"
MUTANT_DIR = BENCHMARK_DIR / "mutants"
RULE_DIR = BENCHMARK_DIR / "rules"
BUILT_DIR = BENCHMARK_DIR / "built"
INTAKE_DIR = BENCHMARK_DIR / "source-intake"

EVENT_CSV = INPUT_DIR / "external-real-event-template.csv"
DOCUMENT_CSV = INPUT_DIR / "external-real-document-template.csv"
CANDIDATE_CSV = INPUT_DIR / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE_DIR / "external-real-oracle-template.csv"
SOURCE_FAMILY_CSV = INTAKE_DIR / "external-real-v3-source-families.csv"

ALLOWED_SEMANTIC_TYPES = {"TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}
PUBLIC_FORBIDDEN_HEADERS = re.compile(r"oracle|gold|ground.?truth|correct.?answer|answer", re.I)
PUBLIC_ANSWER_HINTS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"正确答案\s*(是|为|:)",
        r"应选择\s*(CAND|OPTION)",
        r"oracle\s*(is|=|:)",
        r"gold\s*answer",
        r"ground\s*truth",
    )
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="validate external-real-v3")
    parser.add_argument("--min-ready-events", type=int, default=200)
    parser.add_argument("--min-domains", type=int, default=10)
    parser.add_argument("--min-domain-events", type=int, default=12)
    parser.add_argument("--min-per-type", type=int, default=60)
    parser.add_argument("--prefix", default="external-real-v3-validation-213")
    return parser.parse_args()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), list(reader)


def write_audit_csv(path: Path, rows: list[dict[str, str]]) -> None:
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


class Audit:
    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []

    def add(self, level: str, code: str, entity: str, message: str) -> None:
        self.rows.append({"level": level, "code": code, "entity": entity, "message": message})

    def error(self, code: str, entity: str, message: str) -> None:
        self.add("ERROR", code, entity, message)

    def warn(self, code: str, entity: str, message: str) -> None:
        self.add("WARNING", code, entity, message)


def key_map(audit: Audit, rows: list[dict[str, str]], key: str, label: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows, start=2):
        value = row.get(key, "").strip()
        if not value:
            audit.error("EMPTY_KEY", f"{label}:{index}", key)
        elif value in result:
            audit.error("DUPLICATE_KEY", f"{label}:{index}", value)
        else:
            result[value] = row
    return result


def check_public_leakage(audit: Audit, headers: list[str], rows: list[dict[str, str]], label: str) -> None:
    leaked = [header for header in headers if PUBLIC_FORBIDDEN_HEADERS.search(header)]
    if leaked:
        audit.error("PUBLIC_ANSWER_HEADER", label, "|".join(leaked))
    for index, row in enumerate(rows, start=2):
        text = " ".join(str(value) for value in row.values())
        for pattern in PUBLIC_ANSWER_HINTS:
            if pattern.search(text):
                audit.error("PUBLIC_ANSWER_HINT", f"{label}:{index}", pattern.pattern)


def validate_operation(audit: Audit, row: dict[str, str]) -> None:
    entity = f"{row.get('event_id')}/{row.get('candidate_id')}"
    try:
        parsed = json.loads(row.get("operation_json", ""))
    except json.JSONDecodeError as exc:
        audit.error("INVALID_OPERATION_JSON", entity, str(exc))
        return
    if parsed.get("operator") != "REPLACE_PROPERTY_VALUE":
        audit.error("UNSUPPORTED_OPERATOR", entity, str(parsed.get("operator")))
    if "external-real-v2" in json.dumps(parsed, ensure_ascii=False):
        audit.error("STALE_V2_IRI", entity, "operation_json still references external-real-v2")
    for key in ("subject_iri", "predicate_iri", "old_value", "new_value"):
        if key not in parsed:
            audit.error("MISSING_OPERATION_FIELD", entity, key)


def validate_xml(audit: Audit, path: Path, entity: str, code: str) -> None:
    if not path.is_file():
        audit.error(code, entity, str(path))
        return
    try:
        ET.parse(path)
    except ET.ParseError as exc:
        audit.error("INVALID_XML", entity, f"{path}: {exc}")


def validate_public_sources(audit: Audit, source_rows: list[dict[str, str]]) -> None:
    source_map = key_map(audit, source_rows, "source_id", "source_families")
    domains = Counter(row.get("domain", "") for row in source_rows)
    for source_id, row in source_map.items():
        if not row.get("source_url", "").startswith("http"):
            audit.error("BAD_SOURCE_URL", source_id, row.get("source_url", ""))
        if not row.get("publisher", "").strip():
            audit.error("MISSING_SOURCE_PUBLISHER", source_id, "")
        if not row.get("public_basis", "").strip():
            audit.error("MISSING_PUBLIC_BASIS", source_id, "")
    if len(domains) < 10:
        audit.error("TOO_FEW_SOURCE_DOMAINS", "source_families", str(dict(domains)))


def main() -> int:
    args = parse_args()
    audit = Audit()

    event_headers, event_rows = read_csv(EVENT_CSV)
    doc_headers, doc_rows = read_csv(DOCUMENT_CSV)
    candidate_headers, candidate_rows = read_csv(CANDIDATE_CSV)
    oracle_headers, oracle_rows = read_csv(ORACLE_CSV)
    _, source_rows = read_csv(SOURCE_FAMILY_CSV)

    check_public_leakage(audit, event_headers, event_rows, "events")
    check_public_leakage(audit, doc_headers, doc_rows, "documents")
    check_public_leakage(audit, candidate_headers, candidate_rows, "candidates")
    validate_public_sources(audit, source_rows)

    docs = key_map(audit, doc_rows, "document_id", "documents")
    oracles = key_map(audit, oracle_rows, "event_id", "oracle")
    ready_events = [row for row in event_rows if row.get("status", "").strip().upper() == "READY"]
    type_counts = Counter(row.get("semantic_type", "").strip().upper() for row in ready_events)
    domain_counts = Counter(row.get("domain", "").strip() for row in ready_events)

    if len(ready_events) < args.min_ready_events:
        audit.error("TOO_FEW_READY_EVENTS", "events", str(len(ready_events)))
    if len(domain_counts) < args.min_domains:
        audit.error("TOO_FEW_DOMAINS", "events", str(dict(domain_counts)))
    for domain, count in domain_counts.items():
        if count < args.min_domain_events:
            audit.error("DOMAIN_TOO_SMALL", domain, str(count))
    for semantic_type in ALLOWED_SEMANTIC_TYPES:
        if type_counts[semantic_type] < args.min_per_type:
            audit.error("TOO_FEW_EVENTS_PER_TYPE", semantic_type, str(type_counts[semantic_type]))

    for row in doc_rows:
        file_path = DOCUMENT_DIR / row.get("file_name", "")
        if not file_path.is_file():
            audit.error("MISSING_DOCUMENT_FILE", row.get("document_id", ""), str(file_path))
        elif row.get("sha256", "").strip().lower() != sha256_file(file_path):
            audit.error("DOCUMENT_HASH_MISMATCH", row.get("document_id", ""), str(file_path))
        if row.get("source_type", "").startswith("EXTERNAL_PUBLIC") and not row.get("source_url", "").startswith("http"):
            audit.error("MISSING_PUBLIC_SOURCE_URL", row.get("document_id", ""), row.get("source_url", ""))

    candidates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    correct_slot_counts: Counter[str] = Counter()
    oracle_by_event = {row["event_id"]: row for row in oracle_rows}
    for row in candidate_rows:
        candidates_by_event[row.get("event_id", "")].append(row)
        validate_operation(audit, row)
        oracle = oracle_by_event.get(row.get("event_id", ""))
        if oracle and oracle.get("oracle_candidate_id") == row.get("candidate_id"):
            correct_slot_counts[row.get("candidate_id", "")] += 1

    for event in ready_events:
        event_id = event["event_id"]
        if event_id not in oracles:
            audit.error("MISSING_ORACLE_ROW", event_id, "")
        if event.get("semantic_type", "") not in ALLOWED_SEMANTIC_TYPES:
            audit.error("BAD_SEMANTIC_TYPE", event_id, event.get("semantic_type", ""))
        if "external-real-v2" in event.get("source_owl", ""):
            audit.error("STALE_V2_SOURCE_OWL", event_id, event.get("source_owl", ""))
        for doc_id in split_ids(event.get("document_ids", "")):
            if doc_id not in docs:
                audit.error("MISSING_DOCUMENT_ID", event_id, doc_id)
        if len(candidates_by_event[event_id]) != 3:
            audit.error("BAD_CANDIDATE_COUNT", event_id, str(len(candidates_by_event[event_id])))
        if not (RULE_DIR / f"{event_id}-formal-policy.json").is_file():
            audit.error("MISSING_RULE_FILE", event_id, "")
        if not (DOCUMENT_DIR / "excerpts" / f"{event_id}-evidence.md").is_file():
            audit.error("MISSING_EVIDENCE_FILE", event_id, "")
        validate_xml(audit, MUTANT_DIR / f"{event_id}.owl", event_id, "MISSING_MUTANT_OWL")
        for candidate in candidates_by_event[event_id]:
            validate_xml(
                audit,
                BUILT_DIR / "candidate-owls" / event_id / f"{candidate['candidate_id']}.owl",
                f"{event_id}/{candidate['candidate_id']}",
                "MISSING_CANDIDATE_OWL",
            )

    summary = {
        "benchmark": "external-real-v3",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ready_events": len(ready_events),
        "domains": len(domain_counts),
        "type_counts": dict(sorted(type_counts.items())),
        "domain_counts": dict(sorted(domain_counts.items())),
        "documents": len(doc_rows),
        "candidates": len(candidate_rows),
        "oracle_rows": len(oracle_rows),
        "correct_slot_counts": dict(sorted(correct_slot_counts.items())),
        "source_families": len(source_rows),
        "errors": sum(row["level"] == "ERROR" for row in audit.rows),
        "warnings": sum(row["level"] == "WARNING" for row in audit.rows),
    }

    details_path = OUTPUT_DIR / f"{args.prefix}-details.csv"
    summary_path = OUTPUT_DIR / f"{args.prefix}-summary.json"
    report_path = OUTPUT_DIR / f"{args.prefix}-report.md"
    write_audit_csv(details_path, audit.rows)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(
        "\n".join(
            [
                "# External Real V3 Validation Report",
                "",
                f"Generated at {summary['generated_at_utc']} UTC.",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| READY events | {summary['ready_events']} |",
                f"| Domains | {summary['domains']} |",
                f"| Source families | {summary['source_families']} |",
                f"| Documents | {summary['documents']} |",
                f"| Candidates | {summary['candidates']} |",
                f"| Private Oracle rows | {summary['oracle_rows']} |",
                f"| Errors | {summary['errors']} |",
                f"| Warnings | {summary['warnings']} |",
                "",
                "## Domain Counts",
                "",
                "| Domain | READY events |",
                "|---|---:|",
                *[f"| {domain} | {count} |" for domain, count in sorted(domain_counts.items())],
                "",
                "## Semantic Type Counts",
                "",
                "| Semantic type | READY events |",
                "|---|---:|",
                *[f"| {semantic_type} | {count} |" for semantic_type, count in sorted(type_counts.items())],
                "",
                "## Correct Candidate Slot Counts",
                "",
                "| Candidate slot | Oracle rows |",
                "|---|---:|",
                *[f"| {slot} | {count} |" for slot, count in sorted(correct_slot_counts.items())],
                "",
                "## Boundary",
                "",
                "- Validation checks structure, source traceability, candidate artifacts, private Oracle isolation, and minimum scale.",
                "- It does not by itself rerun model experiments on external-real-v3.",
                "- The added events are official/public-source structured benchmark items, not random synthetic rows.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print("external-real-v3 validation")
    print(f"ready_events={summary['ready_events']}")
    print(f"domains={summary['domains']}")
    print(f"type_counts={summary['type_counts']}")
    print(f"domain_counts={summary['domain_counts']}")
    print(f"correct_slot_counts={summary['correct_slot_counts']}")
    print(f"errors={summary['errors']} warnings={summary['warnings']}")
    print(f"details={details_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
