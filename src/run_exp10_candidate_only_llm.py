from __future__ import annotations

"""Exp10-B: candidate-only LLM baseline on candidate-id-permute."""

import argparse
from pathlib import Path
from typing import Any

from exp10_candidate_shortcut_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_SEEDS,
    EXP10_ROOT,
    PROMPT_DIR,
    append_checkpoint,
    apply_closure,
    attach_oracle_fields,
    build_candidate_only_prompt,
    interpret_selection,
    load_checkpoint,
    load_candidate_map,
    load_event_rows,
    load_oracles,
    shuffled_candidates,
    write_method_outputs,
)
from exp9_baseline_common import aggregate_audit, call_deepseek_json
from run_auto_policy_v4_ir_candidate_repair import configure_paths


METHOD = "candidate-only-llm"
CONDITION = "candidate-only-llm"
PROMPT_FILE = PROMPT_DIR / "candidate-only-header.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=EXP10_ROOT / "runs" / "formal" / METHOD)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--event-limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if args.pilot:
        args.runs = 1

    benchmark = args.benchmark_dir.resolve()
    output = args.output_dir.resolve()
    events = load_event_rows(benchmark, args.event_limit)
    oracles = load_oracles(benchmark)
    paths = configure_paths(benchmark)
    seeds = DEFAULT_SEEDS[: args.runs]
    checkpoint = output / "exp10-predictions.jsonl"
    done = load_checkpoint(checkpoint) if args.resume else {}

    graph_cache: dict[Path, Any] = {}
    reasoner_cache: dict[Path, dict[str, Any]] = {}
    owl_dir = output / "candidate-owls"
    owl_dir.mkdir(parents=True, exist_ok=True)
    rows_out: list[dict[str, Any]] = []

    for run_index, seed in enumerate(seeds, start=1):
        for event in events:
            key = (event["event_id"], run_index, CONDITION)
            if key in done:
                rows_out.append(attach_oracle_fields(done[key], oracles[event["event_id"]]))
                continue
            candidates = shuffled_candidates(
                load_candidate_map(benchmark).get(event["event_id"], []),
                event["event_id"],
                seed,
            )
            try:
                parsed, audit = call_deepseek_json(build_candidate_only_prompt(candidates), args.timeout)
                status, selected, reason = interpret_selection(parsed, candidates)
                audits = [audit]
                api_error = ""
            except Exception as exc:  # noqa: BLE001
                parsed = None
                audits = []
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
                "method": CONDITION,
                "decision_path": "CANDIDATE_ONLY_LLM",
                "selection_reason": reason,
                "parsed_json": parsed,
                "api_error": api_error,
                **closure,
                **agg,
            }
            append_checkpoint(
                checkpoint,
                {k: v for k, v in row.items() if k not in {"parsed_json", "oracle_candidate_id", "oracle_value", "selection_oracle_correct"}},
            )
            rows_out.append(row)
            print(
                f"{event['event_id']} run={run_index} {status} {row.get('selected_candidate_id', '')} "
                f"closure={row.get('full_closure_success')}",
                flush=True,
            )

    write_method_outputs(
        condition=CONDITION,
        method="CANDIDATE_ONLY_LLM",
        rows=rows_out,
        output_dir=output,
        benchmark=benchmark,
        script_path=Path(__file__),
        prompt_file=PROMPT_FILE,
        pilot=args.pilot,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
