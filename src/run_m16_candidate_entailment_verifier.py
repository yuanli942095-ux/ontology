from __future__ import annotations

"""M16 candidate entailment verifier for GRE/CSS abstain cases.

The verifier is used only after candidate-blind M14 frame construction and V4
ranking. It reads public evidence/frame plus public repair candidates, asks for
candidate-level entailment labels, and selects only when exactly one candidate
is supported. Oracle is loaded only after selection for evaluation.
"""

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

import validate_benchmark as benchmark_validator
from auto_policy_m12_deepseek_client import DeepSeekConfig, call_deepseek_chat
from m13_llm_backends import LLMBackend, call_llm_json, resume_artifact_is_complete
from run_auto_policy_v2_candidate_repair import (
    graph_delta,
    load_graph,
    materialize_candidate_owl,
    repair_checks,
    resolve_candidate_owl_path,
    resolve_source_owl,
)
from run_auto_policy_v4_ir_candidate_repair import configure_paths, load_candidates, read_csv, ready
from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded" / "m16-candidate-entailment"
DEFAULT_DETAILS = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v1-expanded"
    / "m15-full-holdout-combined"
    / "m15-full-holdout-combined-details.csv"
)
M14_DETAIL_PATHS = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v1-expanded"
    / "m14-clause-level-gre-css-recovery-half-failures"
    / "ir"
    / "m14-clause-level-gre-css-recovery-v4-ir-details.csv",
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v1-expanded"
    / "m14-second-half-eval"
    / "m14-recovery"
    / "ir"
    / "m14-clause-level-gre-css-recovery-v4-ir-details.csv",
)


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def parse_only(raw: str) -> set[str]:
    return {token.strip().upper() for token in raw.split(",") if token.strip()}


def deepseek_config(timeout: int, max_tokens: int) -> DeepSeekConfig:
    import os

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


def key_for(row: dict[str, str]) -> str:
    return f"{row['event_id']}|{row['run']}|{row['seed']}"


def load_m14_rows() -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for path in M14_DETAIL_PATHS:
        if path.is_file():
            for row in read_csv(path):
                rows[key_for(row)] = row
    return rows


def load_m14_rows_from(paths: list[Path] | None = None) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for path in paths or list(M14_DETAIL_PATHS):
        if path.is_file():
            for row in read_csv(path):
                rows[key_for(row)] = row
    return rows


def raw_record(row: dict[str, str]) -> dict[str, Any]:
    path = Path(row["raw_output_file"])
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return load_json(path)


def extract_frame_and_evidence(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    response = record.get("response", {})
    facts = response.get("facts", {}) if isinstance(response, dict) else {}
    frame = facts.get("m14_frame", {}) if isinstance(facts, dict) else {}
    if not isinstance(frame, dict):
        frame = {}
    spans = frame.get("evidence_spans") or facts.get("evidence_spans") if isinstance(facts, dict) else []
    if not isinstance(spans, list):
        spans = [str(spans)]
    spans = [str(item).strip() for item in spans if str(item).strip()]
    return frame, spans


def verifier_prompt(
    event: dict[str, str],
    frame: dict[str, Any],
    evidence_spans: list[str],
    candidate: dict[str, Any],
) -> str:
    operation = candidate.get("operation", {})
    operation_view = {
        "operator": operation.get("operator", "") if isinstance(operation, dict) else "",
        "new_value": operation.get("new_value", {}) if isinstance(operation, dict) else {},
    }
    return f"""
Decide whether one repair candidate is entailed by the public evidence and the
candidate-blind semantic frame.

Do not use oracle labels. Do not compare candidate IDs. Judge only this
candidate against the evidence/frame.

event_id: {event.get("event_id", "")}
semantic_type: {event.get("semantic_type", "")}
subject_label: {event.get("subject_label", "")}
predicate_label: {event.get("predicate_label", "")}
case_context: {event.get("case_context", "")}

Semantic frame JSON:
{json.dumps(frame, ensure_ascii=False, indent=2)}

Evidence spans:
{json.dumps(evidence_spans, ensure_ascii=False, indent=2)}

Candidate:
{{
  "candidate_id": "{candidate.get("candidate_id", "")}",
  "display_value": {json.dumps(candidate.get("display_value", ""), ensure_ascii=False)},
  "operation": {json.dumps(operation_view, ensure_ascii=False)}
}}

Return ONLY JSON:
{{
  "verdict": "supported|contradicted|unrelated|underspecified",
  "confidence": "high|medium|low",
  "supported_value": "the exact candidate value if supported, otherwise empty",
  "evidence_overlap": ["short phrases that support or contradict the candidate"],
  "reason": "brief explanation"
}}

Guidelines:
- supported: the candidate's value is the same normative claim/scope/exception outcome stated by the evidence.
- contradicted: evidence supports a different value.
- unrelated: candidate is a different claim, neighboring clause, or different predicate.
- underspecified: evidence/frame is too incomplete to decide.
- Be conservative. If multiple candidates could be supported, mark ambiguous candidates as underspecified.
""".strip()


def extract_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


def supported(verdict: dict[str, Any]) -> bool:
    return (
        str(verdict.get("verdict", "")).strip().lower() == "supported"
        and str(verdict.get("confidence", "")).strip().lower() in {"high", "medium"}
    )


def run_repair_checks(
    selected: dict[str, Any],
    event: dict[str, str],
    *,
    paths: dict[str, Path],
    output_dir: Path,
    graph_cache: dict[Path, Any],
    reasoner_cache: dict[Path, dict[str, Any]],
    timeout: int,
) -> dict[str, Any]:
    source_path = resolve_source_owl(event, mutants_dir=paths["mutants_dir"], project_dir=PROJECT_DIR)
    candidate_path = resolve_candidate_owl_path(
        event["event_id"],
        selected["candidate_id"],
        benchmark_dir=Path(paths["event_csv"]).parents[2],
        built_dir=paths["built_dir"],
        output_dir=output_dir,
    )
    materialize_candidate_owl(source_path=source_path, operation=selected["operation"], dest_path=candidate_path)
    source_graph = graph_cache.setdefault(source_path, load_graph(source_path))
    candidate_graph = graph_cache.setdefault(candidate_path, load_graph(candidate_path))
    reasoner = reasoner_cache.setdefault(candidate_path, benchmark_validator.run_reasoner(candidate_path, timeout))
    source_trigger, candidate_repair, repair_message = repair_checks(source_graph, candidate_graph, selected["operation"])
    removed, added = graph_delta(source_graph, candidate_graph)
    return {
        "candidate_owl": str(candidate_path.relative_to(PROJECT_DIR)),
        "triples_removed": removed,
        "triples_added": added,
        "minimal_edit_gate": removed == 1 and added == 1,
        "reasoner_result": reasoner.get("status", ""),
        "reasoner_runtime_ms": reasoner.get("runtime_ms", 0),
        "reasoner_gate": reasoner.get("status") == "CONSISTENT",
        "source_triggers_repair_cq": source_trigger,
        "candidate_satisfies_repair_cq": candidate_repair,
        "repair_cq_message": repair_message,
    }


def summarize(details: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_event: list[dict[str, Any]] = []
    by_type: list[dict[str, Any]] = []
    event_groups: dict[str, list[dict[str, Any]]] = {}
    type_groups: dict[str, list[dict[str, Any]]] = {}
    for row in details:
        event_groups.setdefault(row["event_id"], []).append(row)
        type_groups.setdefault(row["semantic_type"], []).append(row)
    for event_id, rows in sorted(event_groups.items()):
        closure = sum(truth(row.get("full_closure_success")) for row in rows)
        by_event.append(
            {
                "event_id": event_id,
                "semantic_type": rows[0]["semantic_type"],
                "domain": rows[0]["domain"],
                "attempts": len(rows),
                "selected": sum(row["selection_status"] == "SELECTED" for row in rows),
                "abstains": sum(row["selection_status"] == "ABSTAIN" for row in rows),
                "full_closure_accuracy": closure / len(rows),
                "strict_event_success": closure == len(rows),
            }
        )
    for semantic_type, rows in [("ALL", details), *sorted(type_groups.items())]:
        events = [row for row in by_event if semantic_type == "ALL" or row["semantic_type"] == semantic_type]
        closure = sum(truth(row.get("full_closure_success")) for row in rows)
        oracle = sum(truth(row.get("selection_oracle_correct")) for row in rows)
        by_type.append(
            {
                "semantic_type": semantic_type,
                "events": len(events),
                "attempts": len(rows),
                "selected": sum(row["selection_status"] == "SELECTED" for row in rows),
                "abstains": sum(row["selection_status"] == "ABSTAIN" for row in rows),
                "oracle_accuracy": oracle / len(rows) if rows else 0,
                "full_closure_accuracy": closure / len(rows) if rows else 0,
                "strict_event_successes": sum(truth(row["strict_event_success"]) for row in events),
                "strict_event_accuracy": sum(truth(row["strict_event_success"]) for row in events) / len(events) if events else 0,
            }
        )
    return by_event, by_type, [by_type[0]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--only", default="")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--m14-details",
        type=Path,
        action="append",
        default=None,
        help="M14 detail CSV containing candidate-blind frames. May be passed more than once.",
    )
    parser.add_argument(
        "--llm-backend",
        choices=("ollama", "deepseek_api"),
        default="deepseek_api",
    )
    args = parser.parse_args()

    args.benchmark_dir = args.benchmark_dir.resolve()
    args.details = args.details.resolve()
    args.output_dir = args.output_dir.resolve()
    verdict_dir = args.output_dir / "verdicts"
    owl_dir = args.output_dir / "candidate-owls"
    for directory in (verdict_dir, owl_dir):
        directory.mkdir(parents=True, exist_ok=True)

    paths = configure_paths(args.benchmark_dir)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    candidates_by_event = load_candidates(paths["candidate_csv"])
    oracles = {row["event_id"]: row for row in read_csv(paths["oracle_csv"]) if ready(row.get("status", ""))}
    m14_paths = [path.resolve() for path in args.m14_details] if args.m14_details else None
    m14_rows = load_m14_rows_from(m14_paths)
    only = parse_only(args.only)
    target_rows = []
    for row in read_csv(args.details):
        if row.get("semantic_type") not in {"GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}:
            continue
        decision_path = row.get("final_decision_path") or row.get("decision_path")
        if decision_path not in {"IR_RANK_ABSTAIN", "IR_RANK_TIE_ABSTAIN"}:
            continue
        if truth(row.get("closure_success")):
            continue
        if only and row["event_id"].upper() not in only:
            continue
        target_rows.append(row)
    if args.limit > 0:
        target_rows = target_rows[: args.limit]

    backend: LLMBackend = args.llm_backend
    graph_cache: dict[Path, Any] = {}
    reasoner_cache: dict[Path, dict[str, Any]] = {}
    details_out: list[dict[str, Any]] = []
    verifier_rows: list[dict[str, Any]] = []
    for row in target_rows:
        event = events[row["event_id"]]
        source_row = m14_rows.get(key_for(row))
        if not source_row:
            raise RuntimeError(f"missing M14 row for {key_for(row)}")
        record = raw_record(source_row)
        frame, spans = extract_frame_and_evidence(record)
        verdicts: list[dict[str, Any]] = []
        for candidate in candidates_by_event.get(row["event_id"], []):
            verdict_path = verdict_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}-{candidate['candidate_id']}.json"
            if args.resume and resume_artifact_is_complete(verdict_path):
                verdict = load_json(verdict_path)
            else:
                if args.resume and verdict_path.is_file():
                    print(f"{row['event_id']} {candidate['candidate_id']} [resume] retry transport-failed verdict", flush=True)
                call = call_llm_json(
                    backend,
                    verifier_prompt(event, frame, spans, candidate),
                    int(row["seed"]),
                    args.timeout,
                    num_predict=500,
                )
                verdict = extract_json(call.text)
                verdict["_audit"] = {
                    "prompt_tokens": call.prompt_tokens,
                    "completion_tokens": call.completion_tokens,
                    "total_tokens": call.prompt_tokens + call.completion_tokens,
                    "runtime_ms": call.runtime_ms,
                    "model": call.model,
                    "backend": call.backend,
                    "candidate_used": True,
                    "oracle_used": False,
                    "manual_policy_used": False,
                    "attempt_count": call.attempt_count,
                    "retry_errors": list(call.retry_errors),
                }
                verdict_path.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            verdict["candidate_id"] = candidate["candidate_id"]
            verdict["display_value"] = candidate["display_value"]
            verdicts.append(verdict)
            verifier_rows.append(
                {
                    "event_id": row["event_id"],
                    "semantic_type": row["semantic_type"],
                    "run": row["run"],
                    "seed": row["seed"],
                    "candidate_id": candidate["candidate_id"],
                    "display_value": candidate["display_value"],
                    "verdict": verdict.get("verdict", ""),
                    "confidence": verdict.get("confidence", ""),
                    "reason": verdict.get("reason", ""),
                    "verdict_file": str(verdict_path.relative_to(PROJECT_DIR)),
                }
            )
        supported_rows = [item for item in verdicts if supported(item)]
        selected = None
        selection_status = "ABSTAIN"
        reason = "no unique supported candidate"
        if len(supported_rows) == 1:
            selected = next(candidate for candidate in candidates_by_event[row["event_id"]] if candidate["candidate_id"] == supported_rows[0]["candidate_id"])
            selection_status = "SELECTED"
            reason = "unique candidate entailed by evidence/frame"
        elif len(supported_rows) > 1:
            reason = "multiple supported candidates"

        out: dict[str, Any] = {
            "event_id": row["event_id"],
            "semantic_type": row["semantic_type"],
            "domain": row.get("domain", ""),
            "run": row["run"],
            "seed": row["seed"],
            "selection_status": selection_status,
            "selected_candidate_id": selected["candidate_id"] if selected else "",
            "selected_value": selected["display_value"] if selected else "",
            "selection_reason": reason,
            "decision_path": "M16_ENTAILMENT_SELECT" if selected else "M16_ENTAILMENT_ABSTAIN",
            "supported_count": len(supported_rows),
            "verdicts_json": json.dumps(verdicts, ensure_ascii=False, sort_keys=True),
            "source_m14_raw_output_file": source_row.get("raw_output_file", ""),
            "candidate_used": True,
            "oracle_used_before_selection": False,
        }
        if selected:
            out.update(
                run_repair_checks(
                    selected,
                    event,
                    paths=paths,
                    output_dir=owl_dir,
                    graph_cache=graph_cache,
                    reasoner_cache=reasoner_cache,
                    timeout=args.timeout,
                )
            )
        oracle = oracles[row["event_id"]]
        out["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        out["oracle_value"] = oracle["oracle_value"]
        out["selection_oracle_correct"] = selection_status == "SELECTED" and selected and selected["candidate_id"] == oracle["oracle_candidate_id"]
        out["full_closure_success"] = all(
            truth(out.get(key))
            for key in (
                "selection_oracle_correct",
                "minimal_edit_gate",
                "reasoner_gate",
                "source_triggers_repair_cq",
                "candidate_satisfies_repair_cq",
            )
        )
        details_out.append(out)
        print(
            f"{row['event_id']} run={row['run']} {row['semantic_type']} supported={len(supported_rows)} "
            f"selected={out['selected_candidate_id']} closure={out['full_closure_success']}",
            flush=True,
        )

    if verifier_rows:
        write_csv(args.output_dir / "m16-candidate-verdicts.csv", verifier_rows)
    if details_out:
        write_csv(args.output_dir / "m16-candidate-entailment-details.csv", details_out)
        by_event, by_type, summary = summarize(details_out)
        write_csv(args.output_dir / "m16-candidate-entailment-by-event.csv", by_event)
        write_csv(args.output_dir / "m16-candidate-entailment-by-type.csv", by_type)
        write_csv(args.output_dir / "m16-candidate-entailment-summary.csv", summary)
        print(json.dumps(summary[0], ensure_ascii=False), flush=True)
    else:
        empty_summary = {
            "semantic_type": "ALL",
            "events": 0,
            "attempts": 0,
            "selected": 0,
            "abstains": 0,
            "oracle_accuracy": 0,
            "full_closure_accuracy": 0,
            "strict_event_successes": 0,
            "strict_event_accuracy": 0,
        }
        write_csv(args.output_dir / "m16-candidate-entailment-summary.csv", [empty_summary])
        print(json.dumps(empty_summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
