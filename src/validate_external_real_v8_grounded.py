from __future__ import annotations

"""Validate staged external-real-v8-grounded files.

public/ must not include candidate CSVs. Isolation scans public excerpts and
stored document windows only. READY events must have provenance hashes,
metadata-only retrieval with zero fallback, and semantic_support=PASS.
repair-stage/ and private/ may exist; they are not construction input.
"""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from external_real_v8_layout import (
    SUPPORT_GATE_VERSION,
    BenchmarkLayout,
    construction_paths,
)
from run_auto_formal_policy_batch_v3 import find_forbidden_input_markers
from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
LAYOUT = BenchmarkLayout(BENCHMARK)

ALLOWED_SEMANTIC_TYPES = {"TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}
PUBLIC_FORBIDDEN_HEADERS = re.compile(r"oracle|gold|ground.?truth|correct.?answer|answer", re.I)
WRAPPER_MARKERS = re.compile(
    r"(?im)^(event|target|benchmark boundary|oracle|public candidate values)\s*:",
)

PROVENANCE_FIELDS = (
    "source_url",
    "retrieved_at",
    "raw_sha256",
    "text_sha256",
    "extraction_mode",
    "cache_path",
    "window_sha256",
)
ZERO_FLAGS = ("fallback_used", "candidate_used", "oracle_used", "note_used")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="validate external-real-v8-grounded")
    parser.add_argument("--min-ready-events", type=int, default=150)
    parser.add_argument("--min-domains", type=int, default=10)
    parser.add_argument("--min-domain-events", type=int, default=7)
    parser.add_argument("--min-per-type", type=int, default=40)
    parser.add_argument("--min-source-families-per-domain", type=int, default=2)
    parser.add_argument("--max-structured-excerpt-rate", type=float, default=0.25)
    parser.add_argument("--target-ready-events", type=int, default=198)
    parser.add_argument("--prefix", default="external-real-v8-grounded-validation")
    return parser.parse_args()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        return [], []
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


def public_text_files() -> list[Path]:
    files: list[Path] = []
    for root in (LAYOUT.public_documents, LAYOUT.public_excerpts):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".html"}:
                files.append(path)
    return files


def isolation_findings() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in public_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        markers = find_forbidden_input_markers(text)
        if markers:
            findings.append(
                {
                    "level": "ERROR",
                    "code": "FORBIDDEN_INPUT_MARKER",
                    "entity": str(path.relative_to(PROJECT_DIR)),
                    "message": "|".join(markers),
                }
            )
        if WRAPPER_MARKERS.search(text):
            findings.append(
                {
                    "level": "ERROR",
                    "code": "WRAPPER_METADATA",
                    "entity": str(path.relative_to(PROJECT_DIR)),
                    "message": "stored window contains Event/Target/Oracle/candidate wrapper",
                }
            )
    return findings


def main() -> int:
    args = parse_args()
    audit: list[dict[str, str]] = []
    paths = construction_paths(BENCHMARK)

    for leaked in LAYOUT.public_candidate_paths():
        audit.append(
            {
                "level": "ERROR",
                "code": "PUBLIC_CANDIDATE_CSV",
                "entity": "public",
                "message": str(leaked),
            }
        )
    if not LAYOUT.rules.exists():
        audit.append(
            {
                "level": "WARNING",
                "code": "RULES_MISSING",
                "entity": "rules",
                "message": "Hard Gate upper-bound rules are absent",
            }
        )
    elif not (LAYOUT.rules / "BLIND_FORBIDDEN.md").is_file():
        audit.append(
            {
                "level": "ERROR",
                "code": "RULES_NOT_MARKED_BLIND_FORBIDDEN",
                "entity": "rules",
                "message": "rules/ must be marked blind_forbidden",
            }
        )
    if not LAYOUT.query_contracts.is_file():
        audit.append(
            {
                "level": "ERROR",
                "code": "MISSING_QUERY_CONTRACTS",
                "entity": "public/retrieval",
                "message": str(LAYOUT.query_contracts),
            }
        )

    event_headers, events = read_csv(paths["event_csv"])
    doc_headers, docs = read_csv(paths["document_csv"])
    family_headers, families = read_csv(LAYOUT.family_csv)
    retrieval_headers, retrievals = read_csv(LAYOUT.retrieval_csv)

    for label, headers in (("events", event_headers), ("documents", doc_headers), ("families", family_headers)):
        leaked = [header for header in headers if PUBLIC_FORBIDDEN_HEADERS.search(header)]
        if leaked:
            audit.append({"level": "ERROR", "code": "PUBLIC_ANSWER_HEADER", "entity": label, "message": "|".join(leaked)})

    ready = [row for row in events if row.get("status", "").upper() == "READY"]
    domain_counts = Counter(row["domain"] for row in ready)
    type_counts = Counter(row["semantic_type"] for row in ready)
    family_counts = Counter(row["domain"] for row in families)
    retrieval_by_event = {row["event_id"]: row for row in retrievals}
    docs_by_id = {row["document_id"]: row for row in docs}

    structured_docs = sum(row.get("source_type") == "EXTERNAL_PUBLIC_STRUCTURED_EXCERPT" for row in docs)
    structured_rate = structured_docs / len(docs) if docs else 0.0

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
        if family_counts[domain] < args.min_source_families_per_domain:
            audit.append({"level": "ERROR", "code": "TOO_FEW_SOURCE_FAMILIES", "entity": domain, "message": str(family_counts[domain])})
    if structured_rate > args.max_structured_excerpt_rate:
        audit.append(
            {
                "level": "ERROR",
                "code": "STRUCTURED_EXCERPT_RATE_TOO_HIGH",
                "entity": "documents",
                "message": f"{structured_docs}/{len(docs)} = {structured_rate:.6f}",
            }
        )

    for event in ready:
        event_id = event["event_id"]
        excerpt = LAYOUT.public_excerpts / f"{event_id}-evidence.md"
        if not excerpt.is_file():
            audit.append({"level": "ERROR", "code": "MISSING_EVIDENCE", "entity": event_id, "message": ""})
        if str(event.get("semantic_support", "")).upper() not in {"PASS", "SEMANTIC_PASS"}:
            audit.append(
                {
                    "level": "ERROR",
                    "code": "SEMANTIC_SUPPORT_NOT_PASS",
                    "entity": event_id,
                    "message": event.get("semantic_support", ""),
                }
            )
        if str(event.get("support_checked_before_model_run", "")).lower() != "true":
            audit.append(
                {
                    "level": "ERROR",
                    "code": "SUPPORT_NOT_CHECKED_BEFORE_MODEL",
                    "entity": event_id,
                    "message": event.get("support_checked_before_model_run", ""),
                }
            )
        if str(event.get("support_gate_version", "")) != SUPPORT_GATE_VERSION:
            audit.append(
                {
                    "level": "ERROR",
                    "code": "SUPPORT_GATE_VERSION_MISMATCH",
                    "entity": event_id,
                    "message": event.get("support_gate_version", ""),
                }
            )
        retrieval = retrieval_by_event.get(event_id)
        if not retrieval:
            audit.append({"level": "ERROR", "code": "MISSING_RETRIEVAL", "entity": event_id, "message": ""})
        else:
            if retrieval.get("retrieval_status") != "RETRIEVAL_READY":
                audit.append({"level": "ERROR", "code": "RETRIEVAL_NOT_READY", "entity": event_id, "message": retrieval.get("retrieval_status", "")})
            for flag in ZERO_FLAGS:
                if str(retrieval.get(flag, "false")).lower() == "true":
                    audit.append({"level": "ERROR", "code": flag.upper(), "entity": event_id, "message": ""})
        for doc_id in split_ids(event.get("document_ids", "")):
            doc = docs_by_id.get(doc_id)
            if not doc:
                audit.append({"level": "ERROR", "code": "MISSING_DOC_ID", "entity": event_id, "message": doc_id})
                continue
            path = LAYOUT.public_documents / doc["file_name"]
            if not path.is_file():
                audit.append({"level": "ERROR", "code": "MISSING_DOC_FILE", "entity": event_id, "message": doc["file_name"]})
            for field in PROVENANCE_FIELDS:
                if not str(doc.get(field, "")).strip():
                    audit.append({"level": "ERROR", "code": "MISSING_PROVENANCE", "entity": f"{event_id}/{doc_id}", "message": field})
            if path.is_file() and doc.get("sha256") and sha256_file(path) != doc["sha256"]:
                audit.append({"level": "ERROR", "code": "DOC_HASH_MISMATCH", "entity": event_id, "message": doc["file_name"]})

    audit.extend(isolation_findings())

    errors = [row for row in audit if row["level"] == "ERROR"]
    warnings = [row for row in audit if row["level"] == "WARNING"]
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "external-real-v8-grounded",
        "layout": "staged",
        "ready_events": len(ready),
        "domains": dict(domain_counts),
        "semantic_types": dict(type_counts),
        "structured_excerpt_rate": structured_rate,
        "isolation_pass": not any(row["code"] in {"FORBIDDEN_INPUT_MARKER", "WRAPPER_METADATA"} for row in errors),
        "public_candidate_csv": bool(LAYOUT.public_candidate_paths()),
        "repair_stage_candidates": LAYOUT.candidate_csv.is_file(),
        "private_oracle_present": LAYOUT.oracle_csv.is_file(),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "min_ready_events": args.min_ready_events,
        "target_ready_events": args.target_ready_events,
        "short_of_target": max(0, args.target_ready_events - len(ready)),
        "curation_not_inference": True,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = OUTPUT_DIR / f"{args.prefix}-audit.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}-summary.json"
    report_path = OUTPUT_DIR / f"{args.prefix}-report.md"
    write_audit(audit_path, audit)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# external-real-v8-grounded validation",
        "",
        f"- READY events: {len(ready)}",
        f"- errors: {len(errors)}",
        f"- warnings: {len(warnings)}",
        f"- isolation_pass: {payload['isolation_pass']}",
        f"- structured_excerpt_rate: {structured_rate:.6f}",
        "",
    ]
    if errors:
        lines.append("## Errors")
        for row in errors[:50]:
            lines.append(f"- `{row['code']}` {row['entity']}: {row['message']}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
