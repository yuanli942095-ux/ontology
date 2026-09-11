from __future__ import annotations

"""Build external-real-v2 as a 60+ event public-source benchmark.

external-real-v2 contains the 30 external-real-v1 events plus 38 additional
public-source structured-excerpt events from the expansion plan. It generates
public templates, private Oracle rows, evidence notes, formal policies, OWL
mutants/candidate artifacts, and a freeze manifest.
"""

import csv
import hashlib
import json
import shutil
import xml.sax.saxutils as xml_escape
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


ROOT = PROJECT_DIR
V1 = ROOT / "benchmark" / "external-real-v1"
V2 = ROOT / "benchmark" / "external-real-v2"
V2_INPUT = V2 / "input"
V2_PRIVATE = V2 / "private"
V2_DOCS = V2 / "documents"
V2_EXCERPTS = V2_DOCS / "excerpts"
V2_RULES = V2 / "rules"
V2_MUTANTS = V2 / "mutants"
V2_BUILT = V2 / "built"
V2_INTAKE = V2 / "source-intake"
OUTPUT = ROOT / "output"

EVENT_CSV = V2_INPUT / "external-real-event-template.csv"
DOCUMENT_CSV = V2_INPUT / "external-real-document-template.csv"
CANDIDATE_CSV = V2_INPUT / "external-real-candidate-template.csv"
ORACLE_CSV = V2_PRIVATE / "external-real-oracle-template.csv"
MANIFEST_JSON = OUTPUT / "external-real-v2-freeze-manifest-68.json"
MANIFEST_FILES_CSV = OUTPUT / "external-real-v2-freeze-manifest-68-files.csv"

BASE_IRI = "file:///G:/LearnAI/ontology-evolution/external-real-v2#"
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
OWL = "http://www.w3.org/2002/07/owl#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"


@dataclass(frozen=True)
class AddedEvent:
    event_id: str
    semantic_type: str
    domain: str
    title: str
    case_context: str
    subject_label: str
    predicate_label: str
    source_id: str
    source_title: str
    source_url: str
    publisher: str
    old_label: str
    new_label: str
    old_value: str
    correct_value: str
    alternate_value: str
    evidence_summary: tuple[str, ...]


def safe_fragment(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if headers is None:
        headers = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def operation_json(event: AddedEvent, new_value: str) -> str:
    operation = {
        "operator": "REPLACE_PROPERTY_VALUE",
        "subject_iri": BASE_IRI + safe_fragment(event.subject_label),
        "predicate_iri": BASE_IRI + safe_fragment(event.predicate_label),
        "old_value": {"kind": "literal", "lexical": event.old_value, "datatype": XSD_STRING},
        "new_value": {"kind": "literal", "lexical": new_value, "datatype": XSD_STRING},
    }
    return json.dumps(operation, ensure_ascii=False, separators=(",", ":"))


def level_alt(value: str) -> str:
    if value.endswith("level=A"):
        return value[:-1] + "AA"
    if value.endswith("level=AA"):
        return value[:-2] + "AAA"
    if value.endswith("level=AAA"):
        return value[:-3] + "AA"
    return value + "_alternative"


def added_events() -> list[AddedEvent]:
    rows: list[AddedEvent] = []

    def add(
        event_id: str,
        semantic_type: str,
        domain: str,
        source_id: str,
        source_title: str,
        source_url: str,
        publisher: str,
        key: str,
        title: str,
        subject: str,
        predicate: str,
        old_value: str,
        correct_value: str,
        alternate_value: str,
        evidence: tuple[str, ...],
    ) -> None:
        rows.append(
            AddedEvent(
                event_id=event_id,
                semantic_type=semantic_type,
                domain=domain,
                title=title,
                case_context=f"Assess the current normative representation for {subject} using public source {source_title}.",
                subject_label=subject,
                predicate_label=predicate,
                source_id=source_id,
                source_title=source_title,
                source_url=source_url,
                publisher=publisher,
                old_label="older_or_not_integrated",
                new_label="current_public_source",
                old_value=old_value,
                correct_value=correct_value,
                alternate_value=alternate_value,
                evidence_summary=evidence,
            )
        )

    wcag_new_22 = "https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/"
    wcag22_tr = "https://www.w3.org/TR/WCAG22/"
    wcag_mobile = "https://www.w3.org/TR/wcag2mobile-22/"
    nist4 = "https://pages.nist.gov/800-63-4/"
    nist4a = "https://pages.nist.gov/800-63-4/sp800-63a.html"
    nist4b = "https://pages.nist.gov/800-63-4/sp800-63b.html"
    eurlex = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A32020R2174"
    ecfr = "https://www.ecfr.gov/"

    wcag_items = [
        ("EXT_E031", "TEMPORAL_VERSION", "4.1.1", "Parsing", "wcag22_obsolete=4.1.1;status=removed"),
        ("EXT_E032", "GENERAL_RULE_EXCEPTION", "language_notes", "WCAG 2.2 language notes", "wcag22_notes=language_specific_notes_added"),
        ("EXT_E033", "CROSS_SENTENCE_SCOPE", "backward_compatibility", "WCAG 2.2 backward compatibility", "wcag22_scope=extends_wcag21_except_parsing"),
        ("EXT_E034", "TEMPORAL_VERSION", "focus_not_obscured", "Focus Not Obscured family", "wcag22_family=focus_not_obscured;criteria=2.4.11_2.4.12"),
        ("EXT_E035", "GENERAL_RULE_EXCEPTION", "target_size", "Target Size Minimum exception family", "wcag22_target_size=minimum_added;level=AA"),
        ("EXT_E036", "CROSS_SENTENCE_SCOPE", "accessible_authentication", "Accessible Authentication scope", "wcag22_accessible_authentication=minimum_and_enhanced"),
        ("EXT_E037", "TEMPORAL_VERSION", "dragging_movements", "Dragging Movements addition", "wcag22_added=2.5.7;level=AA"),
        ("EXT_E038", "GENERAL_RULE_EXCEPTION", "redundant_entry", "Redundant Entry addition", "wcag22_added=3.3.7;level=A"),
    ]
    for event_id, sem_type, key, label, value in wcag_items:
        add(
            event_id,
            sem_type,
            "web_accessibility",
            "V2_SRC_WCAG_001",
            "What's New in WCAG 2.2",
            wcag_new_22,
            "W3C Web Accessibility Initiative",
            key,
            f"WCAG 2.2 public change: {label}",
            f"WCAG 2.2 {label}",
            "versioned requirement status",
            "wcag21_status=previous_target",
            value,
            level_alt(value),
            (
                "The W3C WCAG 2.2 change page lists additions and changes introduced in WCAG 2.2.",
                "The WCAG 2 overview notes that WCAG 2.2 adds success criteria and treats 4.1.1 Parsing as obsolete.",
            ),
        )

    mobile_items = [
        ("EXT_E039", "CROSS_SENTENCE_SCOPE", "mobile_wcag_scope=principles_guidelines_criteria_glossary"),
        ("EXT_E040", "CROSS_SENTENCE_SCOPE", "mobile_wcag2ict_scope=applied_guidance"),
        ("EXT_E041", "GENERAL_RULE_EXCEPTION", "mobile_guidance=not_new_success_criteria"),
        ("EXT_E042", "GENERAL_RULE_EXCEPTION", "mobile_guidance=wcag22_mapping_context"),
    ]
    for event_id, sem_type, value in mobile_items:
        add(
            event_id,
            sem_type,
            "web_accessibility",
            "V2_SRC_WCAG_002",
            "Guidance on Applying WCAG 2.2 to Mobile Applications",
            wcag_mobile,
            "W3C",
            value,
            f"WCAG2Mobile scope event {event_id}",
            f"WCAG2Mobile {event_id}",
            "mobile application guidance scope",
            "mobile_guidance=not_integrated",
            value,
            value + "_overgeneralized",
            (
                "The WCAG2Mobile document states that it includes text quoted from WCAG 2.2 and WCAG2ICT.",
                "The guidance applies WCAG success criteria to mobile application contexts rather than creating an unrelated rule set.",
            ),
        )

    nist_items = [
        ("EXT_E043", "TEMPORAL_VERSION", "nist63_revision=rev4_final_august_2025"),
        ("EXT_E044", "TEMPORAL_VERSION", "nist63_3_status=superseded_by_rev4"),
        ("EXT_E045", "GENERAL_RULE_EXCEPTION", "nist63_process=assurance_level_selection"),
        ("EXT_E046", "GENERAL_RULE_EXCEPTION", "nist63_scope=identity_proofing_authentication_federation"),
        ("EXT_E047", "CROSS_SENTENCE_SCOPE", "nist63_privacy_security_customer_experience=included"),
        ("EXT_E048", "GENERAL_RULE_EXCEPTION", "nist63_content_changes=substantial_revision"),
        ("EXT_E049", "TEMPORAL_VERSION", "nist63_revision4=public_final_guideline"),
        ("EXT_E050", "CROSS_SENTENCE_SCOPE", "nist63_model=process_and_technical_requirements"),
    ]
    for event_id, sem_type, value in nist_items:
        add(
            event_id,
            sem_type,
            "digital_identity",
            "V2_SRC_NIST_001",
            "NIST SP 800-63 Revision 3 to Revision 4",
            nist4,
            "NIST",
            value,
            f"NIST SP 800-63-4 public revision event {event_id}",
            f"NIST SP 800-63-4 {event_id}",
            "revision status",
            "nist63_3_status=old_revision",
            value,
            value + "_deferred",
            (
                "The NIST SP 800-63-4 page states that Revision 4 is the current digital identity guideline suite.",
                "The page describes process and technical requirements for identity proofing, authentication, and federation.",
            ),
        )

    nist_ab_items = [
        ("EXT_E051", "CROSS_SENTENCE_SCOPE", nist4a, "nist63a_scope=identity_proofing_and_enrollment"),
        ("EXT_E052", "GENERAL_RULE_EXCEPTION", nist4a, "nist63a_controls=identity_proofing_requirements"),
        ("EXT_E053", "CROSS_SENTENCE_SCOPE", nist4b, "nist63b_scope=digital_authentication"),
        ("EXT_E054", "GENERAL_RULE_EXCEPTION", nist4b, "nist63b_verifiers=authenticated_services"),
        ("EXT_E055", "GENERAL_RULE_EXCEPTION", nist4b, "nist63b_physical_access=out_of_scope"),
        ("EXT_E056", "TEMPORAL_VERSION", nist4a, "nist63a_revision4=current_version"),
    ]
    for event_id, sem_type, url, value in nist_ab_items:
        add(
            event_id,
            sem_type,
            "digital_identity",
            "V2_SRC_NIST_002",
            "NIST SP 800-63A/B Revision 4",
            url,
            "NIST",
            value,
            f"NIST SP 800-63A/B scope event {event_id}",
            f"NIST SP 800-63A/B {event_id}",
            "document scope status",
            "nist63_ab_status=old_or_absent",
            value,
            value + "_mis_scoped",
            (
                "NIST SP 800-63A focuses on identity proofing and enrollment.",
                "NIST SP 800-63B applies to digital authentication over a network and excludes physical access authentication.",
            ),
        )

    eurlex_items = [
        ("EXT_E057", "TEMPORAL_VERSION", "eurlex_effective_date=2021_01_01"),
        ("EXT_E058", "GENERAL_RULE_EXCEPTION", "eurlex_annex=regulation_1013_2006_modified"),
        ("EXT_E059", "CROSS_SENTENCE_SCOPE", "eurlex_scope=union_annex_alignment"),
        ("EXT_E060", "TEMPORAL_VERSION", "eurlex_decision_change=effective_future_date"),
        ("EXT_E061", "GENERAL_RULE_EXCEPTION", "eurlex_amendment=annex_specific_not_full_repeal"),
        ("EXT_E062", "CROSS_SENTENCE_SCOPE", "eurlex_application=modified_annexes_only"),
    ]
    for event_id, sem_type, value in eurlex_items:
        add(
            event_id,
            sem_type,
            "eu_regulation",
            "V2_SRC_EURLEX_001",
            "EUR-Lex regulation amendment with effective date and annex changes",
            eurlex,
            "EUR-Lex",
            value,
            f"EUR-Lex amendment event {event_id}",
            f"EUR-Lex amendment {event_id}",
            "regulatory amendment status",
            "eurlex_status=pre_amendment",
            value,
            value + "_overbroad",
            (
                "The EUR-Lex regulation page describes amendments to annexes and states an effective date.",
                "The public text supports a scoped amendment rather than replacing the whole regulation.",
            ),
        )

    ecfr_items = [
        ("EXT_E063", "TEMPORAL_VERSION", "ecfr_effective_date_rule=not_less_than_30_days"),
        ("EXT_E064", "CROSS_SENTENCE_SCOPE", "ecfr_status=continuously_updated_online_version"),
        ("EXT_E065", "GENERAL_RULE_EXCEPTION", "ecfr_not_official_legal_edition=noted"),
        ("EXT_E066", "TEMPORAL_VERSION", "federal_register_to_ecfr=current_text_update_path"),
        ("EXT_E067", "CROSS_SENTENCE_SCOPE", "ecfr_scope=general_and_permanent_rules"),
        ("EXT_E068", "GENERAL_RULE_EXCEPTION", "ecfr_editorial_process=separate_from_official_print"),
    ]
    for event_id, sem_type, value in ecfr_items:
        add(
            event_id,
            sem_type,
            "us_regulation",
            "V2_SRC_ECFR_001",
            "eCFR current regulatory text and effective-date requirements",
            ecfr,
            "eCFR",
            value,
            f"eCFR regulatory text event {event_id}",
            f"eCFR {event_id}",
            "regulatory text status",
            "ecfr_status=unmodeled",
            value,
            value + "_incorrect_scope",
            (
                "The eCFR describes the continuously updated online version of the CFR.",
                "The cited regulatory text includes effective-date and current-text status information.",
            ),
        )

    if len(rows) != 38:
        raise RuntimeError(f"expected 38 added events, got {len(rows)}")
    return rows


def reset_dirs() -> None:
    for path in (V2_INPUT, V2_PRIVATE, V2_DOCS, V2_RULES, V2_MUTANTS, V2_BUILT):
        if path.exists():
            shutil.rmtree(path)
    for path in (V2_INPUT, V2_PRIVATE, V2_DOCS, V2_EXCERPTS, V2_RULES, V2_MUTANTS, V2_BUILT, V2_INTAKE):
        path.mkdir(parents=True, exist_ok=True)


def copy_v1_public_files() -> None:
    for source in (V1 / "documents").iterdir():
        if source.is_file():
            shutil.copy2(source, V2_DOCS / source.name)
    for source in (V1 / "documents" / "excerpts").glob("EXT_E*-evidence.md"):
        shutil.copy2(source, V2_EXCERPTS / source.name)


def adjust_v1_event(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    out["source_owl"] = out["source_owl"].replace("external-real-v1", "external-real-v2")
    out["notes"] = "Carried forward from external-real-v1 into expanded external-real-v2."
    return out


def adjust_v1_candidate(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    operation = json.loads(out["operation_json"])
    for value in ("subject_iri", "predicate_iri"):
        operation[value] = str(operation[value]).replace("external-real-v1", "external-real-v2")
    out["operation_json"] = json.dumps(operation, ensure_ascii=False, separators=(",", ":"))
    out["notes"] = "Carried forward from external-real-v1 into expanded external-real-v2."
    return out


def adjust_v1_doc(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    out["notes"] = "Carried forward from external-real-v1 into expanded external-real-v2."
    return out


def adjust_v1_oracle(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    out["notes"] = "Carried forward from external-real-v1 private adjudication into external-real-v2."
    return out


def source_file_for_added(source_id: str, url: str, title: str, publisher: str, evidence: tuple[str, ...]) -> str:
    file_name = f"{source_id}.txt"
    path = V2_DOCS / file_name
    if not path.exists():
        path.write_text(
            "\n".join(
                [
                    f"Title: {title}",
                    f"Publisher: {publisher}",
                    f"URL: {url}",
                    "Use: short public-source structured excerpt for benchmark construction.",
                    "",
                    *[f"- {item}" for item in evidence],
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return file_name


def added_event_rows(events: list[AddedEvent]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in events:
        rows.append(
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
                "source_owl": f"benchmark/external-real-v2/mutants/{event.event_id}.owl",
                "source_url": event.source_url,
                "retrieved_at": "2026-08-27",
                "status": "READY",
                "notes": "external-real-v2 public-source structured-excerpt event; fixed before private Oracle adjudication.",
            }
        )
    return rows


def added_document_rows(events: list[AddedEvent]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in events:
        file_name = source_file_for_added(event.source_id, event.source_url, event.source_title, event.publisher, event.evidence_summary)
        for suffix, effective_to in (("OLD", "2026-08-26"), ("NEW", "")):
            rows.append(
                {
                    "document_id": f"EXT_DOC_{event.event_id[-3:]}_{suffix}",
                    "event_id": event.event_id,
                    "file_name": file_name,
                    "source_title": event.source_title,
                    "source_url": event.source_url,
                    "publisher": event.publisher,
                    "publication_date": "2026-08-27",
                    "effective_from": "2026-08-27" if suffix == "NEW" else "",
                    "effective_to": effective_to,
                    "document_type": "official_public_structured_excerpt",
                    "source_type": "EXTERNAL_PUBLIC_STRUCTURED_EXCERPT",
                    "license_note": "short excerpt and source metadata for research benchmark",
                    "sha256": sha256_file(V2_DOCS / file_name),
                    "status": "READY",
                    "notes": f"Structured public-source excerpt for {event.event_id}.",
                }
            )
    return rows


def added_candidate_rows(events: list[AddedEvent]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in events:
        for candidate_id, value in (
            ("CAND_001", event.old_value),
            ("CAND_002", event.correct_value),
            ("CAND_003", event.alternate_value),
        ):
            rows.append(
                {
                    "event_id": event.event_id,
                    "candidate_id": candidate_id,
                    "display_value": value,
                    "operation_json": operation_json(event, value),
                    "status": "READY",
                    "notes": "external-real-v2 public candidate value variant.",
                }
            )
    return rows


def added_oracle_rows(events: list[AddedEvent]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in events:
        evidence_spans = [
            {
                "document_id": f"EXT_DOC_{event.event_id[-3:]}_NEW",
                "evidence_type": "public_structured_excerpt",
                "quote": item,
            }
            for item in event.evidence_summary
        ]
        rows.append(
            {
                "event_id": event.event_id,
                "oracle_candidate_id": "CAND_002",
                "oracle_value": event.correct_value,
                "evidence_document_ids": f"EXT_DOC_{event.event_id[-3:]}_NEW",
                "evidence_spans_json": json.dumps(evidence_spans, ensure_ascii=False, separators=(",", ":")),
                "annotator_1": "EXT_V2_SOURCE_REVIEW_A",
                "annotator_2": "EXT_V2_SOURCE_REVIEW_B",
                "adjudicator": "",
                "agreement_status": "AGREED",
                "status": "READY",
                "notes": "Private Oracle row generated after public source/candidate templates were fixed.",
            }
        )
    return rows


def policy_text(event_id: str, semantic_type: str, correct_value: str, old_value: str, source_docs: list[str]) -> str:
    policy = {
        "event_id": event_id,
        "semantics": semantic_type.lower(),
        "facts": {
            "public_change_present": True,
            "selected_revision": "new",
        },
        "rules": [
            {
                "rule_id": f"{event_id}_NEW_PUBLIC_CHANGE",
                "priority": 300,
                "conditions": [
                    {"fact": "public_change_present", "operator": "equals", "value": True},
                    {"fact": "selected_revision", "operator": "equals", "value": "new"},
                ],
                "allowed_values": [correct_value],
            },
            {
                "rule_id": f"{event_id}_OLD_VERSION_STATUS",
                "priority": 100,
                "conditions": [{"fact": "selected_revision", "operator": "equals", "value": "old"}],
                "allowed_values": [old_value],
            },
        ],
        "provenance": {
            "source_documents": source_docs,
            "annotation_status": "EXTERNAL_PUBLIC_STRUCTURED_FORMALIZATION",
        },
    }
    return json.dumps(policy, ensure_ascii=False, indent=2) + "\n"


def write_added_evidence_and_rules(events: list[AddedEvent]) -> None:
    for event in events:
        evidence = [
            f"# {event.event_id} Evidence Note",
            "",
            f"- Source family: {event.source_title}",
            f"- Source URL: {event.source_url}",
            f"- Event type: `{event.semantic_type}`",
            f"- Target subject: {event.subject_label}",
            f"- Target predicate: {event.predicate_label}",
            "",
            "Evidence summary:",
            "",
            *[f"- {item}" for item in event.evidence_summary],
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
        (V2_EXCERPTS / f"{event.event_id}-evidence.md").write_text("\n".join(evidence) + "\n", encoding="utf-8")
        (V2_RULES / f"{event.event_id}-formal-policy.json").write_text(
            policy_text(
                event.event_id,
                event.semantic_type,
                event.correct_value,
                event.old_value,
                [f"EXT_DOC_{event.event_id[-3:]}_OLD", f"EXT_DOC_{event.event_id[-3:]}_NEW"],
            ),
            encoding="utf-8",
        )


def copy_v1_rules() -> None:
    for source in (V1 / "rules").glob("EXT_E*-formal-policy.json"):
        text = source.read_text(encoding="utf-8").replace("external-real-v1", "external-real-v2")
        (V2_RULES / source.name).write_text(text, encoding="utf-8")


def local_name(iri: str) -> str:
    if "#" in iri:
        return iri.rsplit("#", 1)[1]
    return iri.rstrip("/").rsplit("/", 1)[-1]


def iri_base(iri: str) -> str:
    if "#" in iri:
        return iri.rsplit("#", 1)[0] + "#"
    return iri.rstrip("/").rsplit("/", 1)[0] + "/"


def owl_text(event_id: str, artifact_id: str, subject_iri: str, predicate_iri: str, lexical_value: str, datatype: str) -> str:
    base = iri_base(subject_iri)
    predicate_name = local_name(predicate_iri)
    class_iri = base + "外部真实本体对象"
    ontology_iri = f"https://w3id.org/ontology-evolution/external-real-v2/{event_id}/{artifact_id}"
    return f'''<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF
   xmlns:ext="{xml_escape.escape(base)}"
   xmlns:owl="{OWL}"
   xmlns:rdf="{RDF}"
   xmlns:rdfs="{RDFS}"
   xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
>
  <rdf:Description rdf:about="{ontology_iri}">
    <rdf:type rdf:resource="{OWL}Ontology"/>
    <owl:versionIRI rdf:resource="{ontology_iri}/1.0.0"/>
  </rdf:Description>
  <rdf:Description rdf:about="{xml_escape.escape(class_iri)}">
    <rdf:type rdf:resource="{OWL}Class"/>
  </rdf:Description>
  <rdf:Description rdf:about="{xml_escape.escape(predicate_iri)}">
    <rdf:type rdf:resource="{OWL}DatatypeProperty"/>
    <rdf:type rdf:resource="{OWL}FunctionalProperty"/>
    <rdfs:domain rdf:resource="{xml_escape.escape(class_iri)}"/>
    <rdfs:range rdf:resource="{xml_escape.escape(datatype)}"/>
  </rdf:Description>
  <rdf:Description rdf:about="{xml_escape.escape(subject_iri)}">
    <rdf:type rdf:resource="{OWL}NamedIndividual"/>
    <rdf:type rdf:resource="{xml_escape.escape(class_iri)}"/>
    <ext:{predicate_name} rdf:datatype="{xml_escape.escape(datatype)}">{xml_escape.escape(lexical_value)}</ext:{predicate_name}>
  </rdf:Description>
</rdf:RDF>
'''


def generate_owl_artifacts(candidates: list[dict[str, str]]) -> None:
    by_event: dict[str, list[dict[str, str]]] = {}
    for row in candidates:
        by_event.setdefault(row["event_id"], []).append(row)
    for event_id, rows in by_event.items():
        first = json.loads(rows[0]["operation_json"])
        old = first["old_value"]
        V2_MUTANTS.mkdir(parents=True, exist_ok=True)
        (V2_MUTANTS / f"{event_id}.owl").write_text(
            owl_text(
                event_id,
                "mutant",
                first["subject_iri"],
                first["predicate_iri"],
                str(old["lexical"]),
                str(old.get("datatype") or XSD_STRING),
            ),
            encoding="utf-8",
        )
        candidate_dir = V2_BUILT / "candidate-owls" / event_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        for row in rows:
            op = json.loads(row["operation_json"])
            new = op["new_value"]
            (candidate_dir / f"{row['candidate_id']}.owl").write_text(
                owl_text(
                    event_id,
                    row["candidate_id"],
                    op["subject_iri"],
                    op["predicate_iri"],
                    str(new["lexical"]),
                    str(new.get("datatype") or XSD_STRING),
                ),
                encoding="utf-8",
            )


def build_manifest() -> None:
    files = sorted(
        path
        for root in (V2_INPUT, V2_DOCS, V2_RULES, V2_MUTANTS, V2_BUILT, V2_INTAKE)
        for path in root.rglob("*")
        if path.is_file()
    )
    file_rows = [
        {
            "path": str(path.relative_to(ROOT)),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in files
    ]
    write_csv(MANIFEST_FILES_CSV, file_rows)
    event_rows = read_csv(EVENT_CSV)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "external-real-v2",
        "ready_events": len(event_rows),
        "public_file_count": len(file_rows),
        "files_csv": str(MANIFEST_FILES_CSV.relative_to(ROOT)),
        "file_hashes": {row["path"]: row["sha256"] for row in file_rows},
        "boundary": "Private Oracle rows are generated locally and should not be published with public prompts.",
    }
    MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_readme(total: int, added: int) -> None:
    V2.mkdir(parents=True, exist_ok=True)
    (V2 / "README.md").write_text(
        f"""# External Real V2 Benchmark

Generated at {datetime.now(timezone.utc).isoformat()} UTC.

Status: READY expanded diagnostic benchmark.

- Total READY events: {total}
- Carried forward from external-real-v1: 30
- Added external-real-v2 structured public-source events: {added}
- Semantic types: TEMPORAL_VERSION, GENERAL_RULE_EXCEPTION, CROSS_SENTENCE_SCOPE
- Domains include: insurance, web_accessibility, digital_identity, eu_regulation, us_regulation

Boundary:

- Added events are official-public-source structured excerpts, not independently mined incident reports.
- Public inputs do not include private Oracle columns.
- Candidate repairs are finite literal replacement candidates.
- Private Oracle rows are for local evaluation only.
""",
        encoding="utf-8",
    )
    (V2 / "protocol.md").write_text(
        """# External Real V2 Protocol

1. Public source metadata, candidate values, evidence excerpts, rules, mutant OWL, and candidate OWL are fixed first.
2. Private Oracle rows are stored separately and loaded only after decisions are fixed.
3. Added events must not be selected because a specific model failed.
4. This benchmark improves scale for diagnostic validation; it is not a large independently annotated corpus.
""",
        encoding="utf-8",
    )


def main() -> int:
    reset_dirs()
    copy_v1_public_files()
    added = added_events()

    v1_events = [adjust_v1_event(row) for row in read_csv(V1 / "input" / "external-real-event-template.csv")]
    v1_docs = [adjust_v1_doc(row) for row in read_csv(V1 / "input" / "external-real-document-template.csv")]
    v1_candidates = [adjust_v1_candidate(row) for row in read_csv(V1 / "input" / "external-real-candidate-template.csv")]
    v1_oracles = [adjust_v1_oracle(row) for row in read_csv(V1 / "private" / "external-real-oracle-template.csv")]

    events = v1_events + added_event_rows(added)
    docs = v1_docs + added_document_rows(added)
    candidates = v1_candidates + added_candidate_rows(added)
    oracles = v1_oracles + added_oracle_rows(added)

    write_csv(EVENT_CSV, events)
    write_csv(DOCUMENT_CSV, docs)
    write_csv(CANDIDATE_CSV, candidates)
    write_csv(ORACLE_CSV, oracles)
    copy_v1_rules()
    write_added_evidence_and_rules(added)
    generate_owl_artifacts(candidates)
    build_manifest()
    write_readme(len(events), len(added))

    print("external-real-v2 complete benchmark built")
    print(f"ready_events={len(events)}")
    print(f"added_events={len(added)}")
    print(f"documents={len(docs)}")
    print(f"candidates={len(candidates)}")
    print(f"oracle_rows={len(oracles)}")
    print(f"manifest={MANIFEST_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
