from __future__ import annotations

"""Build and freeze the RFC-213 confirmatory core from the frozen RFC-264 parent.

The selection logic deliberately reads benchmark construction artifacts only. It
does not inspect model predictions, scores, or experiment outputs.
"""

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_DIR = Path(__file__).resolve().parents[1]
PARENT_NAME = "external-real-holdout-v5-blind-large"
CORE_NAME = "rfc-213-confirmatory-core"
PARENT_DIR = PROJECT_DIR / "benchmark" / PARENT_NAME
CORE_DIR = PROJECT_DIR / "benchmark" / CORE_NAME
OUTPUT_DIR = PROJECT_DIR / "output" / CORE_NAME
PARENT_MANIFEST = (
    PROJECT_DIR
    / "output"
    / PARENT_NAME
    / "external-real-holdout-v5-blind-large-freeze-manifest.json"
)
EXPECTED_PARENT_MANIFEST_SHA256 = (
    "40fcc2c3956c66dc70b3aa176270f7a39708ff44491b9575b39e797c9bb0e038"
)
PROTOCOL_FROZEN_AT = "2026-09-08T09:29:00+00:00"
REVIEWED_AT = "2026-09-08"
TARGET_EVENTS = 213
TARGET_CANDIDATES = 639
SEMANTIC_TYPES = (
    "CROSS_SENTENCE_SCOPE",
    "GENERAL_RULE_EXCEPTION",
    "TEMPORAL_VERSION",
)

EVENT_REL = Path("public/events/external-real-event-template.csv")
DOCUMENT_REL = Path("public/documents/external-real-document-template.csv")
CANDIDATE_REL = Path("repair-stage/candidates/external-real-candidate-template.csv")
ORACLE_REL = Path("private/oracle/external-real-oracle-template.csv")
RETRIEVAL_REL = Path("public/retrieval/external-real-v8-event-retrieval.csv")
SUPPORT_REL = Path("public/retrieval/support-adjudication.csv")
SOURCE_CACHE_REL = Path("public/retrieval/source-cache-manifest.csv")
SOURCE_FAMILY_REL = Path("public/retrieval/external-real-v8-grounded-source-families.csv")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(
    path: Path,
    rows: Iterable[dict[str, Any]],
    fieldnames: Iterable[str] | None = None,
) -> None:
    materialized = list(rows)
    fields = list(fieldnames or ())
    if not fields:
        for row in materialized:
            for key in row:
                if key not in fields:
                    fields.append(key)
    if not fields:
        raise ValueError(f"fieldnames required for empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(materialized)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def relpath(path: Path) -> str:
    return str(path.resolve().relative_to(PROJECT_DIR.resolve())).replace("\\", "/")


def verify_parent() -> dict[str, Any]:
    if not PARENT_DIR.is_dir():
        raise FileNotFoundError(PARENT_DIR)
    manifest = json.loads(PARENT_MANIFEST.read_text(encoding="utf-8"))
    actual = manifest.get("manifest_sha256")
    if actual != EXPECTED_PARENT_MANIFEST_SHA256:
        raise RuntimeError(
            f"parent manifest mismatch: expected {EXPECTED_PARENT_MANIFEST_SHA256}, got {actual}"
        )
    required = (
        EVENT_REL,
        DOCUMENT_REL,
        CANDIDATE_REL,
        ORACLE_REL,
        RETRIEVAL_REL,
        SUPPORT_REL,
        SOURCE_CACHE_REL,
        SOURCE_FAMILY_REL,
    )
    missing = [str(path) for path in required if not (PARENT_DIR / path).is_file()]
    if missing:
        raise RuntimeError(f"parent artifacts missing: {missing}")
    return manifest


def exclusion_protocol() -> dict[str, Any]:
    return {
        "protocol_version": "rfc264-to-rfc213-exclusion-v1",
        "frozen_at": PROTOCOL_FROZEN_AT,
        "parent_benchmark": PARENT_NAME,
        "parent_manifest_sha256": EXPECTED_PARENT_MANIFEST_SHA256,
        "target_benchmark": CORE_NAME,
        "target_event_count": TARGET_EVENTS,
        "model_result_access": "PROHIBITED",
        "decision_timing_definition": (
            "decided_before_model_eval means before RFC-213 confirmatory evaluation; "
            "it does not claim to predate historical evaluations of the RFC-264 parent"
        ),
        "ordered_exclusion_rules": [
            {
                "code": "E07_BAD_PROVENANCE_OR_HASH",
                "criterion": "source URL or required document/window hashes are missing",
                "action": "EXCLUDE",
            },
            {
                "code": "E01_LOW_EVIDENCE_SPECIFICITY",
                "criterion": "the reviewed oracle quote is absent from SOURCE_WINDOW_1",
                "action": "EXCLUDE",
            },
            {
                "code": "E02_MULTI_CHANGE_ENTANGLED",
                "criterion": (
                    "parent semantic review identifies multiple independent normative "
                    "changes without a unique event-level answer"
                ),
                "action": "EXCLUDE",
            },
            {
                "code": "E03_UNSTABLE_CANDIDATE_SPACE",
                "criterion": (
                    "the three candidates do not share one operator, subject, predicate, "
                    "and old value"
                ),
                "action": "EXCLUDE",
            },
            {
                "code": "E04_ORACLE_REVIEW_WEAK",
                "criterion": (
                    "oracle is not AGREED/READY, lacks a reviewed quote, or lacks a "
                    "documented review note"
                ),
                "action": "EXCLUDE",
            },
            {
                "code": "E05_TEMPLATE_NEAR_DUPLICATE",
                "criterion": (
                    "deterministic domain/type quota reduces repeated templates while "
                    "preserving every active source_id"
                ),
                "action": "EXCLUDE_TO_TARGET_SIZE",
            },
            {
                "code": "E06_UNSUPPORTED_GAMMA_CONSTRUCT",
                "criterion": "gold repair is outside the frozen Gamma fragment",
                "action": "DO_NOT_EXCLUDE; COUNT_IN_COVERAGE_DENOMINATOR",
            },
        ],
        "e05_policy": {
            "domain_quota": (
                "sorted domains receive 20 events for the first four domains and 19 "
                "events for the remaining seven domains"
            ),
            "semantic_type_quota": (
                "a deterministic lexicographic constraint solver assigns per-domain "
                "type counts of (7,7,6) for quota 20 or (7,6,6) for quota 19 so that "
                "each of the three semantic types totals 71"
            ),
            "within_domain_type_order": (
                "round-robin over sorted active source_id, then ascending event_id"
            ),
            "expected_balance": {
                "events": 213,
                "candidates": 639,
                "semantic_type_each": 71,
                "domain_min": 19,
                "domain_max": 20,
            },
        },
        "candidate_id_policy": {
            "purpose": "remove the parent benchmark's constant CAND_002 gold-position shortcut",
            "gold_slot_assignment": (
                "ascending included event_id cycles through CAND_001, CAND_002, "
                "CAND_003, yielding exactly 71 gold events per slot"
            ),
            "non_gold_assignment": (
                "the two remaining IDs are ordered by SHA-256 of parent manifest "
                "and event_id"
            ),
            "model_result_access": "PROHIBITED",
        },
        "forbidden_inputs": [
            "model predictions",
            "model success/failure labels",
            "experiment accuracy",
            "ranking scores produced by evaluated methods",
        ],
    }


def first_window(evidence: str) -> str:
    match = re.search(
        r"(?s)\[SOURCE_WINDOW_1\]\s*(.*?)(?:\n\n\[SOURCE_WINDOW_2\]|\Z)",
        evidence,
    )
    return match.group(1).strip() if match else ""


def parsed_operation(candidate: dict[str, str]) -> dict[str, Any]:
    operation = json.loads(candidate["operation_json"])
    if not isinstance(operation, dict):
        raise ValueError("operation_json must be an object")
    return operation


def protocol_audit_rows(
    events: list[dict[str, str]],
    documents: list[dict[str, str]],
    candidates: list[dict[str, str]],
    oracles: list[dict[str, str]],
) -> list[dict[str, str]]:
    docs = {row["event_id"]: row for row in documents}
    oracle_by_event = {row["event_id"]: row for row in oracles}
    candidates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidates:
        candidates_by_event[row["event_id"]].append(row)

    rows: list[dict[str, str]] = []
    for event in sorted(events, key=lambda row: row["event_id"]):
        event_id = event["event_id"]
        document = docs.get(event_id, {})
        oracle = oracle_by_event.get(event_id, {})
        event_candidates = candidates_by_event.get(event_id, [])
        evidence_path = PARENT_DIR / "public" / "excerpts" / f"{event_id}-evidence.md"
        evidence = (
            evidence_path.read_text(encoding="utf-8", errors="replace")
            if evidence_path.is_file()
            else ""
        )
        window1 = first_window(evidence)

        e07 = all(
            document.get(field, "").strip()
            for field in ("source_url", "raw_sha256", "text_sha256", "window_sha256", "sha256")
        )
        spans: list[dict[str, Any]] = []
        try:
            parsed_spans = json.loads(oracle.get("evidence_spans_json", "[]") or "[]")
            if isinstance(parsed_spans, list):
                spans = [item for item in parsed_spans if isinstance(item, dict)]
        except json.JSONDecodeError:
            spans = []
        quote = str(spans[0].get("quote", "")) if spans else ""
        e01 = bool(quote and quote in window1)
        e02 = (
            event.get("support_status") == "SEMANTIC_REVIEW_PASSED"
            and event.get("semantic_support") == "PASS"
            and len(spans) == 1
        )

        operations: list[dict[str, Any]] = []
        operation_error = ""
        try:
            operations = [parsed_operation(row) for row in event_candidates]
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            operation_error = str(exc)
        comparable_keys = (
            "operator",
            "subject_iri",
            "predicate_iri",
        )
        e03 = (
            len(event_candidates) == 3
            and not operation_error
            and all(
                len({json.dumps(op.get(key), sort_keys=True) for op in operations}) == 1
                for key in comparable_keys
            )
            and len(
                {
                    json.dumps(op.get("old_value"), sort_keys=True)
                    for op in operations
                }
            )
            == 1
        )
        e04 = (
            oracle.get("agreement_status") == "AGREED"
            and oracle.get("status") == "READY"
            and bool(quote)
            and bool(oracle.get("notes", "").strip())
            and oracle.get("oracle_candidate_id")
            in {row.get("candidate_id") for row in event_candidates}
        )

        failed = [
            code
            for code, passed in (
                ("E07_BAD_PROVENANCE_OR_HASH", e07),
                ("E01_LOW_EVIDENCE_SPECIFICITY", e01),
                ("E02_MULTI_CHANGE_ENTANGLED", e02),
                ("E03_UNSTABLE_CANDIDATE_SPACE", e03),
                ("E04_ORACLE_REVIEW_WEAK", e04),
            )
            if not passed
        ]
        rows.append(
            {
                "event_id": event_id,
                "E07_BAD_PROVENANCE_OR_HASH": "PASS" if e07 else "FAIL",
                "E01_LOW_EVIDENCE_SPECIFICITY": "PASS" if e01 else "FAIL",
                "E02_MULTI_CHANGE_ENTANGLED": "PASS" if e02 else "FAIL",
                "E03_UNSTABLE_CANDIDATE_SPACE": "PASS" if e03 else "FAIL",
                "E04_ORACLE_REVIEW_WEAK": "PASS" if e04 else "FAIL",
                "first_exclusion_code": failed[0] if failed else "",
                "audit_basis": (
                    "parent benchmark construction artifacts and pre-existing manual "
                    "review fields only; no model evaluation result read"
                ),
            }
        )
    return rows


def type_quota_by_domain(
    domain_quotas: dict[str, int],
) -> dict[str, dict[str, int]]:
    domains = sorted(domain_quotas)
    options: dict[int, list[tuple[int, int, int]]] = {
        19: [(7, 6, 6), (6, 7, 6), (6, 6, 7)],
        20: [(7, 7, 6), (7, 6, 7), (6, 7, 7)],
    }

    def search(
        index: int,
        totals: tuple[int, int, int],
        chosen: list[tuple[int, int, int]],
    ) -> list[tuple[int, int, int]] | None:
        if index == len(domains):
            return chosen if totals == (71, 71, 71) else None
        remaining = len(domains) - index - 1
        quota = domain_quotas[domains[index]]
        if quota not in options:
            raise ValueError(
                f"unsupported domain quota {quota} for domain {domains[index]}"
            )
        for option in options[quota]:
            next_totals = (
                totals[0] + option[0],
                totals[1] + option[1],
                totals[2] + option[2],
            )
            if any(value > 71 for value in next_totals):
                continue
            if any(value + 7 * remaining < 71 for value in next_totals):
                continue
            result = search(index + 1, next_totals, [*chosen, option])
            if result is not None:
                return result
        return None

    assignment = search(0, (0, 0, 0), [])
    if assignment is None:
        raise RuntimeError("unable to satisfy frozen semantic-type quotas")
    return {
        domain: dict(zip(SEMANTIC_TYPES, counts))
        for domain, counts in zip(domains, assignment)
    }


def round_robin_select(
    rows: list[dict[str, str]],
    count: int,
    source_by_event: dict[str, str],
) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[source_by_event[row["event_id"]]].append(row)
    for source_rows in grouped.values():
        source_rows.sort(key=lambda row: row["event_id"])
    selected: list[dict[str, str]] = []
    offsets = {source: 0 for source in grouped}
    sources = sorted(grouped)
    while len(selected) < count:
        made_progress = False
        for source in sources:
            offset = offsets[source]
            if offset >= len(grouped[source]):
                continue
            selected.append(grouped[source][offset])
            offsets[source] += 1
            made_progress = True
            if len(selected) == count:
                break
        if not made_progress:
            raise RuntimeError(f"cannot select {count} rows from {len(rows)}")
    return selected


def select_event_ids(
    events: list[dict[str, str]],
    retrievals: list[dict[str, str]],
    audit_rows: list[dict[str, str]],
) -> tuple[set[str], dict[str, int], dict[str, dict[str, int]], dict[str, int]]:
    failed = [row for row in audit_rows if row["first_exclusion_code"]]
    if failed:
        raise RuntimeError(
            "frozen parent unexpectedly fails quality protocol; protocol amendment "
            f"required before selection: {[(r['event_id'], r['first_exclusion_code']) for r in failed[:10]]}"
        )
    source_by_event = {row["event_id"]: row["source_id"] for row in retrievals}
    missing_sources = sorted(
        row["event_id"] for row in events if row["event_id"] not in source_by_event
    )
    if missing_sources:
        raise RuntimeError(
            f"retrieval source_id missing for events: {missing_sources[:10]}"
        )
    domains = sorted({row["domain"] for row in events})
    if len(domains) != 11:
        raise RuntimeError(f"expected 11 domains, found {len(domains)}")
    domain_quotas = {
        domain: (20 if index < 4 else 19)
        for index, domain in enumerate(domains)
    }
    type_quotas = type_quota_by_domain(domain_quotas)

    selected: list[dict[str, str]] = []
    for domain in domains:
        domain_rows = [row for row in events if row["domain"] == domain]
        for semantic_type in SEMANTIC_TYPES:
            eligible = [
                row for row in domain_rows if row["semantic_type"] == semantic_type
            ]
            selected.extend(
                round_robin_select(
                    eligible,
                    type_quotas[domain][semantic_type],
                    source_by_event,
                )
            )
    selected_ids = {row["event_id"] for row in selected}
    if len(selected_ids) != TARGET_EVENTS:
        raise RuntimeError(f"selection produced {len(selected_ids)} events")
    type_counts = Counter(
        row["semantic_type"] for row in events if row["event_id"] in selected_ids
    )
    if any(type_counts[semantic_type] != 71 for semantic_type in SEMANTIC_TYPES):
        raise RuntimeError(f"semantic type imbalance: {type_counts}")
    source_counts = Counter(
        source_by_event[row["event_id"]]
        for row in events
        if row["event_id"] in selected_ids
    )
    if set(source_counts) != set(source_by_event.values()):
        raise RuntimeError("selection dropped an active source_id")
    return selected_ids, domain_quotas, type_quotas, dict(source_counts)


def candidate_id_mapping(
    event_id: str,
    gold_target_id: str | None = None,
) -> dict[str, str]:
    candidate_ids = ["CAND_001", "CAND_002", "CAND_003"]
    digest = hashlib.sha256(
        f"{EXPECTED_PARENT_MANIFEST_SHA256}:{event_id}:candidate-id-v1".encode("utf-8")
    ).digest()
    target = (
        candidate_ids[digest[0] % 3]
        if gold_target_id is None
        else gold_target_id
    )
    if target not in candidate_ids:
        raise ValueError(f"invalid gold target candidate ID: {target}")
    remaining_new = [item for item in candidate_ids if item != target]
    if digest[1] % 2:
        remaining_new.reverse()
    return {
        "CAND_002": target,
        "CAND_001": remaining_new[0],
        "CAND_003": remaining_new[1],
    }


def parse_rfc_version(url: str) -> str:
    match = re.search(r"/rfc/rfc(\d+)\.txt", url, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"cannot derive RFC target version from URL: {url}")
    return f"RFC{match.group(1)}"


def create_gold_ontology(
    source: Path,
    destination: Path,
    operation: dict[str, Any],
) -> None:
    old_value = str(operation.get("old_value", {}).get("lexical", ""))
    new_value = str(operation.get("new_value", {}).get("lexical", ""))
    if not old_value or not new_value or old_value == new_value:
        raise RuntimeError(f"invalid gold literal replacement for {source.name}")
    text = source.read_text(encoding="utf-8")
    if text.count(old_value) != 1:
        raise RuntimeError(
            f"expected one old literal in {source.name}, found {text.count(old_value)}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text.replace(old_value, new_value, 1), encoding="utf-8")


def build_core(force: bool = False) -> dict[str, Any]:
    verify_parent()
    if CORE_DIR.exists():
        if not force:
            raise FileExistsError(f"{CORE_DIR} exists; pass --force to rebuild")
        shutil.rmtree(CORE_DIR)
    if OUTPUT_DIR.exists():
        if not force:
            raise FileExistsError(f"{OUTPUT_DIR} exists; pass --force to rebuild")
        shutil.rmtree(OUTPUT_DIR)
    CORE_DIR.mkdir(parents=True)
    OUTPUT_DIR.mkdir(parents=True)

    protocol = exclusion_protocol()
    protocol_path = OUTPUT_DIR / "rfc213-exclusion-protocol.json"
    write_json(protocol_path, protocol)
    protocol_sha = sha256_file(protocol_path)

    events = read_csv(PARENT_DIR / EVENT_REL)
    documents = read_csv(PARENT_DIR / DOCUMENT_REL)
    candidates = read_csv(PARENT_DIR / CANDIDATE_REL)
    oracles = read_csv(PARENT_DIR / ORACLE_REL)
    retrievals = read_csv(PARENT_DIR / RETRIEVAL_REL)
    loaded = {
        "events": events,
        "documents": documents,
        "candidates": candidates,
        "oracles": oracles,
        "retrievals": retrievals,
    }
    empty = [name for name, rows in loaded.items() if not rows]
    if empty:
        raise RuntimeError(f"parent CSVs contain no data rows: {empty}")
    audit_rows = protocol_audit_rows(events, documents, candidates, oracles)
    write_csv(OUTPUT_DIR / "rfc264-protocol-audit.csv", audit_rows)

    selected_ids, domain_quotas, type_quotas, source_counts = select_event_ids(
        events,
        retrievals,
        audit_rows,
    )
    retrieval_by_event = {row["event_id"]: row for row in retrievals}
    document_by_event = {row["event_id"]: row for row in documents}
    candidate_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidates:
        candidate_by_event[row["event_id"]].append(row)
    oracle_by_event = {row["event_id"]: row for row in oracles}

    lineage_rows: list[dict[str, str]] = []
    for event in sorted(events, key=lambda row: row["event_id"]):
        event_id = event["event_id"]
        included = event_id in selected_ids
        source_id = retrieval_by_event[event_id]["source_id"]
        lineage_rows.append(
            {
                "event_id": event_id,
                "parent_benchmark": PARENT_NAME,
                "parent_manifest_sha256": EXPECTED_PARENT_MANIFEST_SHA256,
                "included_in_rfc213": "TRUE" if included else "FALSE",
                "exclusion_code": "" if included else "E05_TEMPLATE_NEAR_DUPLICATE",
                "exclusion_reason": (
                    ""
                    if included
                    else (
                        "Excluded by frozen deterministic domain/semantic-type quota "
                        f"to reduce repeated templates; domain={event['domain']}; "
                        f"source_id={source_id}; semantic_type={event['semantic_type']}; "
                        "tie_break=round_robin_source_then_event_id"
                    )
                ),
                "decided_before_model_eval": "TRUE",
                "reviewer": "protocol",
                "reviewed_at": REVIEWED_AT,
            }
        )
    lineage_path = OUTPUT_DIR / "rfc264-to-rfc213-lineage.csv"
    write_csv(lineage_path, lineage_rows)

    selected_documents = [
        row for row in documents if row["event_id"] in selected_ids
    ]
    write_csv(CORE_DIR / DOCUMENT_REL, selected_documents, documents[0].keys())
    for row in selected_documents:
        shutil.copy2(
            PARENT_DIR / "public" / "documents" / row["file_name"],
            CORE_DIR / "public" / "documents" / row["file_name"],
        )
    (CORE_DIR / "public" / "excerpts").mkdir(parents=True, exist_ok=True)
    (CORE_DIR / "repair-stage" / "mutants").mkdir(parents=True, exist_ok=True)
    for event_id in selected_ids:
        shutil.copy2(
            PARENT_DIR / "public" / "excerpts" / f"{event_id}-evidence.md",
            CORE_DIR / "public" / "excerpts" / f"{event_id}-evidence.md",
        )
        shutil.copy2(
            PARENT_DIR / "repair-stage" / "mutants" / f"{event_id}.owl",
            CORE_DIR / "repair-stage" / "mutants" / f"{event_id}.owl",
        )

    selected_retrievals = [
        row for row in retrievals if row["event_id"] in selected_ids
    ]
    write_csv(CORE_DIR / RETRIEVAL_REL, selected_retrievals, retrievals[0].keys())
    support_rows = [
        row
        for row in read_csv(PARENT_DIR / SUPPORT_REL)
        if row["event_id"] in selected_ids
    ]
    for row in support_rows:
        row["evidence_file"] = row["evidence_file"].replace(PARENT_NAME, CORE_NAME)
    write_csv(CORE_DIR / SUPPORT_REL, support_rows)
    active_sources = {row["source_id"] for row in selected_retrievals}
    source_cache_rows = [
        row
        for row in read_csv(PARENT_DIR / SOURCE_CACHE_REL)
        if row["source_id"] in active_sources
    ]
    family_rows = [
        row
        for row in read_csv(PARENT_DIR / SOURCE_FAMILY_REL)
        if row["source_id"] in active_sources
    ]
    write_csv(CORE_DIR / SOURCE_CACHE_REL, source_cache_rows)
    write_csv(CORE_DIR / SOURCE_FAMILY_REL, family_rows)

    remapped_candidates: list[dict[str, str]] = []
    remapped_oracles: list[dict[str, str]] = []
    construct_rows: list[dict[str, str]] = []
    private_metadata_rows: list[dict[str, str]] = []
    public_cq_rows: list[dict[str, str]] = []
    private_cq_rows: list[dict[str, str]] = []
    new_event_rows: list[dict[str, str]] = []

    selected_events = sorted(
        (row for row in events if row["event_id"] in selected_ids),
        key=lambda row: row["event_id"],
    )
    for selected_index, event in enumerate(selected_events):
        event_id = event["event_id"]
        gold_target_id = f"CAND_{selected_index % 3 + 1:03d}"
        mapping = candidate_id_mapping(event_id, gold_target_id)
        original_candidates = candidate_by_event[event_id]
        remapped_current: list[dict[str, str]] = []
        for candidate in original_candidates:
            rewritten = dict(candidate)
            rewritten["candidate_id"] = mapping[candidate["candidate_id"]]
            remapped_current.append(rewritten)
        remapped_candidates.extend(remapped_current)

        oracle = dict(oracle_by_event[event_id])
        oracle["oracle_candidate_id"] = mapping[oracle["oracle_candidate_id"]]
        remapped_oracles.append(oracle)
        gold = next(
            row
            for row in remapped_current
            if row["candidate_id"] == oracle["oracle_candidate_id"]
        )
        operation = parsed_operation(gold)
        old_value = operation.get("old_value", {})
        new_value = operation.get("new_value", {})
        if (
            operation.get("operator") != "REPLACE_PROPERTY_VALUE"
            or old_value.get("kind") != "literal"
            or new_value.get("kind") != "literal"
        ):
            construct_type = "UNSUPPORTED_COMPLEX_AXIOM"
            supported = "FALSE"
            unsupported_reason = "Gamma MVP supports literal property replacement only"
            axiom_type = "UNKNOWN"
        else:
            construct_type = "UPDATE_LITERAL"
            supported = "TRUE"
            unsupported_reason = ""
            axiom_type = "DataPropertyAssertion"
        construct_rows.append(
            {
                "event_id": event_id,
                "gold_candidate_id": oracle["oracle_candidate_id"],
                "operation": str(operation.get("operator", "")),
                "axiom_type": axiom_type,
                "construct_type": construct_type,
                "precondition_pattern": "EXACTLY_ONE_OLD_LITERAL_MATCH",
                "object_kind": str(new_value.get("kind", "")),
                "requires_iri_resolution": "FALSE",
                "requires_literal_normalization": "FALSE",
                "supported_by_gamma_mvp": supported,
                "unsupported_reason": unsupported_reason,
            }
        )
        gold_path = (
            CORE_DIR / "private" / "ontology-after" / f"{event_id}-gold.owl"
        )
        if supported == "TRUE":
            create_gold_ontology(
                CORE_DIR / "repair-stage" / "mutants" / f"{event_id}.owl",
                gold_path,
                operation,
            )

        target_cq_id = f"CQ_{event_id}_TARGET"
        unaffected_cq_id = f"CQ_{event_id}_UNAFFECTED_01"
        base_cq = {
            "event_id": event_id,
            "query_type": "DATA_PROPERTY_EXACT_VALUE",
            "target_or_unaffected": "target",
        }
        public_cq_rows.append(
            {
                "cq_id": target_cq_id,
                **base_cq,
                "query_text": (
                    f'After applying the cited evidence, what value should subject '
                    f'"{event["subject_label"]}" have for predicate '
                    f'"{event["predicate_label"]}"?'
                ),
            }
        )
        private_cq_rows.append(
            {
                "cq_id": target_cq_id,
                **base_cq,
                "query_text": public_cq_rows[-1]["query_text"],
                "expected_answer_private": str(new_value.get("lexical", "")),
            }
        )
        unaffected_public = {
            "cq_id": unaffected_cq_id,
            "event_id": event_id,
            "query_text": (
                f"Does the ontology still classify the individual for {event_id} "
                "as a HoldoutObject?"
            ),
            "query_type": "INSTANCE_TYPE_ENTAILED",
            "target_or_unaffected": "unaffected",
        }
        public_cq_rows.append(unaffected_public)
        private_cq_rows.append(
            {**unaffected_public, "expected_answer_private": "TRUE"}
        )

        document = document_by_event[event_id]
        event_out = dict(event)
        event_out.update(
            {
                "version_chain_id": "",
                "source_version": "",
                "target_version": parse_rfc_version(event["source_url"]),
                "effective_date": "",
                "evidence_provenance": json.dumps(
                    {
                        "document_id": document["document_id"],
                        "source_url": document["source_url"],
                        "window_sha256": document["window_sha256"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "semantic_ir_id": f"SIR_{event_id}",
                "candidate_set_id": f"RFC213_CS_{event_id}",
                "ontology_before": f"repair-stage/mutants/{event_id}.owl",
                "ontology_after": "PRIVATE",
                "cq_ids": f"{target_cq_id}|{unaffected_cq_id}",
                "split": "confirmatory_core",
                "event_kind": "DRIFT_REPAIR",
                "expected_decision": "REPAIR",
                "notes": "",
            }
        )
        new_event_rows.append(event_out)
        private_metadata_rows.append(
            {
                "event_id": event_id,
                "ontology_before": f"repair-stage/mutants/{event_id}.owl",
                "ontology_after_private": (
                    f"private/ontology-after/{event_id}-gold.owl"
                    if supported == "TRUE"
                    else ""
                ),
                "ontology_after_sha256": (
                    sha256_file(gold_path) if supported == "TRUE" else ""
                ),
                "version_metadata_status": "TARGET_RFC_ONLY; NO_VERIFIED_SOURCE_VERSION",
                "effective_date_status": "NOT_AVAILABLE_IN_PARENT",
                "cq_status": "MECHANICALLY_GENERATED_MINIMUM_EXECUTABLE_SET",
            }
        )

    remapped_candidates.sort(key=lambda row: (row["event_id"], row["candidate_id"]))
    write_csv(CORE_DIR / EVENT_REL, new_event_rows)
    write_csv(CORE_DIR / CANDIDATE_REL, remapped_candidates, candidates[0].keys())
    write_csv(CORE_DIR / ORACLE_REL, remapped_oracles, oracles[0].keys())
    write_csv(CORE_DIR / "public/cq/cq-template.csv", public_cq_rows)
    write_csv(
        CORE_DIR / "private/cq/cq-gold-answers.csv",
        private_cq_rows,
    )
    write_csv(
        CORE_DIR / "private/annotation/event-private-metadata.csv",
        private_metadata_rows,
    )
    construct_private = (
        CORE_DIR
        / "private"
        / "construct-audit"
        / "rfc213-gold-repair-construct-audit.csv"
    )
    write_csv(construct_private, construct_rows)
    shutil.copy2(
        construct_private,
        OUTPUT_DIR / "rfc213-gold-repair-construct-audit.csv",
    )
    construction_dir = CORE_DIR / "private" / "construction"
    construction_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        lineage_path,
        construction_dir / "rfc264-to-rfc213-lineage.csv",
    )
    shutil.copy2(
        protocol_path,
        construction_dir / "rfc213-exclusion-protocol.json",
    )
    shutil.copy2(
        OUTPUT_DIR / "rfc264-protocol-audit.csv",
        construction_dir / "rfc264-protocol-audit.csv",
    )

    gamma_manifest = {
        "gamma_version": "gamma-mvp-rfc213-v1",
        "frozen_at": PROTOCOL_FROZEN_AT,
        "supported_constructs": ["UPDATE_LITERAL"],
        "observed_construct_counts": dict(
            sorted(Counter(row["construct_type"] for row in construct_rows).items())
        ),
        "supported_events": sum(
            row["supported_by_gamma_mvp"] == "TRUE" for row in construct_rows
        ),
        "coverage_denominator": len(construct_rows),
        "unsupported_policy": "return UNSUPPORTED and count in coverage denominator",
        "llm_allowed": False,
        "retrieval_allowed": False,
        "sampling_allowed": False,
        "scope_note": (
            "This manifest freezes the dataset fragment contract. It does not claim "
            "that a standalone Gamma compiler implementation exists in this freeze."
        ),
    }
    write_json(
        OUTPUT_DIR / "gamma-supported-fragment-manifest.json",
        gamma_manifest,
    )

    no_drift_fields = [
        "event_id",
        "event_kind",
        "expected_decision",
        "source_doc",
        "target_doc",
        "concept_or_clause",
        "evidence_provenance",
        "reviewer",
        "review_status",
        "notes",
    ]
    longitudinal_fields = [
        "chain_id",
        "step_id",
        "source_doc",
        "target_doc",
        "event_id",
        "future_evidence_allowed",
        "reviewer",
        "review_status",
        "notes",
    ]
    write_csv(
        CORE_DIR / "private/annotation/no-drift-schema-template.csv",
        [],
        no_drift_fields,
    )
    write_csv(
        CORE_DIR / "private/annotation/longitudinal-chain-schema-template.csv",
        [],
        longitudinal_fields,
    )
    write_json(
        CORE_DIR / "private/annotation/no-drift-protocol.json",
        {
            "status": "TEMPLATE_NOT_EVALUATION_READY",
            "target_count": "30-50",
            "event_kind": "NO_DRIFT",
            "expected_decision": "NO_CHANGE",
            "abstain_is_not_no_change": True,
            "metrics": [
                "No-Drift Detection Accuracy",
                "False Repair Rate",
                "No-Change Precision/Recall",
                "ABSTAIN Rate",
            ],
            "requires": "new RFC version-pair research and human gold review",
        },
    )
    write_json(
        CORE_DIR / "private/annotation/longitudinal-chain-protocol.json",
        {
            "status": "TEMPLATE_NOT_EVALUATION_READY",
            "target_chains": "15-20",
            "versions_per_chain": "3-5",
            "future_evidence_allowed": False,
            "metrics": [
                "Version Transition Accuracy",
                "Chain Success Rate",
                "Final-State Accuracy",
                "Historical Query Preservation",
                "Error Accumulation Rate",
            ],
            "requires": "new RFC lineage research and step-level human gold review",
        },
    )

    readme = f"""# RFC-213 Confirmatory Core

Status: `FROZEN_CONFIRMATORY_CORE` after the generated freeze checklist passes.

This benchmark is derived from the frozen `{PARENT_NAME}` parent without
overwriting it. The parent manifest SHA-256 is
`{EXPECTED_PARENT_MANIFEST_SHA256}`.

The RFC-264 set is retained as a construction lineage source. The confirmatory
benchmark uses a pre-specified 213-event core.

All 51 exclusions are recorded in the event-level lineage table. Selection uses
only construction artifacts and pre-existing review fields; model success or
failure is prohibited as an input. E06 never removes an event and remains in the
Gamma coverage denominator.

Public inputs contain no oracle or expected CQ answer. Gold target ontologies,
oracle rows, construct audit, and CQ answers are private. The repair-stage
candidates remain separate from public construction inputs.
"""
    (CORE_DIR / "README.md").write_text(readme, encoding="utf-8")
    status = f"""# RFC-213 Rebuild Status

- Parent: `{PARENT_NAME}`
- Parent manifest SHA-256: `{EXPECTED_PARENT_MANIFEST_SHA256}`
- Exclusion protocol SHA-256: `{protocol_sha}`
- Included repair events: {len(selected_ids)}
- Excluded lineage events: {len(events) - len(selected_ids)}
- Candidates: {len(remapped_candidates)}
- Active source IDs retained: {len(active_sources)}
- Included source counts: `{json.dumps(source_counts, sort_keys=True)}`
- Domain quotas: `{json.dumps(domain_quotas, sort_keys=True)}`
- Semantic-type quotas: `{json.dumps(type_quotas, sort_keys=True)}`
- Gold construct: `UPDATE_LITERAL` ({len(construct_rows)}/{len(construct_rows)})
- CQ status: mechanically generated minimum executable target + unaffected set
- Version metadata limitation: the parent has no verified source-version or
  effective-date relation; these fields are left blank rather than invented.
- No-Drift: `TEMPLATE_NOT_EVALUATION_READY`
- Longitudinal chains: `TEMPLATE_NOT_EVALUATION_READY`
- Gamma compiler: fragment contract frozen; standalone compiler implementation
  is outside this dataset rebuild.
"""
    (CORE_DIR / "REBUILD_STATUS.md").write_text(status, encoding="utf-8")
    return {
        "parent": PARENT_NAME,
        "parent_manifest_sha256": EXPECTED_PARENT_MANIFEST_SHA256,
        "protocol_sha256": protocol_sha,
        "events": len(selected_ids),
        "excluded": len(events) - len(selected_ids),
        "candidates": len(remapped_candidates),
        "semantic_types": dict(
            sorted(Counter(row["semantic_type"] for row in new_event_rows).items())
        ),
        "domains": dict(
            sorted(Counter(row["domain"] for row in new_event_rows).items())
        ),
        "active_sources": len(active_sources),
    }


def git_state() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            text=True,
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_DIR,
            text=True,
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=PROJECT_DIR,
                text=True,
            ).strip()
        )
        return {"commit": commit, "branch": branch, "dirty": dirty}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def freeze_core() -> dict[str, Any]:
    from validate_rfc213_confirmatory_core import validate_core

    audit = validate_core(CORE_DIR, OUTPUT_DIR, write_outputs=True)
    if audit["error_count"]:
        raise RuntimeError(f"refusing to freeze: {audit['error_count']} validation errors")

    excluded_names = {
        "rfc213-freeze-manifest.json",
        "rfc213-freeze-manifest-files.csv",
        "rfc213-freeze-checklist.json",
    }
    paths = [
        path
        for root in (CORE_DIR, OUTPUT_DIR)
        for path in root.rglob("*")
        if path.is_file() and path.name not in excluded_names
    ]
    rows = [
        {
            "role": (
                "benchmark_artifact"
                if CORE_DIR in path.parents
                else "output_artifact"
            ),
            "privacy": (
                "private"
                if CORE_DIR in path.parents and "private" in path.parts
                else (
                    "repair_stage"
                    if CORE_DIR in path.parents and "repair-stage" in path.parts
                    else "public"
                )
            ),
            "path": relpath(path),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(paths, key=lambda item: relpath(item))
    ]
    files_path = OUTPUT_DIR / "rfc213-freeze-manifest-files.csv"
    write_csv(files_path, rows)
    event_path = CORE_DIR / EVENT_REL
    candidate_path = CORE_DIR / CANDIDATE_REL
    lineage_path = OUTPUT_DIR / "rfc264-to-rfc213-lineage.csv"
    construct_path = OUTPUT_DIR / "rfc213-gold-repair-construct-audit.csv"
    gamma_path = OUTPUT_DIR / "gamma-supported-fragment-manifest.json"
    public_cq = CORE_DIR / "public/cq/cq-template.csv"
    private_cq = CORE_DIR / "private/cq/cq-gold-answers.csv"
    protocol_path = OUTPUT_DIR / "rfc213-exclusion-protocol.json"
    script_path = Path(__file__).resolve()
    frozen_at = datetime.now(timezone.utc).isoformat()
    files_digest = sha256_text(
        json.dumps(
            [
                {key: row[key] for key in ("role", "privacy", "path", "sha256")}
                for row in rows
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    manifest = {
        "schema_version": "1.0",
        "benchmark": CORE_NAME,
        "frozen_at": frozen_at,
        "parent_benchmark": PARENT_NAME,
        "parent_manifest_sha256": EXPECTED_PARENT_MANIFEST_SHA256,
        "exclusion_protocol_sha256": sha256_file(protocol_path),
        "lineage_csv_sha256": sha256_file(lineage_path),
        "event_csv_sha256": sha256_file(event_path),
        "candidate_csv_sha256": sha256_file(candidate_path),
        "construct_audit_sha256": sha256_file(construct_path),
        "gamma_fragment_manifest_sha256": sha256_file(gamma_path),
        "cq_files_sha256": {
            "public": sha256_file(public_cq),
            "private": sha256_file(private_cq),
        },
        "code": {
            "git": git_state(),
            "builder_path": relpath(script_path),
            "builder_sha256": sha256_file(script_path),
            "validator_path": "src/validate_rfc213_confirmatory_core.py",
            "validator_sha256": sha256_file(
                PROJECT_DIR / "src" / "validate_rfc213_confirmatory_core.py"
            ),
        },
        "counts": {
            "events": len(read_csv(event_path)),
            "candidates": len(read_csv(candidate_path)),
            "lineage_rows": len(read_csv(lineage_path)),
            "public_cq_rows": len(read_csv(public_cq)),
            "private_cq_rows": len(read_csv(private_cq)),
        },
        "files_manifest_sha256": files_digest,
        "files": rows,
    }
    manifest["manifest_sha256"] = sha256_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    manifest_path = OUTPUT_DIR / "rfc213-freeze-manifest.json"
    write_json(manifest_path, manifest)
    checklist = {
        "benchmark": CORE_NAME,
        "checked_at": frozen_at,
        "status": "PASS",
        "ready_for_confirmatory_evaluation": True,
        "checks": audit["checks"],
        "counts": manifest["counts"],
        "manifest_sha256": manifest["manifest_sha256"],
        "no_drift_extension_status": "TEMPLATE_NOT_EVALUATION_READY",
        "longitudinal_extension_status": "TEMPLATE_NOT_EVALUATION_READY",
        "limitations_acknowledged": [
            "single-annotator parent oracle review",
            "claim graphs contain UPDATE_LITERAL repairs only",
            "source-version and effective-date relations are unavailable in parent",
            "mechanically generated minimum executable CQs",
            (
                "repository worktree was dirty at freeze; exact builder and validator "
                "SHA-256 values are recorded in the manifest"
            ),
        ],
    }
    write_json(OUTPUT_DIR / "rfc213-freeze-checklist.json", checklist)
    return {
        "manifest": relpath(manifest_path),
        "manifest_sha256": manifest["manifest_sha256"],
        "files": len(rows),
        "status": "PASS",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("build", "freeze", "all"),
        default="all",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result: dict[str, Any] = {}
    if args.phase in {"build", "all"}:
        result["build"] = build_core(force=args.force)
    if args.phase in {"freeze", "all"}:
        result["freeze"] = freeze_core()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
