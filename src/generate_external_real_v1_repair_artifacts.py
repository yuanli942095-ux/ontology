from __future__ import annotations

"""Generate simple OWL mutant and candidate artifacts for external-real-v1.

The generator supports the current external-real-v1 finite repair surface:
`REPLACE_PROPERTY_VALUE` over literal values. It reads public event and candidate
CSV files and writes one mutant OWL plus one candidate OWL per READY candidate.
Private Oracle rows are not read.
"""

import argparse
import csv
import json
import xml.sax.saxutils as xml_escape
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v1"
INPUT_DIR = BENCHMARK_DIR / "input"
MUTANT_DIR = BENCHMARK_DIR / "mutants"
BUILT_DIR = BENCHMARK_DIR / "built"
EVENT_CSV = INPUT_DIR / "external-real-event-template.csv"
CANDIDATE_CSV = INPUT_DIR / "external-real-candidate-template.csv"

OWL = "http://www.w3.org/2002/07/owl#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="generate external-real-v1 OWL artifacts")
    parser.add_argument("--only", default="", help="comma-separated event IDs")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def local_name(iri: str) -> str:
    if "#" in iri:
        return iri.rsplit("#", 1)[1]
    return iri.rstrip("/").rsplit("/", 1)[-1]


def iri_base(iri: str) -> str:
    if "#" in iri:
        return iri.rsplit("#", 1)[0] + "#"
    return iri.rstrip("/").rsplit("/", 1)[0] + "/"


def parse_operation(row: dict[str, str]) -> dict[str, Any]:
    operation = json.loads(row["operation_json"])
    if operation.get("operator") != "REPLACE_PROPERTY_VALUE":
        raise RuntimeError(
            f"{row['event_id']}/{row['candidate_id']} unsupported operator: "
            f"{operation.get('operator')}"
        )
    for key in ("subject_iri", "predicate_iri", "old_value", "new_value"):
        if key not in operation:
            raise RuntimeError(f"{row['event_id']}/{row['candidate_id']} missing {key}")
    for key in ("old_value", "new_value"):
        if operation[key].get("kind") != "literal":
            raise RuntimeError(f"{row['event_id']}/{row['candidate_id']} only literal supported")
    return operation


def owl_text(
    *,
    event_id: str,
    artifact_id: str,
    subject_iri: str,
    predicate_iri: str,
    lexical_value: str,
    datatype: str,
) -> str:
    base = iri_base(subject_iri)
    predicate_name = local_name(predicate_iri)
    class_iri = base + "外部真实本体对象"
    ontology_iri = f"https://w3id.org/ontology-evolution/external-real-v1/{event_id}/{artifact_id}"
    escaped_value = xml_escape.escape(lexical_value)
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
    <ext:{predicate_name} rdf:datatype="{xml_escape.escape(datatype)}">{escaped_value}</ext:{predicate_name}>
  </rdf:Description>
</rdf:RDF>
'''


def main() -> int:
    args = parse_args()
    only = {item.strip().upper() for item in args.only.split(",") if item.strip()}
    ready_events = {
        row["event_id"]: row
        for row in read_csv(EVENT_CSV)
        if str(row.get("status", "")).strip().upper() == "READY"
    }
    if only:
        ready_events = {
            event_id: row for event_id, row in ready_events.items() if event_id.upper() in only
        }
    if not ready_events:
        raise RuntimeError("no READY events matched")

    candidates_by_event: dict[str, list[dict[str, str]]] = {event_id: [] for event_id in ready_events}
    for row in read_csv(CANDIDATE_CSV):
        if (
            row.get("event_id") in candidates_by_event
            and str(row.get("status", "")).strip().upper() == "READY"
        ):
            candidates_by_event[row["event_id"]].append(row)

    generated: list[str] = []
    for event_id, candidates in candidates_by_event.items():
        if not candidates:
            raise RuntimeError(f"{event_id} has no READY candidates")
        first_operation = parse_operation(candidates[0])
        old_value = first_operation["old_value"]
        datatype = str(old_value.get("datatype") or XSD_STRING)
        source_text = owl_text(
            event_id=event_id,
            artifact_id="mutant",
            subject_iri=first_operation["subject_iri"],
            predicate_iri=first_operation["predicate_iri"],
            lexical_value=str(old_value["lexical"]),
            datatype=datatype,
        )
        mutant_path = MUTANT_DIR / f"{event_id}.owl"
        mutant_path.parent.mkdir(parents=True, exist_ok=True)
        mutant_path.write_text(source_text, encoding="utf-8")
        generated.append(str(mutant_path.relative_to(PROJECT_DIR)))

        candidate_dir = BUILT_DIR / "candidate-owls" / event_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        for candidate in candidates:
            operation = parse_operation(candidate)
            new_value = operation["new_value"]
            datatype = str(new_value.get("datatype") or XSD_STRING)
            candidate_text = owl_text(
                event_id=event_id,
                artifact_id=str(candidate["candidate_id"]),
                subject_iri=operation["subject_iri"],
                predicate_iri=operation["predicate_iri"],
                lexical_value=str(new_value["lexical"]),
                datatype=datatype,
            )
            candidate_path = candidate_dir / f"{candidate['candidate_id']}.owl"
            candidate_path.write_text(candidate_text, encoding="utf-8")
            generated.append(str(candidate_path.relative_to(PROJECT_DIR)))

    print("generated external-real-v1 OWL artifacts")
    for path in generated:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
