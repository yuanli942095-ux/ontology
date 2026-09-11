from __future__ import annotations

"""Offline R2b-v2 evaluation using frozen v1 model outputs.

This is a development evaluation, not an independent blind result. Candidate
and Oracle files are opened only after the persisted candidate-blind outputs.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import validate_benchmark as benchmark_validator
from rdflib import RDF, URIRef

from evaluate_rfc213_phase3_gamma_closure import canonical_operation, summarize
from rfc213_direct_repair_ir import canonicalize_surface, schema_sha256, to_gamma_operation, validate_predicted_ir
from rfc213_direct_repair_ir_v2 import resolve_source_window
from rfc213_direct_repair_ir_v23 import resolve_source_window_v23
from rfc213_direct_repair_ir_v24 import resolve_source_window_v24
from run_auto_policy_v2_candidate_repair import resolve_source_owl
from run_external_real_v1_symbolic_closure import graph_delta, load_graph, term_from_spec
from run_gamma_gold_ir_closure_eval import materialize_update_literal_atomic
from run_gamma_gold_ir_compile_eval import compile_update_literal
from run_rfc213_direct_ir_gamma import event_map, load_candidates_for_offline_scoring, read_csv
from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark/rfc-213-confirmatory-core"
DEFAULT_RAW = PROJECT_DIR / "output/rfc-213-confirmatory-core/phase3-direct-ir/engineering-v1/raw-predicted-ir"
DEFAULT_MANIFEST = PROJECT_DIR / "output/rfc-213-confirmatory-core/phase3-direct-ir/engineering-v1/r2b-engineering-manifest.csv"
DEFAULT_OUTPUT = PROJECT_DIR / "output/rfc-213-confirmatory-core/phase3-direct-ir/engineering-v2-window-resolution"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--resolver", choices=("v22", "v23", "v24"), default="v22")
    args = parser.parse_args()
    benchmark = args.benchmark_dir.resolve()
    raw_dir = args.raw_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    materialized = output / "materialized-predicted-owl"

    events = event_map(benchmark)
    manifest = read_csv(args.manifest.resolve())
    candidates = load_candidates_for_offline_scoring(
        benchmark / "repair-stage/candidates/external-real-candidate-template.csv"
    )
    gold_ids = {
        row["event_id"]: row["gold_candidate_id"]
        for row in read_csv(benchmark / "private/construct-audit/rfc213-gold-repair-construct-audit.csv")
    }
    rows_out: list[dict[str, Any]] = []
    for item in manifest:
        event_id = item["event_id"]
        event = events[event_id]
        raw_path = raw_dir / f"{event_id}-run1-seed{item['seed']}.json"
        row: dict[str, Any] = {
            "event_id": event_id,
            "semantic_type": event["semantic_type"],
            "domain": event["domain"],
            "run": "1",
            "seed": item["seed"],
            "semantic_ir_valid": False,
            "predicted_gamma_ir_exact": False,
            "gamma_accept": False,
            "precondition_failed": False,
            "decision": "ABSTAIN",
            "wrong_repair": False,
            "target_cq_pass": False,
            "non_target_cq_preserved": False,
            "source_unchanged": True,
            "ses_success": False,
            "schema_status": "NOT_RUN",
            "window_resolution_status": "NOT_RUN",
            "gamma_status": "NOT_RUN",
            "precondition_status": "NOT_RUN",
            "reasoner_status": "NOT_RUN",
            "failure_stage": "GENERATION",
            "candidate_used_before_prediction": False,
            "oracle_used_before_prediction": False,
            "schema_sha256": schema_sha256(),
        }
        if not raw_path.is_file():
            rows_out.append(row)
            continue
        payload = json.loads(raw_path.read_text(encoding="utf-8-sig"))
        predicted_raw = payload.get("predicted_ir_raw", {})
        evidence = (benchmark / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
        source = resolve_source_owl(event, mutants_dir=benchmark / "repair-stage/mutants", project_dir=benchmark)

        v1_canonical = canonicalize_surface(predicted_raw)
        initial_validation = validate_predicted_ir(v1_canonical, source_path=source, evidence=evidence)
        row["schema_status"] = initial_validation["status"]
        row["semantic_ir_valid"] = initial_validation["status"] == "VALID"
        row["validation_errors"] = json.dumps(initial_validation["errors"], ensure_ascii=False)
        row["model_decision"] = str(v1_canonical.get("decision", "ABSTAIN"))
        if initial_validation["status"] != "VALID" or row["model_decision"] != "REPAIR":
            row["decision"] = row["model_decision"]
            row["failure_stage"] = initial_validation["status"] if initial_validation["status"] != "VALID" else row["decision"]
            rows_out.append(row)
            continue

        resolvers = {
            "v22": resolve_source_window,
            "v23": resolve_source_window_v23,
            "v24": resolve_source_window_v24,
        }
        resolution = resolvers[args.resolver](predicted_raw, evidence)
        row["window_resolution_status"] = resolution["status"]
        row["resolver_version"] = args.resolver
        row["resolution_path"] = resolution.get("resolution_path", "V22")
        row["reference_markers"] = json.dumps(resolution.get("reference_markers", {}), ensure_ascii=False, sort_keys=True)
        row["matching_window_ids"] = json.dumps(resolution["matching_window_ids"])
        row["window_canonical_candidates"] = json.dumps(resolution["canonical_candidates"], ensure_ascii=False)
        if resolution["status"] != "RESOLVED":
            row["decision"] = "ABSTAIN"
            row["failure_stage"] = f"WINDOW_RESOLUTION_{resolution['status']}"
            rows_out.append(row)
            continue

        predicted = resolution["canonical_ir"]
        row["predicted_ir_raw_json"] = json.dumps(predicted_raw, ensure_ascii=False, sort_keys=True)
        row["predicted_ir_canonical_json"] = json.dumps(predicted, ensure_ascii=False, sort_keys=True)
        row["canonical_new_value_lexical"] = predicted["replacement"]["new_value"]["lexical"]
        validation = validate_predicted_ir(predicted, source_path=source, evidence=evidence)
        if validation["status"] != "VALID":
            row["schema_status"] = validation["status"]
            row["failure_stage"] = validation["status"]
            rows_out.append(row)
            continue

        operation = to_gamma_operation(predicted)
        gold_operation = candidates[(event_id, gold_ids[event_id])]
        row["predicted_gamma_ir_exact"] = canonical_operation(operation) == canonical_operation(gold_operation)
        compiled = compile_update_literal(operation)
        row["gamma_status"] = compiled["status"]
        row["gamma_accept"] = compiled["status"] == "COMPILED"
        if not row["gamma_accept"]:
            row["failure_stage"] = "GAMMA_VALIDATION"
            rows_out.append(row)
            continue

        destination = materialized / f"{event_id}-run1.owl"
        applied = materialize_update_literal_atomic(source_path=source, operation=operation, dest_path=destination)
        row["precondition_status"] = applied["status"]
        row["precondition_failed"] = applied["status"] == "PRECONDITION_FAILED"
        row["source_unchanged"] = bool(applied["source_unchanged"])
        if applied["status"] != "PASS":
            row["failure_stage"] = "PRECONDITION"
            rows_out.append(row)
            continue

        row["decision"] = "SELECT"
        row["wrong_repair"] = not row["predicted_gamma_ir_exact"]
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
        row["non_target_cq_preserved"] = (
            (gold_subject, RDF.type, holdout_class) in source_graph
            and (gold_subject, RDF.type, holdout_class) in repaired_graph
        )
        row["ses_success"] = all(
            [
                row["predicted_gamma_ir_exact"],
                removed == 1,
                added == 1,
                row["reasoner_status"] == "CONSISTENT",
                row["target_cq_pass"],
                row["non_target_cq_preserved"],
                row["source_unchanged"],
            ]
        )
        row["failure_stage"] = "" if row["ses_success"] else "OWL_OR_CQ_CLOSURE"
        rows_out.append(row)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows_out:
        groups["ALL"].append(row)
        groups[row["semantic_type"]].append(row)
    labels = ("ALL", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE")
    summaries = [summarize(label, groups.get(label, [])) for label in labels]
    prefix = f"r2b-{args.resolver}-window-resolution"
    write_csv(output / f"{prefix}-details.csv", rows_out)
    write_csv(output / f"{prefix}-summary.csv", summaries)
    (output / f"{prefix}-summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summaries[0], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
