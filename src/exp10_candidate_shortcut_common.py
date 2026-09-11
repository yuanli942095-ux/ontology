from __future__ import annotations

"""Shared helpers for Exp10 candidate-only shortcut audit."""

import json
import re
from pathlib import Path
from typing import Any

from exp9_baseline_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_SEEDS,
    aggregate_audit,
    apply_closure,
    attach_oracle_fields,
    call_deepseek_json,
    checkpoint_key,
    compact_operation,
    load_checkpoint,
    load_oracles,
    shuffled_candidates,
    summarize_attempts,
    truth,
)
from paper_final_validation_common import (
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
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
from semantic_v2_common import PROJECT_DIR, write_csv


EXP10_ROOT = PAPER_VALIDATION_ROOT = PROJECT_DIR / "output" / "paper-final-validation" / "10-candidate-shortcut"
PROMPT_DIR = EXP10_ROOT / "baseline-prompts"
FEATURE_FREEZE = EXP10_ROOT / "candidate-feature-freeze.json"

TRAIN_BENCHMARKS = (
    PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded",
    PROJECT_DIR / "benchmark" / "external-real-holdout-v3-large",
    PROJECT_DIR / "benchmark" / "external-real-holdout-v4-blind",
)

CONTENT_FEATURES = (
    "character_length",
    "token_count",
    "digit_count",
    "date_pattern_count",
    "rfc_identifier_count",
    "contains_current",
    "contains_obsolete",
    "contains_supersede",
    "contains_update",
    "operation_type",
    "add_count",
    "delete_count",
    "number_of_triples",
)

ID_FEATURES = ("candidate_id_CAND_001", "candidate_id_CAND_002", "candidate_id_CAND_003", "candidate_position")

FEATURE_NAMES_C1 = list(CONTENT_FEATURES) + list(ID_FEATURES)
FEATURE_NAMES_C2 = list(CONTENT_FEATURES)

DATE_PATTERN = re.compile(
    r"\b(?:19|20)\d{2}|(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
    flags=re.I,
)
RFC_PATTERN = re.compile(r"\bRFC\s*\d{3,5}\b", flags=re.I)


def load_event_rows(benchmark: Path, event_limit: int = 0) -> list[dict[str, str]]:
    paths = benchmark_paths(benchmark)
    events = [row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))]
    events.sort(key=lambda row: row["event_id"])
    if event_limit > 0:
        events = events[:event_limit]
    return events


def load_candidate_map(benchmark: Path) -> dict[str, list[dict[str, Any]]]:
    paths = benchmark_paths(benchmark)
    raw = load_candidates(paths["candidate_csv"])
    out: dict[str, list[dict[str, Any]]] = {}
    for event_id, rows in raw.items():
        items = [dict(row) for row in rows]
        items.sort(key=lambda row: row["candidate_id"])
        out[event_id] = items
    return out


def operation_counts(operation: dict[str, Any]) -> tuple[int, int, int]:
    operator = str(operation.get("operator", "")).strip()
    if operator == "REPLACE_PROPERTY_VALUE":
        old_lex = str(operation.get("old_value", {}).get("lexical", ""))
        new_lex = str(operation.get("new_value", {}).get("lexical", ""))
        if old_lex == new_lex:
            return 0, 0, 0
        return 1, 1, 2
    return 1, 1, 2


def extract_content_features(candidate: dict[str, Any], position: int | None = None) -> dict[str, Any]:
    display = str(candidate.get("display_value", ""))
    operation = candidate.get("operation")
    if isinstance(operation, str):
        operation = json.loads(operation)
    if not isinstance(operation, dict):
        operation = {}
    lower = display.lower()
    tokens = re.findall(r"[A-Za-z0-9_]+", display)
    add_count, delete_count, triples = operation_counts(operation)
    features: dict[str, Any] = {
        "character_length": len(display),
        "token_count": len(tokens),
        "digit_count": sum(ch.isdigit() for ch in display),
        "date_pattern_count": len(DATE_PATTERN.findall(display)),
        "rfc_identifier_count": len(RFC_PATTERN.findall(display)),
        "contains_current": int("current" in lower),
        "contains_obsolete": int("obsolete" in lower),
        "contains_supersede": int("supersede" in lower or "superseded" in lower),
        "contains_update": int("update" in lower),
        "operation_type": str(operation.get("operator", "")),
        "add_count": add_count,
        "delete_count": delete_count,
        "number_of_triples": triples,
    }
    if position is not None:
        features["candidate_position"] = position
    return features


def extract_features(candidate: dict[str, Any], position: int, include_id: bool) -> dict[str, float]:
    content = extract_content_features(candidate, position if include_id else None)
    vector: dict[str, float] = {}
    for name in CONTENT_FEATURES:
        if name == "operation_type":
            op = str(content["operation_type"])
            for op_name in ("REPLACE_PROPERTY_VALUE", "OTHER"):
                vector[f"operation_type_{op_name}"] = 1.0 if op == op_name or (op_name == "OTHER" and op != "REPLACE_PROPERTY_VALUE") else 0.0
            continue
        vector[name] = float(content[name])
    if include_id:
        cid = str(candidate.get("candidate_id", ""))
        for cand in ("CAND_001", "CAND_002", "CAND_003"):
            vector[f"candidate_id_{cand}"] = 1.0 if cid == cand else 0.0
        vector["candidate_position"] = float(position)
    return vector


def feature_names(include_id: bool) -> list[str]:
    names = list(CONTENT_FEATURES)
    op_idx = names.index("operation_type")
    names = names[:op_idx] + ["operation_type_REPLACE_PROPERTY_VALUE", "operation_type_OTHER"] + names[op_idx + 1:]
    if include_id:
        names.extend(ID_FEATURES)
    return names


def vectorize_row(features: dict[str, float], names: list[str]) -> list[float]:
    return [float(features.get(name, 0.0)) for name in names]


def append_checkpoint(path: Path, row: dict[str, Any]) -> None:
    if any(key.startswith("oracle") for key in row):
        raise RuntimeError("refusing to checkpoint Oracle-bearing row")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def export_unified_rows(
    rows: list[dict[str, Any]],
    *,
    condition: str,
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
                "experiment_id": "exp10",
                "condition": condition,
                "method": method,
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


def write_method_outputs(
    *,
    condition: str,
    method: str,
    rows: list[dict[str, Any]],
    output_dir: Path,
    benchmark: Path,
    script_path: Path,
    prompt_file: Path | None,
    pilot: bool,
    extra: dict[str, Any] | None = None,
) -> None:
    benchmark_sha256, benchmark_manifest = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_sha256, method_manifest = load_method_freeze_sha256()
    prompt_sha256 = sha256_file(prompt_file) if prompt_file and prompt_file.is_file() else ""
    unified = export_unified_rows(
        rows,
        condition=condition,
        method=method,
        benchmark_sha256=benchmark_sha256,
        method_sha256=method_sha256,
        prompt_sha256=prompt_sha256,
        pilot=pilot,
    )
    write_csv(output_dir / "exp10-unified-attempts.csv", unified)
    summary = summarize_attempts(rows)
    summary["condition"] = condition
    summary["pilot"] = pilot
    write_binding(
        output_dir,
        experiment_role=f"exp10-{condition}",
        script_path=script_path,
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_manifest,
        method_freeze_sha256=method_sha256,
        extra={"condition": condition, "pilot": pilot, "summary": summary, **(extra or {})},
    )


def build_candidate_only_prompt(candidates: list[dict[str, Any]]) -> str:
    header = read_prompt("candidate-only-header.txt", prompt_dir=PROMPT_DIR)
    options = [
        {
            "candidate_id": row["candidate_id"],
            "display_value": row["display_value"],
            "operation": compact_operation(row["operation"]),
        }
        for row in candidates
    ]
    return header + "\n\nCANDIDATE OPTIONS:\n" + json.dumps(options, ensure_ascii=False, indent=2)


def read_prompt(name: str, prompt_dir: Path = PROMPT_DIR) -> str:
    path = prompt_dir / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def interpret_selection(parsed: dict[str, Any] | None, candidates: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None, str]:
    if not isinstance(parsed, dict):
        return "ABSTAIN", None, "not parseable json object"
    decision = str(parsed.get("decision", "")).strip().upper()
    if decision == "ABSTAIN":
        return "ABSTAIN", None, str(parsed.get("reason", ""))
    if decision != "SELECT":
        return "ABSTAIN", None, f"invalid decision: {decision}"
    candidate_id = str(parsed.get("candidate_id", "")).strip()
    matches = [row for row in candidates if row["candidate_id"] == candidate_id]
    if len(matches) != 1:
        return "ABSTAIN", None, f"candidate_id not in options: {candidate_id}"
    return "SELECTED", matches[0], str(parsed.get("reason", ""))


def compute_always_candidate_baseline(
    *,
    benchmark: Path,
    candidate_id: str,
    runs: int,
    event_limit: int,
    timeout: int,
    output_dir: Path,
    condition: str,
    decision_path: str,
) -> list[dict[str, Any]]:
    events = load_event_rows(benchmark, event_limit)
    oracles = load_oracles(benchmark)
    paths = configure_paths(benchmark)
    seeds = DEFAULT_SEEDS[:runs]
    graph_cache: dict[Path, Any] = {}
    reasoner_cache: dict[Path, dict[str, Any]] = {}
    owl_dir = output_dir / "candidate-owls"
    owl_dir.mkdir(parents=True, exist_ok=True)
    rows_out: list[dict[str, Any]] = []

    for run_index, seed in enumerate(seeds, start=1):
        for event in events:
            candidates = load_candidate_map(benchmark).get(event["event_id"], [])
            selected = next((row for row in candidates if row["candidate_id"] == candidate_id), None)
            status = "SELECTED" if selected else "ABSTAIN"
            closure = apply_closure(
                event=event,
                selected=selected,
                selection_status=status,
                oracle=oracles[event["event_id"]],
                paths=paths,
                output_dir=owl_dir,
                graph_cache=graph_cache,
                reasoner_cache=reasoner_cache,
                timeout=timeout,
            )
            row = {
                "event_id": event["event_id"],
                "semantic_type": event["semantic_type"],
                "domain": event["domain"],
                "document_ids": event.get("document_ids", ""),
                "run": run_index,
                "seed": seed,
                "method": condition,
                "decision_path": decision_path,
                "selection_reason": f"always {candidate_id}",
                "selection_status": status,
                "llm_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "latency_ms": 0,
                "api_error": "",
                **closure,
            }
            rows_out.append(row)
            if len(rows_out) % 50 == 0 or len(rows_out) == len(events) * runs:
                print(
                    f"always-cand {condition}: {len(rows_out)}/{len(events) * runs} "
                    f"{event['event_id']} run={run_index} closure={row.get('full_closure_success')}",
                    flush=True,
                )
    return rows_out
