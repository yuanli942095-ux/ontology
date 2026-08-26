from __future__ import annotations

"""Validate the external-real-v1 benchmark scaffold and public inputs.

The validator is intentionally conservative: it allows an empty scaffold, but
READY events must have complete public metadata, source files, candidates, and
no answer leakage in public templates. The private Oracle is checked only for
schema and row count; its contents are not loaded into public reports.
"""

import argparse
import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR, write_csv


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v1"
INPUT_DIR = BENCHMARK_DIR / "input"
PRIVATE_DIR = BENCHMARK_DIR / "private"
DOCUMENT_DIR = BENCHMARK_DIR / "documents"
MUTANT_DIR = BENCHMARK_DIR / "mutants"
RULE_DIR = BENCHMARK_DIR / "rules"
BUILT_DIR = BENCHMARK_DIR / "built"
EVENT_CSV = INPUT_DIR / "external-real-event-template.csv"
DOCUMENT_CSV = INPUT_DIR / "external-real-document-template.csv"
CANDIDATE_CSV = INPUT_DIR / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE_DIR / "external-real-oracle-template.csv"

ALLOWED_SEMANTIC_TYPES = {
    "TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE",
}
ALLOWED_VALUE_KINDS = {"literal_integer", "literal_string", "iri", "class"}
ALLOWED_STATUS = {"DRAFT", "READY", "RETIRED"}
PUBLIC_FORBIDDEN_HEADERS = re.compile(
    r"oracle|gold|ground.?truth|correct.?answer|answer", re.I
)
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

REQUIRED_EVENT_FIELDS = {
    "event_id",
    "split",
    "semantic_type",
    "domain",
    "title",
    "case_context",
    "subject_label",
    "predicate_label",
    "value_kind",
    "document_ids",
    "source_owl",
    "source_url",
    "retrieved_at",
    "status",
}
REQUIRED_DOCUMENT_FIELDS = {
    "document_id",
    "event_id",
    "file_name",
    "source_title",
    "source_url",
    "publisher",
    "effective_from",
    "effective_to",
    "source_type",
    "sha256",
    "status",
}
REQUIRED_CANDIDATE_FIELDS = {
    "event_id",
    "candidate_id",
    "display_value",
    "operation_json",
    "status",
}
REQUIRED_ORACLE_FIELDS = {
    "event_id",
    "oracle_candidate_id",
    "oracle_value",
    "evidence_document_ids",
    "evidence_spans_json",
    "annotator_1",
    "annotator_2",
    "adjudicator",
    "agreement_status",
    "status",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="validate external-real-v1")
    parser.add_argument("--min-ready-events", type=int, default=0)
    parser.add_argument("--min-per-type", type=int, default=0)
    parser.add_argument("--prefix", default="external-real-v1-validation")
    return parser.parse_args()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    return headers, rows


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
        self.rows.append(
            {"level": level, "code": code, "entity": entity, "message": message}
        )

    def error(self, code: str, entity: str, message: str) -> None:
        self.add("ERROR", code, entity, message)

    def warn(self, code: str, entity: str, message: str) -> None:
        self.add("WARNING", code, entity, message)

    def info(self, code: str, entity: str, message: str) -> None:
        self.add("INFO", code, entity, message)


def check_headers(
    audit: Audit, headers: list[str], required: set[str], label: str
) -> None:
    missing = sorted(required - set(headers))
    if missing:
        audit.error("MISSING_COLUMNS", label, "|".join(missing))


def check_public_leakage(
    audit: Audit, headers: list[str], rows: list[dict[str, str]], label: str
) -> None:
    leaked_headers = [name for name in headers if PUBLIC_FORBIDDEN_HEADERS.search(name)]
    if leaked_headers:
        audit.error("PUBLIC_ANSWER_HEADER", label, "|".join(leaked_headers))
    for index, row in enumerate(rows, start=2):
        text = " ".join(str(value) for value in row.values())
        for pattern in PUBLIC_ANSWER_HINTS:
            if pattern.search(text):
                audit.error("PUBLIC_ANSWER_HINT", f"{label}:{index}", pattern.pattern)


def key_map(
    audit: Audit, rows: list[dict[str, str]], key: str, label: str
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows, start=2):
        value = str(row.get(key, "")).strip()
        if not value:
            audit.error("EMPTY_KEY", f"{label}:{index}", key)
        elif value in result:
            audit.error("DUPLICATE_KEY", f"{label}:{index}", value)
        else:
            result[value] = row
    return result


def validate_operation(audit: Audit, event_id: str, candidate_id: str, value: str) -> None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        audit.error("INVALID_OPERATION_JSON", f"{event_id}/{candidate_id}", str(exc))
        return
    if not isinstance(parsed, dict):
        audit.error("INVALID_OPERATION_JSON", f"{event_id}/{candidate_id}", "not object")
        return
    if not parsed.get("operator"):
        audit.error("MISSING_OPERATOR", f"{event_id}/{candidate_id}", "operator missing")


def validate_xml_file(audit: Audit, code: str, entity: str, path: Path) -> None:
    if not path.is_file():
        audit.error(code, entity, str(path))
        return
    try:
        ET.parse(path)
    except ET.ParseError as exc:
        audit.error("INVALID_XML", entity, f"{path}: {exc}")


def main() -> int:
    args = parse_args()
    audit = Audit()

    event_headers, event_rows = read_csv(EVENT_CSV)
    doc_headers, doc_rows = read_csv(DOCUMENT_CSV)
    candidate_headers, candidate_rows = read_csv(CANDIDATE_CSV)
    oracle_headers, oracle_rows = read_csv(ORACLE_CSV)

    check_headers(audit, event_headers, REQUIRED_EVENT_FIELDS, "events")
    check_headers(audit, doc_headers, REQUIRED_DOCUMENT_FIELDS, "documents")
    check_headers(audit, candidate_headers, REQUIRED_CANDIDATE_FIELDS, "candidates")
    check_headers(audit, oracle_headers, REQUIRED_ORACLE_FIELDS, "oracle")
    check_public_leakage(audit, event_headers, event_rows, "events")
    check_public_leakage(audit, doc_headers, doc_rows, "documents")
    check_public_leakage(audit, candidate_headers, candidate_rows, "candidates")

    events = key_map(audit, event_rows, "event_id", "events")
    documents = key_map(audit, doc_rows, "document_id", "documents")
    oracles = key_map(audit, oracle_rows, "event_id", "oracle")

    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in doc_rows:
        event_id = str(row.get("event_id", "")).strip()
        if event_id:
            docs_by_event[event_id].append(row)

    candidates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    candidate_keys: set[tuple[str, str]] = set()
    for index, row in enumerate(candidate_rows, start=2):
        event_id = str(row.get("event_id", "")).strip()
        candidate_id = str(row.get("candidate_id", "")).strip()
        key = (event_id, candidate_id)
        if key in candidate_keys:
            audit.error("DUPLICATE_CANDIDATE", f"candidates:{index}", "/".join(key))
        candidate_keys.add(key)
        if event_id:
            candidates_by_event[event_id].append(row)
        if row.get("operation_json", "").strip():
            validate_operation(audit, event_id, candidate_id, row["operation_json"])

    ready_events = [
        row for row in event_rows if str(row.get("status", "")).strip().upper() == "READY"
    ]
    type_counts = Counter(str(row.get("semantic_type", "")).strip().upper() for row in ready_events)
    if len(ready_events) < args.min_ready_events:
        audit.error(
            "TOO_FEW_READY_EVENTS",
            "events",
            f"{len(ready_events)} < {args.min_ready_events}",
        )
    for semantic_type in sorted(ALLOWED_SEMANTIC_TYPES):
        if type_counts[semantic_type] < args.min_per_type:
            audit.error(
                "TOO_FEW_READY_BY_TYPE",
                semantic_type,
                f"{type_counts[semantic_type]} < {args.min_per_type}",
            )

    for event_id, row in events.items():
        status = str(row.get("status", "")).strip().upper()
        if status not in ALLOWED_STATUS:
            audit.error("INVALID_STATUS", event_id, status)
        if status != "READY":
            continue
        semantic_type = str(row.get("semantic_type", "")).strip().upper()
        value_kind = str(row.get("value_kind", "")).strip().lower()
        if semantic_type not in ALLOWED_SEMANTIC_TYPES:
            audit.error("INVALID_SEMANTIC_TYPE", event_id, semantic_type)
        if value_kind not in ALLOWED_VALUE_KINDS:
            audit.error("INVALID_VALUE_KIND", event_id, value_kind)
        if not str(row.get("source_url", "")).strip():
            audit.error("MISSING_SOURCE_URL", event_id, "source_url")
        source_owl = str(row.get("source_owl", "")).strip()
        if not source_owl:
            audit.error("MISSING_SOURCE_OWL", event_id, "source_owl")
        else:
            validate_xml_file(
                audit,
                "MISSING_SOURCE_OWL_FILE",
                event_id,
                PROJECT_DIR / source_owl,
            )
        policy_path = RULE_DIR / f"{event_id}-formal-policy.json"
        if not policy_path.is_file():
            audit.error("MISSING_FORMAL_POLICY", event_id, str(policy_path))
        else:
            try:
                policy = json.loads(policy_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                audit.error("INVALID_FORMAL_POLICY_JSON", event_id, str(exc))
            else:
                if policy.get("event_id") != event_id:
                    audit.error("FORMAL_POLICY_EVENT_MISMATCH", event_id, str(policy.get("event_id")))
        doc_ids = split_ids(row.get("document_ids", ""))
        if not doc_ids:
            audit.error("NO_DOCUMENTS", event_id, "document_ids")
        for document_id in doc_ids:
            if document_id not in documents:
                audit.error("UNKNOWN_DOCUMENT", event_id, document_id)
        if not docs_by_event.get(event_id):
            audit.error("NO_DOCUMENT_ROWS", event_id, "documents")
        ready_candidates = [
            item
            for item in candidates_by_event.get(event_id, [])
            if str(item.get("status", "")).strip().upper() == "READY"
        ]
        if len(ready_candidates) < 2:
            audit.error("TOO_FEW_CANDIDATES", event_id, str(len(ready_candidates)))
        for candidate in ready_candidates:
            candidate_id = str(candidate.get("candidate_id", "")).strip()
            candidate_path = BUILT_DIR / "candidate-owls" / event_id / f"{candidate_id}.owl"
            validate_xml_file(
                audit,
                "MISSING_CANDIDATE_OWL",
                f"{event_id}/{candidate_id}",
                candidate_path,
            )
        if event_id not in oracles:
            audit.warn("MISSING_PRIVATE_ORACLE", event_id, "private oracle pending")

    for document_id, row in documents.items():
        status = str(row.get("status", "")).strip().upper()
        if status not in ALLOWED_STATUS:
            audit.error("INVALID_DOCUMENT_STATUS", document_id, status)
        if status != "READY":
            continue
        source_type = str(row.get("source_type", "")).strip().upper()
        if source_type in {"CONTROLLED", "CONTROLLED_DEV", "SYNTHETIC"}:
            audit.error("NON_EXTERNAL_SOURCE_TYPE", document_id, source_type)
        path = DOCUMENT_DIR / str(row.get("file_name", "")).strip()
        if not path.is_file():
            audit.error("MISSING_DOCUMENT_FILE", document_id, str(path))
            continue
        recorded = str(row.get("sha256", "")).strip().lower()
        actual = sha256_file(path)
        if recorded and recorded != actual:
            audit.error("SHA256_MISMATCH", document_id, f"{recorded} != {actual}")

    if not audit.rows:
        audit.info("OK", "external-real-v1", "validation passed")

    summary = [
        {
            "benchmark": "external-real-v1",
            "events_total": len(event_rows),
            "events_ready": len(ready_events),
            "documents_total": len(doc_rows),
            "candidates_total": len(candidate_rows),
            "private_oracle_rows": len(oracle_rows),
            "ready_temporal_version": type_counts["TEMPORAL_VERSION"],
            "ready_general_rule_exception": type_counts["GENERAL_RULE_EXCEPTION"],
            "ready_cross_sentence_scope": type_counts["CROSS_SENTENCE_SCOPE"],
            "errors": sum(1 for row in audit.rows if row["level"] == "ERROR"),
            "warnings": sum(1 for row in audit.rows if row["level"] == "WARNING"),
            "status": "PASS" if not any(row["level"] == "ERROR" for row in audit.rows) else "FAIL",
        }
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    details_csv = OUTPUT_DIR / f"{args.prefix}-details.csv"
    summary_csv = OUTPUT_DIR / f"{args.prefix}-summary.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    write_csv(details_csv, audit.rows)
    write_csv(summary_csv, summary)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_contents_loaded": False,
        "summary": summary,
        "details": audit.rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "external-real-v1 validation",
        f"events_ready={len(ready_events)}",
        f"errors={summary[0]['errors']}",
        f"warnings={summary[0]['warnings']}",
        f"status={summary[0]['status']}",
        "",
        f"details={details_csv}",
        f"summary={summary_csv}",
        f"json={json_path}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if summary[0]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
