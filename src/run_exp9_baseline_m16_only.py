from __future__ import annotations

"""Exp9 B2: M16-only baseline (3 entailment calls per attempt) with formal closure."""

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
    attach_oracle_fields,
    checkpoint_key,
    empty_frame,
    evidence_spans_from_markdown,
    load_checkpoint,
    load_event_bundles,
    load_oracles,
    write_method_outputs,
)
from m13_llm_backends import call_llm_json
from run_auto_policy_v4_ir_candidate_repair import configure_paths
from run_m16_candidate_entailment_verifier import extract_json, supported, verifier_prompt
from semantic_v2_common import PROJECT_DIR


METHOD = "m16-only"
PROMPT_FILE = EXP9_ROOT / "baseline-prompts" / "m16-only.txt"


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

    benchmark = args.benchmark_dir.resolve()
    output = args.output_dir.resolve()
    events = load_event_bundles(benchmark, args.event_limit)
    oracles = load_oracles(benchmark)
    paths = configure_paths(benchmark)
    seeds = DEFAULT_SEEDS[: args.runs]
    checkpoint = output / "exp9-predictions.jsonl"
    done = load_checkpoint(checkpoint) if args.resume else {}
    verdict_dir = output / "verdicts"
    verdict_dir.mkdir(parents=True, exist_ok=True)

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
            spans = evidence_spans_from_markdown(event["evidence"])
            frame = empty_frame()["m14_frame"]
            audits: list[dict[str, Any]] = []
            verdicts: list[dict[str, Any]] = []
            for candidate in event["candidates"]:
                verdict_path = verdict_dir / f"{event['event_id']}-run{run_index}-seed{seed}-{candidate['candidate_id']}.json"
                if args.resume and verdict_path.is_file():
                    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
                else:
                    call = call_llm_json(
                        "deepseek_api",
                        verifier_prompt(event, frame, spans, candidate),
                        int(seed),
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
                    }
                    verdict_path.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                if "_audit" in verdict:
                    audits.append(verdict["_audit"])
                verdict["candidate_id"] = candidate["candidate_id"]
                verdicts.append(verdict)

            supported_rows = [item for item in verdicts if supported(item)]
            selected = None
            status = "ABSTAIN"
            reason = "no unique supported candidate"
            if len(supported_rows) == 1:
                selected = next(
                    row for row in event["candidates"] if row["candidate_id"] == supported_rows[0]["candidate_id"]
                )
                status = "SELECTED"
                reason = "unique candidate entailed by evidence"
            elif len(supported_rows) > 1:
                reason = "multiple supported candidates"

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
                "decision_path": "M16_ONLY_SELECT" if status == "SELECTED" else "M16_ONLY_ABSTAIN",
                "selection_reason": reason,
                "supported_count": len(supported_rows),
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
                f"{event['event_id']} run={run_index} supported={len(supported_rows)} "
                f"{row.get('selected_candidate_id', '')} closure={row.get('full_closure_success')}",
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
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
