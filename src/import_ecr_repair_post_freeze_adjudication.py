from __future__ import annotations

import csv
import json
from pathlib import Path

from ecr_repair_post_freeze_blind_v1_common import BENCHMARK_DIR, PROJECT_DIR, write_csv, write_json, write_jsonl
from run_gamma_gold_ir_closure_eval import materialize_update_literal_atomic
from run_gamma_gold_ir_compile_eval import compile_update_literal


ANNOTATION = BENCHMARK_DIR / "private/annotation"
CONSTRUCTION = BENCHMARK_DIR / "private/construction"
PENDING: dict[str, str] = {}
EXPECTED_EVENTS = 80
PILOT_EVENT_IDS = {f"BLIND_E{index:03d}" for index in range(1, 17)}
ROUND2_EVENT_IDS = {f"BLIND_E{index:03d}" for index in range(17, 81)}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    attest = json.loads((ANNOTATION / "independence-attestation.json").read_text(encoding="utf-8"))
    if not attest.get("ann_b_is_different_human_from_ann_a") or not attest.get("constructor_did_not_fill_ann_b"):
        raise SystemExit("pilot independence attestation is not complete")
    round2_attest = json.loads(
        (ANNOTATION / "round2-64/independence-attestation-template.json").read_text(encoding="utf-8")
    )
    round2_required = (
        "attested_by_annotator_a",
        "attested_by_annotator_b",
        "ann_a_and_ann_b_are_different_humans",
        "annotations_completed_without_communication",
        "annotators_did_not_view_each_others_completed_sheets",
        "annotators_did_not_view_proposed_gold_or_oracle",
        "annotators_did_not_view_candidates_or_v24_outputs",
    )
    if not round2_attest.get("attested_at") or not all(round2_attest.get(key) is True for key in round2_required):
        raise SystemExit("round-2 independence attestation is not complete")
    existing_gold_path = CONSTRUCTION / "adjudicated-gold.jsonl"
    existing_gold = [
        json.loads(line)
        for line in existing_gold_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    existing_by_id = {item["event_id"]: item for item in existing_gold}
    if set(existing_by_id) != PILOT_EVENT_IDS:
        raise SystemExit("existing adjudicated pilot must contain exactly BLIND_E001-BLIND_E016")

    a = {r["event_id"]: r for r in rows(ANNOTATION / "round2-64/annotator-ANN_A-template.csv")}
    b = {r["event_id"]: r for r in rows(ANNOTATION / "round2-64/annotator-ANN_B-template.csv")}
    if set(a) != ROUND2_EVENT_IDS or set(b) != ROUND2_EVENT_IDS:
        raise SystemExit("round-2 annotation sheets must contain exactly BLIND_E017-BLIND_E080")
    adjudications = rows(BENCHMARK_DIR / "private/adjudication/adjudication-template.csv")
    supplemental_path = BENCHMARK_DIR / "private/adjudication/adjudication-supplemental-template.csv"
    if supplemental_path.is_file():
        adjudications.extend(rows(supplemental_path))
    decisions = {(r["event_id"], r["field"]): r for r in adjudications}
    proposed = {r["event_id"]: r for r in (json.loads(line) for line in (CONSTRUCTION / "proposed-gold.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
    output = [existing_by_id[event_id] for event_id in sorted(PILOT_EVENT_IDS)]
    for event_id in sorted(set(a) & set(b)):
        if event_id in PENDING:
            continue
        merged = dict(a[event_id])
        decision_item = decisions.get((event_id, "decision"))
        final_decision = decision_item["adjudicated_value"].strip() if decision_item else a[event_id]["decision"].strip()
        for field in ("decision", "partition", "semantic_type", "supporting_window_ids", "old_lexical", "old_datatype", "new_lexical", "new_datatype", "target_cq_pass"):
            if (event_id, field) in decisions:
                item = decisions[(event_id, field)]
                if not item["adjudicated_value"].strip() or item["adjudicator_id"].strip() != "ADJ_C":
                    raise SystemExit(f"incomplete adjudication: {event_id}:{field}")
                merged[field] = item["adjudicated_value"].strip()
            elif final_decision != "REPAIR" and field in {"new_lexical", "new_datatype", "target_cq_pass"}:
                merged[field] = "n/a"
            elif " ".join(a[event_id][field].split()).casefold() != " ".join(b[event_id][field].split()).casefold():
                raise SystemExit(f"unresolved disagreement: {event_id}:{field}")
        base = proposed[event_id]
        base["status"] = "ADJUDICATED_PENDING_ORACLE_IMPORT"
        base["decision"] = merged["decision"]
        base["partition"] = merged["partition"]
        base["semantic_type"] = merged["semantic_type"]
        base["supporting_window_ids"] = [] if merged["supporting_window_ids"].casefold() == "none" else [x for x in merged["supporting_window_ids"].split(";") if x]
        base["target"]["old_value"] = {"kind": "literal", "lexical": merged["old_lexical"], "datatype": merged["old_datatype"]}
        base["replacement"] = None
        if merged["decision"] == "REPAIR":
            base["replacement"] = {"new_value": {"kind": "literal", "lexical": merged["new_lexical"], "datatype": merged["new_datatype"]}}
        base["adjudication_status"] = "ADJUDICATED_BY_ADJ_C"
        base["annotation_provenance"] = {"annotator_a": "ANN_A", "annotator_b": "ANN_B", "adjudicator": "ADJ_C"}
        base.pop("proposed_baseline_candidate_id", None)
        output.append(base)

    if len(output) != EXPECTED_EVENTS or {item["event_id"] for item in output} != PILOT_EVENT_IDS | ROUND2_EVENT_IDS:
        raise SystemExit("adjudicated Gold must contain exactly 80 events")
    write_jsonl(CONSTRUCTION / "adjudicated-gold.jsonl", output)
    write_json(CONSTRUCTION / "reannotation-required.json", {"status": "BLOCKED_PENDING_HUMAN_REANNOTATION", "events": PENDING})

    repair_dir = CONSTRUCTION / "adjudicated-gold-repaired-owl"
    repair_dir.mkdir(parents=True, exist_ok=True)
    closure = []
    for gold in output:
        if gold["decision"] != "REPAIR":
            closure.append({"event_id": gold["event_id"], "status": "SAFE_NO_EDIT"})
            continue
        operation = {"operator": "REPLACE_PROPERTY_VALUE", "subject_iri": gold["target"]["subject_iri"], "predicate_iri": gold["target"]["predicate_iri"], "old_value": gold["target"]["old_value"], "new_value": gold["replacement"]["new_value"]}
        compiled = compile_update_literal(operation)
        if compiled["status"] != "COMPILED":
            closure.append({"event_id": gold["event_id"], "status": compiled["status"]})
            continue
        source = BENCHMARK_DIR / "repair-stage/mutants" / f"{gold['event_id']}.owl"
        result = materialize_update_literal_atomic(source_path=source, operation=operation, dest_path=repair_dir / f"{gold['event_id']}.owl")
        closure.append({"event_id": gold["event_id"], "status": result["status"], "source_unchanged": result.get("source_unchanged", False)})
    write_csv(CONSTRUCTION / "adjudicated-closure.csv", closure)

    active = {g["event_id"] for g in output}
    cqs = [dict(r, status="ADJUDICATED_PENDING_ORACLE_IMPORT") for r in rows(BENCHMARK_DIR / "public/cq/cq-template.csv") if r["event_id"] in active]
    candidates = [r for r in rows(BENCHMARK_DIR / "repair-stage/candidates/candidate-template.csv") if r["event_id"] in {g["event_id"] for g in output if g["decision"] == "REPAIR"}]
    write_csv(CONSTRUCTION / "adjudicated-cq.csv", cqs)
    write_csv(CONSTRUCTION / "adjudicated-candidates.csv", candidates)
    summary = {"status": "FULLY_ADJUDICATED_PENDING_ORACLE" if len(output) == EXPECTED_EVENTS and not PENDING else "PARTIAL_ADJUDICATED", "adjudicated_events": len(output), "pending_reannotation": sorted(PENDING), "partitions": {}, "closure_pass": sum(r["status"] in {"PASS", "SAFE_NO_EDIT"} for r in closure), "official_oracle_imported": False}
    for gold in output:
        summary["partitions"][gold["partition"]] = summary["partitions"].get(gold["partition"], 0) + 1
    write_json(CONSTRUCTION / "adjudicated-build-summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
