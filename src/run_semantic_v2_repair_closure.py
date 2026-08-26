from __future__ import annotations

"""Validate semantic-v2 selections as OWL repair closures.

This script turns candidate-selection results into an end-to-end repair audit:

selection -> candidate OWL artifact -> Reasoner -> selected-operation CQ ->
offline Oracle repair CQ.

Oracle labels are loaded only after the selection detail file has been read.
They are used for offline repair scoring, not for candidate generation or
selection.
"""

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, RDF, RDFS, URIRef

import validate_benchmark as benchmark_validator
from run_semantic_benchmark_v2 import load_oracle_after_predictions, load_public_events
from semantic_v2_common import (
    BUILD_DIR,
    OUTPUT_DIR,
    PROJECT_DIR,
    load_csv,
    resolve_project_path,
    write_csv,
)
from repair_operators import spec_to_term


DEFAULT_DETAILS = OUTPUT_DIR / "final-main-table-test-r5-seed20260820-details.csv"
DEFAULT_EFFECTS = BUILD_DIR / "candidate-effects.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="semantic-v2 OWL修复闭环验证")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--effects", type=Path, default=DEFAULT_EFFECTS)
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument(
        "--methods",
        default=(
            "DIRECT_FREE,OPTION_VALUE_ONLY,OPTION_FORMAL_OPERATION,"
            "OPTION_FORMAL_POLICY,OPTION_FORMAL_POLICY_HARD_GATE"
        ),
        help="逗号分隔的方法名",
    )
    parser.add_argument("--prefix", default="semantic-v2-repair-closure-test-r5-seed20260820")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_int(value: object) -> int:
    try:
        return int(float(str(value).strip() or "0"))
    except (TypeError, ValueError):
        return 0


def load_effects(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    for row in load_csv(path):
        result[(row["event_id"], row["candidate_id"])] = row
    return result


def load_selection_rows(path: Path, methods: set[str], split: str) -> list[dict[str, str]]:
    rows = [
        row
        for row in load_csv(path)
        if str(row.get("method", "")).upper() in methods
        and (split == "all" or str(row.get("split", "")).lower() == split)
    ]
    if not rows:
        raise RuntimeError(f"no rows matched methods={sorted(methods)} split={split}")
    return rows


def term_from_spec(graph: Graph, spec: dict[str, str]) -> URIRef | Literal:
    return spec_to_term(graph, spec, require_existing_iri_value=False)


def graph_has(graph: Graph, triple: tuple[Any, Any, Any]) -> bool:
    return triple in graph


def triple_checks_for_operation(
    graph: Graph, operation: dict[str, Any]
) -> tuple[list[tuple[str, tuple[Any, Any, Any], bool]], str]:
    operator = str(operation.get("operator", ""))
    checks: list[tuple[str, tuple[Any, Any, Any], bool]] = []
    if operator in {"REPLACE_PROPERTY_VALUE", "ADD_PROPERTY_VALUE", "REMOVE_PROPERTY_VALUE"}:
        subject = URIRef(operation["subject_iri"])
        predicate = URIRef(operation["predicate_iri"])
        if operator in {"REPLACE_PROPERTY_VALUE", "REMOVE_PROPERTY_VALUE"}:
            old_term = term_from_spec(graph, operation["old_value"])
            checks.append(("old_value_absent", (subject, predicate, old_term), False))
        if operator in {"REPLACE_PROPERTY_VALUE", "ADD_PROPERTY_VALUE"}:
            new_term = term_from_spec(graph, operation["new_value"])
            checks.append(("new_value_present", (subject, predicate, new_term), True))
        return checks, ""
    if operator in {"ADD_CLASS_ASSERTION", "REMOVE_CLASS_ASSERTION"}:
        triple = (URIRef(operation["subject_iri"]), RDF.type, URIRef(operation["class_iri"]))
        checks.append(("class_assertion_present" if operator.startswith("ADD") else "class_assertion_absent", triple, operator.startswith("ADD")))
        return checks, ""
    if operator in {"ADD_SUBCLASS_AXIOM", "REMOVE_SUBCLASS_AXIOM"}:
        triple = (
            URIRef(operation["subclass_iri"]),
            RDFS.subClassOf,
            URIRef(operation["superclass_iri"]),
        )
        checks.append(("subclass_axiom_present" if operator.startswith("ADD") else "subclass_axiom_absent", triple, operator.startswith("ADD")))
        return checks, ""
    if operator == "REPLACE_SUPERCLASS":
        subclass = URIRef(operation["subclass_iri"])
        checks.append(
            (
                "old_superclass_absent",
                (subclass, RDFS.subClassOf, URIRef(operation["old_superclass_iri"])),
                False,
            )
        )
        checks.append(
            (
                "new_superclass_present",
                (subclass, RDFS.subClassOf, URIRef(operation["new_superclass_iri"])),
                True,
            )
        )
        return checks, ""
    if operator in {"ADD_PROPERTY_DOMAIN", "REMOVE_PROPERTY_DOMAIN"}:
        triple = (
            URIRef(operation["property_iri"]),
            RDFS.domain,
            URIRef(operation["domain_iri"]),
        )
        checks.append(("domain_present" if operator.startswith("ADD") else "domain_absent", triple, operator.startswith("ADD")))
        return checks, ""
    if operator == "REPLACE_PROPERTY_DOMAIN":
        prop = URIRef(operation["property_iri"])
        checks.append(("old_domain_absent", (prop, RDFS.domain, URIRef(operation["old_domain_iri"])), False))
        checks.append(("new_domain_present", (prop, RDFS.domain, URIRef(operation["new_domain_iri"])), True))
        return checks, ""
    if operator in {"ADD_PROPERTY_RANGE", "REMOVE_PROPERTY_RANGE"}:
        triple = (
            URIRef(operation["property_iri"]),
            RDFS.range,
            URIRef(operation["range_iri"]),
        )
        checks.append(("range_present" if operator.startswith("ADD") else "range_absent", triple, operator.startswith("ADD")))
        return checks, ""
    if operator == "REPLACE_PROPERTY_RANGE":
        prop = URIRef(operation["property_iri"])
        checks.append(("old_range_absent", (prop, RDFS.range, URIRef(operation["old_range_iri"])), False))
        checks.append(("new_range_present", (prop, RDFS.range, URIRef(operation["new_range_iri"])), True))
        return checks, ""
    return checks, f"unsupported_operator={operator}"


def evaluate_checks(graph: Graph, checks: list[tuple[str, tuple[Any, Any, Any], bool]]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for name, triple, expected_present in checks:
        present = graph_has(graph, triple)
        if present != expected_present:
            failures.append(f"{name}: expected_present={expected_present}, actual_present={present}")
    return not failures, failures


def source_trigger_for_oracle(source_graph: Graph, oracle_operation: dict[str, Any]) -> tuple[bool, str]:
    checks, unsupported = triple_checks_for_operation(source_graph, oracle_operation)
    if unsupported:
        return False, unsupported
    # A repair-triggering mutant should fail the post-repair oracle checks.
    pass_now, _ = evaluate_checks(source_graph, checks)
    return not pass_now, "" if not pass_now else "source_already_satisfies_oracle_cq"


def load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="xml")
    return graph


def selected_candidate(event: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    for candidate in event.get("candidates", []):
        if candidate.get("candidate_id") == candidate_id:
            return candidate
    return None


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["method"]), "ALL")].append(row)
        groups[(str(row["method"]), str(row["semantic_type"]))].append(row)

    result: list[dict[str, Any]] = []
    for (method, semantic_type), subset in sorted(groups.items()):
        attempts = len(subset)
        selected = [row for row in subset if row["selection_status"] == "SELECTED"]
        full_success = sum(parse_bool(row["full_repair_closure_success"]) for row in subset)
        reasoner_pass = sum(parse_bool(row["reasoner_gate"]) for row in selected)
        selected_cq_pass = sum(parse_bool(row["selected_operation_cq_pass"]) for row in selected)
        oracle_cq_pass = sum(parse_bool(row["oracle_repair_cq_pass"]) for row in selected)
        mutant_triggered = sum(parse_bool(row["mutant_oracle_cq_fail_before_repair"]) for row in subset)
        runtimes = [parse_int(row["reasoner_runtime_ms"]) for row in selected if parse_int(row["reasoner_runtime_ms"]) > 0]
        result.append(
            {
                "method": method,
                "semantic_type": semantic_type,
                "attempts": attempts,
                "selected_repairs": len(selected),
                "repair_coverage": len(selected) / attempts if attempts else 0,
                "reasoner_pass_rate_selected": reasoner_pass / len(selected) if selected else 0,
                "selected_operation_cq_pass_rate": selected_cq_pass / len(selected) if selected else 0,
                "oracle_repair_cq_pass_rate_selected": oracle_cq_pass / len(selected) if selected else 0,
                "full_repair_closure_accuracy": full_success / attempts if attempts else 0,
                "mutant_oracle_cq_trigger_rate": mutant_triggered / attempts if attempts else 0,
                "mean_reasoner_runtime_ms_selected": round(statistics.mean(runtimes), 2) if runtimes else 0,
            }
        )
    return result


def by_event(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["method"]), str(row["event_id"]))].append(row)
    result: list[dict[str, Any]] = []
    for (method, event_id), subset in sorted(groups.items()):
        attempts = len(subset)
        successes = sum(parse_bool(row["full_repair_closure_success"]) for row in subset)
        result.append(
            {
                "method": method,
                "event_id": event_id,
                "semantic_type": subset[0]["semantic_type"],
                "attempts": attempts,
                "full_repair_successes": successes,
                "full_repair_closure_accuracy": successes / attempts if attempts else 0,
                "strict_event_repair_success": successes == attempts,
                "selected_repairs": sum(row["selection_status"] == "SELECTED" for row in subset),
                "failed_runs": attempts - successes,
            }
        )
    return result


def main() -> int:
    args = parse_args()
    methods = {item.strip().upper() for item in args.methods.split(",") if item.strip()}
    events = {event["event_id"]: event for event in load_public_events(args.split)}
    effects = load_effects(args.effects)
    selection_rows = load_selection_rows(args.details, methods, args.split)
    oracles = load_oracle_after_predictions()

    reasoner_cache: dict[Path, dict[str, Any]] = {}
    graph_cache: dict[Path, Graph] = {}

    rows: list[dict[str, Any]] = []
    for index, selection in enumerate(selection_rows, start=1):
        event_id = selection["event_id"]
        event = events[event_id]
        oracle = oracles[event_id]
        oracle_candidate_id = oracle["oracle_candidate_id"]
        oracle_candidate = selected_candidate(event, oracle_candidate_id)
        if oracle_candidate is None:
            raise RuntimeError(f"{event_id} oracle candidate missing from public event")
        oracle_operation = oracle_candidate["operation"]
        source_path = resolve_project_path(event["source_owl"])
        source_graph = graph_cache.setdefault(source_path, load_graph(source_path))
        mutant_trigger, mutant_trigger_error = source_trigger_for_oracle(source_graph, oracle_operation)

        row: dict[str, Any] = {
            "row_index": index,
            "method": selection["method"],
            "event_id": event_id,
            "semantic_type": event["semantic_type"],
            "run": selection.get("run", ""),
            "selection_status": selection.get("status", ""),
            "selected_candidate_id": selection.get("selected_candidate_id", ""),
            "selected_value": selection.get("selected_value", ""),
            "oracle_candidate_id": oracle_candidate_id,
            "oracle_value": oracle["oracle_value"],
            "selection_oracle_correct": parse_bool(selection.get("oracle_correct", "")),
            "source_owl": event["source_owl"],
            "mutant_oracle_cq_fail_before_repair": mutant_trigger,
            "mutant_oracle_cq_error": mutant_trigger_error,
            "candidate_owl": "",
            "repair_operator": "",
            "triples_removed": "",
            "triples_added": "",
            "minimal_edit_gate": False,
            "reasoner_result": "",
            "reasoner_runtime_ms": 0,
            "reasoner_gate": False,
            "selected_operation_cq_pass": False,
            "selected_operation_cq_failures": "",
            "oracle_repair_cq_pass": False,
            "oracle_repair_cq_failures": "",
            "full_repair_closure_success": False,
            "closure_failure_reason": "",
        }

        if selection.get("status") != "SELECTED":
            row["closure_failure_reason"] = f"selection_status={selection.get('status')}"
            rows.append(row)
            continue

        candidate_id = selection.get("selected_candidate_id", "")
        candidate = selected_candidate(event, candidate_id)
        effect = effects.get((event_id, candidate_id))
        if candidate is None or effect is None:
            row["closure_failure_reason"] = "selected candidate/effect missing"
            rows.append(row)
            continue

        candidate_path = resolve_project_path(effect["candidate_owl"])
        candidate_graph = graph_cache.setdefault(candidate_path, load_graph(candidate_path))
        if candidate_path not in reasoner_cache:
            reasoner_cache[candidate_path] = benchmark_validator.run_reasoner(
                candidate_path, args.timeout
            )
        reasoner = reasoner_cache[candidate_path]

        selected_checks, selected_unsupported = triple_checks_for_operation(
            candidate_graph, candidate["operation"]
        )
        selected_cq_pass, selected_failures = (
            (False, [selected_unsupported])
            if selected_unsupported
            else evaluate_checks(candidate_graph, selected_checks)
        )
        oracle_checks, oracle_unsupported = triple_checks_for_operation(
            candidate_graph, oracle_operation
        )
        oracle_cq_pass, oracle_failures = (
            (False, [oracle_unsupported])
            if oracle_unsupported
            else evaluate_checks(candidate_graph, oracle_checks)
        )

        row.update(
            {
                "candidate_owl": effect["candidate_owl"],
                "repair_operator": effect["operator"],
                "triples_removed": effect["triples_removed"],
                "triples_added": effect["triples_added"],
                "minimal_edit_gate": parse_bool(effect["minimal_edit"]),
                "reasoner_result": reasoner.get("status", ""),
                "reasoner_runtime_ms": reasoner.get("runtime_ms", 0),
                "reasoner_gate": reasoner.get("status") == "CONSISTENT",
                "selected_operation_cq_pass": selected_cq_pass,
                "selected_operation_cq_failures": " || ".join(selected_failures),
                "oracle_repair_cq_pass": oracle_cq_pass,
                "oracle_repair_cq_failures": " || ".join(oracle_failures),
            }
        )
        gates = {
            "selection_oracle_correct": row["selection_oracle_correct"],
            "mutant_oracle_cq_fail_before_repair": row["mutant_oracle_cq_fail_before_repair"],
            "minimal_edit_gate": row["minimal_edit_gate"],
            "reasoner_gate": row["reasoner_gate"],
            "selected_operation_cq_pass": row["selected_operation_cq_pass"],
            "oracle_repair_cq_pass": row["oracle_repair_cq_pass"],
        }
        row["full_repair_closure_success"] = all(gates.values())
        row["closure_failure_reason"] = " || ".join(
            key for key, passed in gates.items() if not passed
        )
        rows.append(row)

    summary = summarize(rows)
    event_rows = by_event(rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    detail_csv = OUTPUT_DIR / f"{args.prefix}-details.csv"
    summary_csv = OUTPUT_DIR / f"{args.prefix}-summary.csv"
    by_event_csv = OUTPUT_DIR / f"{args.prefix}-by-event.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    write_csv(detail_csv, rows)
    write_csv(summary_csv, summary)
    write_csv(by_event_csv, event_rows)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_loaded_after_selection_rows": True,
        "source_details": str(args.details),
        "source_effects": str(args.effects),
        "split": args.split,
        "methods": sorted(methods),
        "reasoner_cache_size": len(reasoner_cache),
        "details": rows,
        "summary": summary,
        "by_event": event_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "semantic-v2 repair closure",
        f"split={args.split}",
        f"methods={','.join(sorted(methods))}",
        f"rows={len(rows)}",
        f"unique_reasoner_runs={len(reasoner_cache)}",
        "oracle_loaded_after_selection_rows=True",
        "",
    ]
    for row in summary:
        if row["semantic_type"] != "ALL":
            continue
        lines.append(
            "[{method}] full_repair={full_repair_closure_accuracy:.2%} | "
            "coverage={repair_coverage:.2%} | reasoner_selected={reasoner_pass_rate_selected:.2%} | "
            "selected_cq={selected_operation_cq_pass_rate:.2%} | oracle_cq_selected={oracle_repair_cq_pass_rate_selected:.2%}".format(**row)
        )
    lines.extend(["", f"details={detail_csv}", f"summary={summary_csv}", f"by_event={by_event_csv}", f"json={json_path}"])
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
