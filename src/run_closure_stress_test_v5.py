from __future__ import annotations

"""Formal closure stress test for frozen v5 holdout (264 events x 5 variants = 1320 cases)."""

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import validate_benchmark as benchmark_validator
from external_real_v8_layout import candidate_selection_paths
from paper_final_validation_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    audit_cq_implementation,
    benchmark_paths,
    load_candidates,
    load_freeze_sha256,
    load_method_freeze_sha256,
    load_oracles,
    load_ready_events,
    sha256_file,
    write_binding,
    write_manifest_csv,
    write_summary_json,
)
from rdflib import Graph, Literal, OWL, RDF, URIRef
from run_auto_policy_v2_candidate_repair import materialize_candidate_owl, resolve_source_owl
from run_external_real_v1_symbolic_closure import graph_delta, load_graph, repair_checks, term_from_spec
from semantic_v2_common import PROJECT_DIR


STRESS_TYPES = (
    "VALID_ORACLE_CONTROL",
    "EXTRA_UNDECLARED_EDIT",
    "WRONG_SEMANTIC_PATCH",
    "REPAIR_CHECK_BREAK",
    "INCONSISTENT_PATCH",
)
HOLDOUT_NS = "file:///G:/LearnAI/ontology-evolution/external-real-holdout-v1#"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=PAPER_VALIDATION_ROOT / "01-closure-stress")
    parser.add_argument("--method-freeze-dir", type=Path, default=DEFAULT_METHOD_FREEZE_DIR)
    parser.add_argument("--benchmark-freeze-summary", type=Path, default=DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--only", default="", help="Comma-separated event ids")
    return parser.parse_args()


def pick_wrong_candidate(
    candidates: list[dict[str, Any]],
    oracle_candidate_id: str,
) -> dict[str, Any] | None:
    non_oracle = [candidate for candidate in candidates if candidate["candidate_id"] != oracle_candidate_id]
    changing = [
        candidate
        for candidate in non_oracle
        if candidate["operation"].get("old_value") != candidate["operation"].get("new_value")
    ]
    if changing:
        return changing[0]
    return non_oracle[0] if non_oracle else None


def apply_inconsistent_axioms(graph: Graph, event_id: str, subject_iri: str) -> int:
    class_a = URIRef(f"{HOLDOUT_NS}{event_id}_StressDisjointA")
    class_b = URIRef(f"{HOLDOUT_NS}{event_id}_StressDisjointB")
    subject = URIRef(subject_iri)
    graph.add((class_a, RDF.type, OWL.Class))
    graph.add((class_b, RDF.type, OWL.Class))
    graph.add((class_a, OWL.disjointWith, class_b))
    graph.add((subject, RDF.type, class_a))
    graph.add((subject, RDF.type, class_b))
    return 3


def materialize_stress_patch(
    *,
    source_path: Path,
    operation: dict[str, Any],
    dest_path: Path,
    stress_type: str,
    event_id: str,
) -> None:
    if dest_path.is_file():
        dest_path.unlink()
    graph = load_graph(source_path)
    if operation.get("operator") != "REPLACE_PROPERTY_VALUE":
        raise RuntimeError(f"unsupported operator: {operation.get('operator')}")
    subject = URIRef(operation["subject_iri"])
    predicate = URIRef(operation["predicate_iri"])
    old_term = term_from_spec(operation["old_value"])
    new_term = term_from_spec(operation["new_value"])
    graph.remove((subject, predicate, old_term))
    graph.add((subject, predicate, new_term))
    if stress_type == "EXTRA_UNDECLARED_EDIT":
        extra_predicate = URIRef(f"{HOLDOUT_NS}{event_id}_stress_extra_property")
        graph.add((subject, extra_predicate, Literal(f"stress-extra-{event_id}")))
    elif stress_type == "INCONSISTENT_PATCH":
        apply_inconsistent_axioms(graph, event_id, operation["subject_iri"])
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(dest_path), format="xml")


def expected_outcome(stress_type: str) -> tuple[str, str]:
    mapping = {
        "VALID_ORACLE_CONTROL": ("PASS", ""),
        "EXTRA_UNDECLARED_EDIT": ("FAIL", "graph_delta"),
        "WRONG_SEMANTIC_PATCH": ("FAIL", "repair_check"),
        "REPAIR_CHECK_BREAK": ("FAIL", "repair_check"),
        "INCONSISTENT_PATCH": ("FAIL", "reasoner"),
    }
    return mapping[stress_type]


def actual_failure_stage(
    *,
    materialization_pass: bool,
    reasoner_pass: bool,
    graph_delta_pass: bool,
    repair_check_pass: bool,
) -> str:
    if not materialization_pass:
        return "materialization"
    if not reasoner_pass:
        return "reasoner"
    if not graph_delta_pass:
        return "graph_delta"
    if not repair_check_pass:
        return "repair_check"
    return ""


def build_stress_case(
    *,
    event: dict[str, str],
    oracle: dict[str, str],
    candidates: list[dict[str, Any]],
    stress_type: str,
    paths: dict[str, Path],
    output_dir: Path,
    timeout: int,
    cq_audit: dict[str, Any],
) -> dict[str, Any]:
    event_id = event["event_id"]
    oracle_candidate_id = oracle["oracle_candidate_id"]
    oracle_candidate = next(c for c in candidates if c["candidate_id"] == oracle_candidate_id)
    operation = copy.deepcopy(oracle_candidate["operation"])
    repair_operation = operation
    stress_applicable = True
    notes = ""

    wrong_candidate_id = ""
    if stress_type == "WRONG_SEMANTIC_PATCH":
        wrong = pick_wrong_candidate(candidates, oracle_candidate_id)
        if wrong is None:
            stress_applicable = False
            notes = "no non-oracle candidate available"
        else:
            operation = copy.deepcopy(wrong["operation"])
            repair_operation = operation
            wrong_candidate_id = wrong["candidate_id"]
    elif stress_type == "REPAIR_CHECK_BREAK":
        repair_operation = copy.deepcopy(operation)
        repair_operation["new_value"] = copy.deepcopy(operation["old_value"])

    expected_final, expected_failure_stage_name = expected_outcome(stress_type)
    row: dict[str, Any] = {
        "event_id": event_id,
        "semantic_type": event["semantic_type"],
        "stress_type": (
            cq_audit["stress_variant_name"]
            if stress_type == "REPAIR_CHECK_BREAK"
            else stress_type
        ),
        "oracle_candidate_id": oracle_candidate_id,
        "applied_candidate_id": wrong_candidate_id or oracle_candidate_id,
        "stress_applicable": stress_applicable,
        "expected_final": expected_final,
        "expected_failure_stage": expected_failure_stage_name,
        "notes": notes,
    }
    if not stress_applicable:
        row.update(
            {
                "materialization_pass": False,
                "reasoner_pass": False,
                "graph_delta_pass": False,
                "cq_or_repair_check_pass": False,
                "final_closure_pass": False,
                "actual_failure_stage": "not_applicable",
                "correct_detection": False,
            }
        )
        return row

    source_path = resolve_source_owl(
        event,
        mutants_dir=paths["mutants_dir"],
        project_dir=PROJECT_DIR,
    )
    dest_path = output_dir / "candidate-owls" / event_id / f"{stress_type}.owl"
    materialization_pass = True
    materialization_error = ""
    try:
        materialize_stress_patch(
            source_path=source_path,
            operation=operation,
            dest_path=dest_path,
            stress_type=stress_type,
            event_id=event_id,
        )
    except Exception as exc:  # noqa: BLE001 - stress harness must continue
        materialization_pass = False
        materialization_error = f"{type(exc).__name__}: {exc}"

    if not materialization_pass:
        row.update(
            {
                "materialization_pass": False,
                "reasoner_pass": False,
                "graph_delta_pass": False,
                "cq_or_repair_check_pass": False,
                "final_closure_pass": False,
                "actual_failure_stage": "materialization",
                "correct_detection": expected_failure_stage_name == "materialization",
                "materialization_error": materialization_error,
            }
        )
        return row

    source_graph = load_graph(source_path)
    candidate_graph = load_graph(dest_path)
    reasoner = benchmark_validator.run_reasoner(dest_path, timeout)
    reasoner_pass = reasoner.get("status") == "CONSISTENT"
    removed, added = graph_delta(source_graph, candidate_graph)
    graph_delta_pass = removed == 1 and added == 1
    if stress_type == "INCONSISTENT_PATCH":
        graph_delta_pass = removed == 1 and added >= 4
    source_trigger, candidate_repair, repair_message = repair_checks(
        source_graph,
        candidate_graph,
        repair_operation,
    )
    repair_check_pass = source_trigger and candidate_repair
    final_closure_pass = (
        materialization_pass
        and reasoner_pass
        and graph_delta_pass
        and repair_check_pass
    )
    actual_stage = actual_failure_stage(
        materialization_pass=materialization_pass,
        reasoner_pass=reasoner_pass,
        graph_delta_pass=graph_delta_pass,
        repair_check_pass=repair_check_pass,
    )
    if expected_final == "PASS":
        correct_detection = final_closure_pass
    elif expected_failure_stage_name:
        correct_detection = actual_stage == expected_failure_stage_name or (
            expected_failure_stage_name == "repair_check" and actual_stage == "repair_check"
        )
    else:
        correct_detection = not final_closure_pass

    row.update(
        {
            "materialization_pass": materialization_pass,
            "reasoner_pass": reasoner_pass,
            "reasoner_result": reasoner.get("status", ""),
            "triples_removed": removed,
            "triples_added": added,
            "graph_delta_pass": graph_delta_pass,
            "cq_or_repair_check_pass": repair_check_pass,
            "repair_check_message": repair_message,
            "final_closure_pass": final_closure_pass,
            "actual_failure_stage": actual_stage,
            "correct_detection": correct_detection,
            "candidate_owl": str(dest_path.relative_to(PROJECT_DIR)),
        }
    )
    return row


def summarize(details: list[dict[str, Any]], cq_audit: dict[str, Any]) -> dict[str, Any]:
    applicable = [row for row in details if row.get("stress_applicable")]
    valid = [row for row in applicable if row["stress_type"] == "VALID_ORACLE_CONTROL"]
    invalid = [row for row in applicable if row["stress_type"] != "VALID_ORACLE_CONTROL"]
    valid_accept = sum(bool(row["final_closure_pass"]) for row in valid)
    invalid_reject = sum(not bool(row["final_closure_pass"]) for row in invalid)
    stage_correct = sum(bool(row["correct_detection"]) for row in applicable)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in applicable:
        by_type.setdefault(str(row["stress_type"]), []).append(row)
    per_type = {
        stress_type: {
            "cases": len(rows),
            "final_closure_pass": sum(bool(row["final_closure_pass"]) for row in rows),
            "correct_detection": sum(bool(row["correct_detection"]) for row in rows),
        }
        for stress_type, rows in sorted(by_type.items())
    }
    oracle_ids = {row["oracle_candidate_id"] for row in details}
    oracle_distribution: dict[str, int] = {}
    for row in details:
        cid = str(row["oracle_candidate_id"])
        oracle_distribution[cid] = oracle_distribution.get(cid, 0) + 1
    wrong_semantic = [row for row in applicable if row["stress_type"] == "WRONG_SEMANTIC_PATCH"]
    wrong_semantic_blind_spot = {
        "cases": len(wrong_semantic),
        "closure_passed_despite_wrong_semantic": sum(
            bool(row["final_closure_pass"]) for row in wrong_semantic
        ),
        "explanation": (
            "repair_checks() validates only that the applied operation's old/new triple "
            "regression holds on source/candidate graphs. It does not compare against the "
            "private oracle operation, so semantically wrong but self-consistent patches "
            "can pass closure."
        ),
    }
    return {
        "total_cases": len(details),
        "applicable_cases": len(applicable),
        "valid_patch_acceptance_rate": valid_accept / len(valid) if valid else 0.0,
        "invalid_patch_rejection_rate": invalid_reject / len(invalid) if invalid else 0.0,
        "failure_stage_accuracy": stage_correct / len(applicable) if applicable else 0.0,
        "per_stress_type": per_type,
        "oracle_candidate_distribution": oracle_distribution,
        "oracle_candidate_unique": sorted(oracle_ids),
        "wrong_semantic_closure_blind_spot": wrong_semantic_blind_spot,
        "cq_audit": cq_audit,
    }


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_sha256, benchmark_manifest = load_freeze_sha256(args.benchmark_freeze_summary)
    method_freeze_sha256, method_freeze_manifest = load_method_freeze_sha256(args.method_freeze_dir)
    cq_audit = audit_cq_implementation()
    paths = benchmark_paths(args.benchmark_dir)
    events = load_ready_events(paths)
    if args.only.strip():
        only = {item.strip().upper() for item in args.only.split(",") if item.strip()}
        events = [row for row in events if row["event_id"].upper() in only]
    candidates_by_event = load_candidates(paths)
    oracles = load_oracles(paths)

    manifest_rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for event in events:
        event_id = event["event_id"]
        oracle = oracles[event_id]
        candidates = candidates_by_event[event_id]
        for stress_type in STRESS_TYPES:
            manifest_rows.append(
                {
                    "event_id": event_id,
                    "semantic_type": event["semantic_type"],
                    "stress_type": (
                        cq_audit["stress_variant_name"]
                        if stress_type == "REPAIR_CHECK_BREAK"
                        else stress_type
                    ),
                    "oracle_candidate_id": oracle["oracle_candidate_id"],
                    "expected_final": expected_outcome(stress_type)[0],
                    "expected_failure_stage": expected_outcome(stress_type)[1],
                }
            )
            details.append(
                build_stress_case(
                    event=event,
                    oracle=oracle,
                    candidates=candidates,
                    stress_type=stress_type,
                    paths=paths,
                    output_dir=args.output_dir,
                    timeout=args.timeout,
                    cq_audit=cq_audit,
                )
            )

    manifest_path = args.output_dir / "stress-manifest.csv"
    details_path = args.output_dir / "details.csv"
    summary_path = args.output_dir / "summary.json"
    write_manifest_csv(manifest_path, manifest_rows)
    write_manifest_csv(args.output_dir / "manifest.csv", manifest_rows)
    write_manifest_csv(details_path, details)
    summary = summarize(details, cq_audit)
    summary["generated_at_utc"] = __import__("paper_final_validation_common", fromlist=["utc_now_iso"]).utc_now_iso()
    summary["cases_expected"] = len(events) * len(STRESS_TYPES)
    summary["cases_actual"] = len(details)
    write_summary_json(summary_path, summary)
    write_binding(
        args.output_dir,
        experiment_role="01-closure-stress",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_freeze_manifest,
        method_freeze_sha256=method_freeze_sha256,
        extra={
            "input_prediction_dir": "",
            "llm_backend": "",
            "model": "",
            "cq_audit": cq_audit,
        },
    )
    print(f"[closure-stress] cases={len(details)} output={args.output_dir}")
    print(
        "[closure-stress] "
        f"valid_accept={summary['valid_patch_acceptance_rate']:.4f} "
        f"invalid_reject={summary['invalid_patch_rejection_rate']:.4f} "
        f"stage_acc={summary['failure_stage_accuracy']:.4f}"
    )
    print(f"[closure-stress] oracle_distribution={summary['oracle_candidate_distribution']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
