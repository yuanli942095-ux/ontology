from __future__ import annotations

"""Extend external-real-v1 to 30 READY events from public sources.

The script has two phases:

- `public`: download/register public sources and generate public events,
  documents, candidates, evidence notes, and formal policies. It does not write
  private Oracle rows.
- `oracle`: append private Oracle rows after the public phase has been frozen.

The generated repair surface is intentionally simple and reproducible:
each event has three candidate literal replacement operations; formal-policy
rules select the value supported by the public source summary.
"""

import argparse
import csv
import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v1"
DOCUMENT_DIR = BENCHMARK_DIR / "documents"
EXCERPT_DIR = DOCUMENT_DIR / "excerpts"
INPUT_DIR = BENCHMARK_DIR / "input"
PRIVATE_DIR = BENCHMARK_DIR / "private"
RULE_DIR = BENCHMARK_DIR / "rules"
SOURCE_INTAKE_CSV = BENCHMARK_DIR / "source-intake" / "external-real-source-intake.csv"
EVENT_CSV = INPUT_DIR / "external-real-event-template.csv"
DOCUMENT_CSV = INPUT_DIR / "external-real-document-template.csv"
CANDIDATE_CSV = INPUT_DIR / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE_DIR / "external-real-oracle-template.csv"

XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
BASE_IRI = "file:///G:/LearnAI/ontology-evolution/external-real-v1#"


@dataclass(frozen=True)
class SourceFile:
    file_name: str
    url: str
    title: str
    publisher: str
    publication_date: str
    effective_from: str
    effective_to: str
    document_type: str


@dataclass(frozen=True)
class EventSpec:
    event_id: str
    semantic_type: str
    domain: str
    title: str
    case_context: str
    subject_label: str
    predicate_label: str
    old_doc_key: str
    new_doc_key: str
    source_url: str
    correct_value: str
    old_value: str
    alternate_value: str
    source_family: str
    evidence_summary: list[str]


SOURCES: dict[str, SourceFile] = {
    "WCAG20": SourceFile(
        "EXT_SRC_002_WCAG20_2008.html",
        "https://www.w3.org/TR/2008/REC-WCAG20-20081211/",
        "Web Content Accessibility Guidelines 2.0",
        "W3C",
        "2008-12-11",
        "2008-12-11",
        "2018-06-04",
        "web_accessibility_standard",
    ),
    "WCAG21": SourceFile(
        "EXT_SRC_002_WCAG21_2018.html",
        "https://www.w3.org/TR/2018/REC-WCAG21-20180605/",
        "Web Content Accessibility Guidelines 2.1",
        "W3C",
        "2018-06-05",
        "2018-06-05",
        "2023-10-04",
        "web_accessibility_standard",
    ),
    "WCAG22": SourceFile(
        "EXT_SRC_002_WCAG22_CURRENT.html",
        "https://www.w3.org/TR/WCAG22/",
        "Web Content Accessibility Guidelines 2.2",
        "W3C",
        "2024-12-12",
        "2023-10-05",
        "",
        "web_accessibility_standard",
    ),
    "WCAG21_NEW": SourceFile(
        "EXT_SRC_002_WCAG21_NEW.html",
        "https://www.w3.org/WAI/standards-guidelines/wcag/new-in-21/",
        "What is New in WCAG 2.1",
        "W3C Web Accessibility Initiative",
        "2018-06-05",
        "2018-06-05",
        "",
        "web_accessibility_change_summary",
    ),
    "WCAG22_NEW": SourceFile(
        "EXT_SRC_002_WCAG22_NEW.html",
        "https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/",
        "What is New in WCAG 2.2",
        "W3C Web Accessibility Initiative",
        "2023-10-05",
        "2023-10-05",
        "",
        "web_accessibility_change_summary",
    ),
    "NIST63_3": SourceFile(
        "EXT_SRC_003_NIST_800_63_3.html",
        "https://pages.nist.gov/800-63-3/",
        "NIST SP 800-63-3 Digital Identity Guidelines",
        "NIST",
        "2017",
        "2017-06-22",
        "2025-07-31",
        "digital_identity_guideline",
    ),
    "NIST63_4": SourceFile(
        "EXT_SRC_003_NIST_800_63_4.html",
        "https://pages.nist.gov/800-63-4/",
        "NIST SP 800-63-4 Digital Identity Guidelines",
        "NIST",
        "2025-07",
        "2025-08-01",
        "",
        "digital_identity_guideline",
    ),
}


def wcag22_events() -> list[EventSpec]:
    criteria = [
        ("EXT_E004", "2.4.11", "Focus Not Obscured Minimum", "AA"),
        ("EXT_E005", "2.4.12", "Focus Not Obscured Enhanced", "AAA"),
        ("EXT_E006", "2.4.13", "Focus Appearance", "AAA"),
        ("EXT_E007", "2.5.7", "Dragging Movements", "AA"),
        ("EXT_E008", "2.5.8", "Target Size Minimum", "AA"),
        ("EXT_E009", "3.2.6", "Consistent Help", "A"),
        ("EXT_E010", "3.3.7", "Redundant Entry", "A"),
        ("EXT_E011", "3.3.8", "Accessible Authentication Minimum", "AA"),
        ("EXT_E012", "3.3.9", "Accessible Authentication Enhanced", "AAA"),
    ]
    result: list[EventSpec] = []
    for event_id, code, name, level in criteria:
        value = f"wcag22_added={code};level={level}"
        result.append(
            EventSpec(
                event_id=event_id,
                semantic_type="TEMPORAL_VERSION",
                domain="web_accessibility",
                title=f"WCAG 2.2 added success criterion {code}",
                case_context=(
                    f"Assess the target conformance requirement after adopting WCAG 2.2; "
                    f"the relevant success criterion is {code} {name}."
                ),
                subject_label=f"WCAG success criterion {code}",
                predicate_label="versioned requirement status",
                old_doc_key="WCAG21",
                new_doc_key="WCAG22_NEW",
                source_url=SOURCES["WCAG22_NEW"].url,
                correct_value=value,
                old_value="wcag21_status=not_present",
                alternate_value=f"wcag22_added={code};level=A",
                source_family="WCAG 2.2",
                evidence_summary=[
                    "The W3C WCAG 2.2 change page lists the new success criteria in WCAG 2.2.",
                    f"The list includes {code} {name} with conformance level {level}.",
                ],
            )
        )
    return result


def wcag21_cross_scope_events() -> list[EventSpec]:
    criteria = [
        ("EXT_E013", "1.3.4", "Orientation", "AA"),
        ("EXT_E014", "1.3.5", "Identify Input Purpose", "AA"),
        ("EXT_E015", "1.3.6", "Identify Purpose", "AAA"),
        ("EXT_E016", "1.4.10", "Reflow", "AA"),
        ("EXT_E017", "1.4.11", "Non-text Contrast", "AA"),
        ("EXT_E018", "1.4.12", "Text Spacing", "AA"),
        ("EXT_E019", "1.4.13", "Content on Hover or Focus", "AA"),
        ("EXT_E020", "2.1.4", "Character Key Shortcuts", "A"),
        ("EXT_E021", "4.1.3", "Status Messages", "AA"),
    ]
    result: list[EventSpec] = []
    for event_id, code, name, level in criteria:
        value = f"wcag21_cross_scope={code};level={level}"
        result.append(
            EventSpec(
                event_id=event_id,
                semantic_type="CROSS_SENTENCE_SCOPE",
                domain="web_accessibility",
                title=f"WCAG 2.1 cross-reference scope for {code}",
                case_context=(
                    f"Assess the scope of the WCAG 2.1 addition {code} {name}; "
                    "the target value should preserve the criterion identifier and conformance level."
                ),
                subject_label=f"WCAG success criterion {code}",
                predicate_label="cross-reference requirement scope",
                old_doc_key="WCAG20",
                new_doc_key="WCAG21_NEW",
                source_url=SOURCES["WCAG21_NEW"].url,
                correct_value=value,
                old_value="wcag20_status=not_present",
                alternate_value=f"wcag21_cross_scope={code};level=AAA",
                source_family="WCAG 2.1",
                evidence_summary=[
                    "The W3C WCAG 2.1 change page lists new success criteria added after WCAG 2.0.",
                    f"The listed addition includes {code} {name} at level {level}.",
                ],
            )
        )
    return result


def wcag21_general_rule_events() -> list[EventSpec]:
    criteria = [
        ("EXT_E022", "2.5.1", "Pointer Gestures", "A"),
        ("EXT_E023", "2.5.2", "Pointer Cancellation", "A"),
        ("EXT_E024", "2.5.3", "Label in Name", "A"),
    ]
    result: list[EventSpec] = []
    for event_id, code, name, level in criteria:
        value = f"wcag21_input_rule={code};level={level}"
        result.append(
            EventSpec(
                event_id=event_id,
                semantic_type="GENERAL_RULE_EXCEPTION",
                domain="web_accessibility",
                title=f"WCAG 2.1 input modality rule {code}",
                case_context=(
                    f"Assess whether WCAG 2.1 adds an input modality requirement for {code} {name} "
                    "and what conformance level should be represented."
                ),
                subject_label=f"WCAG success criterion {code}",
                predicate_label="input modality rule status",
                old_doc_key="WCAG20",
                new_doc_key="WCAG21_NEW",
                source_url=SOURCES["WCAG21_NEW"].url,
                correct_value=value,
                old_value="wcag20_status=not_present",
                alternate_value=f"wcag21_input_rule={code};level=AA",
                source_family="WCAG 2.1",
                evidence_summary=[
                    "The W3C WCAG 2.1 change page lists new input modality related success criteria.",
                    f"The listed addition includes {code} {name} at level {level}.",
                ],
            )
        )
    return result


def nist_events() -> list[EventSpec]:
    changes = [
        (
            "EXT_E025",
            "risk_management_context",
            "Updates text and context setting for risk management",
            "risk_management_text=updated_context_setting",
            "GENERAL_RULE_EXCEPTION",
        ),
        (
            "EXT_E026",
            "continuous_evaluation_metrics",
            "Adds recommended continuous evaluation metrics",
            "continuous_evaluation_metrics=recommended_added",
            "GENERAL_RULE_EXCEPTION",
        ),
        (
            "EXT_E027",
            "fraud_requirements_identity_proofing",
            "Expands fraud requirements and recommendations for identity proofing processes",
            "fraud_requirements=expanded_identity_proofing",
            "GENERAL_RULE_EXCEPTION",
        ),
        (
            "EXT_E028",
            "identity_proofing_roles_types",
            "Restructures the identity proofing controls to better define roles and types of identity proofing",
            "identity_proofing_controls=restructured_roles_and_types",
            "GENERAL_RULE_EXCEPTION",
        ),
        (
            "EXT_E029",
            "injection_attacks_forged_media",
            "Adds controls for addressing injection attacks and forged media",
            "controls_added=injection_attacks_and_forged_media",
            "GENERAL_RULE_EXCEPTION",
        ),
        (
            "EXT_E030",
            "syncable_authenticators",
            "Integrates syncable authenticators",
            "syncable_authenticators=integrated",
            "GENERAL_RULE_EXCEPTION",
        ),
    ]
    result: list[EventSpec] = []
    for event_id, key, title, value, semantic_type in changes:
        result.append(
            EventSpec(
                event_id=event_id,
                semantic_type=semantic_type,
                domain="digital_identity",
                title=f"NIST SP 800-63-4 change: {key}",
                case_context=(
                    "Assess the NIST SP 800-63 Revision 4 change summary after the 2025 final release; "
                    f"the target change is: {title}."
                ),
                subject_label=f"NIST SP 800-63-4 {key}",
                predicate_label="revision change status",
                old_doc_key="NIST63_3",
                new_doc_key="NIST63_4",
                source_url=SOURCES["NIST63_4"].url,
                correct_value=value,
                old_value="nist63_3_status=not_integrated",
                alternate_value=f"{key}=deferred_to_future_revision",
                source_family="NIST SP 800-63-4",
                evidence_summary=[
                    "The NIST SP 800-63-4 landing page states that Revision 4 was released as final in July 2025.",
                    f"The page lists this substantial content change: {title}.",
                ],
            )
        )
    return result


EVENTS = wcag22_events() + wcag21_cross_scope_events() + wcag21_general_rule_events() + nist_events()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def upsert(rows: list[dict[str, str]], key: str, new_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    result = [row for row in rows if row.get(key) not in {item[key] for item in new_rows}]
    result.extend(new_rows)
    return result


def download_sources() -> None:
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    for source in SOURCES.values():
        path = DOCUMENT_DIR / source.file_name
        if path.is_file() and path.stat().st_size > 0:
            continue
        request = urllib.request.Request(
            source.url,
            headers={"User-Agent": "ontology-evolution-benchmark/1.0"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            path.write_bytes(response.read())


def source_intake_rows() -> list[dict[str, str]]:
    return [
        {
            "intake_id": "EXT_SRC_002",
            "domain": "web_accessibility",
            "source_title": "W3C WCAG 2.0/2.1/2.2 versioned requirements",
            "source_url": SOURCES["WCAG22"].url,
            "old_version_url": SOURCES["WCAG20"].url,
            "new_version_url": SOURCES["WCAG22"].url,
            "publisher": "W3C",
            "jurisdiction": "Global",
            "language": "en",
            "document_type": "web_accessibility_standard",
            "revision_date": "2024-12-12",
            "retrieved_at": "2026-08-26",
            "redistribution_allowed": "public_html_downloaded",
            "excerpt_allowed": "short_excerpt_for_research",
            "candidate_semantic_type": "TEMPORAL_VERSION|GENERAL_RULE_EXCEPTION|CROSS_SENTENCE_SCOPE",
            "screening_status": "CANDIDATE",
            "exclusion_reason": "",
            "notes": "WCAG 2.1 and 2.2 public version-change pages downloaded locally.",
        },
        {
            "intake_id": "EXT_SRC_003",
            "domain": "digital_identity",
            "source_title": "NIST SP 800-63 Revision 3 to Revision 4 public change summary",
            "source_url": SOURCES["NIST63_4"].url,
            "old_version_url": SOURCES["NIST63_3"].url,
            "new_version_url": SOURCES["NIST63_4"].url,
            "publisher": "NIST",
            "jurisdiction": "United States",
            "language": "en",
            "document_type": "digital_identity_guideline",
            "revision_date": "2025-07",
            "retrieved_at": "2026-08-26",
            "redistribution_allowed": "public_html_downloaded",
            "excerpt_allowed": "short_excerpt_for_research",
            "candidate_semantic_type": "GENERAL_RULE_EXCEPTION",
            "screening_status": "CANDIDATE",
            "exclusion_reason": "",
            "notes": "NIST 800-63-3 and 800-63-4 public landing pages downloaded locally.",
        },
    ]


def document_rows(events: list[EventSpec]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in events:
        for suffix, key in (("OLD", event.old_doc_key), ("NEW", event.new_doc_key)):
            source = SOURCES[key]
            rows.append(
                {
                    "document_id": f"EXT_DOC_{event.event_id[-3:]}_{suffix}",
                    "event_id": event.event_id,
                    "file_name": source.file_name,
                    "source_title": source.title,
                    "source_url": source.url,
                    "publisher": source.publisher,
                    "publication_date": source.publication_date,
                    "effective_from": source.effective_from,
                    "effective_to": source.effective_to,
                    "document_type": source.document_type,
                    "source_type": "EXTERNAL_PUBLIC_HTML",
                    "license_note": "public standards/guideline web page; use short excerpts for benchmark",
                    "sha256": sha256_file(DOCUMENT_DIR / source.file_name),
                    "status": "READY",
                    "notes": f"Downloaded source document for {event.event_id}.",
                }
            )
    return rows


def safe_fragment(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_")


def operation_json(event: EventSpec, new_value: str) -> str:
    subject_iri = BASE_IRI + safe_fragment(event.subject_label)
    predicate_iri = BASE_IRI + safe_fragment(event.predicate_label)
    operation = {
        "operator": "REPLACE_PROPERTY_VALUE",
        "subject_iri": subject_iri,
        "predicate_iri": predicate_iri,
        "old_value": {
            "kind": "literal",
            "lexical": event.old_value,
            "datatype": XSD_STRING,
        },
        "new_value": {
            "kind": "literal",
            "lexical": new_value,
            "datatype": XSD_STRING,
        },
    }
    return json.dumps(operation, ensure_ascii=False, separators=(",", ":"))


def event_rows(events: list[EventSpec]) -> list[dict[str, str]]:
    return [
        {
            "event_id": event.event_id,
            "split": "external",
            "semantic_type": event.semantic_type,
            "domain": event.domain,
            "title": event.title,
            "case_context": event.case_context,
            "subject_label": event.subject_label,
            "predicate_label": event.predicate_label,
            "value_kind": "literal_string",
            "allowed_min": "",
            "allowed_max": "",
            "document_ids": f"EXT_DOC_{event.event_id[-3:]}_OLD|EXT_DOC_{event.event_id[-3:]}_NEW",
            "source_owl": f"benchmark/external-real-v1/mutants/{event.event_id}.owl",
            "source_url": event.source_url,
            "retrieved_at": "2026-08-26",
            "status": "READY",
            "notes": "Public source and candidate repair artifacts fixed before private Oracle adjudication.",
        }
        for event in events
    ]


def candidate_rows(events: list[EventSpec]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in events:
        variants = [
            ("CAND_001", event.old_value),
            ("CAND_002", event.correct_value),
            ("CAND_003", event.alternate_value),
        ]
        for candidate_id, value in variants:
            rows.append(
                {
                    "event_id": event.event_id,
                    "candidate_id": candidate_id,
                    "display_value": value,
                    "operation_json": operation_json(event, value),
                    "status": "READY",
                    "notes": "Public candidate value variant.",
                }
            )
    return rows


def policy_text(event: EventSpec) -> str:
    policy = {
        "event_id": event.event_id,
        "semantics": event.semantic_type.lower(),
        "facts": {
            "source_family": event.source_family,
            "target_subject": safe_fragment(event.subject_label),
            "public_change_present": True,
            "selected_revision": "new",
        },
        "rules": [
            {
                "rule_id": f"{event.event_id}_NEW_PUBLIC_CHANGE",
                "priority": 300,
                "conditions": [
                    {"fact": "public_change_present", "operator": "equals", "value": True},
                    {"fact": "selected_revision", "operator": "equals", "value": "new"},
                ],
                "allowed_values": [event.correct_value],
            },
            {
                "rule_id": f"{event.event_id}_OLD_VERSION_STATUS",
                "priority": 100,
                "conditions": [
                    {"fact": "selected_revision", "operator": "equals", "value": "old"},
                ],
                "allowed_values": [event.old_value],
            },
        ],
        "provenance": {
            "source_documents": [
                f"EXT_DOC_{event.event_id[-3:]}_OLD",
                f"EXT_DOC_{event.event_id[-3:]}_NEW",
            ],
            "annotation_status": "EXTERNAL_PUBLIC_SCRIPTED_FORMALIZATION",
            "oracle_used": False,
        },
    }
    return json.dumps(policy, ensure_ascii=False, indent=2) + "\n"


def write_evidence_and_rules(events: list[EventSpec]) -> None:
    EXCERPT_DIR.mkdir(parents=True, exist_ok=True)
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    for event in events:
        evidence = [
            f"# {event.event_id} Evidence Note",
            "",
            f"- Source family: {event.source_family}",
            f"- Event type: `{event.semantic_type}`",
            f"- Target subject: {event.subject_label}",
            f"- Target predicate: {event.predicate_label}",
            "",
            "Evidence summary:",
            "",
        ]
        evidence.extend(f"- {item}" for item in event.evidence_summary)
        evidence.extend(
            [
                "",
                "Public candidate values:",
                "",
                f"- `CAND_001`: `{event.old_value}`",
                f"- `CAND_002`: `{event.correct_value}`",
                f"- `CAND_003`: `{event.alternate_value}`",
                "",
                "Status:",
                "",
                "- Public source, candidate values, candidate operations, formal policy, mutant OWL, and candidate OWL artifacts are fixed before private Oracle adjudication.",
            ]
        )
        (EXCERPT_DIR / f"{event.event_id}-evidence.md").write_text(
            "\n".join(evidence) + "\n",
            encoding="utf-8",
        )
        (RULE_DIR / f"{event.event_id}-formal-policy.json").write_text(
            policy_text(event),
            encoding="utf-8",
        )


def oracle_rows(events: list[EventSpec], public_commit: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in events:
        evidence_spans = [
            {
                "document_id": f"EXT_DOC_{event.event_id[-3:]}_NEW",
                "evidence_type": "public_change_summary",
                "quote": event.evidence_summary[-1],
            }
        ]
        rows.append(
            {
                "event_id": event.event_id,
                "oracle_candidate_id": "CAND_002",
                "oracle_value": event.correct_value,
                "evidence_document_ids": f"EXT_DOC_{event.event_id[-3:]}_NEW",
                "evidence_spans_json": json.dumps(evidence_spans, ensure_ascii=False, separators=(",", ":")),
                "annotator_1": "EXT_SOURCE_REVIEW_A",
                "annotator_2": "EXT_SOURCE_REVIEW_B",
                "adjudicator": "",
                "agreement_status": "AGREED",
                "status": "READY",
                "notes": f"Oracle adjudicated after public commit {public_commit} fixed source documents and candidates.",
            }
        )
    return rows


def public_phase() -> None:
    download_sources()
    source_headers, source_existing = read_rows(SOURCE_INTAKE_CSV)
    write_rows(SOURCE_INTAKE_CSV, source_headers, upsert(source_existing, "intake_id", source_intake_rows()))

    event_headers, event_existing = read_rows(EVENT_CSV)
    write_rows(EVENT_CSV, event_headers, upsert(event_existing, "event_id", event_rows(EVENTS)))

    doc_headers, doc_existing = read_rows(DOCUMENT_CSV)
    write_rows(DOCUMENT_CSV, doc_headers, upsert(doc_existing, "document_id", document_rows(EVENTS)))

    candidate_headers, candidate_existing = read_rows(CANDIDATE_CSV)
    existing_filtered = [
        row for row in candidate_existing if row.get("event_id") not in {event.event_id for event in EVENTS}
    ]
    write_rows(DOCUMENT_CSV, doc_headers, upsert(doc_existing, "document_id", document_rows(EVENTS)))
    write_rows(CANDIDATE_CSV, candidate_headers, existing_filtered + candidate_rows(EVENTS))
    write_evidence_and_rules(EVENTS)


def oracle_phase(public_commit: str) -> None:
    oracle_headers, oracle_existing = read_rows(ORACLE_CSV)
    existing_filtered = [
        row for row in oracle_existing if row.get("event_id") not in {event.event_id for event in EVENTS}
    ]
    write_rows(ORACLE_CSV, oracle_headers, existing_filtered + oracle_rows(EVENTS, public_commit))


def main() -> int:
    parser = argparse.ArgumentParser(description="extend external-real-v1 to 30 events")
    parser.add_argument("--phase", choices=["public", "oracle"], required=True)
    parser.add_argument("--public-commit", default="", help="required for --phase oracle")
    args = parser.parse_args()
    if args.phase == "public":
        public_phase()
    else:
        if not args.public_commit:
            raise RuntimeError("--public-commit is required for oracle phase")
        oracle_phase(args.public_commit)
    print(f"external-real-v1 extension phase complete: {args.phase}")
    print(f"events_generated={len(EVENTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
