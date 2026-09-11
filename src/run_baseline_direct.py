from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Run the semantic-v2 direct-value baseline.

This baseline uses the same public event loading, document material, candidate
randomization, model call, and post-hoc Oracle scoring as the official
semantic-v2 experiment. The model input does not include candidate descriptions,
candidate IDs, repair operations, formal effects, mutant OWL text, or formal
policy rules. It only asks the model to generate the document-supported value;
the generated value is mapped to the formally safe candidate list for scoring.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from semantic_v2_common import OUTPUT_DIR, check_ollama, stable_seed, write_csv
from run_semantic_benchmark_v2 import (
    blinded_event,
    load_oracle_after_predictions,
    load_public_events,
    qwen_direct,
    summarize,
)


METHOD = "DIRECT_FREE"
PREFIX = "baseline-direct"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="semantic-v2 Direct Value baseline")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--only", default="", help="Only run selected events, e.g. E14,E35")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--num-predict", type=int, default=300)
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--keep-alive", default="30m")
    return parser.parse_args()


def filter_events(events: list[dict[str, Any]], only: str) -> list[dict[str, Any]]:
    wanted = {item.strip().upper() for item in only.split(",") if item.strip()}
    if not wanted:
        return events
    filtered = [
        event
        for event in events
        if str(event.get("event_id", "")).strip().upper() in wanted
    ]
    if not filtered:
        raise RuntimeError(f"--only={sorted(wanted)} has no matching event")
    return filtered


def by_event_summary(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        groups[str(row["event_id"])].append(row)

    rows: list[dict[str, Any]] = []
    for event_id, items in sorted(groups.items()):
        successes = sum(bool(item["oracle_correct"]) for item in items)
        rows.append(
            {
                "method": METHOD,
                "event_id": event_id,
                "semantic_type": items[0]["semantic_type"],
                "attempts": len(items),
                "oracle_successes": successes,
                "oracle_accuracy": successes / len(items),
                "all_runs_correct": successes == len(items),
                "selection_stable": (
                    len({str(item["selected_candidate_id"]) for item in items}) == 1
                ),
                "invalid_outputs": sum(
                    str(item["status"]).startswith("REJECTED") for item in items
                ),
                "abstains": sum(item["status"] == "ABSTAIN" for item in items),
            }
        )
    return rows


def event_level_summary(by_event: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not by_event:
        return []
    return [
        {
            "method": METHOD,
            "independent_events": len(by_event),
            "all_runs_correct_events": sum(
                bool(item["all_runs_correct"]) for item in by_event
            ),
            "strict_event_accuracy": (
                sum(bool(item["all_runs_correct"]) for item in by_event)
                / len(by_event)
            ),
            "macro_event_accuracy": (
                sum(float(item["oracle_accuracy"]) for item in by_event)
                / len(by_event)
            ),
            "stable_events": sum(bool(item["selection_stable"]) for item in by_event),
        }
    ]


def score_after_predictions(details: list[dict[str, Any]]) -> None:
    oracles = load_oracle_after_predictions()
    for row in details:
        oracle = oracles.get(str(row["event_id"]))
        if oracle is None:
            raise RuntimeError(f"{row['event_id']} lacks READY Oracle")
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        row["oracle_value"] = oracle["oracle_value"]
        row["oracle_correct"] = (
            row["status"] == "SELECTED"
            and row["selected_candidate_id"] == oracle["oracle_candidate_id"]
        )


def output_paths() -> dict[str, Any]:
    return {
        "detail": OUTPUT_DIR / f"{PREFIX}-details.csv",
        "summary": OUTPUT_DIR / f"{PREFIX}-summary.csv",
        "by_event": OUTPUT_DIR / f"{PREFIX}-by-event.csv",
        "event_level": OUTPUT_DIR / f"{PREFIX}-event-level.csv",
        "json": OUTPUT_DIR / f"{PREFIX}.json",
        "log": OUTPUT_DIR / f"{PREFIX}.log",
    }


def write_outputs(
    args: argparse.Namespace,
    details: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    by_event: list[dict[str, Any]],
    event_level: list[dict[str, Any]],
) -> dict[str, Any]:
    paths = output_paths()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(paths["detail"], details)
    write_csv(paths["summary"], summary)
    write_csv(paths["by_event"], by_event)
    write_csv(paths["event_level"], event_level)

    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": METHOD,
        "baseline_definition": (
            "Direct value generation from public event context and documents only; "
            "candidate data is withheld from the model and used only for post-hoc "
            "value-to-candidate scoring."
        ),
        "oracle_loaded_after_predictions": True,
        "candidate_information_in_prompt": False,
        "mutant_owl_text_in_prompt": False,
        "formal_policy_in_prompt": False,
        "split": args.split,
        "only": args.only,
        "runs": args.runs,
        "seed": args.seed,
        "temperature": args.temperature,
        "model": args.model,
        "summary": summary,
        "by_event": by_event,
        "event_level_summary": event_level,
        "details": details,
    }
    paths["json"].write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "semantic-v2 Direct Value baseline",
        "Oracle loaded after all predictions=True",
        "Candidate information in model prompt=False",
        "",
    ]
    for row in summary:
        if row["semantic_type"] == "ALL":
            lines.append(
                f"[{row['method']}] attempts={row['attempts']} | "
                f"Oracle={row['oracle_accuracy']:.2%} | "
                f"invalid={row['invalid_output_rate']:.2%} | "
                f"ABSTAIN={row['abstain_rate']:.2%}"
            )
    for row in event_level:
        lines.append(
            f"event-level strict={row['all_runs_correct_events']}/"
            f"{row['independent_events']} ({row['strict_event_accuracy']:.2%}) | "
            f"macro={row['macro_event_accuracy']:.2%}"
        )
    lines.extend(
        [
            "",
            f"detail={paths['detail']}",
            f"summary={paths['summary']}",
            f"by_event={paths['by_event']}",
            f"event_level={paths['event_level']}",
            f"json={paths['json']}",
        ]
    )
    paths["log"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return paths


def main() -> int:
    block_legacy_entrypoint(__file__)
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs must be greater than 0")

    events = filter_events(load_public_events(args.split), args.only)

    print(f"[connection] Ollama={args.ollama_url}, model={args.model}")
    check_ollama(args.ollama_url, args.model, args.timeout)
    print("[connection] Ollama and model are available.")

    total = len(events) * args.runs
    print("\nsemantic-v2 Direct Value baseline")
    print(f"split={args.split}, events={len(events)}, runs={args.runs}, attempts={total}")
    print("Prompt boundary: no candidate descriptions, no candidate IDs, no OWL, no rules.")

    details: list[dict[str, Any]] = []
    counter = 0
    for run in range(1, args.runs + 1):
        run_seed = args.seed + run - 1
        for source_event in events:
            event = blinded_event(source_event, run_seed)
            counter += 1
            call_seed = stable_seed(run_seed, event["event_id"], METHOD)
            print(f"[{counter}/{total}] {event['event_id']} run={run} {METHOD}", flush=True)
            try:
                selected, status, reason, extra = qwen_direct(event, call_seed, args)
            except Exception as exc:
                selected = None
                status = "REJECTED_ERROR"
                reason = f"{type(exc).__name__}: {exc}"
                extra = {}

            original_id = selected.get("original_candidate_id", "") if selected else ""
            displayed_id = selected.get("candidate_id", "") if selected else ""
            display_value = selected.get("display_value", "") if selected else ""
            details.append(
                {
                    "split": source_event["split"],
                    "event_id": event["event_id"],
                    "semantic_type": event["semantic_type"],
                    "run": run,
                    "seed": run_seed,
                    "method": METHOD,
                    "status": status,
                    "selected_option_id": displayed_id,
                    "selected_candidate_id": original_id,
                    "selected_value": display_value,
                    "reason": reason,
                    "candidate_order": "|".join(
                        f"{item['candidate_id']}={item['original_candidate_id']}"
                        for item in event["candidates"]
                    ),
                    "runtime_ms": extra.get("runtime_ms", 0),
                    "response_mode": extra.get("response_mode", ""),
                    "prompt_eval_count": extra.get("prompt_eval_count", 0),
                    "eval_count": extra.get("eval_count", 0),
                    "done_reason": extra.get("done_reason", ""),
                    "raw_content": extra.get("raw_content", ""),
                    "qwen_called": True,
                }
            )
            print(
                f"  result={status} | candidate={original_id or '-'} | "
                f"value={display_value or '-'} | {extra.get('runtime_ms', 0)}ms",
                flush=True,
            )

    score_after_predictions(details)
    summary = summarize(details)
    by_event = by_event_summary(details)
    event_level = event_level_summary(by_event)
    paths = write_outputs(args, details, summary, by_event, event_level)

    print("\nCall-level summary")
    for row in summary:
        if row["semantic_type"] == "ALL":
            print(
                f"[{row['method']}] Oracle={row['oracle_accuracy']:.2%} | "
                f"invalid={row['invalid_output_rate']:.2%} | "
                f"ABSTAIN={row['abstain_rate']:.2%}"
            )
    print("\nEvent-level summary")
    for row in event_level:
        print(
            f"[{row['method']}] strict={row['all_runs_correct_events']}/"
            f"{row['independent_events']} ({row['strict_event_accuracy']:.2%}) | "
            f"macro={row['macro_event_accuracy']:.2%}"
        )
    print(f"\nDetail: {paths['detail']}")
    print(f"Summary: {paths['summary']}")
    print(f"By event: {paths['by_event']}")
    print(f"JSON: {paths['json']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[baseline stopped] {type(exc).__name__}: {exc}")
        sys.exit(2)
