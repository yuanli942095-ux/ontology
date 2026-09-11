from __future__ import annotations

"""Shared helpers for Exp9 strong baselines on candidate-id-permute."""

import json
import random
import re
from pathlib import Path
from typing import Any

from auto_policy_m12_deepseek_client import DeepSeekConfig, call_deepseek_chat
from paper_final_validation_common import (
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    PAPER_VALIDATION_ROOT,
    benchmark_paths,
    load_freeze_sha256,
    load_method_freeze_sha256,
    read_csv,
    ready,
    sha256_file,
    utc_now_iso,
    write_binding,
)
from paper_final_validation_schema import UNIFIED_ATTEMPT_FIELDS
from run_auto_policy_v4_ir_candidate_repair import configure_paths, load_candidates
from run_m16_candidate_entailment_verifier import run_repair_checks, supported, verifier_prompt
from semantic_v2_common import PROJECT_DIR, write_csv


EXP9_ROOT = PAPER_VALIDATION_ROOT / "09-strong-baselines"
DEFAULT_BENCHMARK = (
    PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large-robustness-variants" / "candidate-id-permute"
)
ECR_CONTROL_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v5-blind-large"
    / "robustness-online"
    / "candidate-id-permute-full-r5-deepseek"
)
DEFAULT_SEEDS = [20260829, 20260830, 20260831, 20260901, 20260902]
BASELINE_METHODS = ("direct-llm", "m16-only", "compute-matched")
PROMPT_DIR = EXP9_ROOT / "baseline-prompts"


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def load_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def compact_operation(operation_json: str | dict[str, Any]) -> dict[str, Any]:
    raw = operation_json if isinstance(operation_json, dict) else json.loads(operation_json)
    return {
        "operator": raw.get("operator", ""),
        "subject_iri": raw.get("subject_iri", ""),
        "predicate_iri": raw.get("predicate_iri", ""),
        "old_value": raw.get("old_value", {}),
        "new_value": raw.get("new_value", {}),
    }


def load_event_bundles(benchmark: Path, event_limit: int = 0) -> list[dict[str, Any]]:
    paths = benchmark_paths(benchmark)
    events = [row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))]
    events.sort(key=lambda row: row["event_id"])
    if event_limit > 0:
        events = events[:event_limit]

    docs_by_event: dict[str, list[dict[str, str]]] = {}
    for row in read_csv(paths["document_csv"]):
        if ready(row.get("status", "")):
            docs_by_event.setdefault(row["event_id"], []).append(row)

    candidates_by_event = load_candidates(paths["candidate_csv"])
    bundles: list[dict[str, Any]] = []
    for event in events:
        event_id = event["event_id"]
        evidence_path = benchmark / "public" / "excerpts" / f"{event_id}-evidence.md"
        bundles.append(
            {
                **event,
                "documents": docs_by_event.get(event_id, []),
                "candidates": candidates_by_event.get(event_id, []),
                "evidence": evidence_path.read_text(encoding="utf-8-sig"),
            }
        )
    return bundles


def load_oracles(benchmark: Path) -> dict[str, dict[str, str]]:
    paths = benchmark_paths(benchmark)
    return {row["event_id"]: row for row in read_csv(paths["oracle_csv"]) if ready(row.get("status", ""))}


def shuffled_candidates(candidates: list[dict[str, Any]], event_id: str, seed: int) -> list[dict[str, Any]]:
    items = [dict(row) for row in candidates]
    rng = random.Random(f"{event_id}|{seed}|exp9-order")
    rng.shuffle(items)
    return items


def evidence_spans_from_markdown(evidence: str) -> list[str]:
    spans: list[str] = []
    for match in re.finditer(r"\[SOURCE_WINDOW_\d+\]\s*(.+?)(?=\[SOURCE_WINDOW_\d+\]|$)", evidence, flags=re.S):
        text = " ".join(match.group(1).split())
        if text:
            spans.append(text[:500])
    if not spans and evidence.strip():
        spans.append(evidence.strip()[:2000])
    return spans


def empty_frame() -> dict[str, Any]:
    return {"m14_frame": {}, "evidence_spans": []}


def call_deepseek_json(prompt: str, timeout: int, max_tokens: int = 700) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    config = DeepSeekConfig.from_env()
    config = DeepSeekConfig(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        temperature=0,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    result = call_deepseek_chat(prompt, config=config, response_json=True)
    parsed = load_json_object(result.content)
    audit = {
        "backend": "deepseek_api",
        "model": result.model,
        "runtime_ms": result.runtime_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "raw_output": result.content,
    }
    return parsed, audit


def aggregate_audit(audits: list[dict[str, Any]]) -> dict[str, Any]:
    llm_calls = len(audits)
    input_tokens = sum(int(item.get("prompt_tokens", 0) or 0) for item in audits)
    output_tokens = sum(int(item.get("completion_tokens", 0) or 0) for item in audits)
    total_tokens = sum(int(item.get("total_tokens", 0) or 0) for item in audits)
    latency_ms = sum(int(item.get("runtime_ms", 0) or 0) for item in audits)
    return {
        "llm_calls": llm_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
    }


def closure_success(out: dict[str, Any]) -> bool:
    return all(
        truth(out.get(key))
        for key in (
            "selection_oracle_correct",
            "minimal_edit_gate",
            "reasoner_gate",
            "source_triggers_repair_cq",
            "candidate_satisfies_repair_cq",
        )
    )


def apply_closure(
    *,
    event: dict[str, str],
    selected: dict[str, Any] | None,
    selection_status: str,
    oracle: dict[str, str],
    paths: dict[str, Path],
    output_dir: Path,
    graph_cache: dict[Path, Any],
    reasoner_cache: dict[Path, dict[str, Any]],
    timeout: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "selection_status": selection_status,
        "selected_candidate_id": selected["candidate_id"] if selected else "",
        "selected_value": selected.get("display_value", "") if selected else "",
        "oracle_candidate_id": oracle["oracle_candidate_id"],
        "oracle_value": oracle.get("oracle_value", ""),
        "selection_oracle_correct": (
            selection_status == "SELECTED"
            and selected is not None
            and selected["candidate_id"] == oracle["oracle_candidate_id"]
        ),
    }
    if selected and selection_status == "SELECTED":
        out.update(
            run_repair_checks(
                selected,
                event,
                paths=paths,
                output_dir=output_dir,
                graph_cache=graph_cache,
                reasoner_cache=reasoner_cache,
                timeout=timeout,
            )
        )
    else:
        out.update(
            {
                "minimal_edit_gate": False,
                "reasoner_gate": False,
                "source_triggers_repair_cq": False,
                "candidate_satisfies_repair_cq": False,
                "reasoner_result": "",
            }
        )
    out["full_closure_success"] = closure_success(out)
    return out


def attach_oracle_fields(row: dict[str, Any], oracle: dict[str, str]) -> dict[str, Any]:
    selected = str(row.get("selected_candidate_id", ""))
    status = str(row.get("selection_status", "")).upper()
    return {
        **row,
        "oracle_candidate_id": oracle["oracle_candidate_id"],
        "oracle_value": oracle.get("oracle_value", ""),
        "selection_oracle_correct": status == "SELECTED" and selected == oracle["oracle_candidate_id"],
    }


def checkpoint_key(row: dict[str, Any]) -> tuple[str, int, str]:
    return str(row["event_id"]), int(row["run"]), str(row.get("method", row.get("baseline", "")))


def load_checkpoint(path: Path) -> dict[tuple[str, int, str], dict[str, Any]]:
    if not path.is_file():
        return {}
    rows: dict[tuple[str, int, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            row = json.loads(line)
            rows[checkpoint_key(row)] = row
    return rows


def append_checkpoint(path: Path, row: dict[str, Any]) -> None:
    if any(key.startswith("oracle") for key in row):
        raise RuntimeError("refusing to checkpoint Oracle-bearing row")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def export_unified_rows(
    rows: list[dict[str, Any]],
    *,
    method: str,
    benchmark_sha256: str,
    method_sha256: str,
    prompt_sha256: str,
    pilot: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        selected = str(row.get("selection_status", "")).upper()
        decision = "SELECT" if selected == "SELECTED" else "ABSTAIN"
        closure = truth(row.get("full_closure_success"))
        oracle_ok = truth(row.get("selection_oracle_correct"))
        wrong_select = decision == "SELECT" and not (oracle_ok and closure)
        out.append(
            {
                "experiment_id": "exp9",
                "condition": method,
                "method": method.upper().replace("-", "_"),
                "event_id": row["event_id"],
                "run_id": row["run"],
                "semantic_type": row.get("semantic_type", ""),
                "source_id": row.get("document_ids", ""),
                "source_family": row.get("domain", ""),
                "benchmark_sha256": benchmark_sha256,
                "method_sha256": method_sha256,
                "prompt_sha256": prompt_sha256,
                "oracle_candidate_id": row.get("oracle_candidate_id", ""),
                "selected_candidate_id": row.get("selected_candidate_id", ""),
                "decision": decision,
                "oracle_correct": str(oracle_ok),
                "closure_pass": str(closure),
                "strict_success": "",
                "wrong_select": str(wrong_select),
                "abstain": str(decision == "ABSTAIN"),
                "failure_stage": row.get("decision_path", ""),
                "failure_reason": row.get("selection_reason", ""),
                "m13_status": "",
                "m14_status": "",
                "m15_status": "",
                "ranking_status": "",
                "m16_status": str(row.get("supported_count", "")),
                "closure_status": row.get("reasoner_result", ""),
                "api_error": row.get("api_error", ""),
                "timeout": row.get("timeout", ""),
                "llm_calls": str(row.get("llm_calls", "")),
                "input_tokens": str(row.get("input_tokens", "")),
                "output_tokens": str(row.get("output_tokens", "")),
                "total_tokens": str(row.get("total_tokens", "")),
                "latency_ms": str(row.get("latency_ms", "")),
                "random_seed": str(row.get("seed", "")),
                "timestamp": utc_now_iso(),
            }
        )
    return out


def summarize_attempts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = len(rows)
    closure = sum(truth(row.get("full_closure_success")) for row in rows)
    oracle = sum(truth(row.get("selection_oracle_correct")) for row in rows)
    abstain = sum(1 for row in rows if str(row.get("selection_status", "")).upper() != "SELECTED")
    wrong = sum(
        1
        for row in rows
        if str(row.get("selection_status", "")).upper() == "SELECTED"
        and not truth(row.get("selection_oracle_correct")) and not truth(row.get("full_closure_success"))
    )
    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_event.setdefault(row["event_id"], []).append(row)
    strict = sum(all(truth(item.get("full_closure_success")) for item in items) for items in by_event.values())
    return {
        "attempts": attempts,
        "events": len(by_event),
        "closure_success": closure,
        "closure_accuracy": closure / attempts if attempts else 0.0,
        "oracle_success": oracle,
        "oracle_accuracy": oracle / attempts if attempts else 0.0,
        "strict_event_successes": strict,
        "strict_event_accuracy": strict / len(by_event) if by_event else 0.0,
        "abstain_count": abstain,
        "wrong_select_count": wrong,
    }


def write_method_outputs(
    *,
    method: str,
    rows: list[dict[str, Any]],
    output_dir: Path,
    benchmark: Path,
    script_path: Path,
    prompt_file: Path,
    pilot: bool,
    extra: dict[str, Any] | None = None,
) -> None:
    benchmark_sha256, benchmark_manifest = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_sha256, method_manifest = load_method_freeze_sha256()
    prompt_sha256 = sha256_file(prompt_file)
    unified = export_unified_rows(
        rows,
        method=method,
        benchmark_sha256=benchmark_sha256,
        method_sha256=method_sha256,
        prompt_sha256=prompt_sha256,
        pilot=pilot,
    )
    write_csv(output_dir / "exp9-unified-attempts.csv", unified)
    summary = summarize_attempts(rows)
    summary["condition"] = method
    summary["pilot"] = pilot
    write_binding(
        output_dir,
        experiment_role=f"exp9-{method}",
        script_path=script_path,
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_manifest,
        method_freeze_sha256=method_sha256,
        extra={"condition": method, "pilot": pilot, "summary": summary, **(extra or {})},
    )


def import_ecr_control_rows(pilot: bool) -> list[dict[str, Any]]:
    details = ECR_CONTROL_DIR / "m16-full-holdout-combined" / "m16-full-holdout-combined-full-details.csv"
    if not details.is_file():
        raise FileNotFoundError(details)
    benchmark_sha256, _ = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_sha256, _ = load_method_freeze_sha256()
    rows_out: list[dict[str, Any]] = []
    for row in read_csv(details):
        selected = str(row.get("selection_status", "")).upper()
        decision = "SELECT" if selected == "SELECTED" else "ABSTAIN"
        closure = truth(row.get("full_closure_success"))
        oracle_ok = truth(row.get("selection_oracle_correct"))
        wrong_select = decision == "SELECT" and not (oracle_ok and closure)
        rows_out.append(
            {
                "experiment_id": "exp9",
                "condition": "ecr-control",
                "method": "ECR",
                "event_id": row["event_id"],
                "run_id": row["run"],
                "semantic_type": row.get("semantic_type", ""),
                "source_id": "",
                "source_family": row.get("source_family", row.get("domain", "")),
                "benchmark_sha256": benchmark_sha256,
                "method_sha256": method_sha256,
                "prompt_sha256": "",
                "oracle_candidate_id": row.get("oracle_candidate_id", ""),
                "selected_candidate_id": row.get("selected_candidate_id", ""),
                "decision": decision,
                "oracle_correct": str(oracle_ok),
                "closure_pass": str(closure),
                "strict_success": "",
                "wrong_select": str(wrong_select),
                "abstain": str(decision == "ABSTAIN"),
                "failure_stage": row.get("decision_path", ""),
                "failure_reason": row.get("selection_reason", ""),
                "m13_status": row.get("generation_status", ""),
                "m14_status": row.get("source_m14_raw_output_file", ""),
                "m15_status": "",
                "ranking_status": row.get("decision_path", ""),
                "m16_status": row.get("supported_count", ""),
                "closure_status": row.get("reasoner_result", ""),
                "api_error": "",
                "timeout": "",
                "llm_calls": "",
                "input_tokens": "",
                "output_tokens": "",
                "total_tokens": "",
                "latency_ms": row.get("runtime_ms", ""),
                "random_seed": row.get("seed", ""),
                "timestamp": utc_now_iso(),
                "pilot": str(pilot),
            }
        )
    return rows_out
