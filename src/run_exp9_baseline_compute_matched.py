from __future__ import annotations

"""Exp9 B3: Compute-matched deliberative LLM baseline (3 stages) with formal closure."""

import argparse
import json
from pathlib import Path
from typing import Any

from exp9_baseline_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_SEEDS,
    EXP9_ROOT,
    aggregate_audit,
    append_checkpoint,
    apply_closure,
    call_deepseek_json,
    attach_oracle_fields,
    compact_operation,
    load_checkpoint,
    load_event_bundles,
    load_oracles,
    read_prompt,
    shuffled_candidates,
    write_method_outputs,
)
from run_auto_policy_v4_ir_candidate_repair import configure_paths
from semantic_v2_common import PROJECT_DIR


METHOD = "compute-matched"
PROMPT_FILE = EXP9_ROOT / "baseline-prompts" / "compute-matched-stage1.txt"
BUDGET_PATH = EXP9_ROOT / "ecr-compute-budget.json"


def event_context(event: dict[str, Any]) -> str:
    return (
        f"event_id: {event['event_id']}\n"
        f"semantic_type: {event['semantic_type']}\n"
        f"domain: {event['domain']}\n"
        f"case_context: {event['case_context']}\n"
        f"subject_label: {event['subject_label']}\n"
        f"predicate_label: {event['predicate_label']}\n\n"
        f"PUBLIC EVIDENCE:\n{event['evidence']}\n"
    )


def candidate_options(candidates: list[dict[str, Any]]) -> str:
    options = [
        {
            "candidate_id": row["candidate_id"],
            "display_value": row["display_value"],
            "operation": compact_operation(row["operation"]),
        }
        for row in candidates
    ]
    return json.dumps(options, ensure_ascii=False, indent=2)


def load_budget() -> dict[str, Any]:
    if not BUDGET_PATH.is_file():
        return {"max_calls_per_attempt": 3, "max_total_tokens_per_attempt": 20000}
    return json.loads(BUDGET_PATH.read_text(encoding="utf-8"))


def interpret_final(parsed: dict[str, Any] | None, candidates: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None, str]:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=EXP9_ROOT / "runs" / "formal" / METHOD)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--event-limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if args.pilot:
        args.runs = 1

    budget = load_budget()
    max_calls = int(budget.get("max_calls_per_attempt", 3))
    max_tokens = int(budget.get("max_total_tokens_per_attempt", 20000))

    benchmark = args.benchmark_dir.resolve()
    output = args.output_dir.resolve()
    events = load_event_bundles(benchmark, args.event_limit)
    oracles = load_oracles(benchmark)
    paths = configure_paths(benchmark)
    seeds = DEFAULT_SEEDS[: args.runs]
    checkpoint = output / "exp9-predictions.jsonl"
    done = load_checkpoint(checkpoint) if args.resume else {}

    stage1 = read_prompt("compute-matched-stage1.txt")
    stage2 = read_prompt("compute-matched-stage2.txt")
    stage3 = read_prompt("compute-matched-stage3.txt")

    graph_cache: dict[Path, Any] = {}
    reasoner_cache: dict[Path, dict[str, Any]] = {}
    owl_dir = output / "candidate-owls"
    owl_dir.mkdir(parents=True, exist_ok=True)
    rows_out: list[dict[str, Any]] = []

    for run_index, seed in enumerate(seeds, start=1):
        for event in events:
            key = (event["event_id"], run_index, METHOD)
            if key in done:
                rows_out.append(attach_oracle_fields(done[key], oracles[event["event_id"]]))
                continue
            candidates = shuffled_candidates(event["candidates"], event["event_id"], seed)
            audits: list[dict[str, Any]] = []
            api_error = ""
            p2: dict[str, Any] | None = None
            try:
                p1, a1 = call_deepseek_json(stage1 + "\n\n" + event_context(event), args.timeout)
                audits.append(a1)
                if max_calls >= 2 and sum(a.get("total_tokens", 0) for a in audits) < max_tokens:
                    p2, a2 = call_deepseek_json(
                        stage2
                        + "\n\n"
                        + event_context(event)
                        + "\nEVIDENCE ANALYSIS:\n"
                        + json.dumps(p1 or {}, ensure_ascii=False)
                        + "\n\nCANDIDATE OPTIONS:\n"
                        + candidate_options(candidates),
                        args.timeout,
                    )
                    audits.append(a2)
                if max_calls >= 3 and sum(a.get("total_tokens", 0) for a in audits) < max_tokens:
                    p3, a3 = call_deepseek_json(
                        stage3
                        + "\n\n"
                        + event_context(event)
                        + "\nEVIDENCE ANALYSIS:\n"
                        + json.dumps(p1 or {}, ensure_ascii=False)
                        + "\n\nCANDIDATE COMPARISON:\n"
                        + json.dumps(p2 if len(audits) > 1 else {}, ensure_ascii=False),
                        args.timeout,
                    )
                    audits.append(a3)
                    status, selected, reason = interpret_final(p3, candidates)
                else:
                    status, selected, reason = "ABSTAIN", None, "compute budget exhausted before final stage"
            except Exception as exc:  # noqa: BLE001
                status, selected, reason = "ABSTAIN", None, str(exc)
                api_error = type(exc).__name__

            closure = apply_closure(
                event=event,
                selected=selected,
                selection_status=status,
                oracle=oracles[event["event_id"]],
                paths=paths,
                output_dir=owl_dir,
                graph_cache=graph_cache,
                reasoner_cache=reasoner_cache,
                timeout=args.timeout,
            )
            agg = aggregate_audit(audits)
            row = {
                "event_id": event["event_id"],
                "semantic_type": event["semantic_type"],
                "domain": event["domain"],
                "document_ids": event.get("document_ids", ""),
                "run": run_index,
                "seed": seed,
                "method": METHOD,
                "decision_path": "COMPUTE_MATCHED_3STAGE",
                "selection_reason": reason,
                "api_error": api_error,
                **closure,
                **agg,
            }
            row_for_checkpoint = {
                k: v
                for k, v in row.items()
                if k not in {"oracle_candidate_id", "oracle_value", "selection_oracle_correct"}
            }
            append_checkpoint(checkpoint, row_for_checkpoint)
            rows_out.append(row)
            print(
                f"{event['event_id']} run={run_index} {status} {row.get('selected_candidate_id', '')} "
                f"calls={agg['llm_calls']} closure={row.get('full_closure_success')}",
                flush=True,
            )

    write_method_outputs(
        method=METHOD,
        rows=rows_out,
        output_dir=output,
        benchmark=benchmark,
        script_path=Path(__file__),
        prompt_file=PROMPT_FILE,
        pilot=args.pilot,
        extra={"compute_budget": budget},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
