from __future__ import annotations

"""Frozen ECR-IR-Gamma V2 evaluation on the independent v6 hold-out.

`generate` is candidate/Oracle blind. `evaluate` opens private Gold only after
raw predictions have been persisted. This file is experiment infrastructure;
it does not alter the frozen method.
"""

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import validate_benchmark as benchmark_validator
from rdflib import RDF, URIRef

from ecr_ir_gamma_v22_gate import repair_is_no_change_equivalent
from evaluate_rfc213_phase3_gamma_closure import canonical_operation
from m13_llm_backends import call_llm_json, resume_artifact_is_complete
from rfc213_direct_repair_ir import (
    build_direct_ir_prompt,
    ontology_literal_assertions,
    schema_sha256,
    to_gamma_operation,
    validate_predicted_ir,
)
from rfc213_direct_ir_v6_2_prompt import build_direct_ir_prompt_v62
from rfc213_direct_repair_ir_v2 import resolve_source_window
from rfc213_direct_repair_ir_v23 import resolve_source_window_v23
from rfc213_direct_repair_ir_v24 import resolve_source_window_v24
from run_auto_formal_policy_batch_v3 import extract_json
from run_auto_policy_v2_candidate_repair import resolve_source_owl
from run_external_real_v1_symbolic_closure import graph_delta, load_graph, term_from_spec
from run_gamma_gold_ir_closure_eval import materialize_update_literal_atomic
from run_gamma_gold_ir_compile_eval import compile_update_literal
from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark/external-real-holdout-v6-1-direct-ir-blind"
OUTPUT = PROJECT_DIR / "output/external-real-holdout-v6-1-direct-ir-blind/final-blind-r2b-v2-r5"
BENCHMARK_FREEZE = PROJECT_DIR / "output/external-real-holdout-v6-1-direct-ir-blind/external-real-holdout-v6-1-direct-ir-blind-freeze-manifest.json"
METHOD_FREEZE = PROJECT_DIR / "method/ecr-ir-gamma/freeze-v2/method-freeze-manifest.json"
METHOD_FREEZE_V2_1 = PROJECT_DIR / "method/ecr-ir-gamma/freeze-v2-1/method-freeze-manifest.json"
METHOD_FREEZE_V2_2 = PROJECT_DIR / "method/ecr-ir-gamma/freeze-v2-2/method-freeze-manifest.json"
PARENT_FREEZE = PROJECT_DIR / "method/ecr-ir-gamma/freeze-v1/method-freeze-manifest.json"
EXECUTION_AMENDMENT = PROJECT_DIR / "method/ecr-ir-gamma/freeze-v2/execution-infrastructure-amendment-v2-v6-1.json"
EXECUTION_AMENDMENT_V6_2 = PROJECT_DIR / "method/ecr-ir-gamma/freeze-v2/execution-infrastructure-amendment-v2-v6-2.json"
BENCHMARK_SHA = "c6cdb9430f3be3da5fed83c5ed15e25e518479e82622e9c6fce0525675c5cdee"
METHOD_SHA = "ed250c97d056306ccc43b661ad78face6b607aceff1076cada98afb3ef612b54"
METHOD_SHA_V2_1 = "9170b8ae050c30ffbbce56d034a7db1ca551634190ca51b280d75f632f5d2ea1"
METHOD_SHA_V2_2 = "1bdedf0f736d261be9d7e2d1cc267bdb771f3cd9395437f334e3902fe2272c98"
SEEDS = (20260827, 20260828, 20260829, 20260830, 20260831)
REPAIR_TYPES = {"TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}
PREFLIGHT_BLOCKERS: tuple[str, ...] = ()


DATASET_CONFIGS = {
    "external-real-holdout-v6-1-direct-ir-blind": {
        "benchmark": PROJECT_DIR / "benchmark/external-real-holdout-v6-1-direct-ir-blind",
        "output": PROJECT_DIR / "output/external-real-holdout-v6-1-direct-ir-blind/final-blind-r2b-v2-r5",
        "freeze": PROJECT_DIR / "output/external-real-holdout-v6-1-direct-ir-blind/external-real-holdout-v6-1-direct-ir-blind-freeze-manifest.json",
        "benchmark_sha": "c6cdb9430f3be3da5fed83c5ed15e25e518479e82622e9c6fce0525675c5cdee",
        "method_freeze": METHOD_FREEZE,
        "method_sha": METHOD_SHA,
        "parent_freeze": PARENT_FREEZE,
        "events": 300,
        "prompt": "v6.1",
        "execution_amendment": EXECUTION_AMENDMENT,
    },
    "external-real-holdout-v6-2-direct-ir-blind": {
        "benchmark": PROJECT_DIR / "benchmark/external-real-holdout-v6-2-direct-ir-blind",
        "output": PROJECT_DIR / "output/external-real-holdout-v6-2-direct-ir-blind/final-blind-r2b-v2-r5",
        "freeze": PROJECT_DIR / "output/external-real-holdout-v6-2-direct-ir-blind/external-real-holdout-v6-2-direct-ir-blind-freeze-manifest.json",
        "benchmark_sha": "018907a5970b97401ada95a8ff6db3b52a8a3ea6451c45d4be3c85f0f35c9389",
        "method_freeze": METHOD_FREEZE_V2_2,
        "method_sha": METHOD_SHA_V2_2,
        "parent_freeze": METHOD_FREEZE_V2_1,
        "events": 300,
        "prompt": "v6.2-claim-level",
        "execution_amendment": EXECUTION_AMENDMENT_V6_2,
    },
}


def dataset_config(benchmark: Path) -> dict[str, Any]:
    name = benchmark.name
    if name not in DATASET_CONFIGS:
        known = ", ".join(sorted(DATASET_CONFIGS))
        raise SystemExit(f"unsupported benchmark dataset: {name}; expected one of: {known}")
    return DATASET_CONFIGS[name]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_method_manifest(
    path: Path,
    expected: str,
    allowed_replacement: dict[str, str] | None = None,
    *,
    verify_files: bool = True,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {k: v for k, v in payload.items() if k != "manifest_sha256"}
    actual = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if payload.get("manifest_sha256") != expected or actual != expected:
        raise SystemExit(f"method manifest mismatch: {path}")
    if verify_files:
        for item in payload.get("files", []):
            target = PROJECT_DIR / item["path"]
            actual_file_hash = sha256_file(target) if target.is_file() else "MISSING"
            replacement = (allowed_replacement or {}).get(item["path"])
            if actual_file_hash != item["sha256"] and actual_file_hash != replacement:
                raise SystemExit(f"frozen method file mismatch: {item['path']}")
    return payload


def verify_all(benchmark: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    amendment = None
    replacement = None
    if cfg.get("execution_amendment") is not None:
        amendment = json.loads(cfg["execution_amendment"].read_text(encoding="utf-8"))
        expected_status = f"REGISTERED_BEFORE_{benchmark.name.split('-holdout-')[1].split('-direct-')[0].replace('-', '_').upper()}_MODEL_EXECUTION"
        if amendment.get("status") != expected_status or amendment.get("scope") != "TRANSPORT_AND_RESUME_ONLY":
            raise SystemExit("execution infrastructure amendment is invalid")
        if amendment.get("base_method_manifest_sha256") not in {METHOD_SHA, cfg["method_sha"]} or amendment.get("benchmark_manifest_sha256") != cfg["benchmark_sha"]:
            raise SystemExit("execution amendment binding mismatch")
        if amendment.get("benchmark_id") != benchmark.name:
            raise SystemExit("execution amendment benchmark id mismatch")
        replacement = {amendment["changed_file"]: amendment["execution_sha256"]}
    method_freeze = cfg["method_freeze"]
    parent_freeze = cfg["parent_freeze"]
    parent_expected = json.loads(method_freeze.read_text(encoding="utf-8"))["parent_manifest_sha256"]
    parent_verify_files = parent_freeze == PARENT_FREEZE
    parent = verify_method_manifest(parent_freeze, parent_expected, replacement, verify_files=parent_verify_files)
    method = verify_method_manifest(method_freeze, cfg["method_sha"], replacement)
    if method["parent_manifest_sha256"] != parent["manifest_sha256"]:
        raise SystemExit("method parent chain mismatch")
    manifest = json.loads(cfg["freeze"].read_text(encoding="utf-8"))
    if manifest.get("manifest_sha256") != cfg["benchmark_sha"]:
        raise SystemExit("benchmark freeze binding mismatch")
    if manifest.get("method_freeze_manifest_sha256", cfg["method_sha"]) not in {METHOD_SHA, cfg["method_sha"]}:
        raise SystemExit("benchmark method binding mismatch")
    for item in manifest.get("files", []):
        target = PROJECT_DIR / item["path"]
        if not target.is_file() or sha256_file(target) != item["sha256"]:
            raise SystemExit(f"frozen benchmark file mismatch: {item['path']}")
    events = load_events(benchmark)
    if len(events) != cfg["events"]:
        raise SystemExit(f"expected {cfg['events']} events, found {len(events)}")
    return {"method": method, "parent": parent, "benchmark": manifest, "events": events, "amendment": amendment, "cfg": cfg}


def load_events(benchmark: Path) -> dict[str, dict[str, Any]]:
    rows = [json.loads(line) for line in (benchmark / "public/events/events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["event_id"]: row for row in rows}


def manifest_path(output: Path) -> Path:
    return output / "v6-final-blind-manifest-r5.csv"


def selected_events(verified: dict[str, Any], sample_review_csv: Path | None) -> dict[str, dict[str, Any]]:
    events = verified["events"]
    if sample_review_csv is None:
        return events
    rows = read_csv(sample_review_csv)
    event_ids = [row["event_id"] for row in rows if row.get("event_id")]
    missing = [event_id for event_id in event_ids if event_id not in events]
    if missing:
        raise SystemExit(f"sample review CSV references unknown events: {missing[:5]}")
    return {event_id: events[event_id] for event_id in event_ids}


def prepare(output: Path, verified: dict[str, Any], sample_review_csv: Path | None, runs_per_event: int) -> None:
    if runs_per_event < 1 or runs_per_event > len(SEEDS):
        raise SystemExit(f"--runs-per-event must be between 1 and {len(SEEDS)}")
    events = selected_events(verified, sample_review_csv)
    rows = []
    for event_id, event in sorted(events.items()):
        for run, seed in enumerate(SEEDS[:runs_per_event], 1):
            rows.append({"event_id": event_id, "run": run, "seed": seed, "semantic_type": event["semantic_type"], "domain": event["domain"]})
    write_csv(manifest_path(output), rows)
    backend_config = verified["parent"].get("llm")
    if backend_config is None:
        backend_config = json.loads(PARENT_FREEZE.read_text(encoding="utf-8")).get("llm", {})
    protocol = {
        "status": "REGISTERED_BEFORE_MODEL_EXECUTION",
        "benchmark": verified["cfg"]["benchmark"].name,
        "benchmark_manifest_sha256": verified["cfg"]["benchmark_sha"],
        "method": verified["method"]["method_id"],
        "method_manifest_sha256": verified["cfg"]["method_sha"],
        "execution_infrastructure_amendment_sha256": sha256_file(verified["cfg"]["execution_amendment"]) if verified["cfg"].get("execution_amendment") else "",
        "backend": backend_config,
        "prompt_variant": verified["cfg"]["prompt"],
        "runs_per_event": runs_per_event,
        "seeds": list(SEEDS[:runs_per_event]),
        "events": len(events),
        "attempts": len(events) * runs_per_event,
        "sample_review_csv": str(sample_review_csv) if sample_review_csv else "",
        "candidate_blind_generation": True,
        "oracle_blind_generation": True,
        "repair_and_safety_reported_separately": True,
        "preflight_blockers": list(PREFLIGHT_BLOCKERS),
    }
    (output / "experiment-protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(protocol, ensure_ascii=False, indent=2))


def raw_path(output: Path, event_id: str, run: str, seed: str) -> Path:
    return output / "raw-predicted-ir" / f"{event_id}-run{run}-seed{seed}.json"


def build_prompt_for_dataset(cfg: dict[str, Any], event: dict[str, Any], evidence: str, assertions: list[dict[str, str]]) -> str:
    if cfg["prompt"] == "v6.2-claim-level":
        return build_direct_ir_prompt_v62(event, evidence, assertions)
    return build_direct_ir_prompt(event, evidence, assertions)


def generate(args: argparse.Namespace, benchmark: Path, output: Path, events: dict[str, Any], cfg: dict[str, Any]) -> None:
    rows = read_csv(manifest_path(output))
    (output / "raw-predicted-ir").mkdir(parents=True, exist_ok=True)
    for item in rows:
        path = raw_path(output, item["event_id"], item["run"], item["seed"])
        if args.resume and resume_artifact_is_complete(path):
            print(f"{item['event_id']} run={item['run']} [resume] skipped", flush=True)
            continue
        event = events[item["event_id"]]
        evidence = (benchmark / "public/excerpts" / f"{item['event_id']}-evidence.md").read_text(encoding="utf-8")
        source = resolve_source_owl(event, mutants_dir=benchmark / "repair-stage/mutants", project_dir=benchmark)
        assertions = ontology_literal_assertions(source)
        prompt = build_prompt_for_dataset(cfg, event, evidence, assertions)
        try:
            call = call_llm_json(args.llm_backend, prompt, int(item["seed"]), args.timeout, num_predict=900)
            parsed = extract_json(call.text)
            payload = {
                "status": "GENERATED", "event_id": item["event_id"], "run": int(item["run"]), "seed": int(item["seed"]),
                "raw_model_output": call.text, "predicted_ir_raw": parsed if isinstance(parsed, dict) else {},
                "candidate_used": False, "oracle_used": False, "private_data_used": False,
                "ontology_assertion_count": len(assertions), "schema_sha256": schema_sha256(),
                "audit": {"backend": call.backend, "model": call.model, "runtime_ms": call.runtime_ms,
                          "prompt_tokens": call.prompt_tokens, "completion_tokens": call.completion_tokens,
                          "attempt_count": call.attempt_count, "retry_errors": list(call.retry_errors)},
            }
        except Exception as exc:
            payload = {"status": f"error:{type(exc).__name__}", "error": str(exc), "event_id": item["event_id"],
                       "run": int(item["run"]), "seed": int(item["seed"]), "candidate_used": False,
                       "oracle_used": False, "private_data_used": False}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{item['event_id']} run={item['run']} status={payload['status']}", flush=True)


def load_gold(benchmark: Path) -> dict[str, dict[str, Any]]:
    path = benchmark / "private/oracle/gold-repair-ir.jsonl"
    return {row["event_id"]: row for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())}


def gold_operation(gold: dict[str, Any]) -> dict[str, Any]:
    return {
        "operator": "REPLACE_PROPERTY_VALUE",
        "subject_iri": gold["target"]["subject_iri"], "predicate_iri": gold["target"]["predicate_iri"],
        "old_value": gold["target"]["old_value"], "new_value": gold["replacement"]["new_value"],
    }


def base_row(item: dict[str, str], gold: dict[str, Any]) -> dict[str, Any]:
    return {"event_id": item["event_id"], "run": item["run"], "seed": item["seed"], "domain": item["domain"],
            "semantic_type": gold["semantic_type"], "partition": "REPAIR" if gold["decision"] == "REPAIR" else "SAFETY",
            "gold_decision": gold["decision"], "model_decision": "ABSTAIN", "schema_status": "NOT_RUN",
            "window_resolution_status": "NOT_RUN", "gamma_status": "NOT_RUN", "precondition_status": "NOT_RUN",
            "reasoner_status": "NOT_RUN", "gamma_accept": False, "selected": False, "wrong_repair": False,
            "source_unchanged": True, "target_cq_pass": False, "non_target_cq_preserved": False,
            "decision_correct": False, "ses_success": False, "failure_stage": "GENERATION"}


def evaluate(args: argparse.Namespace, benchmark: Path, output: Path, events: dict[str, Any]) -> None:
    manifest = read_csv(manifest_path(output))
    golds = load_gold(benchmark)
    materialized = output / "materialized-predicted-owl"
    rows_out: list[dict[str, Any]] = []
    for item in manifest:
        event_id, event, gold = item["event_id"], events[item["event_id"]], golds[item["event_id"]]
        row = base_row(item, gold)
        path = raw_path(output, event_id, item["run"], item["seed"])
        if not path.is_file():
            rows_out.append(row); continue
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("status") != "GENERATED":
            row["failure_stage"] = "TRANSPORT_OR_GENERATION"; rows_out.append(row); continue
        predicted_raw = payload.get("predicted_ir_raw", {})
        evidence = (benchmark / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
        source = resolve_source_owl(event, mutants_dir=benchmark / "repair-stage/mutants", project_dir=benchmark)
        resolvers = {"v22": resolve_source_window, "v23": resolve_source_window_v23, "v24": resolve_source_window_v24}
        resolution = resolvers[args.resolver](predicted_raw, evidence)
        predicted = resolution.get("canonical_ir")
        if not isinstance(predicted, dict):
            predicted = {}
        row["window_resolution_status"] = resolution["status"]
        row["resolver_version"] = args.resolver
        row["resolution_path"] = resolution.get("resolution_path", "V22")
        if resolution["status"] == "REFERENCE_NON_ASSERTION":
            row["model_decision"] = "ABSTAIN"
            row["decision_correct"] = gold["decision"] == "ABSTAIN"
            row["target_cq_pass"] = row["decision_correct"]
            row["non_target_cq_preserved"] = True
            row["ses_success"] = row["decision_correct"]
            row["failure_stage"] = "" if row["ses_success"] else "REFERENCE_GATE_FALSE_BLOCK"
            rows_out.append(row)
            continue
        row["model_decision"] = str(predicted.get("decision", "ABSTAIN"))
        validation = validate_predicted_ir(predicted, source_path=source, evidence=evidence)
        row["schema_status"] = validation["status"]
        row["validation_errors"] = json.dumps(validation["errors"], ensure_ascii=False)
        row["predicted_ir_json"] = json.dumps(predicted, ensure_ascii=False, sort_keys=True)

        if validation["status"] == "VALID" and repair_is_no_change_equivalent(predicted):
            row["model_decision"] = "NO_CHANGE"
            row["decision_correct"] = gold["decision"] == "NO_CHANGE"
            row["target_cq_pass"] = row["decision_correct"]
            row["non_target_cq_preserved"] = True
            row["ses_success"] = row["decision_correct"]
            row["failure_stage"] = "" if row["ses_success"] else "NO_CHANGE_EQUIVALENCE_FALSE_POSITIVE"
            rows_out.append(row)
            continue

        if row["partition"] == "SAFETY" and validation["status"] == "VALID" and row["model_decision"] != "REPAIR":
            row["decision_correct"] = row["model_decision"] == gold["decision"]
            row["target_cq_pass"] = row["decision_correct"]
            row["non_target_cq_preserved"] = True
            row["ses_success"] = row["decision_correct"]
            row["failure_stage"] = "" if row["ses_success"] else "SAFETY_DECISION"
            rows_out.append(row); continue

        if validation["status"] != "VALID" or row["model_decision"] != "REPAIR" or resolution["status"] != "RESOLVED":
            row["failure_stage"] = validation["status"] if validation["status"] != "VALID" else f"DECISION_{row['model_decision']}"
            rows_out.append(row); continue

        operation = to_gamma_operation(predicted)
        expected = gold_operation(gold) if row["partition"] == "REPAIR" else None
        exact = expected is not None and canonical_operation(operation) == canonical_operation(expected)
        row["predicted_gamma_ir_exact"] = exact
        compiled = compile_update_literal(operation)
        row["gamma_status"] = compiled["status"]
        row["gamma_accept"] = compiled["status"] == "COMPILED"
        if not row["gamma_accept"]:
            row["failure_stage"] = "GAMMA_VALIDATION"; rows_out.append(row); continue
        destination = materialized / f"{event_id}-run{item['run']}.owl"
        applied = materialize_update_literal_atomic(source_path=source, operation=operation, dest_path=destination)
        row["precondition_status"] = applied["status"]
        row["source_unchanged"] = bool(applied["source_unchanged"])
        if applied["status"] != "PASS":
            row["failure_stage"] = "PRECONDITION"; rows_out.append(row); continue
        row["selected"] = True
        row["wrong_repair"] = not exact
        source_graph, repaired_graph = load_graph(source), load_graph(destination)
        removed, added = graph_delta(source_graph, repaired_graph)
        reasoner = benchmark_validator.run_reasoner(destination, args.timeout)
        row["reasoner_status"] = reasoner.get("status", "ERROR")
        if expected is not None:
            target = (URIRef(expected["subject_iri"]), URIRef(expected["predicate_iri"]), term_from_spec(expected["new_value"]))
            row["target_cq_pass"] = target in repaired_graph
        row["non_target_cq_preserved"] = all(triple in repaired_graph for triple in source_graph if triple[1] != URIRef(operation["predicate_iri"]))
        row["decision_correct"] = exact
        row["ses_success"] = bool(exact and removed == 1 and added == 1 and row["reasoner_status"] == "CONSISTENT" and row["target_cq_pass"] and row["non_target_cq_preserved"] and row["source_unchanged"])
        row["failure_stage"] = "" if row["ses_success"] else ("UNSAFE_REPAIR" if row["partition"] == "SAFETY" else "OWL_OR_CQ_CLOSURE")
        rows_out.append(row)

    write_csv(output / "v6-final-blind-details.csv", rows_out)
    summaries = summarize_rows(rows_out)
    write_csv(output / "v6-final-blind-summary.csv", summaries)
    (output / "v6-final-blind-summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


def summarize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups["ALL"].append(row); groups[row["partition"]].append(row); groups[row["semantic_type"]].append(row)
    out = []
    for label in ("ALL", "REPAIR", "SAFETY", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE", "NO_CHANGE", "INSUFFICIENT_EVIDENCE", "CONFLICTING_EVIDENCE"):
        items = groups.get(label, [])
        events = defaultdict(list)
        for row in items: events[row["event_id"]].append(row)
        attempts = len(items); selected = sum(bool(r["selected"]) for r in items); wrong = sum(bool(r["wrong_repair"]) for r in items)
        success = sum(bool(r["ses_success"]) for r in items)
        out.append({"group": label, "events": len(events), "attempts": attempts, "successes": success,
                    "success_rate": success / attempts if attempts else 0, "selected": selected,
                    "coverage": selected / attempts if attempts else 0, "wrong_repairs": wrong,
                    "wrr": wrong / attempts if attempts else 0, "selective_risk": wrong / selected if selected else 0,
                    "abstains": sum(r["model_decision"] == "ABSTAIN" for r in items),
                    "no_change": sum(r["model_decision"] == "NO_CHANGE" for r in items),
                    "strict_event_successes": sum(all(bool(x["ses_success"]) for x in event_rows) for event_rows in events.values()),
                    "strict_event_accuracy": (sum(all(bool(x["ses_success"]) for x in event_rows) for event_rows in events.values()) / len(events)) if events else 0})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=("preflight", "prepare", "generate", "evaluate"), required=True)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--llm-backend", choices=("deepseek_api", "ollama"), default="deepseek_api")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--sample-review-csv", type=Path)
    parser.add_argument("--runs-per-event", type=int, default=5)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--resolver", choices=("v22", "v23", "v24"), default="v22")
    args = parser.parse_args()
    benchmark, output = args.benchmark_dir.resolve(), args.output_dir.resolve()
    cfg = dataset_config(benchmark)
    if args.benchmark_dir == BENCHMARK and args.output_dir == OUTPUT:
        output = cfg["output"].resolve()
    verified = verify_all(benchmark, cfg)
    event_count = len(selected_events(verified, args.sample_review_csv))
    attempt_count = event_count * args.runs_per_event
    if args.step == "preflight":
        print(json.dumps({"status": "READY", "events": event_count, "attempts": attempt_count,
                          "benchmark": benchmark.name, "benchmark_sha": cfg["benchmark_sha"],
                          "method_sha": cfg["method_sha"], "prompt_variant": cfg["prompt"],
                          "sample_review_csv": str(args.sample_review_csv) if args.sample_review_csv else "",
                          "runs_per_event": args.runs_per_event, "blockers": []}, indent=2))
        return 0
    output.mkdir(parents=True, exist_ok=True)
    if args.step == "prepare": prepare(output, verified, args.sample_review_csv, args.runs_per_event); return 0
    if not manifest_path(output).is_file(): raise SystemExit("run --step prepare first")
    if args.step == "generate": generate(args, benchmark, output, verified["events"], cfg)
    else: evaluate(args, benchmark, output, verified["events"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
