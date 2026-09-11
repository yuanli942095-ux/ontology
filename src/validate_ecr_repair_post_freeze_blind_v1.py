from __future__ import annotations

"""Quality and proposed-Gold closure checks. Official freeze is a separate script."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ecr_repair_post_freeze_blind_v1_common import (
    AS_OF,
    BENCHMARK_DIR,
    PROJECT_DIR,
    DOMAINS,
    MAX_EVENTS_PER_DOCUMENT,
    MAX_EVENTS_PER_FAMILY,
    MAX_IETF_EVENTS,
    MAX_WINDOW_WORDS,
    MIN_WINDOW_WORDS,
    FULL_PARTITION_COUNTS,
    TRANCHE1_PARTITION_COUNTS,
    XSD_STRING,
    evidence_windows,
    is_ietf_family,
    read_csv,
    read_json,
    read_jsonl,
    rfc_number_from_url,
    sha256_file,
    word_count,
    write_json,
)
from rfc213_direct_repair_ir import ontology_literal_assertions
from run_gamma_gold_ir_closure_eval import materialize_update_literal_atomic
from run_gamma_gold_ir_compile_eval import compile_update_literal
from scan_ecr_repair_post_freeze_blind_v1_leakage import scan as scan_leakage


def _audit(name: str, passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {"audit": name, "passed": passed, **details}


def audit_structure(events: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    expected_events = 80 if len(events) > 16 else 16
    expected_per_domain = 10 if expected_events == 80 else 2
    if len(events) != expected_events:
        errors.append(f"event_count={len(events)}")
    domains = {event["domain"] for event in events}
    if domains != set(DOMAINS):
        errors.append(f"domains={sorted(domains)}")
    for domain in DOMAINS:
        count = sum(1 for event in events if event["domain"] == domain)
        if count != expected_per_domain:
            errors.append(f"{domain}={count}")
        docs = {doc for event in events if event["domain"] == domain for doc in event["document_ids"]}
        if len(docs) < 2:
            errors.append(f"{domain}:docs={sorted(docs)}")
    for event in events:
        if event.get("as_of") != AS_OF:
            errors.append(f"{event['event_id']}:as_of")
        if event.get("status") != "PROPOSED_NOT_FROZEN":
            errors.append(f"{event['event_id']}:status")
        if "partition" in event:
            errors.append(f"{event['event_id']}:public_partition")
        excerpt = BENCHMARK_DIR / event["evidence_file"]
        mutant = BENCHMARK_DIR / event["source_owl"]
        if not excerpt.is_file():
            errors.append(f"{event['event_id']}:missing_evidence")
        else:
            windows = evidence_windows(excerpt.read_text(encoding="utf-8"))
            if not 3 <= len(windows) <= 7:
                errors.append(f"{event['event_id']}:windows={len(windows)}")
            for window_id, text in windows:
                words = word_count(text)
                if words < MIN_WINDOW_WORDS or words > MAX_WINDOW_WORDS:
                    errors.append(f"{event['event_id']}:{window_id}:words={words}")
        if not mutant.is_file():
            errors.append(f"{event['event_id']}:missing_mutant")
    return _audit("structure", not errors, {"errors": errors[:30], "error_count": len(errors)})


def audit_quotas(events: list[dict[str, Any]], gold: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    partitions = Counter(row["partition"] for row in gold)
    expected = FULL_PARTITION_COUNTS if len(events) == 80 else TRANCHE1_PARTITION_COUNTS
    if dict(partitions) != expected:
        errors.append(f"partitions={dict(partitions)}")
    families = Counter(event["source_family"] for event in events)
    if any(count > MAX_EVENTS_PER_FAMILY for count in families.values()):
        errors.append(f"families={dict(families)}")
    docs = Counter(doc for event in events for doc in event["document_ids"])
    if any(count > MAX_EVENTS_PER_DOCUMENT for count in docs.values()):
        errors.append(f"documents={dict(docs)}")
    ietf = sum(1 for event in events if is_ietf_family(event["source_family"]))
    if ietf > MAX_IETF_EVENTS:
        errors.append(f"ietf={ietf}")
    return _audit("quotas", not errors, {"errors": errors, "families": dict(families), "ietf": ietf})


def audit_sources() -> dict[str, Any]:
    errors: list[str] = []
    exclusions = read_json(BENCHMARK_DIR / "private/construction/used-source-exclusions.json")
    manifest = read_csv(BENCHMARK_DIR / "source-manifest.csv")
    for row in manifest:
        cache = PROJECT_DIR / row["source_file"]
        if not cache.is_file():
            errors.append(f"{row['document_id']}:cache_missing")
            continue
        digest = sha256_file(cache)
        if digest != row["source_sha256"]:
            errors.append(f"{row['document_id']}:sha_mismatch")
        if digest in set(exclusions["sha256"]):
            errors.append(f"{row['document_id']}:sha_excluded")
        if row["official_url"] in set(exclusions["urls"]):
            errors.append(f"{row['document_id']}:url_excluded")
        rfc = rfc_number_from_url(row["official_url"])
        if rfc is not None and rfc in {int(item) for item in exclusions["rfc_numbers"]}:
            errors.append(f"{row['document_id']}:rfc_excluded:{rfc}")
        if not row.get("line_start"):
            pass
    locators = read_csv(BENCHMARK_DIR / "private/construction/window-locators.csv")
    missing_lines = [row["event_id"] for row in locators if not row.get("line_start") or not row.get("line_end")]
    if missing_lines:
        errors.append(f"missing_line_numbers={missing_lines[:8]}")
    return _audit("sources", not errors, {"errors": errors[:20], "error_count": len(errors)})


def audit_gold_closure(events: list[dict[str, Any]], gold: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    gold_by_id = {row["event_id"]: row for row in gold}
    for event in events:
        row = gold_by_id[event["event_id"]]
        mutant = BENCHMARK_DIR / event["source_owl"]
        source_sha = sha256_file(mutant)
        assertions = ontology_literal_assertions(mutant)
        target_pred = row["target"]["predicate_iri"]
        matches = [item for item in assertions if item["predicate_iri"] == target_pred]
        if len(matches) != 1:
            errors.append(f"{event['event_id']}:target_not_unique")
            continue
        if matches[0]["lexical"] != row["target"]["old_value"]["lexical"]:
            errors.append(f"{event['event_id']}:old_mismatch")
        if row["partition"] != "REPAIR":
            if sha256_file(mutant) != source_sha:
                errors.append(f"{event['event_id']}:source_changed")
            continue
        replacement = row.get("replacement") or {}
        new_value = replacement.get("new_value") or {}
        operation = {
            "operator": "REPLACE_PROPERTY_VALUE",
            "subject_iri": row["target"]["subject_iri"],
            "predicate_iri": target_pred,
            "old_value": row["target"]["old_value"],
            "new_value": new_value,
        }
        compiled = compile_update_literal(operation)
        if compiled.get("status") != "COMPILED":
            errors.append(f"{event['event_id']}:compile={compiled}")
            continue
        dest = BENCHMARK_DIR / "private/construction/proposed-gold-repaired-owl" / f"{event['event_id']}-closure.owl"
        result = materialize_update_literal_atomic(
            source_path=mutant,
            operation=operation,
            dest_path=dest,
            expected_source_sha256=source_sha,
        )
        if result.get("status") != "PASS":
            errors.append(f"{event['event_id']}:closure={result}")
        if sha256_file(mutant) != source_sha:
            errors.append(f"{event['event_id']}:source_mutated")
        if new_value.get("datatype") != XSD_STRING:
            errors.append(f"{event['event_id']}:datatype")
    return _audit("proposed_gold_closure", not errors, {"errors": errors[:20], "error_count": len(errors)})


def audit_oracle_absent() -> dict[str, Any]:
    files = [
        path.name
        for path in (BENCHMARK_DIR / "private/oracle").glob("*")
        if path.is_file() and path.name.lower() != "readme.md"
    ]
    gold_official = [
        path.name
        for path in (BENCHMARK_DIR / "private/gold-repaired-owl").glob("*")
        if path.is_file() and path.name.lower() != "readme.md"
    ]
    return _audit("oracle_absent", not files and not gold_official, {"oracle_files": files, "official_repaired": gold_official})


def validate() -> dict[str, Any]:
    events = read_jsonl(BENCHMARK_DIR / "public/events/events.jsonl")
    gold = read_jsonl(BENCHMARK_DIR / "private/construction/proposed-gold.jsonl")
    audits = [
        audit_structure(events),
        audit_quotas(events, gold),
        audit_sources(),
        audit_gold_closure(events, gold),
        audit_oracle_absent(),
        _audit("leakage", scan_leakage()["PUBLIC_GOLD_LEAKAGE"] == "PASS", {"scan": "see leakage-scan.json"}),
    ]
    passed = all(item["passed"] for item in audits)
    report = {
        "status": "PASS" if passed else "FAIL",
        "freeze_ready": False,
        "reason": "Dual annotation, adjudication, and official Oracle import are incomplete.",
        "audits": audits,
    }
    write_json(BENCHMARK_DIR / "private/construction/validation-report.json", report)
    return report


def main() -> int:
    report = validate()
    print(json.dumps({"status": report["status"], "freeze_ready": report["freeze_ready"]}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
