from __future__ import annotations

"""Prepare the V2.6 development regression on the previously used 80-event set."""

import argparse
import csv
import hashlib
import json
import os
import shutil
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from ecr_repair_v26 import align_target_frames, construct_finite_candidates, decide_policy, select_candidate, validate_frame_bundle
from ecr_repair_v26_prompt import build_semantic_frame_prompt
from m13_llm_backends import call_llm_json, resume_artifact_is_complete
from rfc213_direct_repair_ir import ontology_literal_assertions
from run_auto_formal_policy_batch_v3 import extract_json
from run_auto_policy_v2_candidate_repair import resolve_source_owl
from rfc213_direct_repair_ir_v25 import resolve_source_window_v25
import run_external_real_holdout_v6_direct_ir_blind as closure_engine
from run_ecr_repair_post_freeze_blind_v1_v25_posthoc import apply_fail_closed_safety_semantics, summarize


PROJECT = Path(__file__).resolve().parents[1]
BENCHMARK = PROJECT / "benchmark/ecr-repair-post-freeze-blind-v1"
METHOD = PROJECT / "method/v26-development"
OUTPUT = PROJECT / "output/ecr-repair-v26-development/80-event-regression"
SEEDS = (20260910, 20260911, 20260912, 20260913, 20260914)


def load_local_env() -> None:
    path = PROJECT / ".env"
    if not path.is_file(): return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped: continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def window_document_maps() -> dict[str, dict[str, str]]:
    """Read only locator identity fields; role/support columns are deliberately ignored."""
    path = BENCHMARK / "private/construction/window-locators.csv"
    maps: dict[str, dict[str, str]] = {}
    for row in csv_rows(path):
        maps.setdefault(row["event_id"], {})[row["window_id"]] = row["document_id"]
    return maps


def source_metadata() -> dict[str, dict[str, str]]:
    return {row["document_id"]: row for row in csv_rows(BENCHMARK / "source-manifest.csv")}


def inject_frozen_metadata(bundle: dict, event: dict, window_map: dict[str, str], sources: dict[str, dict[str, str]]) -> dict:
    enriched = json.loads(json.dumps(bundle))
    allowed = set(event.get("document_ids", []))
    for frame in enriched.get("frames", []):
        document_id = window_map.get(frame.get("window_id", ""), "")
        if document_id not in allowed or document_id not in sources:
            frame["document_id"] = "INVALID_WINDOW_DOCUMENT"
            continue
        metadata = sources[document_id]
        frame["document_id"] = document_id
        frame["valid_from"] = (metadata.get("effective_from") or None)
        frame["valid_to"] = (metadata.get("effective_to") or None)
        frame["authority"] = 100
    return enriched


def audit() -> dict:
    events_path = BENCHMARK / "public/events/events.jsonl"
    gold_path = BENCHMARK / "private/oracle/gold.jsonl"
    candidates_path = BENCHMARK / "repair-stage/candidates/candidate-template.csv"
    events, gold, candidates = jsonl(events_path), jsonl(gold_path), csv_rows(candidates_path)
    event_ids, gold_ids = {row["event_id"] for row in events}, {row["event_id"] for row in gold}
    candidate_counts = Counter(row["event_id"] for row in candidates)
    repair_ids = {row["event_id"] for row in gold if row["decision"] == "REPAIR"}
    missing_evidence = [row["event_id"] for row in events if not (BENCHMARK / row["evidence_file"]).is_file()]
    missing_owl = [row["event_id"] for row in events if not (BENCHMARK / row["source_owl"]).is_file()]
    failures = []
    if len(events) != 80 or event_ids != gold_ids: failures.append("event_or_oracle_coverage")
    if missing_evidence: failures.append("missing_evidence")
    if missing_owl: failures.append("missing_source_owl")
    return {
        "status": "READY_FOR_DEVELOPMENT_REGRESSION" if not failures else "BLOCKED",
        "claim_boundary": "Development regression only; not blind or confirmatory.",
        "events": len(events), "gold_events": len(gold), "repair_events": len(repair_ids),
        "candidate_events": len(candidate_counts), "candidate_rows": len(candidates),
        "candidate_count_distribution": dict(sorted(Counter(candidate_counts.values()).items())),
        "missing_evidence": missing_evidence, "missing_source_owl": missing_owl,
        "legacy_candidate_pool_role": "IGNORED_BY_V26",
        "candidate_constructor": "POST_GATE_FROM_GROUNDED_FRAMES_AND_CURRENT_LITERAL",
        "legacy_repair_events_without_candidates": sorted(repair_ids - set(candidate_counts)),
        "legacy_safety_events_with_candidates": sorted((set(candidate_counts) - repair_ids) & event_ids),
        "failures": failures,
    }


def prepare(output: Path, runs: int) -> dict:
    report = audit()
    if report["status"] != "READY_FOR_DEVELOPMENT_REGRESSION":
        raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
    events = jsonl(BENCHMARK / "public/events/events.jsonl")
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for event in sorted(events, key=lambda row: row["event_id"]):
        for run, seed in enumerate(SEEDS[:runs], 1):
            manifest.append({"event_id": event["event_id"], "run": run, "seed": seed, "domain": event["domain"], "semantic_type": "WITHHELD"})
    with (output / "run-manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest[0]))
        writer.writeheader(); writer.writerows(manifest)
    protocol = {
        "status": "REGISTERED_DEVELOPMENT_REGRESSION", "method_id": "ECR-REPAIR-V2.6-DEVELOPMENT",
        "benchmark": BENCHMARK.name, "benchmark_role": "DEVELOPMENT_AFTER_ERROR_ANALYSIS",
        "events": len(events), "runs_per_event": runs, "attempts": len(manifest), "seeds": list(SEEDS[:runs]),
        "candidate_visibility": "DELAYED_UNTIL_REPAIR_CANDIDATES_REQUIRED",
        "legacy_candidate_csv_used": False,
        "oracle_visibility": "EVALUATION_ONLY", "semantic_type_withheld": True,
        "method_manifest_sha256": sha256(METHOD / "method-manifest.json"),
        "semantic_frame_schema_sha256": sha256(METHOD / "semantic-frame.schema.json"),
    }
    (output / "development-protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return protocol


def raw_path(output: Path, event_id: str, run: str, seed: str) -> Path:
    return output / "raw-semantic-frames" / f"{event_id}-run{run}-seed{seed}.json"


def generate(output: Path, backend: str, timeout: int, resume: bool) -> None:
    events = {row["event_id"]: row for row in jsonl(BENCHMARK / "public/events/events.jsonl")}
    manifest = csv_rows(output / "run-manifest.csv")
    locator_maps = window_document_maps()
    (output / "raw-semantic-frames").mkdir(parents=True, exist_ok=True)
    for item in manifest:
        path = raw_path(output, item["event_id"], item["run"], item["seed"])
        if resume and resume_artifact_is_complete(path):
            print(f"{item['event_id']} run={item['run']} [resume] skipped", flush=True); continue
        event = events[item["event_id"]]
        evidence = (BENCHMARK / event["evidence_file"]).read_text(encoding="utf-8")
        source = resolve_source_owl(event, mutants_dir=BENCHMARK / "repair-stage/mutants", project_dir=BENCHMARK)
        assertions = ontology_literal_assertions(source)
        prompt = build_semantic_frame_prompt(event, evidence, assertions, locator_maps.get(item["event_id"], {}))
        try:
            call = call_llm_json(backend, prompt, int(item["seed"]), timeout, num_predict=1800)
            parsed = extract_json(call.text)
            payload = {"status": "GENERATED", "event_id": item["event_id"], "run": int(item["run"]),
                       "seed": int(item["seed"]), "semantic_frame_raw": parsed if isinstance(parsed, dict) else {},
                       "candidate_used": False, "oracle_used": False, "semantic_type_used": False,
                       "audit": {"backend": call.backend, "model": call.model, "runtime_ms": call.runtime_ms,
                                 "prompt_tokens": call.prompt_tokens, "completion_tokens": call.completion_tokens,
                                 "attempt_count": call.attempt_count, "retry_errors": list(call.retry_errors)}}
        except Exception as exc:
            payload = {"status": f"error:{type(exc).__name__}", "error": str(exc), "event_id": item["event_id"],
                       "run": int(item["run"]), "seed": int(item["seed"]), "candidate_used": False,
                       "oracle_used": False, "semantic_type_used": False}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{item['event_id']} run={item['run']} status={payload['status']}", flush=True)


def execute(output: Path) -> dict:
    events = {row["event_id"]: row for row in jsonl(BENCHMARK / "public/events/events.jsonl")}
    rows = []
    locator_maps, sources = window_document_maps(), source_metadata()
    for item in csv_rows(output / "run-manifest.csv"):
        event, event_id = events[item["event_id"]], item["event_id"]
        path = raw_path(output, event_id, item["run"], item["seed"])
        base = {**item, "decision": "ABSTAIN", "reason": "GENERATION_MISSING", "candidate_revealed": False}
        if not path.is_file(): rows.append(base); continue
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("status") != "GENERATED":
            base["reason"] = "GENERATION_ERROR"; rows.append(base); continue
        evidence = (BENCHMARK / event["evidence_file"]).read_text(encoding="utf-8")
        enriched = inject_frozen_metadata(payload.get("semantic_frame_raw", {}), event, locator_maps.get(event_id, {}), sources)
        validated = validate_frame_bundle(enriched, evidence)
        if validated["status"] == "INVALID_FRAME_SET":
            base["reason"] = "FRAME_OR_GROUNDING_INVALID"; base["errors"] = json.dumps(validated["errors"], ensure_ascii=False)
            rows.append(base); continue
        source = resolve_source_owl(event, mutants_dir=BENCHMARK / "repair-stage/mutants", project_dir=BENCHMARK)
        assertions = [row for row in ontology_literal_assertions(source) if not row["predicate_iri"].endswith("#regressionSentinel")]
        if len(assertions) != 1:
            base["reason"] = "TARGET_ASSERTION_AMBIGUOUS"; rows.append(base); continue
        assertion = assertions[0]
        target = {"subject_iri": assertion["subject_iri"], "predicate_iri": assertion["predicate_iri"],
                  "old_value": {"kind": "literal", "lexical": assertion["lexical"], "datatype": assertion["datatype"]}}
        aligned = align_target_frames(validated["frames"], event["target"]["subject_label"], event["target"]["predicate_label"])
        policy = decide_policy(aligned, event["as_of"], assertion["lexical"], event.get("case_context", ""))
        candidates = construct_finite_candidates(policy, aligned, target)
        decision = select_candidate(policy, candidates, target)
        base.update({"decision": decision["decision"], "reason": decision.get("reason", ""),
                     "candidate_revealed": bool(candidates), "frame_count": len(validated["frames"]), "aligned_frame_count": len(aligned),
                     "predicted_ir_json": json.dumps(decision, ensure_ascii=False, sort_keys=True)})
        rows.append(base)
    with (output / "v26-staged-decisions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    counts = Counter(row["decision"] for row in rows)
    summary = {"status": "EXECUTED_WITHOUT_ORACLE", "attempts": len(rows), "decisions": dict(counts),
               "candidate_revealed_attempts": sum(bool(row["candidate_revealed"]) for row in rows)}
    (output / "v26-staged-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def evaluate(output: Path, timeout: int) -> dict:
    """Open Oracle only here, after immutable raw frames and staged decisions exist."""
    staged = csv_rows(output / "v26-staged-decisions.csv")
    if len(staged) != len(csv_rows(output / "run-manifest.csv")):
        raise SystemExit("staged decision coverage mismatch")
    closure = output / "closure-evaluation"
    raw_dir = closure / "raw-predicted-ir"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    for row in staged:
        manifest_rows.append({key: row[key] for key in ("event_id", "run", "seed", "semantic_type", "domain")})
        parsed = json.loads(row.get("predicted_ir_json") or "{}")
        if parsed.get("decision") == "REPAIR":
            parsed = {key: parsed[key] for key in ("schema_version", "decision", "operation", "target", "replacement", "evidence_spans", "confidence")}
        else:
            parsed = {"schema_version": "predicted-repair-ir-v1", "decision": parsed.get("decision", "ABSTAIN"),
                      "reason": parsed.get("reason") or row.get("reason") or "V26_FAIL_CLOSED"}
        name = f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"
        (raw_dir / name).write_text(json.dumps({"status": "GENERATED", "predicted_ir_raw": parsed}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (closure / "v6-final-blind-manifest-r5.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest_rows[0])); writer.writeheader(); writer.writerows(manifest_rows)

    gold_path = BENCHMARK / "private/oracle/gold.jsonl"
    gold = {row["event_id"]: row for row in jsonl(gold_path)}
    events = {row["event_id"]: row for row in jsonl(BENCHMARK / "public/events/events.jsonl")}
    original_base_row, original_resolver, original_load_gold, original_summarize = (
        closure_engine.base_row, closure_engine.resolve_source_window_v24, closure_engine.load_gold, closure_engine.summarize_rows
    )
    def base_row(item: dict[str, str], row_gold: dict) -> dict:
        result = original_base_row(item, row_gold); result["gold_partition"] = row_gold["partition"]; return result
    try:
        closure_engine.base_row = base_row
        closure_engine.resolve_source_window_v24 = resolve_source_window_v25
        closure_engine.load_gold = lambda _benchmark: gold
        closure_engine.summarize_rows = summarize
        closure_engine.evaluate(SimpleNamespace(timeout=timeout, resolver="v24"), BENCHMARK, closure, events)
        apply_fail_closed_safety_semantics(closure, gold)
    finally:
        closure_engine.base_row, closure_engine.resolve_source_window_v24 = original_base_row, original_resolver
        closure_engine.load_gold, closure_engine.summarize_rows = original_load_gold, original_summarize
    summary = json.loads((closure / "v6-final-blind-summary.json").read_text(encoding="utf-8"))
    protocol = {"status": "DEVELOPMENT_REGRESSION_EVALUATED", "confirmatory": False, "method_frozen": False,
                "oracle_opened_only_in_evaluate": True, "events": 80, "attempts": len(staged)}
    (closure / "evaluation-protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"protocol": protocol, "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=("preflight", "prepare", "generate", "execute", "evaluate"), required=True)
    parser.add_argument("--runs-per-event", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--llm-backend", choices=("deepseek_api", "ollama"), default="deepseek_api")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.runs_per_event <= len(SEEDS):
        raise SystemExit("--runs-per-event must be between 1 and 5")
    output = args.output_dir.resolve()
    if args.step == "preflight": result = audit()
    elif args.step == "prepare": result = prepare(output, args.runs_per_event)
    elif args.step == "generate":
        if not (output / "run-manifest.csv").is_file(): raise SystemExit("run --step prepare first")
        generate(output, args.llm_backend, args.timeout, args.resume); return 0
    elif args.step == "execute":
        if not (output / "run-manifest.csv").is_file(): raise SystemExit("run --step prepare first")
        result = execute(output)
    else:
        if not (output / "v26-staged-decisions.csv").is_file(): raise SystemExit("run --step execute first")
        result = evaluate(output, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") != "BLOCKED" else 2


if __name__ == "__main__":
    load_local_env()
    raise SystemExit(main())
