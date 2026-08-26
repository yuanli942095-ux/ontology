from __future__ import annotations

"""Run symbolic policy selection and OWL closure for external-real-v1.

This script is intentionally limited to READY external events. The online phase
loads only public event, candidate, and formal-policy files. Private Oracle rows
are loaded after selections are fixed, then used for offline scoring.
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, RDF, URIRef
from rdflib.compare import graph_diff, to_isomorphic
from rdflib.namespace import OWL, XSD

import validate_benchmark as benchmark_validator
from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR, write_csv


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v1"
INPUT_DIR = BENCHMARK_DIR / "input"
PRIVATE_DIR = BENCHMARK_DIR / "private"
RULE_DIR = BENCHMARK_DIR / "rules"
BUILT_DIR = BENCHMARK_DIR / "built"

EVENT_CSV = INPUT_DIR / "external-real-event-template.csv"
CANDIDATE_CSV = INPUT_DIR / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE_DIR / "external-real-oracle-template.csv"

SUPPORTED_POLICY_OPERATORS = {
    "equals",
    "not_equals",
    "on_or_after",
    "on_or_before",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="external-real-v1 symbolic closure")
    parser.add_argument("--only", default="", help="comma-separated event IDs")
    parser.add_argument("--prefix", default="external-real-v1-symbolic-closure")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_ready_events(only: set[str]) -> list[dict[str, str]]:
    rows = [
        row
        for row in read_csv(EVENT_CSV)
        if str(row.get("status", "")).strip().upper() == "READY"
    ]
    if only:
        rows = [row for row in rows if str(row["event_id"]).upper() in only]
    if not rows:
        raise RuntimeError("no READY external events matched")
    return rows


def load_candidates() -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(CANDIDATE_CSV):
        if str(row.get("status", "")).strip().upper() == "READY":
            result[str(row["event_id"])].append(row)
    return result


def load_oracles_after_selection() -> dict[str, dict[str, str]]:
    return {
        str(row["event_id"]): row
        for row in read_csv(ORACLE_CSV)
        if str(row.get("status", "")).strip().upper() == "READY"
    }


def as_date(value: Any) -> date:
    return date.fromisoformat(str(value))


def as_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"not numeric: {value!r}") from exc


def clause_matches(facts: dict[str, Any], clause: dict[str, Any]) -> bool:
    actual = facts[str(clause["fact"])]
    expected = clause["value"]
    operator = str(clause["operator"])
    if operator == "equals":
        return type(actual) is type(expected) and actual == expected
    if operator == "not_equals":
        return not (type(actual) is type(expected) and actual == expected)
    if operator == "on_or_after":
        return as_date(actual) >= as_date(expected)
    if operator == "on_or_before":
        return as_date(actual) <= as_date(expected)
    if operator == "greater_than":
        return as_decimal(actual) > as_decimal(expected)
    if operator == "greater_or_equal":
        return as_decimal(actual) >= as_decimal(expected)
    if operator == "less_than":
        return as_decimal(actual) < as_decimal(expected)
    if operator == "less_or_equal":
        return as_decimal(actual) <= as_decimal(expected)
    raise ValueError(f"unsupported operator: {operator}")


def load_policy(event_id: str, candidate_values: set[str]) -> dict[str, Any]:
    path = RULE_DIR / f"{event_id}-formal-policy.json"
    policy = json.loads(path.read_text(encoding="utf-8-sig"))
    if policy.get("event_id") != event_id:
        raise RuntimeError(f"policy event mismatch for {event_id}")
    facts = policy.get("facts")
    rules = policy.get("rules")
    if not isinstance(facts, dict) or not isinstance(rules, list):
        raise RuntimeError(f"invalid policy shape for {event_id}")
    normalized: list[dict[str, Any]] = []
    for rule in rules:
        conditions = rule.get("conditions")
        allowed_values = [str(value).strip() for value in rule.get("allowed_values", [])]
        if not isinstance(conditions, list) or not allowed_values:
            raise RuntimeError(f"invalid rule in {event_id}: {rule.get('rule_id')}")
        if not set(allowed_values).issubset(candidate_values):
            raise RuntimeError(f"policy references non-candidate values for {event_id}")
        for clause in conditions:
            if set(clause) != {"fact", "operator", "value"}:
                raise RuntimeError(f"invalid condition fields in {event_id}")
            if str(clause["fact"]) not in facts:
                raise RuntimeError(f"unknown policy fact in {event_id}: {clause['fact']}")
            if str(clause["operator"]) not in SUPPORTED_POLICY_OPERATORS:
                raise RuntimeError(f"unsupported policy operator in {event_id}: {clause['operator']}")
        normalized.append(
            {
                "rule_id": str(rule["rule_id"]),
                "priority": int(rule.get("priority", 0)),
                "conditions": conditions,
                "allowed_values": allowed_values,
            }
        )
    return {"facts": facts, "rules": normalized}


def select_rule(policy: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    applicable = [
        rule
        for rule in policy["rules"]
        if all(clause_matches(policy["facts"], clause) for clause in rule["conditions"])
    ]
    if not applicable:
        return None, "no_applicable_rule"
    highest = max(int(rule["priority"]) for rule in applicable)
    winners = [rule for rule in applicable if int(rule["priority"]) == highest]
    if len(winners) != 1:
        return None, "non_unique_highest_priority_rule"
    return winners[0], ""


def load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="xml")
    return graph


def term_from_spec(spec: dict[str, str]) -> URIRef | Literal:
    if spec["kind"] == "literal":
        datatype = URIRef(spec.get("datatype", str(XSD.string)))
        return Literal(spec["lexical"], datatype=datatype)
    if spec["kind"] == "iri":
        return URIRef(spec["iri"])
    raise RuntimeError(f"unsupported term spec: {spec}")


def strip_ontology_metadata(graph: Graph) -> Graph:
    result = Graph()
    ontology_nodes = set(graph.subjects(RDF.type, OWL.Ontology))
    for triple in graph:
        subject, predicate, _ = triple
        if subject in ontology_nodes or predicate == OWL.versionIRI:
            continue
        result.add(triple)
    return result


def graph_delta(source: Graph, candidate: Graph) -> tuple[int, int]:
    _, removed, added = graph_diff(
        to_isomorphic(strip_ontology_metadata(source)),
        to_isomorphic(strip_ontology_metadata(candidate)),
    )
    return len(removed), len(added)


def repair_checks(
    source_graph: Graph, candidate_graph: Graph, operation: dict[str, Any]
) -> tuple[bool, bool, str]:
    if operation.get("operator") != "REPLACE_PROPERTY_VALUE":
        return False, False, f"unsupported_operator={operation.get('operator')}"
    subject = URIRef(operation["subject_iri"])
    predicate = URIRef(operation["predicate_iri"])
    old_triple = (subject, predicate, term_from_spec(operation["old_value"]))
    new_triple = (subject, predicate, term_from_spec(operation["new_value"]))
    source_triggers = old_triple in source_graph and new_triple not in source_graph
    candidate_repairs = old_triple not in candidate_graph and new_triple in candidate_graph
    message = ""
    if not source_triggers:
        message += "source_does_not_trigger_oracle_cq"
    if not candidate_repairs:
        message += (" || " if message else "") + "candidate_does_not_satisfy_repair_cq"
    return source_triggers, candidate_repairs, message


def main() -> int:
    args = parse_args()
    only = {item.strip().upper() for item in args.only.split(",") if item.strip()}
    events = load_ready_events(only)
    candidates_by_event = load_candidates()

    # Online phase: public policy execution only.
    rows: list[dict[str, Any]] = []
    graph_cache: dict[Path, Graph] = {}
    reasoner_cache: dict[Path, dict[str, Any]] = {}
    for event in events:
        event_id = str(event["event_id"])
        candidates = candidates_by_event[event_id]
        candidate_values = {str(candidate["display_value"]).strip() for candidate in candidates}
        policy = load_policy(event_id, candidate_values)
        rule, selection_error = select_rule(policy)
        allowed = set(rule["allowed_values"]) if rule else set()
        survivors = [
            candidate
            for candidate in candidates
            if str(candidate["display_value"]).strip() in allowed
        ]
        selected = survivors[0] if len(survivors) == 1 else None
        if not selection_error and selected is None:
            selection_error = f"survivor_count={len(survivors)}"
        row: dict[str, Any] = {
            "event_id": event_id,
            "semantic_type": event["semantic_type"],
            "selection_status": "SELECTED" if selected else "ABSTAIN",
            "applicable_rule_id": rule["rule_id"] if rule else "",
            "allowed_values": "|".join(sorted(allowed)),
            "survivor_count": len(survivors),
            "selected_candidate_id": selected["candidate_id"] if selected else "",
            "selected_value": selected["display_value"] if selected else "",
            "selection_error": selection_error,
        }
        if selected:
            source_path = PROJECT_DIR / event["source_owl"]
            candidate_path = (
                BUILT_DIR / "candidate-owls" / event_id / f"{selected['candidate_id']}.owl"
            )
            source_graph = graph_cache.setdefault(source_path, load_graph(source_path))
            candidate_graph = graph_cache.setdefault(candidate_path, load_graph(candidate_path))
            reasoner = reasoner_cache.setdefault(
                candidate_path,
                benchmark_validator.run_reasoner(candidate_path, args.timeout),
            )
            operation = json.loads(selected["operation_json"])
            source_trigger, candidate_repair, repair_message = repair_checks(
                source_graph, candidate_graph, operation
            )
            removed, added = graph_delta(source_graph, candidate_graph)
            row.update(
                {
                    "candidate_owl": str(candidate_path.relative_to(PROJECT_DIR)),
                    "triples_removed": removed,
                    "triples_added": added,
                    "minimal_edit_gate": removed == 1 and added == 1,
                    "reasoner_result": reasoner.get("status", ""),
                    "reasoner_runtime_ms": reasoner.get("runtime_ms", 0),
                    "reasoner_gate": reasoner.get("status") == "CONSISTENT",
                    "source_triggers_repair_cq": source_trigger,
                    "candidate_satisfies_repair_cq": candidate_repair,
                    "repair_cq_message": repair_message,
                }
            )
        rows.append(row)

    # Offline scoring phase: private Oracle only after all selections are fixed.
    oracles = load_oracles_after_selection()
    for row in rows:
        oracle = oracles.get(str(row["event_id"]))
        if oracle is None:
            raise RuntimeError(f"missing private oracle for {row['event_id']}")
        row["oracle_loaded_after_selection"] = True
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        row["oracle_value"] = oracle["oracle_value"]
        row["selection_oracle_correct"] = (
            row["selection_status"] == "SELECTED"
            and row["selected_candidate_id"] == oracle["oracle_candidate_id"]
        )
        row["full_closure_success"] = all(
            bool(row.get(key))
            for key in (
                "selection_oracle_correct",
                "minimal_edit_gate",
                "reasoner_gate",
                "source_triggers_repair_cq",
                "candidate_satisfies_repair_cq",
            )
        )

    summary = [
        {
            "benchmark": "external-real-v1",
            "events": len(rows),
            "selected": sum(row["selection_status"] == "SELECTED" for row in rows),
            "oracle_correct": sum(bool(row["selection_oracle_correct"]) for row in rows),
            "full_closure_success": sum(bool(row["full_closure_success"]) for row in rows),
            "oracle_accuracy": (
                sum(bool(row["selection_oracle_correct"]) for row in rows) / len(rows)
                if rows
                else 0
            ),
            "full_closure_accuracy": (
                sum(bool(row["full_closure_success"]) for row in rows) / len(rows)
                if rows
                else 0
            ),
            "oracle_loaded_after_selection": True,
        }
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    details_csv = OUTPUT_DIR / f"{args.prefix}-details.csv"
    summary_csv = OUTPUT_DIR / f"{args.prefix}-summary.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    write_csv(details_csv, rows)
    write_csv(summary_csv, summary)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_loaded_after_selection": True,
        "details": rows,
        "summary": summary,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "external-real-v1 symbolic closure",
        f"events={len(rows)}",
        f"selected={summary[0]['selected']}",
        f"oracle_correct={summary[0]['oracle_correct']}/{len(rows)}",
        f"full_closure_success={summary[0]['full_closure_success']}/{len(rows)}",
        "oracle_loaded_after_selection=True",
        "boundary=one-event external smoke validation; not a full external benchmark result",
        "",
        f"details={details_csv}",
        f"summary={summary_csv}",
        f"json={json_path}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if summary[0]["full_closure_success"] == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
