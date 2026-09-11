from __future__ import annotations

"""Evaluate predicted RFC-213 repairs through frozen Gamma and CQ closure."""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import validate_benchmark as benchmark_validator
from rdflib import RDF, URIRef

from run_auto_policy_v2_candidate_repair import resolve_source_owl
from run_external_real_v1_symbolic_closure import graph_delta, load_graph, term_from_spec
from run_gamma_gold_ir_closure_eval import materialize_update_literal_atomic
from run_gamma_gold_ir_compile_eval import compile_update_literal
from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "rfc-213-confirmatory-core"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def canonical_operation(operation: dict[str, Any]) -> str:
    return json.dumps(operation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_candidates(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (row["event_id"], row["candidate_id"]): {**row, "operation": json.loads(row["operation_json"])}
        for row in read_csv(path)
        if row.get("status") == "READY"
    }


def summarize(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = len(rows)
    selected = sum(row["decision"] == "SELECT" for row in rows)
    wrong = sum(row["wrong_repair"] for row in rows)
    ses = sum(row["ses_success"] for row in rows)
    events = sorted({row["event_id"] for row in rows})
    event_groups = {event: [row for row in rows if row["event_id"] == event] for event in events}
    strict_events = sum(all(row["ses_success"] for row in items) for items in event_groups.values())
    return {
        "semantic_type": label,
        "events": len(events),
        "attempts": attempts,
        "semantic_ir_valid": sum(row["semantic_ir_valid"] for row in rows),
        "semantic_ir_valid_rate": sum(row["semantic_ir_valid"] for row in rows) / attempts if attempts else 0,
        "predicted_gamma_ir_exact": sum(row["predicted_gamma_ir_exact"] for row in rows),
        "predicted_gamma_ir_accuracy": sum(row["predicted_gamma_ir_exact"] for row in rows) / attempts if attempts else 0,
        "gamma_accepted": sum(row["gamma_accept"] for row in rows),
        "gamma_acceptance_rate": sum(row["gamma_accept"] for row in rows) / attempts if attempts else 0,
        "precondition_failures": sum(row["precondition_failed"] for row in rows),
        "precondition_failure_rate": sum(row["precondition_failed"] for row in rows) / attempts if attempts else 0,
        "selected": selected,
        "coverage": selected / attempts if attempts else 0,
        "wrong_repairs": wrong,
        "wrr": wrong / attempts if attempts else 0,
        "selective_risk": wrong / selected if selected else 0,
        "abstains": sum(row["decision"] == "ABSTAIN" for row in rows),
        "no_change": sum(row["decision"] == "NO_CHANGE" for row in rows),
        "target_cq_pass": sum(row["target_cq_pass"] for row in rows),
        "non_target_cq_preserved": sum(row["non_target_cq_preserved"] for row in rows),
        "source_unchanged": sum(row["source_unchanged"] for row in rows),
        "source_unchanged_rate": sum(row["source_unchanged"] for row in rows) / attempts if attempts else 0,
        "ses_success": ses,
        "ses": ses / attempts if attempts else 0,
        "strict_event_successes": strict_events,
        "strict_event_accuracy": strict_events / len(events) if events else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    benchmark = args.benchmark_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    events = {row["event_id"]: row for row in read_csv(benchmark / "public/events/external-real-event-template.csv")}
    candidates = load_candidates(benchmark / "repair-stage/candidates/external-real-candidate-template.csv")
    gold_ids = {row["event_id"]: row["gold_candidate_id"] for row in read_csv(benchmark / "private/construct-audit/rfc213-gold-repair-construct-audit.csv")}
    rows_out: list[dict[str, Any]] = []
    materialized = output / "materialized-predicted-owl"

    for prediction in read_csv(args.predictions.resolve()):
        event_id = prediction["event_id"]
        event = events[event_id]
        selected_id = prediction.get("selected_candidate_id", "").strip()
        gold_id = gold_ids[event_id]
        gold_operation = candidates[(event_id, gold_id)]["operation"]
        row: dict[str, Any] = {
            "event_id": event_id,
            "semantic_type": event["semantic_type"],
            "domain": event["domain"],
            "run": prediction.get("run", ""),
            "seed": prediction.get("seed", ""),
            "semantic_ir_valid": prediction.get("ir_status") == "OK",
            "decision": "SELECT" if selected_id else "ABSTAIN",
            "selected_candidate_id": selected_id,
            "oracle_candidate_id": gold_id,
            "predicted_gamma_ir_exact": False,
            "gamma_status": "NOT_RUN",
            "gamma_accept": False,
            "precondition_status": "NOT_RUN",
            "precondition_failed": False,
            "triples_removed": 0,
            "triples_added": 0,
            "reasoner_status": "NOT_RUN",
            "target_cq_pass": False,
            "non_target_cq_preserved": False,
            "source_unchanged": True,
            "destination_written": False,
            "wrong_repair": False,
            "ses_success": False,
            "failure_stage": "ABSTAIN" if not selected_id else "",
        }
        if not selected_id:
            rows_out.append(row)
            continue
        selected = candidates.get((event_id, selected_id))
        if selected is None:
            row.update(gamma_status="INVALID_IR", failure_stage="CANDIDATE_MAPPING", wrong_repair=True)
            rows_out.append(row)
            continue
        operation = selected["operation"]
        row["predicted_gamma_ir_exact"] = canonical_operation(operation) == canonical_operation(gold_operation)
        compiled = compile_update_literal(operation)
        row["gamma_status"] = compiled["status"]
        row["gamma_accept"] = compiled["status"] == "COMPILED"
        if not row["gamma_accept"]:
            row["failure_stage"] = "GAMMA_VALIDATION"
            row["wrong_repair"] = selected_id != gold_id
            rows_out.append(row)
            continue

        source = resolve_source_owl(event, mutants_dir=benchmark / "repair-stage/mutants", project_dir=benchmark)
        destination = materialized / event_id / f"run-{prediction.get('run', '0')}-{selected_id}.owl"
        result = materialize_update_literal_atomic(source_path=source, operation=operation, dest_path=destination)
        row["precondition_status"] = result["status"]
        row["precondition_failed"] = result["status"] == "PRECONDITION_FAILED"
        row["source_unchanged"] = bool(result["source_unchanged"])
        row["destination_written"] = bool(result["dest_written"])
        row["triples_removed"] = result["triples_removed"]
        row["triples_added"] = result["triples_added"]
        if result["status"] != "PASS":
            row["failure_stage"] = "PRECONDITION"
            row["wrong_repair"] = selected_id != gold_id
            rows_out.append(row)
            continue

        source_graph = load_graph(source)
        repaired_graph = load_graph(destination)
        removed, added = graph_delta(source_graph, repaired_graph)
        reasoner = benchmark_validator.run_reasoner(destination, args.timeout)
        row["reasoner_status"] = reasoner.get("status", "ERROR")
        gold_subject = URIRef(gold_operation["subject_iri"])
        gold_predicate = URIRef(gold_operation["predicate_iri"])
        gold_new = term_from_spec(gold_operation["new_value"])
        holdout_class = URIRef("file:///G:/LearnAI/ontology-evolution/external-real-holdout-v1#HoldoutObject")
        row["target_cq_pass"] = (gold_subject, gold_predicate, gold_new) in repaired_graph
        before_unaffected = (gold_subject, RDF.type, holdout_class) in source_graph
        after_unaffected = (gold_subject, RDF.type, holdout_class) in repaired_graph
        row["non_target_cq_preserved"] = before_unaffected and after_unaffected
        row["wrong_repair"] = selected_id != gold_id
        row["ses_success"] = all([
            row["predicted_gamma_ir_exact"], row["gamma_accept"], result["status"] == "PASS",
            removed == 1, added == 1, row["reasoner_status"] == "CONSISTENT",
            row["target_cq_pass"], row["non_target_cq_preserved"], not row["wrong_repair"],
        ])
        if not row["ses_success"]:
            row["failure_stage"] = "OWL_OR_CQ_CLOSURE"
        rows_out.append(row)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows_out:
        groups["ALL"].append(row)
        groups[row["semantic_type"]].append(row)
    summary = [summarize(label, groups[label]) for label in ("ALL", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE")]
    write_csv(output / "rfc213-phase3-gamma-details.csv", rows_out)
    write_csv(output / "rfc213-phase3-gamma-summary.csv", summary)
    (output / "rfc213-phase3-gamma-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary[0], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
