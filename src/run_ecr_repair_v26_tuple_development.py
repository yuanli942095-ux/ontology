from __future__ import annotations

"""Offline tuple-unified V2.6 development regression on existing 80-event raw artifacts."""

import argparse
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import run_external_real_holdout_v6_direct_ir_blind as closure_engine
from ecr_repair_v26 import align_target_frames, validate_frame_bundle
from ecr_repair_v26_tuple import NormativeTuple, compile_literal, gate_tuples, rank_tuples, tuple_from_frame, tuple_from_slot
from ecr_repair_v26_canonical_mapper import build_predicate_vocabulary, canonical_gate, map_tuple, rank_canonical
from ecr_repair_v26_typed_reasoner import compile_typed_literal, infer_profile, rank_typed, typed_gate
from rfc213_direct_repair_ir_v25 import resolve_source_window_v25
from run_ecr_repair_post_freeze_blind_v1_v25_posthoc import apply_fail_closed_safety_semantics, summarize
from run_ecr_repair_v26_development import BENCHMARK, PROJECT, csv_rows, inject_frozen_metadata, jsonl, source_metadata, window_document_maps
from run_ecr_repair_v26_hybrid_development import target_assertion


FRONT = PROJECT / "output/ecr-repair-v26-development/80-event-regression"
SLOTS = PROJECT / "output/ecr-repair-v26-development/80-event-hybrid-slot-regression/raw-independent-candidates"
OUTPUT = PROJECT / "output/ecr-repair-v26-development/80-event-tuple-unified-v4"
CANONICAL_OUTPUT = PROJECT / "output/ecr-repair-v26-development/80-event-canonical-tuple-v5"
TYPED_OUTPUT = PROJECT / "output/ecr-repair-v26-development/80-event-typed-schema-v6"


def slot_path(event_id: str, run: str, seed: str) -> Path:
    return SLOTS / f"{event_id}-run{run}-seed{seed}.json"


def candidate_tuples(bundle: dict, current: str) -> list[dict]:
    rows = [{"candidate_id": "TUPLE_001", "tuple": NormativeTuple(current), "source": {"core_value": current}, "quote": None}]
    seen = {rows[0]["tuple"].value_key() + rows[0]["tuple"].applicability_key()}
    for frame in bundle.get("property_frames", [])[:6]:
        item = tuple_from_slot(frame); key = item.value_key() + item.applicability_key()
        if not item.core or key in seen: continue
        seen.add(key); rows.append({"candidate_id": f"TUPLE_{len(rows)+1:03d}", "tuple": item, "source": frame, "quote": frame.get("quote")})
    return rows


def run(canonical: bool = False, typed: bool = False) -> None:
    output = TYPED_OUTPUT if typed else (CANONICAL_OUTPUT if canonical else OUTPUT)
    output.mkdir(parents=True, exist_ok=True)
    events = {row["event_id"]: row for row in jsonl(BENCHMARK / "public/events/events.jsonl")}
    locators, sources = window_document_maps(), source_metadata()
    decisions = []
    for item in csv_rows(FRONT / "run-manifest.csv"):
        event_id, event = item["event_id"], events[item["event_id"]]
        evidence = (BENCHMARK / event["evidence_file"]).read_text(encoding="utf-8")
        raw_frame = json.loads((FRONT / "raw-semantic-frames" / f"{event_id}-run{item['run']}-seed{item['seed']}.json").read_text(encoding="utf-8-sig"))
        enriched = inject_frozen_metadata(raw_frame.get("semantic_frame_raw", {}), event, locators.get(event_id, {}), sources)
        validated = validate_frame_bundle(enriched, evidence)
        aligned = align_target_frames(validated["frames"], event["target"]["subject_label"], event["target"]["predicate_label"])
        assertion = target_assertion(event)
        vocabulary = build_predicate_vocabulary(assertion["predicate_iri"], event["target"]["predicate_label"])
        evidence_tuples = [tuple_from_frame(frame) for frame in aligned]
        canonical_evidence = [map_tuple(item, vocabulary) for item in evidence_tuples]
        profile = infer_profile(assertion["predicate_iri"], event["target"]["predicate_label"], canonical_evidence)
        gate = typed_gate(canonical_evidence, profile, event.get("case_context", ""), event["as_of"]) if typed else (canonical_gate(canonical_evidence, event["as_of"]) if canonical else gate_tuples(evidence_tuples, event["as_of"]))
        ir: dict
        if gate["decision"] != "CANDIDATE_EVALUATION_REQUIRED":
            ir = {"schema_version": "predicted-repair-ir-v1", "decision": "ABSTAIN", "reason": gate["reason"]}
            ranking = {"decision": "NOT_RUN"}
        else:
            slot_payload = json.loads(slot_path(event_id, item["run"], item["seed"]).read_text(encoding="utf-8-sig"))
            candidates = candidate_tuples(slot_payload.get("candidate_set_raw", {}), assertion["lexical"])
            if canonical or typed:
                for candidate in candidates: candidate["canonical_tuple"] = map_tuple(candidate["tuple"], vocabulary)
                ranking = rank_typed(gate["tuple"], candidates, profile) if typed else rank_canonical(gate["tuple"], candidates)
            else:
                ranking = rank_tuples(gate["tuple"], candidates)
            if ranking["decision"] != "SELECT":
                ir = {"schema_version": "predicted-repair-ir-v1", "decision": "ABSTAIN", "reason": ranking["reason"]}
            else:
                selected = ranking["candidate"]; literal = compile_typed_literal(selected, profile) if typed else compile_literal(selected)
                if selected["candidate_id"] == "TUPLE_001":
                    ir = {"schema_version": "predicted-repair-ir-v1", "decision": "NO_CHANGE", "reason": "CURRENT_TUPLE_ENTAILED"}
                elif not selected.get("quote") or selected["quote"] not in evidence:
                    ir = {"schema_version": "predicted-repair-ir-v1", "decision": "ABSTAIN", "reason": "SELECTED_TUPLE_NOT_GROUNDED"}
                else:
                    old = {"kind": "literal", "lexical": assertion["lexical"], "datatype": assertion["datatype"]}
                    ir = {"schema_version": "predicted-repair-ir-v1", "decision": "REPAIR", "operation": "UPDATE_LITERAL",
                          "target": {"subject_iri": assertion["subject_iri"], "predicate_iri": assertion["predicate_iri"], "old_value": old},
                          "replacement": {"new_value": {"kind": "literal", "lexical": literal, "datatype": assertion["datatype"]}},
                          "evidence_spans": [selected["quote"]], "confidence": ranking["ranking"][0]["score"]}
        decisions.append({**item, "gate_decision": gate["decision"], "ranking_decision": ranking["decision"],
                          "decision": ir["decision"], "predicted_ir_json": json.dumps(ir, ensure_ascii=False, sort_keys=True)})
    fields = list(decisions[0])
    with (output / "tuple-decisions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(decisions)
    evaluate(decisions, events, output, canonical, typed)


def evaluate(rows: list[dict], events: dict, output: Path, canonical: bool, typed: bool) -> None:
    closure=output/"closure-evaluation";raw=closure/"raw-predicted-ir";raw.mkdir(parents=True,exist_ok=True);manifest=[]
    for row in rows:
        manifest.append({key:row[key] for key in ("event_id","run","seed","semantic_type","domain")})
        (raw/f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json").write_text(json.dumps({"status":"GENERATED","predicted_ir_raw":json.loads(row["predicted_ir_json"])},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with (closure/"v6-final-blind-manifest-r5.csv").open("w",encoding="utf-8-sig",newline="") as h:
        w=csv.DictWriter(h,fieldnames=list(manifest[0]));w.writeheader();w.writerows(manifest)
    gold={x["event_id"]:x for x in jsonl(BENCHMARK/"private/oracle/gold.jsonl")};old=(closure_engine.base_row,closure_engine.resolve_source_window_v24,closure_engine.load_gold,closure_engine.summarize_rows)
    def base(item,g):r=old[0](item,g);r["gold_partition"]=g["partition"];return r
    try:
        closure_engine.base_row=base;closure_engine.resolve_source_window_v24=resolve_source_window_v25;closure_engine.load_gold=lambda _:gold;closure_engine.summarize_rows=summarize
        closure_engine.evaluate(SimpleNamespace(timeout=180,resolver="v24"),BENCHMARK,closure,events);apply_fail_closed_safety_semantics(closure,gold)
    finally:closure_engine.base_row,closure_engine.resolve_source_window_v24,closure_engine.load_gold,closure_engine.summarize_rows=old
    status = "TYPED_SCHEMA_DEVELOPMENT_REGRESSION" if typed else ("CANONICAL_TUPLE_DEVELOPMENT_REGRESSION" if canonical else "TUPLE_UNIFIED_DEVELOPMENT_REGRESSION")
    (output/"protocol.json").write_text(json.dumps({"status":status,"model_calls":0,"confirmatory":False,"canonical_mapper":canonical or typed,"predicate_value_ontology":typed,"tuple_ranking":True,"literal_compiled_after_selection":True},indent=2)+"\n",encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical", action="store_true")
    parser.add_argument("--typed", action="store_true")
    args=parser.parse_args()
    run(args.canonical, args.typed)
