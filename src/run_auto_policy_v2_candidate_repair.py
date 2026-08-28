from __future__ import annotations

"""Use AUTO_POLICY_V2 semantic results to select external-real-v1 repair candidates.

The selection phase reads generated auto-policy-v2 outputs and public candidate
values. Private Oracle is loaded only after all selections are fixed.
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import validate_benchmark as benchmark_validator
from evaluate_auto_formal_policy_v2_semantic import auto_semantic_result, evaluate_semantic
from external_real_v8_layout import candidate_selection_paths, evaluation_paths, is_staged_layout
from rdflib import URIRef

from run_external_real_v1_symbolic_closure import (
    graph_delta,
    load_graph,
    repair_checks,
    term_from_spec,
)
from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR, write_csv


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v1"
INPUT_DIR = BENCHMARK_DIR / "input"
PRIVATE_DIR = BENCHMARK_DIR / "private"
BUILT_DIR = BENCHMARK_DIR / "built"
MUTANTS_DIR = BENCHMARK_DIR / "mutants"
CANDIDATE_OWL_OUTPUT_DIR = OUTPUT_DIR / "candidate-owls"
RAW_DIR = OUTPUT_DIR / "auto-policy-v2" / "raw"

EVENT_CSV = INPUT_DIR / "external-real-event-template.csv"
CANDIDATE_CSV = INPUT_DIR / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE_DIR / "external-real-oracle-template.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="auto-policy-v2 candidate repair selection")
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--prefix", default="auto-policy-v2-candidate-repair-r5-seed20260820")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--only", default="", help="comma-separated event IDs for smoke runs")
    return parser.parse_args()


def configure_benchmark(benchmark_dir: Path) -> None:
    global BENCHMARK_DIR, INPUT_DIR, PRIVATE_DIR, BUILT_DIR, MUTANTS_DIR
    global CANDIDATE_OWL_OUTPUT_DIR, EVENT_CSV, CANDIDATE_CSV, ORACLE_CSV, RAW_DIR

    BENCHMARK_DIR = benchmark_dir.resolve()
    if is_staged_layout(BENCHMARK_DIR):
        selection = candidate_selection_paths(BENCHMARK_DIR)
        evaluation = evaluation_paths(BENCHMARK_DIR)
        INPUT_DIR = selection["event_csv"].parent
        PRIVATE_DIR = evaluation["oracle_csv"].parent
        MUTANTS_DIR = selection["mutants_dir"]
        BUILT_DIR = BENCHMARK_DIR / "repair-stage"
        EVENT_CSV = selection["event_csv"]
        CANDIDATE_CSV = selection["candidate_csv"]
        ORACLE_CSV = evaluation["oracle_csv"]
    else:
        INPUT_DIR = BENCHMARK_DIR / "input"
        PRIVATE_DIR = BENCHMARK_DIR / "private"
        BUILT_DIR = BENCHMARK_DIR / "built"
        MUTANTS_DIR = BENCHMARK_DIR / "mutants"
        EVENT_CSV = INPUT_DIR / "external-real-event-template.csv"
        CANDIDATE_CSV = INPUT_DIR / "external-real-candidate-template.csv"
        ORACLE_CSV = PRIVATE_DIR / "external-real-oracle-template.csv"
    RAW_DIR = PROJECT_DIR / "output" / BENCHMARK_DIR.name / "auto-policy-v3" / "raw"
    CANDIDATE_OWL_OUTPUT_DIR = PROJECT_DIR / "output" / BENCHMARK_DIR.name / "candidate-owls"


def experiment_name(prefix: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", prefix.upper()).strip("_")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def ready(value: str) -> bool:
    return str(value or "").strip().upper() == "READY"


def parse_only(value: str) -> set[str]:
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def select_ready_events(rows: list[dict[str, str]], only: set[str]) -> list[dict[str, str]]:
    selected = [row for row in rows if ready(row.get("status", ""))]
    if only:
        selected = [row for row in selected if str(row.get("event_id", "")).upper() in only]
    return selected


def load_ready_events(only: set[str] | None = None) -> list[dict[str, str]]:
    rows = select_ready_events(read_csv(EVENT_CSV), only or set())
    if not rows:
        raise RuntimeError(f"no READY events in {EVENT_CSV}")
    return rows


def resolve_source_owl(
    event: dict[str, str],
    *,
    mutants_dir: Path,
    project_dir: Path,
) -> Path:
    source = str(event.get("source_owl", "")).strip()
    if source:
        path = Path(source)
        if not path.is_absolute():
            path = project_dir / path
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    path = mutants_dir / f"{event['event_id']}.owl"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def resolve_candidate_owl_path(
    event_id: str,
    candidate_id: str,
    *,
    benchmark_dir: Path,
    built_dir: Path,
    output_dir: Path,
) -> Path:
    filename = f"{candidate_id}.owl"
    for candidate in (
        built_dir / "candidate-owls" / event_id / filename,
        benchmark_dir / "repair-stage" / "candidate-owls" / event_id / filename,
        benchmark_dir / "built" / "candidate-owls" / event_id / filename,
    ):
        if candidate.is_file():
            return candidate
    return output_dir / "candidate-owls" / event_id / filename


def materialize_candidate_owl(
    *,
    source_path: Path,
    operation: dict[str, Any],
    dest_path: Path,
) -> Path:
    if dest_path.is_file():
        return dest_path
    if operation.get("operator") != "REPLACE_PROPERTY_VALUE":
        raise RuntimeError(f"unsupported operator: {operation.get('operator')}")
    graph = load_graph(source_path)
    subject = URIRef(operation["subject_iri"])
    predicate = URIRef(operation["predicate_iri"])
    old_term = term_from_spec(operation["old_value"])
    new_term = term_from_spec(operation["new_value"])
    graph.remove((subject, predicate, old_term))
    graph.add((subject, predicate, new_term))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(dest_path), format="xml")
    return dest_path


def load_candidates() -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_csv(CANDIDATE_CSV):
        if ready(row.get("status", "")):
            result[str(row["event_id"])].append({**row, "operation": json.loads(row["operation_json"])})
    return result


def load_oracles_after_selection() -> dict[str, dict[str, str]]:
    return {str(row["event_id"]): row for row in read_csv(ORACLE_CSV) if ready(row.get("status", ""))}


def select_candidate(
    event: dict[str, str],
    candidates: list[dict[str, Any]],
    auto_result: str,
) -> tuple[dict[str, Any] | None, str, str, list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        correct, evidence = evaluate_semantic(str(candidate["display_value"]), auto_result, event)
        evidence_rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "display_value": candidate["display_value"],
                "match": correct,
                "evidence": evidence,
            }
        )
        if correct:
            matches.append(candidate)
    if len(matches) == 1:
        return matches[0], "SELECTED", "unique semantic candidate match", evidence_rows
    if not matches:
        return None, "ABSTAIN", "no candidate value matched auto semantic_result", evidence_rows
    return None, "ABSTAIN", f"multiple candidate values matched: {[item['candidate_id'] for item in matches]}", evidence_rows


def summarize(
    details: list[dict[str, Any]],
    experiment: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_type_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_event_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        by_type_groups[str(row["semantic_type"])].append(row)
        by_event_groups[str(row["event_id"])].append(row)

    by_event: list[dict[str, Any]] = []
    for event_id, items in sorted(by_event_groups.items()):
        correct = sum(bool(row["selection_oracle_correct"]) for row in items)
        closure = sum(bool(row["full_closure_success"]) for row in items)
        by_event.append(
            {
                "event_id": event_id,
                "semantic_type": items[0]["semantic_type"],
                "attempts": len(items),
                "selected": sum(row["selection_status"] == "SELECTED" for row in items),
                "oracle_correct": correct,
                "oracle_accuracy": correct / len(items) if items else 0,
                "full_closure_success": closure,
                "full_closure_accuracy": closure / len(items) if items else 0,
                "strict_event_success": closure == len(items),
                "abstains": sum(row["selection_status"] == "ABSTAIN" for row in items),
            }
        )

    by_type: list[dict[str, Any]] = []
    for semantic_type, items in [("ALL", details), *sorted(by_type_groups.items())]:
        events = sorted({row["event_id"] for row in items})
        correct = sum(bool(row["selection_oracle_correct"]) for row in items)
        closure = sum(bool(row["full_closure_success"]) for row in items)
        strict_rows = [
            row
            for row in by_event
            if semantic_type == "ALL" or row["semantic_type"] == semantic_type
        ]
        by_type.append(
            {
                "semantic_type": semantic_type,
                "events": len(events),
                "attempts": len(items),
                "selected": sum(row["selection_status"] == "SELECTED" for row in items),
                "oracle_correct": correct,
                "oracle_accuracy": correct / len(items) if items else 0,
                "full_closure_success": closure,
                "full_closure_accuracy": closure / len(items) if items else 0,
                "strict_event_successes": sum(bool(row["strict_event_success"]) for row in strict_rows),
                "strict_event_accuracy": (
                    sum(bool(row["strict_event_success"]) for row in strict_rows) / len(strict_rows)
                    if strict_rows
                    else 0
                ),
                "abstains": sum(row["selection_status"] == "ABSTAIN" for row in items),
            }
        )
    summary = [
        {
            "experiment": experiment,
            "events": by_type[0]["events"],
            "attempts": by_type[0]["attempts"],
            "selected": by_type[0]["selected"],
            "oracle_correct": by_type[0]["oracle_correct"],
            "oracle_accuracy": by_type[0]["oracle_accuracy"],
            "full_closure_success": by_type[0]["full_closure_success"],
            "full_closure_accuracy": by_type[0]["full_closure_accuracy"],
            "strict_event_successes": by_type[0]["strict_event_successes"],
            "strict_event_accuracy": by_type[0]["strict_event_accuracy"],
            "abstains": by_type[0]["abstains"],
        }
    ]
    return by_event, by_type, summary


def main() -> int:
    args = parse_args()
    configure_benchmark(args.benchmark_dir)
    args.raw_dir = args.raw_dir or RAW_DIR
    experiment = experiment_name(args.prefix)
    events = load_ready_events(parse_only(args.only))
    candidates_by_event = load_candidates()
    graph_cache: dict[Path, Any] = {}
    reasoner_cache: dict[Path, dict[str, Any]] = {}
    details: list[dict[str, Any]] = []

    for event in events:
        event_id = str(event["event_id"])
        candidates = candidates_by_event[event_id]
        for run in range(1, args.runs + 1):
            seed = args.seed + run - 1
            raw_path = args.raw_dir / f"{event_id}-run{run}-seed{seed}.json"
            auto_record = json.loads(raw_path.read_text(encoding="utf-8-sig"))
            generation_status = str(auto_record.get("status", ""))
            auto_result = auto_semantic_result(auto_record)
            selected, status, reason, match_evidence = select_candidate(event, candidates, auto_result)
            row: dict[str, Any] = {
                "event_id": event_id,
                "semantic_type": event["semantic_type"],
                "run": run,
                "seed": seed,
                "generation_status": generation_status,
                "selection_status": status,
                "selected_candidate_id": selected["candidate_id"] if selected else "",
                "selected_value": selected["display_value"] if selected else "",
                "auto_semantic_result": auto_result,
                "selection_reason": reason,
                "candidate_match_evidence": json.dumps(match_evidence, ensure_ascii=False, sort_keys=True),
                "runtime_ms": auto_record.get("runtime_ms", 0),
                "prompt_eval_count": auto_record.get("prompt_eval_count", 0),
                "eval_count": auto_record.get("eval_count", 0),
            }
            if selected:
                source_path = resolve_source_owl(
                    event,
                    mutants_dir=MUTANTS_DIR,
                    project_dir=PROJECT_DIR,
                )
                candidate_path = resolve_candidate_owl_path(
                    event_id,
                    selected["candidate_id"],
                    benchmark_dir=BENCHMARK_DIR,
                    built_dir=BUILT_DIR,
                    output_dir=CANDIDATE_OWL_OUTPUT_DIR.parent,
                )
                materialize_candidate_owl(
                    source_path=source_path,
                    operation=selected["operation"],
                    dest_path=candidate_path,
                )
                source_graph = graph_cache.setdefault(source_path, load_graph(source_path))
                candidate_graph = graph_cache.setdefault(candidate_path, load_graph(candidate_path))
                reasoner = reasoner_cache.setdefault(
                    candidate_path,
                    benchmark_validator.run_reasoner(candidate_path, args.timeout),
                )
                source_trigger, candidate_repair, repair_message = repair_checks(
                    source_graph,
                    candidate_graph,
                    selected["operation"],
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
            details.append(row)

    oracles = load_oracles_after_selection()
    for row in details:
        oracle = oracles[str(row["event_id"])]
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

    by_event, by_type, summary = summarize(details, experiment)
    details_csv = OUTPUT_DIR / f"{args.prefix}-details.csv"
    by_event_csv = OUTPUT_DIR / f"{args.prefix}-by-event.csv"
    by_type_csv = OUTPUT_DIR / f"{args.prefix}-by-type.csv"
    summary_csv = OUTPUT_DIR / f"{args.prefix}-summary.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    write_csv(details_csv, details)
    write_csv(by_event_csv, by_event)
    write_csv(by_type_csv, by_type)
    write_csv(summary_csv, summary)
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "oracle_loaded_after_selection": True,
                "details": str(details_csv),
                "by_event": str(by_event_csv),
                "by_type": str(by_type_csv),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    lines = [
        f"{experiment} candidate repair",
        f"details={details_csv}",
        f"by_event={by_event_csv}",
        f"by_type={by_type_csv}",
        f"summary={summary_csv}",
        f"json={json_path}",
        "",
    ]
    for row in by_type:
        lines.append(
            "[{semantic_type}] oracle={oracle_accuracy:.2%} closure={full_closure_accuracy:.2%} "
            "strict={strict_event_successes}/{events} abstains={abstains}/{attempts}".format(**row)
        )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
