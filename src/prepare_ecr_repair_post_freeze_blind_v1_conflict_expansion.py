from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmark" / "ecr-repair-post-freeze-blind-v1"
RAW = BENCHMARK / "public" / "documents" / "raw"
TEXT = BENCHMARK / "public" / "documents" / "text"
OUT = BENCHMARK / "private" / "construction" / "conflict-expansion"
BASE = "https://w3id.org/ontology-evolution/ecr-repair-post-freeze-blind-v1#"


SOURCES = {
    "DOC_WHO_GDM_2013": {
        "title": "Diagnostic criteria and classification of hyperglycaemia first detected in pregnancy",
        "issuer": "World Health Organization",
        "document_type": "WHO guideline PDF",
        "publication_date": "2013-01-01",
        "url": "https://iris.who.int/server/api/core/bitstreams/612e0faa-04b5-4984-abcb-5fe3b1703677/content",
        "raw": "DOC_WHO_GDM_2013.pdf",
        "text": "DOC_WHO_GDM_2013.txt",
    },
    "DOC_NICE_NG3": {
        "title": "Diabetes in pregnancy: management from preconception to the postnatal period (NG3)",
        "issuer": "National Institute for Health and Care Excellence",
        "document_type": "Official guideline recommendations HTML snapshot",
        "publication_date": "2015-02-25",
        "url": "https://www.nice.org.uk/guidance/ng3/chapter/recommendations",
        "raw": "DOC_NICE_NG3.html",
        "text": "DOC_NICE_NG3.txt",
    },
    "DOC_WHO_HYPERTENSION_FACT": {
        "title": "Hypertension fact sheet",
        "issuer": "World Health Organization",
        "document_type": "Official WHO fact sheet HTML snapshot",
        "publication_date": "2025-09-25",
        "url": "https://www.who.int/news-room/fact-sheets/detail/hypertension",
        "raw": "DOC_WHO_HYPERTENSION_FACT.html",
        "text": "DOC_WHO_HYPERTENSION_FACT.txt",
    },
    "DOC_AHA_BP_2025_NEWS": {
        "title": "New high blood pressure guideline emphasizes prevention, early treatment to reduce CVD risk",
        "issuer": "American Heart Association",
        "document_type": "Official issuing-organization guideline news release HTML snapshot",
        "publication_date": "2025-08-14",
        "url": "https://newsroom.heart.org/news/new-high-blood-pressure-guideline-emphasizes-prevention-early-treatment-to-reduce-cvd-risk",
        "raw": "DOC_AHA_BP_2025_NEWS.html",
        "text": "DOC_AHA_BP_2025_NEWS.txt",
    },
}


EVENTS = [
    {
        "event_id": "BLIND_E081",
        "domain": "clinical_guideline_transfer",
        "title": "Discordant fasting thresholds for gestational diabetes",
        "case_context": (
            "A pregnant patient at 24-28 weeks has a 75-g OGTT fasting plasma glucose of "
            "5.3 mmol/L and a 2-hour value of 7.0 mmol/L. The ontology records that both "
            "named guidance systems apply but records no jurisdiction or precedence rule."
        ),
        "document_ids": ["DOC_WHO_GDM_2013", "DOC_NICE_NG3"],
        "subject": "pregnant patient with 75-g OGTT fasting 5.3 mmol/L and 2-hour 7.0 mmol/L",
        "predicate": "gestational diabetes diagnosis",
        "predicate_slug": "GestationalDiabetesDiagnosis081",
        "old_value": "not diagnosed with gestational diabetes",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "source_a_outcome": "GDM under WHO because fasting plasma glucose is at least 5.1 mmol/L",
        "source_b_outcome": "No GDM under NICE because fasting is below 5.6 and 2-hour is below 7.8 mmol/L",
        "windows": [
            ("DOC_WHO_GDM_2013", 1238, 1255),
            ("DOC_NICE_NG3", 291, 309),
            ("DOC_WHO_GDM_2013", 1256, 1266),
            ("DOC_NICE_NG3", 281, 290),
        ],
    },
    {
        "event_id": "BLIND_E082",
        "domain": "clinical_guideline_transfer",
        "title": "Discordant two-hour thresholds for gestational diabetes",
        "case_context": (
            "A pregnant patient at 24-28 weeks has a 75-g OGTT fasting plasma glucose of "
            "5.0 mmol/L and a 2-hour value of 8.0 mmol/L. The ontology records that both "
            "named guidance systems apply but records no jurisdiction or precedence rule."
        ),
        "document_ids": ["DOC_WHO_GDM_2013", "DOC_NICE_NG3"],
        "subject": "pregnant patient with 75-g OGTT fasting 5.0 mmol/L and 2-hour 8.0 mmol/L",
        "predicate": "gestational diabetes diagnosis",
        "predicate_slug": "GestationalDiabetesDiagnosis082",
        "old_value": "diagnosed with gestational diabetes",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "source_a_outcome": "No GDM under WHO because fasting is below 5.1 and 2-hour is below 8.5 mmol/L",
        "source_b_outcome": "GDM under NICE because 2-hour plasma glucose is at least 7.8 mmol/L",
        "windows": [
            ("DOC_NICE_NG3", 291, 309),
            ("DOC_WHO_GDM_2013", 1238, 1255),
            ("DOC_NICE_NG3", 281, 290),
            ("DOC_WHO_GDM_2013", 1256, 1266),
        ],
    },
    {
        "event_id": "BLIND_E083",
        "domain": "clinical_guideline_transfer",
        "title": "Discordant adult hypertension classification thresholds",
        "case_context": (
            "An adult has office blood pressure readings of 135/85 mm Hg on two different "
            "days. The ontology records that both named classification systems apply but "
            "records no jurisdiction or precedence rule."
        ),
        "document_ids": ["DOC_WHO_HYPERTENSION_FACT", "DOC_AHA_BP_2025_NEWS"],
        "subject": "adult with office blood pressure 135/85 mm Hg on two different days",
        "predicate": "hypertension diagnosis or classification",
        "predicate_slug": "HypertensionClassification083",
        "old_value": "not hypertension",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "source_a_outcome": "Not hypertension under the WHO 140/90 mm Hg diagnostic threshold",
        "source_b_outcome": "Stage 1 hypertension under the AHA/ACC 130-139 or 80-89 mm Hg criteria",
        "windows": [
            ("DOC_WHO_HYPERTENSION_FACT", 32, 61),
            ("DOC_AHA_BP_2025_NEWS", 44, 60),
            ("DOC_WHO_HYPERTENSION_FACT", 96, 103),
            ("DOC_AHA_BP_2025_NEWS", 38, 45),
        ],
    },
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def slice_lines(path: Path, start: int, end: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not (1 <= start <= end <= len(lines)):
        raise ValueError(f"Invalid line range {path}:{start}-{end}; total={len(lines)}")
    return "\n".join(lines[start - 1 : end]).strip()


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def ontology(event: dict[str, object]) -> str:
    event_id = str(event["event_id"])
    predicate = str(event["predicate_slug"])
    old_value = str(event["old_value"])
    return f'''<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns:blind="{BASE}" xmlns:owl="http://www.w3.org/2002/07/owl#" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#" xmlns:xsd="http://www.w3.org/2001/XMLSchema#">
  <rdf:Description rdf:about="https://w3id.org/ontology-evolution/ecr-repair-post-freeze-blind-v1/{event_id}/candidate-mutant">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#Ontology"/>
  </rdf:Description>
  <rdf:Description rdf:about="{BASE}BenchmarkObject">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#Class"/>
  </rdf:Description>
  <rdf:Description rdf:about="{BASE}{predicate}">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#DatatypeProperty"/>
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#FunctionalProperty"/>
    <rdfs:domain rdf:resource="{BASE}BenchmarkObject"/>
    <rdfs:range rdf:resource="http://www.w3.org/2001/XMLSchema#string"/>
  </rdf:Description>
  <rdf:Description rdf:about="{BASE}{event_id}">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#NamedIndividual"/>
    <rdf:type rdf:resource="{BASE}BenchmarkObject"/>
    <blind:{predicate} rdf:datatype="http://www.w3.org/2001/XMLSchema#string">{old_value}</blind:{predicate}>
    <blind:regressionSentinel rdf:datatype="http://www.w3.org/2001/XMLSchema#string">preserve</blind:regressionSentinel>
  </rdf:Description>
</rdf:RDF>
'''


def main() -> int:
    evidence_dir = OUT / "evidence"
    mutant_dir = OUT / "mutants"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    mutant_dir.mkdir(parents=True, exist_ok=True)

    source_rows = []
    for document_id, source in SOURCES.items():
        raw_path = RAW / str(source["raw"])
        text_path = TEXT / str(source["text"])
        if not raw_path.is_file() or not text_path.is_file():
            raise FileNotFoundError(f"Missing frozen source for {document_id}")
        source_rows.append({
            "document_id": document_id,
            "official_title": source["title"],
            "issuer": source["issuer"],
            "document_type": source["document_type"],
            "publication_date": source["publication_date"],
            "official_url": source["url"],
            "raw_file": raw_path.relative_to(ROOT).as_posix(),
            "raw_sha256": sha256(raw_path),
            "text_file": text_path.relative_to(ROOT).as_posix(),
            "text_sha256": sha256(text_path),
            "status": "CANDIDATE_NOT_IN_MAIN_MANIFEST",
        })

    write_csv(OUT / "source-candidate-manifest.csv", list(source_rows[0]), source_rows)

    review_rows = []
    event_specs = []
    for event in EVENTS:
        event_id = str(event["event_id"])
        blocks = []
        locators = []
        for index, (document_id, start, end) in enumerate(event["windows"], start=1):
            text_path = TEXT / str(SOURCES[document_id]["text"])
            excerpt = slice_lines(text_path, start, end)
            blocks.append(
                f"[SOURCE_WINDOW_{index}]\n"
                f"document_id: {document_id}\n"
                f"frozen_text_locator: {text_path.relative_to(ROOT).as_posix()}:{start}-{end}\n\n"
                f"{excerpt}"
            )
            locators.append({"window_id": f"SOURCE_WINDOW_{index}", "document_id": document_id, "start_line": start, "end_line": end})
        (evidence_dir / f"{event_id}-evidence.md").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        (mutant_dir / f"{event_id}.owl").write_text(ontology(event), encoding="utf-8")

        event_specs.append({
            "event_id": event_id,
            "status": "CANDIDATE_PENDING_Q_NOT_FROZEN",
            "split": "construction_only",
            "domain": event["domain"],
            "title": event["title"],
            "case_context": event["case_context"],
            "document_ids": event["document_ids"],
            "proposed_partition": "CONFLICTING_EVIDENCE",
            "proposed_semantic_type": event["semantic_type"],
            "target": {"subject_label": event["subject"], "predicate_label": event["predicate"], "value_kind": "literal_string"},
            "source_owl": f"private/construction/conflict-expansion/mutants/{event_id}.owl",
            "evidence_file": f"private/construction/conflict-expansion/evidence/{event_id}-evidence.md",
            "source_a_outcome": event["source_a_outcome"],
            "source_b_outcome": event["source_b_outcome"],
            "window_locators": locators,
        })
        review_rows.append({
            "event_id": event_id,
            "domain": event["domain"],
            "document_ids": "|".join(event["document_ids"]),
            "proposed_partition": "CONFLICTING_EVIDENCE",
            "proposed_semantic_type": event["semantic_type"],
            "subject": event["subject"],
            "predicate": event["predicate"],
            "old_value": event["old_value"],
            "source_a_outcome": event["source_a_outcome"],
            "source_b_outcome": event["source_b_outcome"],
            "normative_not_metadata": "",
            "target_specific": "",
            "same_subject_predicate_time_scope": "",
            "authority_date_scope_or_exception_resolves": "",
            "partition_supported": "",
            "semantic_type_supported": "",
            "mutant_plausible": "",
            "source_quality_adequate": "",
            "reviewer_decision": "",
            "reviewer_notes": "",
        })

    with (OUT / "candidate-events.jsonl").open("w", encoding="utf-8") as handle:
        for event in event_specs:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    write_csv(OUT / "quality-review-template.csv", list(review_rows[0]), review_rows)

    report = {
        "status": "PASS_CANDIDATE_PACKET_ONLY",
        "events": len(EVENTS),
        "sources": len(SOURCES),
        "existing_main_event_count_unchanged": sum(1 for line in (BENCHMARK / "public" / "events" / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()),
        "main_event_files_modified": False,
        "oracle_created": False,
        "freeze_created": False,
    }
    (OUT / "build-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
