from __future__ import annotations

"""Offline audit for the shortcut-fixed v6.1 benchmark revision."""

import csv
import json
from collections import Counter
from pathlib import Path

import validate_benchmark as benchmark_validator
from rdflib import URIRef

from rfc213_direct_repair_ir import benchmark_literal_from_span, source_windows
from run_external_real_v1_symbolic_closure import load_graph, term_from_spec
from run_gamma_gold_ir_closure_eval import materialize_update_literal_atomic
from run_gamma_gold_ir_compile_eval import compile_update_literal
from semantic_v2_common import PROJECT_DIR


NAME = "external-real-holdout-v6-1-direct-ir-blind"
BENCHMARK = PROJECT_DIR / "benchmark" / NAME
OUTPUT = PROJECT_DIR / "output" / NAME / "audits"
NEUTRAL = "NORMATIVE_EVIDENCE_ASSESSMENT"


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def gold_rows() -> list[dict]:
    return [json.loads(line) for line in (BENCHMARK / "private/oracle/gold-repair-ir.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def operation(row: dict) -> dict:
    return {"operator": "REPLACE_PROPERTY_VALUE", "subject_iri": row["target"]["subject_iri"],
            "predicate_iri": row["target"]["predicate_iri"], "old_value": row["target"]["old_value"],
            "new_value": row["replacement"]["new_value"]}


def main() -> int:
    errors: list[str] = []
    events = [json.loads(line) for line in (BENCHMARK / "public/events/events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = gold_rows()
    gold_map = {row["event_id"]: row for row in gold}
    if len(events) != 300 or len(gold) != 300:
        errors.append("event_or_gold_count")
    if any(row["semantic_type"] != NEUTRAL for row in events):
        errors.append("public_semantic_type_not_neutral")
    if any(row["status"] != "READY" for row in events):
        errors.append("public_event_not_ready")
    if any(not row["source_owl"].startswith("public/ontology-current/") for row in events):
        errors.append("ontology_not_public")

    positions = Counter(row.get("gold_source_window") for row in gold if row["decision"] != "ABSTAIN")
    if positions != Counter({f"SOURCE_WINDOW_{i}": 52 for i in range(1, 6)}):
        errors.append(f"non_abstain_position_balance:{dict(positions)}")
    repair_positions = Counter(row.get("gold_source_window") for row in gold if row["decision"] == "REPAIR")
    if set(repair_positions) != {f"SOURCE_WINDOW_{i}" for i in range(1, 6)} or max(repair_positions.values()) - min(repair_positions.values()) > 3:
        errors.append(f"repair_position_imbalance:{dict(repair_positions)}")

    target_exact = 0
    for event in events:
        event_id = event["event_id"]
        current = BENCHMARK / event["source_owl"]
        mutant = BENCHMARK / "repair-stage/mutants" / f"{event_id}.owl"
        if not current.is_file() or current.read_bytes() != mutant.read_bytes():
            errors.append(f"public_ontology_mismatch:{event_id}")
        row = gold_map[event_id]
        if row["decision"] != "REPAIR":
            continue
        evidence = (BENCHMARK / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
        windows = dict(source_windows(evidence))
        marker = str(row["gold_source_window"]).replace("SOURCE_WINDOW_", "")
        selected = windows.get(marker, "")
        expected = benchmark_literal_from_span(row["target"]["old_value"]["lexical"], selected)
        if expected == row["replacement"]["new_value"]["lexical"]:
            target_exact += 1
        else:
            errors.append(f"target_contract:{event_id}")

    for name in ("H6-A01.csv", "H6-B01.csv"):
        rows = csv_rows(BENCHMARK / "private/annotation" / name)
        if len(rows) != 300 or any(not row.get("decision") for row in rows):
            errors.append(f"annotation_incomplete:{name}")
        if any(row.get("gold_source_window") not in {"NONE", *(f"SOURCE_WINDOW_{i}" for i in range(1, 6))} for row in rows):
            errors.append(f"annotation_window_invalid:{name}")

    closure_dir = OUTPUT / "gamma-closure"
    closure_dir.mkdir(parents=True, exist_ok=True)
    compiled = closed = 0
    for row in gold:
        if row["decision"] != "REPAIR":
            continue
        op = operation(row)
        result = compile_update_literal(op)
        if result["status"] != "COMPILED":
            errors.append(f"gamma_compile:{row['event_id']}"); continue
        compiled += 1
        source = BENCHMARK / "public/ontology-current" / f"{row['event_id']}.owl"
        destination = closure_dir / f"{row['event_id']}.owl"
        applied = materialize_update_literal_atomic(source_path=source, operation=op, dest_path=destination)
        if applied["status"] != "PASS" or not applied["source_unchanged"]:
            errors.append(f"gamma_apply:{row['event_id']}"); continue
        graph = load_graph(destination)
        target = (URIRef(op["subject_iri"]), URIRef(op["predicate_iri"]), term_from_spec(op["new_value"]))
        reasoner = benchmark_validator.run_reasoner(destination, 180)
        if target in graph and reasoner.get("status") == "CONSISTENT":
            closed += 1
        else:
            errors.append(f"closure:{row['event_id']}")

    report = {"benchmark": NAME, "status": "PASS" if not errors else "FAIL", "events": len(events),
              "repair_events": sum(row["decision"] == "REPAIR" for row in gold),
              "safety_events": sum(row["decision"] != "REPAIR" for row in gold),
              "public_semantic_type": NEUTRAL, "gold_position_all_non_abstain": dict(sorted(positions.items())),
              "gold_position_repair": dict(sorted(repair_positions.items())), "target_contract_exact": target_exact,
              "gamma_compiled": compiled, "gamma_closed": closed, "errors": errors}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "v6-1-full-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
