from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""LLM candidate-alignment judge for M7 task-misaligned IR cases.

This pilot is candidate-aware by design, but only at the candidate-selection
stage. It does not rewrite candidate-blind policy construction. It tests whether
an additional LLM call can compare the extracted evidence/rule against ontology
repair candidates and reject document-level distractors.
"""

import argparse
import csv
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from run_auto_policy_v4_ir_candidate_repair import (
    configure_paths,
    load_candidates,
    read_csv,
    ready,
)
from semantic_v2_common import PROJECT_DIR, write_csv


DETAILS = (
    PROJECT_DIR
    / "output"
    / "m13-rule-refinement-pilot"
    / "arm-d-rule-refinement"
    / "ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
RAW_DIR = PROJECT_DIR / "output" / "m13-rule-refinement-pilot" / "arm-d-rule-refinement" / "raw_window_metadata_light" / "raw"
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
OUTPUT_ROOT = PROJECT_DIR / "output" / "m14-llm-candidate-alignment-pilot"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def operation_summary(candidate: dict[str, Any]) -> str:
    op = candidate.get("operation")
    if not isinstance(op, dict):
        return ""
    return json.dumps(op, ensure_ascii=False, sort_keys=True)


def candidate_prompt(event: dict[str, str], detail: dict[str, str], record: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    audit = record.get("m13_rule_refinement_audit", {})
    rule = audit.get("refined_rule", {}) if isinstance(audit, dict) else {}
    candidate_lines = []
    for cand in candidates:
        candidate_lines.append(
            "\n".join(
                [
                    f"- candidate_id: {cand.get('candidate_id', '')}",
                    f"  display_value: {cand.get('display_value', '')}",
                    f"  description: {cand.get('description', '')}",
                    f"  operation: {operation_summary(cand)[:900]}",
                ]
            )
        )
    return f"""
You are the final ontology repair candidate alignment judge.

This is candidate selection, not policy generation. Compare the evidence-grounded
extracted rule with the ontology repair candidates. Select a candidate only if
its display_value/operation repairs the same property-level semantic claim.

Do not prefer candidates merely because they share generic words such as eCFR,
regulatory, amends, title 17, or disclosure. Reject document-level amendment
status when the candidate is about a property-level ontology fact, and reject
property-level facts when the extracted rule is only document-level.

event_id: {event.get("event_id", "")}
semantic_type: {event.get("semantic_type", "")}
subject_label: {event.get("subject_label", "")}
predicate_label: {event.get("predicate_label", "")}
case_context: {event.get("case_context", "")}

Extracted IR:
{detail.get("ir_semantic_string", "")}

Refined evidence-grounded rule:
RULE_TYPE: {rule.get("rule_type", "")}
SUBJECT: {rule.get("subject", "")}
PREDICATE: {rule.get("predicate", "")}
CONDITION: {rule.get("condition", "")}
NORMATIVE_VALUE: {rule.get("normative_value", "")}
RELATION: {rule.get("relation", "")}
EVIDENCE: {" | ".join(str(x) for x in rule.get("evidence", []))}

Candidates:
{chr(10).join(candidate_lines)}

Return exactly these lines:
DECISION: SELECT | ABSTAIN
CANDIDATE_ID: CAND_001 or empty
CONFIDENCE: high | medium | low
ALIGNMENT_REASON: one sentence explaining property-level alignment
EVIDENCE_SUPPORT: yes | no
""".strip()


def call_judge(prompt: str, seed: int, timeout: int) -> tuple[str, int, int]:
    payload = {
        "model": v3.MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0,
            "num_predict": 500,
            "num_ctx": 16384,
            "seed": seed,
        },
    }
    request = urllib.request.Request(
        v3.OLLAMA_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
    return str(raw.get("response", "")), int((time.perf_counter() - start) * 1000), int(raw.get("eval_count", 0) or 0)


def field_line(text: str, name: str) -> str:
    match = re.search(rf"(?im)^[ \t]*{re.escape(name)}[ \t]*:[ \t]*([^\r\n]*)", text)
    return match.group(1).strip() if match else ""


def parse_judge(text: str) -> dict[str, str]:
    return {
        "decision": field_line(text, "DECISION").upper(),
        "candidate_id": field_line(text, "CANDIDATE_ID").upper(),
        "confidence": field_line(text, "CONFIDENCE").lower(),
        "alignment_reason": field_line(text, "ALIGNMENT_REASON"),
        "evidence_support": field_line(text, "EVIDENCE_SUPPORT").lower(),
    }


def eligible_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row.get("ir_status") == "OK"
        and row.get("selection_status") == "ABSTAIN"
        and row.get("decision_path") == "IR_RANK_ABSTAIN"
    ]


def main() -> int:
    block_legacy_entrypoint(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--details", type=Path, default=DETAILS)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--qwen-timeout", type=int, default=180)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    paths = configure_paths(args.benchmark_dir)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    candidates_by_event = load_candidates(paths["candidate_csv"])
    rows = eligible_rows(read_csv(args.details))
    out_rows: list[dict[str, Any]] = []
    raw_judge_dir = args.output_dir / "judge-outputs"
    raw_judge_dir.mkdir(parents=True, exist_ok=True)

    for row in rows:
        filename = f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"
        record = load_json(args.raw_dir / filename)
        prompt = candidate_prompt(events[row["event_id"]], row, record, candidates_by_event.get(row["event_id"], []))
        try:
            text, runtime_ms, eval_count = call_judge(prompt, int(row["seed"]) + 2003, args.qwen_timeout)
            parsed = parse_judge(text)
        except Exception as exc:
            text = ""
            runtime_ms = 0
            eval_count = 0
            parsed = {
                "decision": "ABSTAIN",
                "candidate_id": "",
                "confidence": "",
                "alignment_reason": f"error:{type(exc).__name__}:{exc}",
                "evidence_support": "no",
            }
        (raw_judge_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}.txt").write_text(text, encoding="utf-8")
        valid_select = (
            parsed["decision"] == "SELECT"
            and parsed["candidate_id"] in {cand.get("candidate_id", "").upper() for cand in candidates_by_event.get(row["event_id"], [])}
            and parsed["confidence"] in {"high", "medium"}
            and parsed["evidence_support"] == "yes"
        )
        selected = parsed["candidate_id"] if valid_select else ""
        correct = bool(selected and selected == row.get("oracle_candidate_id", "").upper())
        out_rows.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "seed": row["seed"],
                "semantic_type": row["semantic_type"],
                "domain": row["domain"],
                "original_decision_path": row["decision_path"],
                "original_top_scores": row["candidate_scores_json"],
                "judge_decision": parsed["decision"],
                "judge_candidate_id": parsed["candidate_id"],
                "judge_confidence": parsed["confidence"],
                "judge_evidence_support": parsed["evidence_support"],
                "judge_valid_select": valid_select,
                "oracle_candidate_id": row.get("oracle_candidate_id", ""),
                "judge_oracle_correct": correct,
                "alignment_reason": parsed["alignment_reason"],
                "runtime_ms": runtime_ms,
                "eval_count": eval_count,
            }
        )
        print(
            f"{row['event_id']} run={row['run']} judge={parsed['decision']} "
            f"candidate={parsed['candidate_id']} correct={correct}",
            flush=True,
        )

    write_csv(args.output_dir / "llm-candidate-alignment-details.csv", out_rows)
    attempts = len(out_rows)
    selected = sum(bool(row["judge_valid_select"]) for row in out_rows)
    correct = sum(bool(row["judge_oracle_correct"]) for row in out_rows)
    write_csv(
        args.output_dir / "llm-candidate-alignment-summary.csv",
        [
            {
                "attempts": attempts,
                "selected": selected,
                "oracle_correct": correct,
                "coverage": selected / attempts if attempts else 0,
                "oracle_accuracy": correct / attempts if attempts else 0,
                "selected_precision": correct / selected if selected else 0,
                "wrong_selection": selected - correct,
            }
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
