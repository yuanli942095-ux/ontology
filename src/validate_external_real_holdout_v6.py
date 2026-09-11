from __future__ import annotations

"""Twelve-gate audit for external-real-holdout-v6-direct-ir-blind."""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import validate_benchmark as benchmark_validator
from build_external_real_holdout_v6 import (
    BENCHMARK_DIR,
    BENCHMARK_NAME,
    ONTOLOGY_NS,
    PROTOCOL_PATH,
    PUBLIC_EVENT_FIELDS,
    PUBLIC_LEAKAGE_TERMS,
    REPAIR_TYPES,
    SAFETY_TYPES,
    XSD_STRING,
    collect_historical_contamination,
    evidence_windows,
    historical_text_collision,
    source_url,
)
from rfc213_direct_repair_ir import benchmark_literal_from_span, source_windows
from run_external_real_v1_symbolic_closure import load_graph
from run_gamma_gold_ir_closure_eval import materialize_update_literal_atomic
from run_gamma_gold_ir_compile_eval import compile_update_literal
from semantic_v2_common import PROJECT_DIR


OUTPUT_DIR = PROJECT_DIR / "output" / BENCHMARK_NAME


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def audit_result(name: str, passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {"audit": name, "passed": passed, **details}


def load_proposed_gold() -> list[dict[str, Any]]:
    human = BENCHMARK_DIR / "private/oracle/gold-repair-ir.jsonl"
    proposed = BENCHMARK_DIR / "private/construction/proposed-gold-repair-ir.jsonl"
    if human.is_file():
        return read_jsonl(human)
    return read_jsonl(proposed)


def audit_structure(events: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if len(events) != int(profile["events"]):
        errors.append(f"event_count={len(events)}")
    for event in events:
        missing = [field for field in PUBLIC_EVENT_FIELDS if field not in event]
        if missing:
            errors.append(f"{event.get('event_id')}:missing={missing}")
        public_text = json.dumps(event, ensure_ascii=False).casefold()
        leaks = [term for term in PUBLIC_LEAKAGE_TERMS if term.casefold() in public_text]
        if leaks:
            errors.append(f"{event.get('event_id')}:public_leak={leaks}")
        excerpt = BENCHMARK_DIR / "public/excerpts" / f"{event['event_id']}-evidence.md"
        mutant = BENCHMARK_DIR / event["source_owl"]
        if not excerpt.is_file():
            errors.append(f"{event['event_id']}:missing_evidence")
        else:
            windows = evidence_windows(excerpt.read_text(encoding="utf-8"))
            if len(windows) != 5:
                errors.append(f"{event['event_id']}:windows={len(windows)}")
            excerpt_text = excerpt.read_text(encoding="utf-8").casefold()
            if any(term in excerpt_text for term in ("gold", "oracle", "正确答案")):
                errors.append(f"{event['event_id']}:evidence_leak")
        if not mutant.is_file():
            errors.append(f"{event['event_id']}:missing_mutant")
    return audit_result("structure_required_fields", not errors, {"errors": errors[:20], "error_count": len(errors)})


def audit_sources(documents: list[dict[str, str]], manifest: list[dict[str, str]]) -> dict[str, Any]:
    errors: list[str] = []
    by_id = {row["source_id"]: row for row in manifest}
    required = ("source_url", "accessed_at_utc", "source_sha256", "document_version", "issuer")
    for row in documents:
        for field in required:
            if not row.get(field):
                errors.append(f"{row.get('event_id')}:missing_{field}")
        registered = by_id.get(row.get("source_id", ""))
        if not registered:
            errors.append(f"{row.get('event_id')}:source_not_in_manifest")
            continue
        if row["source_sha256"] != registered["sha256"]:
            errors.append(f"{row.get('event_id')}:sha_mismatch")
        if row["source_url"] != registered["source_url"]:
            errors.append(f"{row.get('event_id')}:url_mismatch")
        cache = PROJECT_DIR / registered["cache_path"]
        if cache.is_file():
            digest = __import__("hashlib").sha256(cache.read_bytes()).hexdigest()
            if digest != registered["sha256"]:
                errors.append(f"{registered['source_id']}:cache_sha_mismatch")
        else:
            errors.append(f"{registered['source_id']}:cache_missing")
    return audit_result("url_source_sha256", not errors, {"errors": errors[:20], "error_count": len(errors)})


def audit_historical_dedup(events: list[dict[str, Any]], documents: list[dict[str, str]]) -> dict[str, Any]:
    ledger = collect_historical_contamination()
    historical_texts = set(ledger.pop("_window_texts", []))
    historical_hashes = set(ledger["normalized_evidence_sha256"])
    errors: list[str] = []
    for row in documents:
        rfc = int(re.search(r"RFC(\d+)$", row["source_id"]).group(1))
        if rfc in set(ledger["rfc_numbers"]) or source_url(rfc) in set(ledger["urls"]):
            errors.append(f"{row['event_id']}:source_overlap")
        if row["source_id"] in set(ledger["source_ids"]):
            errors.append(f"{row['event_id']}:source_id_overlap")
    for event in events:
        excerpt = BENCHMARK_DIR / "public/excerpts" / f"{event['event_id']}-evidence.md"
        for window in evidence_windows(excerpt.read_text(encoding="utf-8")):
            if historical_text_collision(window, historical_texts, historical_hashes):
                errors.append(f"{event['event_id']}:evidence_overlap")
                break
    return audit_result("historical_text_dedup", not errors, {"errors": errors[:20], "error_count": len(errors)})


def audit_quota(events: list[dict[str, Any]], documents: list[dict[str, str]], profile: dict[str, Any]) -> dict[str, Any]:
    repair = Counter()
    safety = Counter()
    domains: dict[str, set[str]] = defaultdict(set)
    families: Counter[str] = Counter()
    for event in events:
        family = next(row["source_family"] for row in documents if row["event_id"] == event["event_id"])
        domains[event["domain"]].add(family)
        families[family] += 1
        semantic_type = event["semantic_type"]
        if semantic_type in REPAIR_TYPES:
            repair[semantic_type] += 1
        elif semantic_type in SAFETY_TYPES:
            safety[semantic_type] += 1
        else:
            safety[semantic_type] += 1
    max_family = profile.get("max_events_per_source_family")
    if max_family is None:
        max_family = int(profile["events"]) * float(profile.get("max_source_family_fraction", 1))
    errors = []
    if dict(repair) != profile["repair"]:
        errors.append(f"repair={dict(repair)}")
    if dict(safety) != profile["safety"]:
        errors.append(f"safety={dict(safety)}")
    required_domains = int(profile.get("domains", profile.get("domains_minimum", 0)))
    if len(domains) < required_domains:
        errors.append(f"domains={len(domains)}")
    required_families = int(
        profile.get("source_families_per_domain", profile.get("source_families_per_domain_minimum", 0))
    )
    short = {domain: len(items) for domain, items in domains.items() if len(items) < required_families}
    if short:
        errors.append(f"families={short}")
    over = {family: count for family, count in families.items() if count > max_family}
    if over:
        errors.append(f"family_cap={over}")
    return audit_result(
        "source_family_domain_quota",
        not errors,
        {"errors": errors, "domains": len(domains), "families": len(families)},
    )


def audit_window_gold(events: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    gold_rows = load_proposed_gold()
    for row in gold_rows:
        excerpt = (BENCHMARK_DIR / "public/excerpts" / f"{row['event_id']}-evidence.md").read_text(encoding="utf-8")
        windows = dict(source_windows(excerpt))
        if row["decision"] in {"REPAIR", "NO_CHANGE"}:
            marker = str(row.get("gold_source_window") or "").replace("SOURCE_WINDOW_", "")
            if marker not in windows:
                errors.append(f"{row['event_id']}:missing_gold_window")
                continue
            old_lexical = row["target"]["old_value"]["lexical"]
            derived = benchmark_literal_from_span(old_lexical, windows[marker])
            if row["decision"] == "REPAIR":
                expected = row["replacement"]["new_value"]["lexical"]
                if derived != expected:
                    errors.append(f"{row['event_id']}:gold_literal_mismatch")
            elif derived != old_lexical:
                errors.append(f"{row['event_id']}:no_change_literal_mismatch")
    return audit_result("evidence_window_gold", not errors, {"errors": errors[:20], "error_count": len(errors)})


def audit_target_contract(events: list[dict[str, Any]]) -> dict[str, Any]:
    from rdflib import Literal, URIRef

    details: list[dict[str, Any]] = []
    gold_rows = {row["event_id"]: row for row in load_proposed_gold()}
    for event in events:
        gold = gold_rows[event["event_id"]]
        graph = load_graph(BENCHMARK_DIR / event["source_owl"])
        subject = URIRef(gold["target"]["subject_iri"])
        predicate = URIRef(gold["target"]["predicate_iri"])
        old = gold["target"]["old_value"]
        old_term = Literal(old["lexical"], datatype=URIRef(old["datatype"]))
        matches = list(graph.triples((subject, predicate, old_term)))
        sentinel = list(graph.triples((subject, URIRef(f"{ONTOLOGY_NS}regressionSentinel"), None)))
        rebuildable = len(matches) == 1 and len(sentinel) >= 1
        if gold["decision"] == "REPAIR":
            excerpt = (BENCHMARK_DIR / "public/excerpts" / f"{event['event_id']}-evidence.md").read_text(encoding="utf-8")
            marker = str(gold.get("gold_source_window") or "SOURCE_WINDOW_1").replace("SOURCE_WINDOW_", "")
            window = dict(source_windows(excerpt)).get(marker, "")
            derived = benchmark_literal_from_span(old["lexical"], window)
            rebuildable = rebuildable and derived == gold["replacement"]["new_value"]["lexical"]
        details.append({"event_id": event["event_id"], "rebuildable": rebuildable})
    ready = all(row["rebuildable"] for row in details) and len(details) == len(events)
    write_json(
        OUTPUT_DIR / "target-contract-audit" / "v6-target-contract-summary.json",
        {"events": len(details), "derivable": sum(row["rebuildable"] for row in details), "ready": ready},
    )
    return audit_result("target_contract", ready, {"events": len(details), "rebuildable": sum(row["rebuildable"] for row in details)})


def audit_gamma(events: list[dict[str, Any]]) -> dict[str, Any]:
    gold_rows = load_proposed_gold()
    compiled = 0
    closed = 0
    repair_total = 0
    errors: list[str] = []
    for row in gold_rows:
        if row["decision"] != "REPAIR":
            continue
        repair_total += 1
        operation = {
            "operator": "REPLACE_PROPERTY_VALUE",
            "subject_iri": row["target"]["subject_iri"],
            "predicate_iri": row["target"]["predicate_iri"],
            "old_value": row["target"]["old_value"],
            "new_value": row["replacement"]["new_value"],
        }
        compiled_row = compile_update_literal(operation)
        if compiled_row.get("status") != "COMPILED":
            errors.append(f"{row['event_id']}:compile={compiled_row.get('error')}")
            continue
        compiled += 1
        dest = OUTPUT_DIR / "gamma-closure" / f"{row['event_id']}-compiled.owl"
        result = materialize_update_literal_atomic(
            source_path=BENCHMARK_DIR / f"repair-stage/mutants/{row['event_id']}.owl",
            operation=operation,
            dest_path=dest,
        )
        if result.get("status") != "PASS" or result.get("triples_removed") != 1 or result.get("triples_added") != 1:
            errors.append(f"{row['event_id']}:closure={result.get('error') or result.get('status')}")
            continue
        closed += 1
    passed = repair_total > 0 and compiled == repair_total and closed == repair_total
    return audit_result(
        "gamma_compile_closure",
        passed,
        {"repair_events": repair_total, "compiled": compiled, "closed": closed, "errors": errors[:20]},
    )


def audit_candidate_leakage(events: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    public_root = BENCHMARK_DIR / "public"
    for path in public_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".json", ".jsonl", ".md"}:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if re.search(r"\bCAND_00[123]\b", text):
            errors.append(f"{path.name}:candidate_id")
        lowered = text.casefold()
        if "oracle" in lowered or "正确答案" in lowered:
            errors.append(f"{path.name}:oracle_term")
    notes = read_csv(BENCHMARK_DIR / "repair-stage/candidates/external-real-candidate-template.csv")
    for row in notes:
        if re.search(r"(?i)gold|oracle|correct|正确答案", row.get("notes", "")):
            errors.append(f"{row['event_id']}:{row['candidate_id']}:note_leak")
    return audit_result("candidate_leakage", not errors, {"errors": errors[:20], "error_count": len(errors)})


def audit_gold_id_distribution(profile: dict[str, Any]) -> dict[str, Any]:
    gold_rows = [row for row in load_proposed_gold() if row["decision"] == "REPAIR"]
    counts = Counter(row.get("proposed_baseline_candidate_id") for row in gold_rows)
    expected = profile["candidate_gold_id_counts"]
    passed = dict(counts) == expected
    return audit_result("gold_id_distribution", passed, {"observed": dict(counts), "expected": expected})


def audit_annotation() -> dict[str, Any]:
    summary_path = BENCHMARK_DIR / "private/annotation/agreement-summary.json"
    adjudication_path = BENCHMARK_DIR / "private/annotation/adjudication.csv"
    gold_path = BENCHMARK_DIR / "private/oracle/gold-repair-ir.jsonl"
    if not summary_path.is_file():
        return audit_result(
            "dual_annotation",
            False,
            {
                "status": "DRAFT_AWAITING_DUAL_ANNOTATION",
                "automatic_approval": False,
                "reason": "completed dual annotation artifacts are absent",
            },
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("annotator_a_id") == summary.get("annotator_b_id"):
        return audit_result("dual_annotation", False, {"reason": "same_annotator"})
    if "人工复核通过" in json.dumps(summary, ensure_ascii=False):
        return audit_result("dual_annotation", False, {"reason": "automatic_approval_language"})
    disagreements = int(summary.get("disagreement_field_count", 0))
    adjudicated = 0
    if adjudication_path.is_file():
        adjudicated = sum(1 for row in read_csv(adjudication_path) if row.get("adjudicated_value"))
    passed = bool(gold_path.is_file()) and (disagreements == 0 or adjudicated == disagreements)
    return audit_result(
        "dual_annotation",
        passed,
        {
            "raw_decision_agreement": summary.get("field_agreement", {}).get("decision", {}),
            "decision_cohen_kappa": summary.get("decision_cohen_kappa"),
            "semantic_type_cohen_kappa": summary.get("semantic_type_cohen_kappa"),
            "per_semantic_type_kappa": summary.get("per_semantic_type_kappa"),
            "disagreement_field_count": disagreements,
            "adjudicated_field_count": adjudicated,
            "human_gold_imported": gold_path.is_file(),
            "automatic_approval": False,
        },
    )


def audit_reasoner_cq(events: list[dict[str, Any]]) -> dict[str, Any]:
    from rdflib import Literal, URIRef

    gold_rows = {row["event_id"]: row for row in load_proposed_gold()}
    errors: list[str] = []
    passed_events = 0
    for event in events:
        mutant = BENCHMARK_DIR / event["source_owl"]
        reasoner = benchmark_validator.run_reasoner(mutant, 60)
        if reasoner.get("status") != "CONSISTENT":
            errors.append(f"{event['event_id']}:mutant_reasoner={reasoner.get('status')}")
            continue
        graph = load_graph(mutant)
        gold = gold_rows[event["event_id"]]
        subject = URIRef(gold["target"]["subject_iri"])
        predicate = URIRef(gold["target"]["predicate_iri"])
        old = gold["target"]["old_value"]
        if (subject, predicate, Literal(old["lexical"], datatype=URIRef(old["datatype"]))) not in graph:
            errors.append(f"{event['event_id']}:target_cq_old_missing")
            continue
        if (subject, URIRef(f"{ONTOLOGY_NS}regressionSentinel"), Literal("preserve", datatype=URIRef(XSD_STRING))) not in graph:
            errors.append(f"{event['event_id']}:non_target_cq_missing")
            continue
        if gold["decision"] == "REPAIR":
            after = BENCHMARK_DIR / "private/ontology-after" / f"{event['event_id']}-gold.owl"
            if not after.is_file():
                after = BENCHMARK_DIR / "private/ontology-after" / f"{event['event_id']}-proposed-gold.owl"
            if not after.is_file():
                errors.append(f"{event['event_id']}:missing_gold_owl")
                continue
            after_reasoner = benchmark_validator.run_reasoner(after, 60)
            if after_reasoner.get("status") != "CONSISTENT":
                errors.append(f"{event['event_id']}:gold_reasoner={after_reasoner.get('status')}")
                continue
            after_graph = load_graph(after)
            new = gold["replacement"]["new_value"]
            if (subject, predicate, Literal(new["lexical"], datatype=URIRef(new["datatype"]))) not in after_graph:
                errors.append(f"{event['event_id']}:target_cq_new_missing")
                continue
            if (subject, URIRef(f"{ONTOLOGY_NS}regressionSentinel"), Literal("preserve", datatype=URIRef(XSD_STRING))) not in after_graph:
                errors.append(f"{event['event_id']}:non_target_not_preserved")
                continue
        passed_events += 1
    return audit_result(
        "reasoner_cq",
        passed_events == len(events),
        {"passed_events": passed_events, "events": len(events), "errors": errors[:20]},
    )


def audit_freeze_manifest() -> dict[str, Any]:
    path = OUTPUT_DIR / "external-real-holdout-v6-direct-ir-blind-freeze-manifest.json"
    if not path.is_file():
        return audit_result(
            "freeze_manifest",
            False,
            {"status": "ABSENT", "reason": "benchmark is not frozen; dual annotation remains open"},
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    missing = [row["path"] for row in files if not (PROJECT_DIR / row["path"]).is_file()]
    return audit_result("freeze_manifest", not missing and bool(files), {"files": len(files), "missing": missing[:10]})


def validate(profile_name: str) -> dict[str, Any]:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    profile = protocol["profiles"][profile_name]
    events = read_jsonl(BENCHMARK_DIR / "public/events/events.jsonl")
    documents = read_csv(BENCHMARK_DIR / "public/documents/external-real-document-template.csv")
    manifest = read_csv(BENCHMARK_DIR / "public/retrieval/source-cache-manifest.csv")
    audits = [
        audit_structure(events, profile),
        audit_sources(documents, manifest),
        audit_historical_dedup(events, documents),
        audit_quota(events, documents, profile),
        audit_window_gold(events),
        audit_target_contract(events),
        audit_gamma(events),
        audit_candidate_leakage(events),
        audit_gold_id_distribution(profile),
        audit_annotation(),
        audit_reasoner_cq(events),
        audit_freeze_manifest(),
    ]
    hard = [row for row in audits if row["audit"] != "freeze_manifest"]
    machine_ready = all(row["passed"] for row in hard if row["audit"] != "dual_annotation")
    freeze_ready = all(row["passed"] for row in hard)
    report = {
        "benchmark": BENCHMARK_NAME,
        "profile": profile_name,
        "status": "FREEZE_READY" if freeze_ready else "DRAFT_AWAITING_DUAL_ANNOTATION",
        "machine_audits_ready": machine_ready,
        "freeze_ready": freeze_ready,
        "automatic_human_approval": False,
        "audits": audits,
    }
    write_json(OUTPUT_DIR / "audits" / f"{profile_name}-audit-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("pilot", "full"), default="pilot")
    args = parser.parse_args()
    report = validate(args.profile)
    print(json.dumps({k: report[k] for k in report if k != "audits"}, ensure_ascii=False, indent=2))
    for audit in report["audits"]:
        mark = "PASS" if audit["passed"] else "FAIL"
        print(f"{mark} {audit['audit']}")
    return 0 if report["freeze_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
