from __future__ import annotations

"""V2.6 hybrid development regression with independent candidates and entailment ranking."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import run_external_real_holdout_v6_direct_ir_blind as closure_engine
from ecr_repair_v26 import align_target_frames, normalize, validate_frame_bundle
from ecr_repair_v26_candidate_prompt import build_candidate_construction_prompt, build_entailment_ranking_prompt
from m13_llm_backends import call_llm_json, resume_artifact_is_complete
from rfc213_direct_repair_ir import ontology_literal_assertions
from rfc213_direct_repair_ir_v25 import resolve_source_window_v25
from run_auto_formal_policy_batch_v3 import extract_json
from run_auto_policy_v2_candidate_repair import resolve_source_owl
from run_ecr_repair_post_freeze_blind_v1_v25_posthoc import apply_fail_closed_safety_semantics, summarize
from run_ecr_repair_v26_development import (
    BENCHMARK, PROJECT, csv_rows, inject_frozen_metadata, jsonl,
    load_local_env, source_metadata, window_document_maps,
)


FRONTEND_OUTPUT = PROJECT / "output/ecr-repair-v26-development/80-event-regression"
OUTPUT = PROJECT / "output/ecr-repair-v26-development/80-event-hybrid-canonical-safe-v3"


def target_assertion(event: dict[str, Any]) -> dict[str, str]:
    source = resolve_source_owl(event, mutants_dir=BENCHMARK / "repair-stage/mutants", project_dir=BENCHMARK)
    rows = [row for row in ontology_literal_assertions(source) if not row["predicate_iri"].endswith("#regressionSentinel")]
    if len(rows) != 1: raise RuntimeError(f"target assertion count={len(rows)} for {event['event_id']}")
    return rows[0]


def raw_candidate_path(output: Path, item: dict[str, str]) -> Path:
    return output / "raw-independent-candidates" / f"{item['event_id']}-run{item['run']}-seed{item['seed']}.json"


def raw_rank_path(output: Path, item: dict[str, str]) -> Path:
    return output / "raw-entailment-ranking" / f"{item['event_id']}-run{item['run']}-seed{item['seed']}.json"


def prepare(output: Path) -> dict[str, Any]:
    source_manifest = FRONTEND_OUTPUT / "run-manifest.csv"
    source_protocol = FRONTEND_OUTPUT / "development-protocol.json"
    source_stages = FRONTEND_OUTPUT / "v26-staged-decisions.csv"
    if not all(path.is_file() for path in (source_manifest, source_protocol, source_stages)):
        raise SystemExit("complete V2.6 front-end prepare/generate/execute first")
    output.mkdir(parents=True, exist_ok=True)
    for source, name in ((source_manifest, "run-manifest.csv"), (source_stages, "frozen-front-end-decisions.csv")):
        (output / name).write_bytes(source.read_bytes())
    protocol = {
        "status": "REGISTERED_HYBRID_DEVELOPMENT_REGRESSION", "confirmatory": False,
        "events": 80, "attempts": len(csv_rows(source_manifest)),
        "front_end_artifact_sha256": hashlib.sha256(source_stages.read_bytes()).hexdigest(),
        "candidate_constructor": "SLOT_BASED_V2", "candidate_constructor_independent_of_frames": True, "candidate_constructor_oracle_blind": True,
        "candidate_reveal_after_gate_only": True, "ranking_output_restricted_to_candidate_id": True,
    }
    (output / "hybrid-protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return protocol


def compile_slot_candidates(bundle: dict[str, Any], event: dict[str, Any], evidence: str, current: str) -> tuple[list[dict], list[str]]:
    """Compile bounded literal variants without model-side candidate selection."""
    errors, accepted, seen = [], [], set()
    if bundle.get("schema_version") != "candidate-slots-v2" or bundle.get("event_id") != event["event_id"]:
        return [], ["candidate_bundle_contract"]
    def add(value: Any, window_id: Any, quote: Any, kind: str) -> None:
        if not isinstance(value, str) or not value.strip() or normalize(value) in seen: return
        seen.add(normalize(value)); accepted.append({"candidate_id": f"CAND_{len(accepted)+1:03d}", "value": value.strip(),
                                                     "window_id": window_id, "quote": quote, "kind": kind})
    add(current, None, None, "CURRENT_VALUE")
    for index, frame in enumerate(bundle.get("property_frames", [])[:6]):
        quote = frame.get("quote"); window_id = frame.get("window_id")
        if not isinstance(quote, str) or quote not in evidence:
            errors.append(f"frame[{index}] quote_not_verbatim"); continue
        add(frame.get("core_value"), window_id, quote, "CANONICAL_VALUE")
        add(frame.get("scope_complete_value"), window_id, quote, "SCOPE_COMPLETE_VALUE")
        add(frame.get("surface_value"), window_id, quote, "VERBATIM_VALUE")
        add(quote, window_id, quote, "FULL_QUOTE_VALUE")
        members = [str(value).strip() for value in frame.get("enumeration_members", []) if str(value).strip()]
        if members:
            add(", ".join(members), window_id, quote, "ENUMERATION_VALUE")
            if len(members) > 1: add(", ".join(members[:-1]) + ", and " + members[-1], window_id, quote, "ENUMERATION_VALUE")
        numeric, unit = frame.get("numeric_value"), frame.get("unit")
        if numeric is not None:
            add(f"{numeric} {unit}".strip(), window_id, quote, "NUMERIC_UNIT_VALUE")
        core, modality = frame.get("core_value"), frame.get("modality")
        if isinstance(core, str) and modality in {"MUST", "MUST_NOT", "SHOULD", "SHOULD_NOT", "MAY"}:
            add(f"{modality} {core}", window_id, quote, "MODAL_VALUE")
            add(f"{core} ({modality})", window_id, quote, "MODAL_SUFFIX_VALUE")
        if isinstance(core, str):
            for condition in frame.get("conditions", [])[:2]:
                add(f"{core} when {condition}", window_id, quote, "CONDITIONAL_VALUE")
                add(f"{core} if {condition}", window_id, quote, "CONDITIONAL_VALUE")
            for exception in frame.get("exceptions", [])[:2]:
                add(f"{core} unless {exception}", window_id, quote, "EXCEPTION_VALUE")
    if len(accepted) > 32: accepted = accepted[:32]
    return accepted, errors


def generate_candidates(output: Path, backend: str, timeout: int, resume: bool) -> None:
    events = {row["event_id"]: row for row in jsonl(BENCHMARK / "public/events/events.jsonl")}
    (output / "raw-independent-candidates").mkdir(parents=True, exist_ok=True)
    for item in csv_rows(output / "run-manifest.csv"):
        path = raw_candidate_path(output, item)
        if resume and resume_artifact_is_complete(path): print(f"{item['event_id']} candidates [resume] skipped", flush=True); continue
        event, assertion = events[item["event_id"]], target_assertion(events[item["event_id"]])
        evidence = (BENCHMARK / event["evidence_file"]).read_text(encoding="utf-8")
        try:
            call = call_llm_json(backend, build_candidate_construction_prompt(event, evidence, assertion), int(item["seed"]), timeout, num_predict=1400)
            parsed = extract_json(call.text)
            payload = {"status": "GENERATED", "candidate_set_raw": parsed if isinstance(parsed, dict) else {},
                       "semantic_frames_used": False, "gate_decision_used": False, "oracle_used": False,
                       "audit": {"backend": call.backend, "model": call.model, "runtime_ms": call.runtime_ms,
                                 "prompt_tokens": call.prompt_tokens, "completion_tokens": call.completion_tokens}}
        except Exception as exc: payload = {"status": f"error:{type(exc).__name__}", "error": str(exc), "oracle_used": False}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{item['event_id']} candidates status={payload['status']}", flush=True)


def grounded_aligned_frames(event: dict[str, Any], item: dict[str, str]) -> list[dict]:
    raw = FRONTEND_OUTPUT / "raw-semantic-frames" / f"{item['event_id']}-run{item['run']}-seed{item['seed']}.json"
    payload = json.loads(raw.read_text(encoding="utf-8-sig"))
    evidence = (BENCHMARK / event["evidence_file"]).read_text(encoding="utf-8")
    enriched = inject_frozen_metadata(payload["semantic_frame_raw"], event, window_document_maps().get(item["event_id"], {}), source_metadata())
    validated = validate_frame_bundle(enriched, evidence)
    return align_target_frames(validated["frames"], event["target"]["subject_label"], event["target"]["predicate_label"])


def rank(output: Path, backend: str, timeout: int, resume: bool) -> None:
    events = {row["event_id"]: row for row in jsonl(BENCHMARK / "public/events/events.jsonl")}
    front = {(row["event_id"], row["run"]): row for row in csv_rows(output / "frozen-front-end-decisions.csv")}
    (output / "raw-entailment-ranking").mkdir(parents=True, exist_ok=True)
    decisions = []
    for item in csv_rows(output / "run-manifest.csv"):
        gate = front[(item["event_id"], item["run"])]
        if gate["decision"] != "REPAIR":
            terminal_decision = gate["decision"] if gate["decision"] in {"NO_CHANGE", "ABSTAIN"} else "ABSTAIN"
            terminal = json.dumps({"schema_version": "predicted-repair-ir-v1", "decision": terminal_decision,
                                   "reason": gate.get("reason") or "FRONT_END_FAIL_CLOSED"}, ensure_ascii=False)
            decisions.append({**item, "decision": terminal_decision, "reason": gate["reason"], "candidate_revealed": False, "predicted_ir_json": terminal}); continue
        candidate_payload = json.loads(raw_candidate_path(output, item).read_text(encoding="utf-8-sig"))
        event, assertion = events[item["event_id"]], target_assertion(events[item["event_id"]])
        evidence = (BENCHMARK / event["evidence_file"]).read_text(encoding="utf-8")
        candidates, errors = compile_slot_candidates(candidate_payload.get("candidate_set_raw", {}), event, evidence, assertion["lexical"])
        path = raw_rank_path(output, item)
        if not (resume and resume_artifact_is_complete(path)):
            try:
                call = call_llm_json(backend, build_entailment_ranking_prompt(event, grounded_aligned_frames(event, item), candidates), int(item["seed"]), timeout, num_predict=500)
                parsed = extract_json(call.text)
                rank_payload = {"status": "GENERATED", "ranking_raw": parsed if isinstance(parsed, dict) else {}, "oracle_used": False,
                                "audit": {"backend": call.backend, "model": call.model, "runtime_ms": call.runtime_ms,
                                          "prompt_tokens": call.prompt_tokens, "completion_tokens": call.completion_tokens}}
            except Exception as exc: rank_payload = {"status": f"error:{type(exc).__name__}", "error": str(exc), "oracle_used": False}
            path.write_text(json.dumps(rank_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rank_payload = json.loads(path.read_text(encoding="utf-8-sig"))
        choice = rank_payload.get("ranking_raw", {})
        matches = [row for row in candidates if row["candidate_id"] == choice.get("candidate_id")] if choice.get("decision") == "SELECT" and choice.get("entailed") is True else []
        if len(matches) != 1:
            ir = {"schema_version": "predicted-repair-ir-v1", "decision": "ABSTAIN", "reason": "NO_UNIQUE_ENTAILED_CANDIDATE"}
        else:
            selected = matches[0]
            old = {"kind": "literal", "lexical": assertion["lexical"], "datatype": assertion["datatype"]}
            ir = {"schema_version": "predicted-repair-ir-v1", "decision": "REPAIR", "operation": "UPDATE_LITERAL",
                  "target": {"subject_iri": assertion["subject_iri"], "predicate_iri": assertion["predicate_iri"], "old_value": old},
                  "replacement": {"new_value": {"kind": "literal", "lexical": selected["value"], "datatype": assertion["datatype"]}},
                  "evidence_spans": [selected["quote"] or grounded_aligned_frames(event, item)[0]["quote"]], "confidence": 1.0}
        decisions.append({**item, "decision": ir["decision"], "reason": ir.get("reason", ""), "candidate_revealed": True,
                          "candidate_validation_errors": json.dumps(errors, ensure_ascii=False), "predicted_ir_json": json.dumps(ir, ensure_ascii=False, sort_keys=True)})
        print(f"{item['event_id']} ranking decision={ir['decision']}", flush=True)
    fields = sorted({key for row in decisions for key in row})
    with (output / "hybrid-decisions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(decisions)
    summary = {"attempts": len(decisions), "decisions": dict(Counter(row["decision"] for row in decisions))}
    (output / "hybrid-stage-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def evaluate(output: Path, timeout: int) -> None:
    rows = csv_rows(output / "hybrid-decisions.csv"); closure = output / "closure-evaluation"; raw_dir = closure / "raw-predicted-ir"; raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for row in rows:
        manifest.append({key: row[key] for key in ("event_id", "run", "seed", "semantic_type", "domain")})
        ir = json.loads(row["predicted_ir_json"])
        (raw_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json").write_text(json.dumps({"status":"GENERATED","predicted_ir_raw":ir}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    with (closure / "v6-final-blind-manifest-r5.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(manifest[0]));writer.writeheader();writer.writerows(manifest)
    events={row["event_id"]:row for row in jsonl(BENCHMARK/"public/events/events.jsonl")}; gold={row["event_id"]:row for row in jsonl(BENCHMARK/"private/oracle/gold.jsonl")}
    old=(closure_engine.base_row,closure_engine.resolve_source_window_v24,closure_engine.load_gold,closure_engine.summarize_rows)
    def base(item,g):
        row=old[0](item,g);row["gold_partition"]=g["partition"];return row
    try:
        closure_engine.base_row=base;closure_engine.resolve_source_window_v24=resolve_source_window_v25;closure_engine.load_gold=lambda _:gold;closure_engine.summarize_rows=summarize
        closure_engine.evaluate(SimpleNamespace(timeout=timeout,resolver="v24"),BENCHMARK,closure,events);apply_fail_closed_safety_semantics(closure,gold)
    finally: closure_engine.base_row,closure_engine.resolve_source_window_v24,closure_engine.load_gold,closure_engine.summarize_rows=old


def main() -> int:
    load_local_env(); parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step",required=True,choices=("prepare","generate-candidates","rank","evaluate"));parser.add_argument("--output-dir",type=Path,default=OUTPUT)
    parser.add_argument("--llm-backend",choices=("deepseek_api","ollama"),default="deepseek_api");parser.add_argument("--timeout",type=int,default=180);parser.add_argument("--resume",action="store_true")
    args=parser.parse_args();output=args.output_dir.resolve()
    if args.step=="prepare": print(json.dumps(prepare(output),ensure_ascii=False,indent=2))
    elif args.step=="generate-candidates": generate_candidates(output,args.llm_backend,args.timeout,args.resume)
    elif args.step=="rank": rank(output,args.llm_backend,args.timeout,args.resume)
    else: evaluate(output,args.timeout)
    return 0


if __name__ == "__main__": raise SystemExit(main())
