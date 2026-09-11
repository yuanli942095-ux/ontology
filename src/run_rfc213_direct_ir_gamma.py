from __future__ import annotations

"""Candidate-blind R2b Direct Predicted-IR to frozen Gamma smoke runner.

Before a separate method freeze is produced this runner intentionally supports
only the five pre-registered smoke events. It never reads candidates or Oracle
during generation or validation. Private data is opened only by --step evaluate.
"""

import argparse
import csv
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
import validate_benchmark as benchmark_validator
from rdflib import RDF, URIRef

from evaluate_rfc213_phase3_gamma_closure import canonical_operation, summarize
from m13_llm_backends import call_llm_json, resume_artifact_is_complete
from rfc213_direct_repair_ir import (
    build_direct_ir_prompt,
    canonicalize_surface,
    ontology_literal_assertions,
    schema_sha256,
    to_gamma_operation,
    validate_predicted_ir,
)
from run_auto_policy_v2_candidate_repair import resolve_source_owl
from run_external_real_v1_symbolic_closure import graph_delta, load_graph, term_from_spec
from run_gamma_gold_ir_closure_eval import materialize_update_literal_atomic
from run_gamma_gold_ir_compile_eval import compile_update_literal
from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "rfc-213-confirmatory-core"
DEFAULT_OUTPUT_ROOT = PROJECT_DIR / "output" / "rfc-213-confirmatory-core" / "phase3-direct-ir"
DEFAULT_FREEZE_MANIFEST = PROJECT_DIR / "method" / "ecr-ir-gamma" / "freeze-v1" / "method-freeze-manifest.json"
SEED = 20260829
SMOKE_IDS = ("H5_E001", "H5_E002", "H5_E003", "H5_E004", "H5_E005")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def event_map(benchmark: Path) -> dict[str, dict[str, str]]:
    rows = read_csv(benchmark / "public/events/external-real-event-template.csv")
    return {row["event_id"]: row for row in rows if row.get("status") == "READY"}


def profile_manifest(events: dict[str, dict[str, str]], profile: str) -> list[dict[str, str]]:
    event_ids = list(SMOKE_IDS) if profile == "smoke" else sorted(events)
    return [
        {
            "event_id": event_id,
            "run": "1",
            "seed": str(SEED),
            "semantic_type": events[event_id]["semantic_type"],
            "domain": events[event_id]["domain"],
        }
        for event_id in event_ids
    ]


def manifest_path(output: Path, profile: str) -> Path:
    return output / f"r2b-{profile}-manifest.csv"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_method_freeze(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"frozen method manifest is required: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    claimed = payload.get("manifest_sha256", "")
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    actual = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if claimed != actual:
        raise SystemExit("method freeze manifest self-hash mismatch")
    for item in payload.get("files", []):
        file_path = PROJECT_DIR / item["path"]
        if not file_path.is_file() or sha256_file(file_path) != item["sha256"]:
            raise SystemExit(f"frozen method file mismatch: {item['path']}")
    requested_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip()
    if payload.get("llm", {}).get("requested_model") != requested_model:
        raise SystemExit(
            f"DEEPSEEK_MODEL mismatch: frozen={payload.get('llm', {}).get('requested_model')} current={requested_model}"
        )
    return payload


def verify_benchmark_freeze(path: Path, expected_manifest_sha256: str) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("manifest_sha256") != expected_manifest_sha256:
        raise SystemExit("RFC-213 benchmark manifest mismatch")
    prefix = "benchmark/rfc-213-confirmatory-core/"
    artifacts = [item for item in payload.get("files", []) if item.get("path", "").startswith(prefix)]
    for item in artifacts:
        file_path = PROJECT_DIR / item["path"]
        if not file_path.is_file() or sha256_file(file_path) != item["sha256"]:
            raise SystemExit(f"frozen RFC-213 benchmark file mismatch: {item['path']}")
    return len(artifacts)


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    write_csv(path, rows)


def load_candidates_for_offline_scoring(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (row["event_id"], row["candidate_id"]): json.loads(row["operation_json"])
        for row in read_csv(path)
        if row.get("status") == "READY"
    }


def extract_json(text: str) -> dict[str, Any]:
    parsed = v3.extract_json(text)
    return parsed if isinstance(parsed, dict) else {}


def generate(args: argparse.Namespace, benchmark: Path, output: Path) -> None:
    events = event_map(benchmark)
    manifest = read_csv(manifest_path(output, args.profile))
    raw_dir = output / "raw-predicted-ir"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows_out: list[dict[str, Any]] = []
    for row in manifest:
        event = events[row["event_id"]]
        raw_path = raw_dir / f"{row['event_id']}-run1-seed{row['seed']}.json"
        if args.resume and resume_artifact_is_complete(raw_path):
            print(f"{row['event_id']} [resume] skipped", flush=True)
            continue
        evidence_path = benchmark / "public/excerpts" / f"{row['event_id']}-evidence.md"
        evidence = evidence_path.read_text(encoding="utf-8")
        source = resolve_source_owl(
            event, mutants_dir=benchmark / "repair-stage/mutants", project_dir=benchmark
        )
        assertions = ontology_literal_assertions(source)
        prompt = build_direct_ir_prompt(event, evidence, assertions)
        try:
            call = call_llm_json(
                args.llm_backend, prompt, int(row["seed"]), args.timeout, num_predict=900
            )
            parsed = extract_json(call.text)
            payload = {
                "event_id": row["event_id"],
                "run": 1,
                "seed": int(row["seed"]),
                "raw_model_output": call.text,
                "predicted_ir_raw": parsed,
                "candidate_used": False,
                "oracle_used": False,
                "private_data_used": False,
                "ontology_assertion_count": len(assertions),
                "schema_sha256": schema_sha256(),
                "audit": {
                    "backend": call.backend,
                    "model": call.model,
                    "runtime_ms": call.runtime_ms,
                    "prompt_tokens": call.prompt_tokens,
                    "completion_tokens": call.completion_tokens,
                    "attempt_count": call.attempt_count,
                    "retry_errors": list(call.retry_errors),
                },
            }
            status = "GENERATED"
        except Exception as exc:
            payload = {
                "event_id": row["event_id"], "run": 1, "seed": int(row["seed"]),
                "status": f"error:{type(exc).__name__}", "error": str(exc),
                "candidate_used": False, "oracle_used": False, "private_data_used": False,
            }
            status = payload["status"]
        payload["status"] = status
        raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rows_out.append({"event_id": row["event_id"], "status": status, "raw_file": str(raw_path.relative_to(PROJECT_DIR))})
        print(f"{row['event_id']} status={status}", flush=True)
    if rows_out:
        existing_path = output / "r2b-generation-summary.csv"
        existing = read_csv(existing_path) if args.resume and existing_path.is_file() else []
        merged = {row["event_id"]: row for row in existing}
        merged.update({row["event_id"]: row for row in rows_out})
        write_csv(existing_path, [merged[key] for key in sorted(merged)])


def evaluate(args: argparse.Namespace, benchmark: Path, output: Path) -> None:
    events = event_map(benchmark)
    manifest = read_csv(manifest_path(output, args.profile))
    raw_dir = output / "raw-predicted-ir"
    materialized_dir = output / "materialized-predicted-owl"
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
            "event_id": event_id, "semantic_type": event["semantic_type"], "domain": event["domain"],
            "run": "1", "seed": item["seed"], "semantic_ir_valid": False,
            "predicted_gamma_ir_exact": False, "gamma_accept": False, "precondition_failed": False,
            "decision": "ABSTAIN", "wrong_repair": False, "target_cq_pass": False,
            "non_target_cq_preserved": False, "source_unchanged": True, "ses_success": False,
            "schema_status": "NOT_RUN", "gamma_status": "NOT_RUN", "precondition_status": "NOT_RUN",
            "reasoner_status": "NOT_RUN", "failure_stage": "GENERATION", "candidate_used_before_prediction": False,
            "oracle_used_before_prediction": False,
            "schema_sha256": schema_sha256(),
        }
        if not raw_path.is_file():
            rows_out.append(row)
            continue
        payload = json.loads(raw_path.read_text(encoding="utf-8-sig"))
        predicted_raw = payload.get("predicted_ir_raw", {})
        predicted = canonicalize_surface(predicted_raw)
        row["predicted_ir_raw_json"] = json.dumps(predicted_raw, ensure_ascii=False, sort_keys=True)
        row["predicted_ir_canonical_json"] = json.dumps(predicted, ensure_ascii=False, sort_keys=True)
        raw_new = predicted_raw.get("replacement", {}).get("new_value", {}).get("lexical", "") if isinstance(predicted_raw, dict) else ""
        canonical_new = predicted.get("replacement", {}).get("new_value", {}).get("lexical", "") if isinstance(predicted, dict) else ""
        row["model_new_value_lexical"] = raw_new
        row["canonical_new_value_lexical"] = canonical_new
        row["canonicalization_changed"] = raw_new != canonical_new
        evidence = (benchmark / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
        source = resolve_source_owl(event, mutants_dir=benchmark / "repair-stage/mutants", project_dir=benchmark)
        validation = validate_predicted_ir(predicted, source_path=source, evidence=evidence)
        row["schema_status"] = validation["status"]
        row["validation_errors"] = json.dumps(validation["errors"], ensure_ascii=False)
        row["decision"] = str(predicted.get("decision", "ABSTAIN"))
        row["semantic_ir_valid"] = validation["status"] == "VALID"
        row["confidence"] = predicted.get("confidence", "")
        if validation["status"] != "VALID" or row["decision"] != "REPAIR":
            row["failure_stage"] = validation["status"] if validation["status"] != "VALID" else row["decision"]
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
        destination = materialized_dir / f"{event_id}-run1.owl"
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
        row["ses_success"] = all([
            row["predicted_gamma_ir_exact"], removed == 1, added == 1,
            row["reasoner_status"] == "CONSISTENT", row["target_cq_pass"],
            row["non_target_cq_preserved"], row["source_unchanged"],
        ])
        row["failure_stage"] = "" if row["ses_success"] else "OWL_OR_CQ_CLOSURE"
        rows_out.append(row)

    write_csv(output / "r2b-direct-ir-gamma-details.csv", rows_out)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows_out:
        groups["ALL"].append(row)
        groups[row["semantic_type"]].append(row)
    labels = ("ALL", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE")
    summaries = [summarize(label, groups.get(label, [])) for label in labels]
    write_csv(output / "r2b-direct-ir-gamma-summary.csv", summaries)
    (output / "r2b-direct-ir-gamma-summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summaries[0], ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=("prepare", "generate", "evaluate"), required=True)
    parser.add_argument("--profile", choices=("smoke", "engineering"), required=True)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--method-freeze-manifest", type=Path, default=DEFAULT_FREEZE_MANIFEST)
    parser.add_argument("--llm-backend", choices=("ollama", "deepseek_api"), default="deepseek_api")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    benchmark = args.benchmark_dir.resolve()
    output = (args.output_dir or (DEFAULT_OUTPUT_ROOT / args.profile)).resolve()
    output.mkdir(parents=True, exist_ok=True)
    events = event_map(benchmark)
    if args.profile == "engineering":
        freeze = verify_method_freeze(args.method_freeze_manifest.resolve())
        expected_benchmark = freeze.get("benchmark", {}).get("manifest_sha256")
        verify_benchmark_freeze(
            PROJECT_DIR / "output/rfc-213-confirmatory-core/rfc213-freeze-manifest.json",
            expected_benchmark,
        )
    if args.step == "prepare":
        manifest = profile_manifest(events, args.profile)
        write_manifest(manifest_path(output, args.profile), manifest)
        print(json.dumps({"profile": args.profile, "events": len(manifest), "candidate_blind": True, "full_run_locked": True}, indent=2))
    elif args.step == "generate":
        if not manifest_path(output, args.profile).is_file():
            raise SystemExit("run --step prepare first")
        generate(args, benchmark, output)
    else:
        if not manifest_path(output, args.profile).is_file():
            raise SystemExit("run --step prepare first")
        evaluate(args, benchmark, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
