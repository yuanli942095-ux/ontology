from __future__ import annotations

"""Materialize RFC-213 Gamma gold repairs and validate OWL/CQ closure."""

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import validate_benchmark as benchmark_validator
from rdflib import URIRef
from rdflib.compare import isomorphic

from run_auto_policy_v2_candidate_repair import resolve_source_owl
from run_external_real_v1_symbolic_closure import (
    graph_delta,
    load_graph,
    repair_checks,
    strip_ontology_metadata,
    term_from_spec,
)
from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "rfc-213-confirmatory-core"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "rfc-213-confirmatory-core" / "gamma"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RFC-213 Gamma gold-IR OWL closure eval")
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--prefix", default="rfc213-gamma-gold-ir-closure")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def load_candidates(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_csv(path):
        if str(row.get("status", "")).strip().upper() != "READY":
            continue
        result[(row["event_id"], row["candidate_id"])] = {
            **row,
            "operation": json.loads(row["operation_json"]),
        }
    return result


def load_events(path: Path) -> dict[str, dict[str, str]]:
    return {row["event_id"]: row for row in read_csv(path) if row.get("status") == "READY"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def materialize_update_literal_atomic(
    *,
    source_path: Path,
    operation: dict[str, Any],
    dest_path: Path,
    expected_source_sha256: str = "",
) -> dict[str, Any]:
    """Apply one deterministic UPDATE_LITERAL patch after all preconditions pass."""
    if operation.get("operator") != "REPLACE_PROPERTY_VALUE":
        return {
            "status": "UNSUPPORTED",
            "error": f"operator={operation.get('operator')}",
            "triples_removed": 0,
            "triples_added": 0,
            "source_unchanged": True,
            "dest_written": False,
        }
    actual_source_sha256 = sha256_file(source_path)
    if expected_source_sha256 and actual_source_sha256 != expected_source_sha256:
        return {
            "status": "PRECONDITION_FAILED",
            "error": "source_sha256_mismatch",
            "triples_removed": 0,
            "triples_added": 0,
            "source_unchanged": True,
            "dest_written": False,
        }
    source_before_sha256 = actual_source_sha256
    graph = load_graph(source_path)
    subject = URIRef(operation["subject_iri"])
    predicate = URIRef(operation["predicate_iri"])
    old_term = term_from_spec(operation["old_value"])
    new_term = term_from_spec(operation["new_value"])
    old_triple = (subject, predicate, old_term)
    new_triple = (subject, predicate, new_term)
    if old_triple not in graph:
        return {
            "status": "PRECONDITION_FAILED",
            "error": "old_triple_missing",
            "triples_removed": 0,
            "triples_added": 0,
            "source_unchanged": sha256_file(source_path) == source_before_sha256,
            "dest_written": False,
        }
    if new_triple in graph:
        return {
            "status": "PRECONDITION_FAILED",
            "error": "new_triple_already_present",
            "triples_removed": 0,
            "triples_added": 0,
            "source_unchanged": sha256_file(source_path) == source_before_sha256,
            "dest_written": False,
        }
    before_size = len(graph)
    graph.remove(old_triple)
    graph.add(new_triple)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(dest_path), format="xml")
    return {
        "status": "PASS",
        "error": "",
        "triples_removed": 1 if len(graph) == before_size else 0,
        "triples_added": 1 if len(graph) == before_size else 0,
        "source_unchanged": sha256_file(source_path) == source_before_sha256,
        "dest_written": dest_path.is_file(),
    }


def main() -> int:
    args = parse_args()
    benchmark_dir = args.benchmark_dir.resolve()
    output_dir = args.output_dir.resolve()
    event_path = benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    candidate_path = benchmark_dir / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    construct_path = benchmark_dir / "private" / "construct-audit" / "rfc213-gold-repair-construct-audit.csv"

    events = load_events(event_path)
    candidates = load_candidates(candidate_path)
    constructs = read_csv(construct_path)
    rows: list[dict[str, Any]] = []
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    materialized_dir = output_dir / "materialized-gold-owl"

    graph_cache: dict[Path, Any] = {}
    reasoner_cache: dict[Path, dict[str, Any]] = {}
    for construct in constructs:
        event_id = construct["event_id"]
        gold_candidate_id = construct["gold_candidate_id"]
        event = events[event_id]
        selected = candidates[(event_id, gold_candidate_id)]
        operation = selected["operation"]
        source_path = resolve_source_owl(event, mutants_dir=benchmark_dir / "repair-stage" / "mutants", project_dir=benchmark_dir)
        materialized_path = materialized_dir / f"{event_id}-gamma-gold.owl"
        gold_path = benchmark_dir / "private" / "ontology-after" / f"{event_id}-gold.owl"

        status = "PASS"
        error = ""
        try:
            materialized = materialize_update_literal_atomic(
                source_path=source_path,
                operation=operation,
                dest_path=materialized_path,
            )
            if materialized["status"] != "PASS":
                raise RuntimeError(materialized["error"])
            source_graph = graph_cache.setdefault(source_path, load_graph(source_path))
            candidate_graph = graph_cache.setdefault(materialized_path, load_graph(materialized_path))
            gold_graph = graph_cache.setdefault(gold_path, load_graph(gold_path))
            removed, added = graph_delta(source_graph, candidate_graph)
            source_trigger, candidate_repair, repair_message = repair_checks(
                source_graph, candidate_graph, operation
            )
            ontology_matches_gold = isomorphic(
                strip_ontology_metadata(candidate_graph),
                strip_ontology_metadata(gold_graph),
            )
            reasoner = reasoner_cache.setdefault(
                materialized_path,
                benchmark_validator.run_reasoner(materialized_path, args.timeout),
            )
            minimal_edit = removed == 1 and added == 1
            reasoner_gate = reasoner.get("status") == "CONSISTENT"
            full_closure_success = all(
                [
                    minimal_edit,
                    reasoner_gate,
                    source_trigger,
                    candidate_repair,
                    ontology_matches_gold,
                ]
            )
            if not full_closure_success:
                status = "FAIL"
                error = repair_message
        except Exception as exc:  # noqa: BLE001 - written to audit details.
            removed = added = 0
            source_trigger = candidate_repair = ontology_matches_gold = False
            minimal_edit = reasoner_gate = full_closure_success = False
            reasoner = {"status": "ERROR", "runtime_ms": 0}
            status = "ERROR"
            error = f"{type(exc).__name__}: {exc}"

        row = {
            "event_id": event_id,
            "semantic_type": event["semantic_type"],
            "domain": event["domain"],
            "gold_candidate_id": gold_candidate_id,
            "construct_type": construct["construct_type"],
            "operation_operator": operation.get("operator", ""),
            "materialized_owl": str(materialized_path.relative_to(PROJECT_DIR)),
            "gold_owl": str(gold_path.relative_to(PROJECT_DIR)),
            "triples_removed": removed,
            "triples_added": added,
            "minimal_edit_gate": minimal_edit,
            "reasoner_result": reasoner.get("status", ""),
            "reasoner_runtime_ms": reasoner.get("runtime_ms", 0),
            "reasoner_gate": reasoner_gate,
            "source_triggers_repair_cq": source_trigger,
            "candidate_satisfies_repair_cq": candidate_repair,
            "ontology_matches_private_gold": ontology_matches_gold,
            "full_closure_success": full_closure_success,
            "source_unchanged": True,
            "status": status,
            "error": error,
        }
        rows.append(row)
        by_type[event["semantic_type"]].append(row)

    def summarize(label: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(items)
        closure = sum(bool(row["full_closure_success"]) for row in items)
        return {
            "semantic_type": label,
            "events": total,
            "full_closure_success": closure,
            "full_closure_accuracy": closure / total if total else 0,
            "minimal_edit_pass": sum(bool(row["minimal_edit_gate"]) for row in items),
            "reasoner_pass": sum(bool(row["reasoner_gate"]) for row in items),
            "cq_pass": sum(bool(row["source_triggers_repair_cq"] and row["candidate_satisfies_repair_cq"]) for row in items),
            "gold_owl_match": sum(bool(row["ontology_matches_private_gold"]) for row in items),
        }

    summary_rows = [summarize("ALL", rows)]
    summary_rows.extend(summarize(key, by_type[key]) for key in sorted(by_type))
    status_counts = Counter(row["status"] for row in rows)
    meta = {
        "benchmark": benchmark_dir.name,
        "prefix": args.prefix,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "status_counts": dict(status_counts),
        "summary": summary_rows,
        "llm_allowed": False,
        "retrieval_allowed": False,
        "sampling_allowed": False,
    }

    write_csv(output_dir / f"{args.prefix}-details.csv", rows)
    write_csv(output_dir / f"{args.prefix}-summary.csv", summary_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{args.prefix}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    all_row = summary_rows[0]
    print("RFC213 Gamma gold-IR closure eval")
    print(f"details={output_dir / f'{args.prefix}-details.csv'}")
    print(f"summary={output_dir / f'{args.prefix}-summary.csv'}")
    print(
        f"[ALL] closure={all_row['full_closure_success']}/{all_row['events']} "
        f"({all_row['full_closure_accuracy']:.2%})"
    )
    for row in summary_rows[1:]:
        print(
            f"[{row['semantic_type']}] closure={row['full_closure_success']}/{row['events']} "
            f"({row['full_closure_accuracy']:.2%})"
        )
    return 0 if all_row["full_closure_success"] == all_row["events"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
