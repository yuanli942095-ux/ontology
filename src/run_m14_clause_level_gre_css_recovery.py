from __future__ import annotations

"""Clause-level GRE/CSS recovery on failed half hold-out attempts.

This diagnostic module is candidate-blind. It only reads public evidence and
event metadata, then writes V4-compatible raw records for offline repair.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_m12_decomposed_extraction import normalize_token
from auto_policy_m12_deepseek_client import DeepSeekConfig, call_deepseek_chat
from m13_llm_backends import LLMBackend, call_llm_json, resume_artifact_is_complete
from auto_policy_task_formulation import infer_repair_dimension, route_task_formulation
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from run_m12_predicate_focus_pilot import build_focused_evidence
from run_m13_rule_refinement_pilot import IR_PREFIX
from run_m12_decomposed_extraction_pilot import load_evidence
from semantic_v2_common import PROJECT_DIR, write_csv

DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
DEFAULT_DETAILS = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v1-expanded"
    / "final-blind-eval-r5-model-ablation-deepseek-repaired-half"
    / "main-method"
    / "m13-pilot"
    / "arm-d-rule-refinement"
    / "ir"
    / f"{IR_PREFIX}-details.csv"
)
DEFAULT_OUTPUT = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v1-expanded"
    / "m14-clause-level-gre-css-recovery"
)


def truth(value: str) -> bool:
    return str(value or "").strip().lower() == "true"


def selected(row: dict[str, str]) -> bool:
    return bool(str(row.get("selected_candidate_id", "")).strip()) or row.get("selection_status", "").upper() in {
        "SELECT",
        "SELECTED",
    }


def failure_bucket(row: dict[str, str]) -> str:
    if truth(row.get("full_closure_success", "")):
        return "CORRECT_CLOSURE"
    if selected(row):
        return "WRONG_SELECTION"
    path = row.get("decision_path", "")
    if path == "IR_FAIL_CLOSED":
        return "FAIL_CLOSED_INCOMPLETE_IR"
    if path == "IR_RANK_ABSTAIN":
        return "RANK_ABSTAIN"
    if path == "IR_RANK_TIE_ABSTAIN":
        return "TIE_ABSTAIN"
    return path or "OTHER_FAILURE"


def parse_only(raw: str) -> set[str]:
    return {token.strip().upper() for token in raw.split(",") if token.strip()}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def extract_json(text: str) -> dict[str, Any]:
    value = v3.extract_json(text)
    return value if isinstance(value, dict) else {}


def deepseek_config(timeout: int, max_tokens: int) -> DeepSeekConfig:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    return DeepSeekConfig(
        api_key=api_key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/"),
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip(),
        temperature=0,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def raw_record(row: dict[str, str]) -> dict[str, Any]:
    path = Path(row["raw_output_file"])
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return load_json(path)


def clause_prompt(event: dict[str, str], focused: str) -> str:
    return f"""
Select the smallest evidence clauses needed for a GRE/CSS ontology repair task.

Use only the supplied public evidence. Do not infer the final answer. Do not
read or mention candidates or oracle labels.

event_id: {event.get("event_id", "")}
semantic_type: {event.get("semantic_type", "")}
subject_label: {event.get("subject_label", "")}
predicate_label: {event.get("predicate_label", "")}
case_context: {event.get("case_context", "")}

Return ONLY JSON:
{{
  "answerable_evidence": true,
  "clauses": [
    {{
      "text": "exact or near-exact clause/span from evidence",
      "cue": "unless|except|only if|applies to|scope|must|should|other",
      "role": "general_rule|exception_condition|exception_rule|scope_qualifier|scope_target|scoped_statement|value_statement"
    }}
  ],
  "missing_reason": ""
}}

Rules:
- Keep clause boundaries; include the qualifier and the clause it modifies.
- For CSS, include enough context to decide scope_relation.
- For GRE, include the general rule, exception condition, and outcome if present.
- If the evidence does not support the predicate, set answerable_evidence=false.

===== FOCUSED PUBLIC EVIDENCE BEGIN =====
{focused}
===== FOCUSED PUBLIC EVIDENCE END =====
""".strip()


def frame_prompt(event: dict[str, str], clauses: dict[str, Any]) -> str:
    dimension = infer_repair_dimension(event.get("predicate_label", ""))
    semantic_type = event.get("semantic_type", "")
    if semantic_type == "CROSS_SENTENCE_SCOPE":
        schema = {
            "answerable": True,
            "subject": "subject from event/evidence",
            "statement": "statement being scoped",
            "qualifier": "scope qualifier",
            "scope_target": "target to which statement applies",
            "scope_relation": "APPLIES_TO",
            "result": f"{dimension}=concise evidence-grounded value",
            "evidence_spans": ["supporting clause text"],
            "confidence": "high|medium|low",
            "rejection_reason": "",
        }
        guidance = (
            "scope_relation must be one of APPLIES_TO, DOES_NOT_APPLY_TO, LIMITED_TO, "
            "EXCLUDES, MODIFIES_CLAUSE, MODIFIES_SENTENCE, UNKNOWN. Use UNKNOWN only if not answerable."
        )
    else:
        schema = {
            "answerable": True,
            "subject": "subject from event/evidence",
            "general_rule": "general rule",
            "exception_condition": "exception or condition",
            "exception_rule": "outcome under the condition",
            "priority": "EXCEPTION|GENERAL|CONDITIONAL|UNKNOWN",
            "result": f"{dimension}=concise evidence-grounded value",
            "relation": "EXCEPTION_OVERRIDES",
            "evidence_spans": ["supporting clause text"],
            "confidence": "high|medium|low",
            "rejection_reason": "",
        }
        guidance = "If the predicate is an attribute rather than an exception, put the supported value in result."
    return f"""
Convert selected clauses into a candidate-blind semantic frame.

Use only these clauses and public event metadata. Do not read or mention repair
candidates or oracle labels. Preserve evidence wording in evidence_spans.

event_id: {event.get("event_id", "")}
semantic_type: {semantic_type}
subject_label: {event.get("subject_label", "")}
predicate_label: {event.get("predicate_label", "")}
repair_dimension: {dimension}
case_context: {event.get("case_context", "")}

Selected clauses JSON:
{json.dumps(clauses, ensure_ascii=False, indent=2)}

Return ONLY JSON matching this shape:
{json.dumps(schema, ensure_ascii=False, indent=2)}

{guidance}
If the clauses do not answer the predicate, set answerable=false and explain rejection_reason.
""".strip()


def valid_frame(event: dict[str, str], frame: dict[str, Any]) -> tuple[bool, str]:
    if frame.get("answerable") is not True:
        return False, "not_answerable"
    if str(frame.get("confidence", "")).lower() == "low":
        return False, "low_confidence"
    if not str(frame.get("result", "")).strip():
        return False, "missing_result"
    spans = frame.get("evidence_spans")
    if not isinstance(spans, list) or not any(str(item).strip() for item in spans):
        return False, "missing_evidence_spans"
    if event.get("semantic_type") == "CROSS_SENTENCE_SCOPE":
        relation = str(frame.get("scope_relation", "")).strip().upper()
        if relation in {"", "UNKNOWN"}:
            return False, "missing_scope_relation"
    return True, "faithful"


def canonical_result(event: dict[str, str], frame: dict[str, Any]) -> dict[str, Any]:
    result = str(frame.get("result", "")).strip()
    dimension = infer_repair_dimension(event.get("predicate_label", ""))
    family = f"{normalize_token(event.get('subject_label', ''))}_claim"
    if "=" in result:
        result_value = result.split("=", 1)[1]
    else:
        result_value = result
    result = f"{family}={normalize_token(result_value)}"
    relation = str(frame.get("relation") or frame.get("scope_relation") or "UNKNOWN").upper()
    return {
        "family": family,
        "semantic_result": result,
        "derived_semantic_result": result,
        "relation": relation,
        "scope_relation": str(frame.get("scope_relation", "")),
        "result": result,
    }


def build_recovery_record(
    source: dict[str, Any],
    event: dict[str, str],
    clauses: dict[str, Any],
    frame: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    ok, faith = valid_frame(event, frame)
    canonical = canonical_result(event, frame) if ok else {"family": f"{normalize_token(event.get('subject_label', ''))}_claim"}
    facts: dict[str, Any] = {
        "m14_clause_selection": clauses,
        "m14_frame": frame,
        "faithfulness_status": faith,
        "task_formulation": route_task_formulation(event, json.dumps(clauses, ensure_ascii=False)).as_dict(),
    }
    if ok:
        facts.update(
            {
                "subject": frame.get("subject") or event.get("subject_label", ""),
                "statement": frame.get("statement") or event.get("predicate_label", ""),
                "scope_relation": frame.get("scope_relation", ""),
                "applies_to": frame.get("scope_relation", ""),
                "extends": frame.get("scope_relation", ""),
                "result": canonical["semantic_result"],
                "evidence_spans": frame.get("evidence_spans", []),
            }
        )
    response = {
        "semantic_type": event.get("semantic_type", ""),
        "facts": facts,
        "canonical_result": canonical,
        "rules": (
            [
                {
                    "priority": 320,
                    "conditions": [],
                    "canonical_result": canonical,
                    "semantic_result": canonical["semantic_result"],
                }
            ]
            if ok
            else []
        ),
        "m14_clause_level_recovery": True,
    }
    return {
        **source,
        "status": "GENERATED" if ok else "INVALID_SCHEMA",
        "response": response,
        "schema_valid": ok,
        "validation_reason": "m14_clause_recovery_converted" if ok else faith,
        "canonical_status": "OK" if ok else "INCOMPLETE",
        "canonical_result": canonical,
        "method": "M14_CLAUSE_LEVEL_GRE_CSS_RECOVERY",
        "candidate_used": False,
        "oracle_used": False,
        "manual_policy_used": False,
        "m14_clause_level_recovery": True,
        "m14_recovery_status": status,
    }


def run_ir(raw_dir: Path, out_dir: Path, benchmark_dir: Path) -> None:
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_auto_policy_v4_ir_candidate_repair.py"),
        "--benchmark-dir",
        str(benchmark_dir),
        "--raw-dir",
        str(raw_dir),
        "--output-dir",
        str(out_dir),
        "--prefix",
        "m14-clause-level-gre-css-recovery-v4-ir",
        "--method-name",
        "M14_Clause_Level_GRE_CSS_Recovery",
        "--min-score",
        "0.30",
        "--gre-css-min-score",
        "0.24",
        "--min-margin",
        "0.00",
        "--reranker",
        "constraint",
        "--temporal-unique-top1",
        "--robust-ir",
        "--discover-raw",
        "--allow-legacy-experiment",
    ]
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--only", default="")
    parser.add_argument("--top-n", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--skip-ir", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--llm-backend",
        choices=("ollama", "deepseek_api"),
        default="deepseek_api",
    )
    args = parser.parse_args()

    args.benchmark_dir = args.benchmark_dir.resolve()
    args.details = args.details.resolve()
    args.output_dir = args.output_dir.resolve()
    raw_dir = args.output_dir / "raw"
    clause_dir = args.output_dir / "clauses"
    frame_dir = args.output_dir / "frames"
    focus_dir = args.output_dir / "focused-evidence"
    for directory in (raw_dir, clause_dir, frame_dir, focus_dir):
        directory.mkdir(parents=True, exist_ok=True)

    paths = configure_paths(args.benchmark_dir)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    only = parse_only(args.only)
    candidates = []
    for row in read_csv(args.details):
        if row.get("semantic_type") not in {"GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}:
            continue
        if truth(row.get("full_closure_success", "")):
            continue
        if row.get("decision_path") not in {"IR_FAIL_CLOSED", "IR_RANK_ABSTAIN", "IR_RANK_TIE_ABSTAIN"}:
            continue
        if only and row["event_id"].upper() not in only:
            continue
        candidates.append(row)
    if args.limit > 0:
        candidates = candidates[: args.limit]

    backend: LLMBackend = args.llm_backend
    rows_out: list[dict[str, Any]] = []
    for row in candidates:
        event = events[row["event_id"]]
        filename = f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"
        raw_path = raw_dir / filename
        if args.resume and resume_artifact_is_complete(raw_path):
            print(f"{row['event_id']} run={row['run']} [resume] skipped", flush=True)
            continue
        if args.resume and raw_path.is_file():
            print(f"{row['event_id']} run={row['run']} [resume] retry transport-failed artifact", flush=True)
        source = raw_record(row)
        focused, selected_units = build_focused_evidence(event, load_evidence(source), top_n=args.top_n)
        focus_path = focus_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}-focused.md"
        clause_path = clause_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"
        frame_path = frame_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"
        focus_path.write_text(focused, encoding="utf-8")
        seed = int(row["seed"])
        try:
            clause_call = call_llm_json(
                backend, clause_prompt(event, focused), seed, args.timeout, num_predict=700
            )
            clauses = extract_json(clause_call.text)
            frame_call = call_llm_json(
                backend, frame_prompt(event, clauses), seed, args.timeout, num_predict=700
            )
            frame = extract_json(frame_call.text)
            status = "OK"
        except Exception as exc:
            clause_call = frame_call = None
            clauses = {}
            frame = {"answerable": False, "rejection_reason": f"{type(exc).__name__}: {exc}"}
            status = f"error:{type(exc).__name__}"
        clause_path.write_text(json.dumps(clauses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        frame_path.write_text(json.dumps(frame, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        record = build_recovery_record(source, event, clauses, frame, status)
        record["m14_clause_level_audit"] = {
            "source_raw_output_file": row.get("raw_output_file", ""),
            "previous_decision_path": row.get("decision_path", ""),
            "previous_ir_reason": row.get("ir_reason", ""),
            "focused_evidence_file": str(focus_path.relative_to(PROJECT_DIR)),
            "clause_json_file": str(clause_path.relative_to(PROJECT_DIR)),
            "frame_json_file": str(frame_path.relative_to(PROJECT_DIR)),
            "selected_units": selected_units,
            "candidate_used": False,
            "oracle_used": False,
            "manual_policy_used": False,
            "llm_backend": backend,
            "llm_model": frame_call.model if frame_call else "",
            "clause_attempt_count": clause_call.attempt_count if clause_call else 0,
            "frame_attempt_count": frame_call.attempt_count if frame_call else 0,
            "clause_retry_errors": list(clause_call.retry_errors) if clause_call else [],
            "frame_retry_errors": list(frame_call.retry_errors) if frame_call else [],
        }
        raw_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ok, faith = valid_frame(event, frame)
        rows_out.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "seed": row["seed"],
                "semantic_type": row["semantic_type"],
                "domain": row.get("domain", ""),
                "previous_bucket": failure_bucket(row),
                "previous_decision_path": row.get("decision_path", ""),
                "previous_ir_reason": row.get("ir_reason", ""),
                "answerable_evidence": clauses.get("answerable_evidence", ""),
                "clause_count": len(clauses.get("clauses", [])) if isinstance(clauses.get("clauses"), list) else 0,
                "frame_answerable": frame.get("answerable", ""),
                "frame_result": frame.get("result", ""),
                "frame_scope_relation": frame.get("scope_relation", ""),
                "faithfulness_status": faith,
                "raw_output_file": str(raw_path.relative_to(PROJECT_DIR)),
                "clause_prompt_tokens": clause_call.prompt_tokens if clause_call else 0,
                "clause_completion_tokens": clause_call.completion_tokens if clause_call else 0,
                "frame_prompt_tokens": frame_call.prompt_tokens if frame_call else 0,
                "frame_completion_tokens": frame_call.completion_tokens if frame_call else 0,
                "llm_model": frame_call.model if frame_call else "",
            }
        )
        print(
            f"{row['event_id']} run={row['run']} type={row['semantic_type']} "
            f"prev={row.get('decision_path','')} frame_ok={ok} faith={faith}",
            flush=True,
        )

    if rows_out:
        write_csv(args.output_dir / "m14-clause-level-summary.csv", rows_out)
    if not args.skip_ir and any(raw_dir.glob("*.json")):
        run_ir(raw_dir, args.output_dir / "ir", args.benchmark_dir)
    elif not args.skip_ir:
        print(f"no m14 raw records under {raw_dir}; skipping IR repair", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
