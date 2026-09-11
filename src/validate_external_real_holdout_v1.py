from __future__ import annotations

"""Validate external-real-holdout-v1-draft construction quality."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-draft"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-draft"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def validate(args: argparse.Namespace) -> int:
    audit: list[dict[str, Any]] = []
    event_csv = args.benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    doc_csv = args.benchmark_dir / "public" / "documents" / "external-real-document-template.csv"
    family_csv = args.benchmark_dir / "public" / "retrieval" / "external-real-v8-grounded-source-families.csv"
    retrieval_csv = args.benchmark_dir / "public" / "retrieval" / "external-real-v8-event-retrieval.csv"
    candidate_csv = args.benchmark_dir / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    oracle_csv = args.benchmark_dir / "private" / "oracle" / "external-real-oracle-template.csv"
    manifest_csv = args.output_dir / "external-real-holdout-v1-freeze-manifest.csv"

    for path in (event_csv, doc_csv, family_csv, retrieval_csv, candidate_csv, oracle_csv, manifest_csv):
        if not path.is_file():
            audit.append({"level": "ERROR", "code": "MISSING_FILE", "entity": str(path), "message": ""})
    if audit:
        write_csv(args.output_dir / "external-real-holdout-v1-validation-audit.csv", audit)
        return 1

    events = read_csv(event_csv)
    docs = read_csv(doc_csv)
    families = read_csv(family_csv)
    retrievals = read_csv(retrieval_csv)
    candidates = read_csv(candidate_csv)
    oracles = read_csv(oracle_csv)
    manifest = read_csv(manifest_csv)

    ready_events = [row for row in events if row.get("status") == "READY"]
    domain_counts = Counter(row["domain"] for row in ready_events)
    type_counts = Counter(row["semantic_type"] for row in ready_events)
    families_by_domain: dict[str, set[str]] = defaultdict(set)
    for row in families:
        if row.get("status") == "SUCCESS":
            families_by_domain[row["domain"]].add(row["source_id"])

    if len(ready_events) < args.min_ready:
        audit.append({"level": "ERROR", "code": "TOO_FEW_READY_EVENTS", "entity": "events", "message": str(len(ready_events))})
    if len(domain_counts) < args.min_domains:
        audit.append({"level": "ERROR", "code": "TOO_FEW_DOMAINS", "entity": "events", "message": str(dict(domain_counts))})
    for domain, count in domain_counts.items():
        if count < args.min_per_domain:
            audit.append({"level": "ERROR", "code": "DOMAIN_TOO_SMALL", "entity": domain, "message": str(count)})
        if len(families_by_domain[domain]) < args.min_source_families_per_domain:
            audit.append({"level": "ERROR", "code": "TOO_FEW_SOURCE_FAMILIES", "entity": domain, "message": str(sorted(families_by_domain[domain]))})
    for semantic_type in ("TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"):
        if type_counts[semantic_type] < args.min_per_type:
            audit.append({"level": "ERROR", "code": "TYPE_TOO_SMALL", "entity": semantic_type, "message": str(type_counts[semantic_type])})

    docs_by_id = {row["document_id"]: row for row in docs}
    retrieval_by_id = {row["event_id"]: row for row in retrievals}
    candidates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidates:
        candidates_by_event[row["event_id"]].append(row)
        try:
            json.loads(row["operation_json"])
        except json.JSONDecodeError as exc:
            audit.append({"level": "ERROR", "code": "BAD_OPERATION_JSON", "entity": f"{row['event_id']}/{row['candidate_id']}", "message": str(exc)})
    oracle_by_event = {row["event_id"]: row for row in oracles}

    for event in ready_events:
        event_id = event["event_id"]
        excerpt = args.benchmark_dir / "public" / "excerpts" / f"{event_id}-evidence.md"
        if not excerpt.is_file() or excerpt.stat().st_size == 0:
            audit.append({"level": "ERROR", "code": "MISSING_EVIDENCE_WINDOW", "entity": event_id, "message": ""})
        retrieval = retrieval_by_id.get(event_id)
        if not retrieval or retrieval.get("retrieval_status") != "RETRIEVAL_READY":
            audit.append({"level": "ERROR", "code": "RETRIEVAL_NOT_READY", "entity": event_id, "message": "" if not retrieval else retrieval.get("retrieval_status", "")})
        if len(candidates_by_event[event_id]) != 3:
            audit.append({"level": "ERROR", "code": "BAD_CANDIDATE_COUNT", "entity": event_id, "message": str(len(candidates_by_event[event_id]))})
        oracle = oracle_by_event.get(event_id)
        candidate_ids = {row["candidate_id"] for row in candidates_by_event[event_id]}
        if not oracle or oracle.get("oracle_candidate_id") not in candidate_ids:
            audit.append({"level": "ERROR", "code": "ORACLE_NOT_IN_CANDIDATES", "entity": event_id, "message": "" if not oracle else oracle.get("oracle_candidate_id", "")})
        for doc_id in event.get("document_ids", "").split("|"):
            doc = docs_by_id.get(doc_id)
            if not doc:
                audit.append({"level": "ERROR", "code": "MISSING_DOC_ROW", "entity": event_id, "message": doc_id})
                continue
            path = args.benchmark_dir / "public" / "documents" / doc["file_name"]
            if not path.is_file() or path.stat().st_size < 500:
                audit.append({"level": "ERROR", "code": "BAD_DOC_FILE", "entity": event_id, "message": doc["file_name"]})
            for field in ("source_url", "retrieved_at", "raw_sha256", "text_sha256", "cache_path", "window_sha256", "sha256"):
                if not doc.get(field):
                    audit.append({"level": "ERROR", "code": "MISSING_DOC_PROVENANCE", "entity": f"{event_id}/{doc_id}", "message": field})

    manifest_ids = {row["event_id"] for row in manifest}
    ready_ids = {row["event_id"] for row in ready_events}
    if manifest_ids != ready_ids:
        audit.append({"level": "ERROR", "code": "MANIFEST_READY_MISMATCH", "entity": "manifest", "message": f"manifest={len(manifest_ids)} ready={len(ready_ids)}"})

    summary = {
        "benchmark": args.benchmark_dir.name,
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ready_events": len(ready_events),
        "domains": dict(sorted(domain_counts.items())),
        "semantic_types": dict(sorted(type_counts.items())),
        "source_families_by_domain": {key: sorted(value) for key, value in sorted(families_by_domain.items())},
        "error_count": sum(1 for row in audit if row["level"] == "ERROR"),
        "warning_count": sum(1 for row in audit if row["level"] == "WARNING"),
        "manual_review_required_before_freeze": True,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "external-real-holdout-v1-validation-audit.csv", audit or [{"level": "INFO", "code": "VALIDATION_PASS", "entity": "benchmark", "message": ""}])
    (args.output_dir / "external-real-holdout-v1-validation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["error_count"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--min-ready", type=int, default=30)
    parser.add_argument("--min-domains", type=int, default=10)
    parser.add_argument("--min-per-domain", type=int, default=3)
    parser.add_argument("--min-source-families-per-domain", type=int, default=2)
    parser.add_argument("--min-per-type", type=int, default=8)
    return parser.parse_args()


def main() -> int:
    return validate(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
