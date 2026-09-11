from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
r"""Run no-model baselines for the semantic-v2 benchmark.

Prediction reads only public events, candidates, and documents. Oracle rows are
loaded only after all predictions are produced.
"""

import argparse
import csv
import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_semantic_benchmark_v2 import (
    blinded_event,
    latest_authority_unique,
    load_oracle_after_predictions,
    load_public_events,
)
from semantic_v2_common import OUTPUT_DIR, stable_seed


METHODS = (
    "FIRST_POSITION",
    "LOWEST_ORIGINAL_ID",
    "HASH_FIRST",
    "RANDOM",
    "LATEST_AUTHORITY_UNIQUE",
)

METHOD_SPECS = {
    "FIRST_POSITION": "选择打乱后第一个候选，诊断候选顺序偏置",
    "LOWEST_ORIGINAL_ID": "选择原始candidate_id最小的候选，诊断候选编号偏置",
    "HASH_FIRST": "对事件和候选ID做稳定哈希后选最小者，不读取语义",
    "RANDOM": "在候选中均匀随机选择，不读取语义",
    "LATEST_AUTHORITY_UNIQUE": "最高authority且最新文档中唯一出现的候选值",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="semantic-v2无模型baseline")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--prefix", default="no-model-baselines-semantic-v2")
    return parser.parse_args()


def hash_key(event: dict[str, Any], candidate: dict[str, Any]) -> int:
    return stable_seed(
        "hash-first",
        event["event_id"],
        candidate.get("original_candidate_id", candidate.get("candidate_id", "")),
    )


def select_candidate(
    event: dict[str, Any], method: str, run_seed: int
) -> tuple[dict[str, Any] | None, str, str]:
    if method == "FIRST_POSITION":
        return event["candidates"][0], "SELECTED", "打乱后第一个候选"
    if method == "LOWEST_ORIGINAL_ID":
        return (
            min(event["candidates"], key=lambda item: str(item.get("original_candidate_id", ""))),
            "SELECTED",
            "原始candidate_id最小",
        )
    if method == "HASH_FIRST":
        return (
            min(event["candidates"], key=lambda item: hash_key(event, item)),
            "SELECTED",
            "稳定哈希最小",
        )
    if method == "RANDOM":
        rng = random.Random(stable_seed(run_seed, event["event_id"], "no-model-random"))
        return rng.choice(event["candidates"]), "SELECTED", "均匀随机"
    if method == "LATEST_AUTHORITY_UNIQUE":
        return latest_authority_unique(event, run_seed)
    raise RuntimeError(f"未知方法：{method}")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def summarize(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        grouped[(row["method"], row["semantic_type"])].append(row)
        grouped[(row["method"], "ALL")].append(row)

    rows: list[dict[str, Any]] = []
    for (method, semantic_type), items in sorted(grouped.items()):
        attempts = len(items)
        correct = sum(bool(row["oracle_correct"]) for row in items)
        selected = sum(row["status"] == "SELECTED" for row in items)
        abstain = sum(row["status"] == "ABSTAIN" for row in items)
        invalid = sum(str(row["status"]).startswith("REJECTED") for row in items)
        wrong = sum(row["status"] == "SELECTED" and not row["oracle_correct"] for row in items)
        rows.append(
            {
                "method": method,
                "semantic_type": semantic_type,
                "attempts": attempts,
                "coverage": selected / attempts if attempts else 0.0,
                "oracle_accuracy": correct / attempts if attempts else 0.0,
                "wrong_selection_rate": wrong / attempts if attempts else 0.0,
                "abstain_rate": abstain / attempts if attempts else 0.0,
                "invalid_output_rate": invalid / attempts if attempts else 0.0,
            }
        )
    return rows


def summarize_by_event(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        grouped[(row["method"], row["event_id"])].append(row)

    rows: list[dict[str, Any]] = []
    for (method, event_id), items in sorted(grouped.items()):
        attempts = len(items)
        correct = sum(bool(row["oracle_correct"]) for row in items)
        selected_candidate_ids = {
            row["selected_candidate_id"] for row in items if row["selected_candidate_id"]
        }
        rows.append(
            {
                "method": method,
                "event_id": event_id,
                "semantic_type": items[0]["semantic_type"],
                "attempts": attempts,
                "oracle_successes": correct,
                "oracle_accuracy": correct / attempts if attempts else 0.0,
                "wrong_selections": sum(
                    row["status"] == "SELECTED" and not row["oracle_correct"]
                    for row in items
                ),
                "abstains": sum(row["status"] == "ABSTAIN" for row in items),
                "selection_stable": len(selected_candidate_ids) == 1,
                "selected_candidate_ids": "|".join(sorted(selected_candidate_ids)),
            }
        )
    return rows


def main() -> int:
    block_legacy_entrypoint(__file__)
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs必须大于0")
    methods = [item.strip().upper() for item in args.methods.split(",") if item.strip()]
    unknown = [method for method in methods if method not in METHODS]
    if unknown:
        raise ValueError(f"未知方法：{unknown}")

    events = load_public_events(args.split)
    details: list[dict[str, Any]] = []
    total = len(events) * args.runs * len(methods)
    counter = 0
    print(f"split={args.split}，事件={len(events)}，runs={args.runs}，方法尝试={total}")
    for run in range(1, args.runs + 1):
        run_seed = args.seed + run - 1
        for source_event in events:
            event = blinded_event(source_event, run_seed)
            for method in methods:
                counter += 1
                selected, status, reason = select_candidate(event, method, run_seed)
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
                        "method": method,
                        "status": status,
                        "selected_option_id": displayed_id,
                        "selected_candidate_id": original_id,
                        "selected_value": display_value,
                        "reason": reason,
                        "candidate_order": "|".join(
                            f"{item['candidate_id']}={item['original_candidate_id']}"
                            for item in event["candidates"]
                        ),
                        "qwen_called": False,
                    }
                )
                print(
                    f"[{counter}/{total}] {event['event_id']} run={run} {method} "
                    f"=> {status} {original_id or '-'} {display_value or '-'}",
                    flush=True,
                )

    oracles = load_oracle_after_predictions()
    for row in details:
        oracle = oracles.get(str(row["event_id"]))
        if oracle is None:
            raise RuntimeError(f"{row['event_id']}缺少READY Oracle")
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        row["oracle_value"] = oracle["oracle_value"]
        row["oracle_correct"] = (
            row["status"] == "SELECTED"
            and row["selected_candidate_id"] == oracle["oracle_candidate_id"]
        )

    summary = summarize(details)
    by_event = summarize_by_event(details)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    detail_csv = OUTPUT_DIR / f"{args.prefix}-details.csv"
    summary_csv = OUTPUT_DIR / f"{args.prefix}-summary.csv"
    by_event_csv = OUTPUT_DIR / f"{args.prefix}-by-event.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"

    write_csv(detail_csv, details)
    write_csv(summary_csv, summary)
    write_csv(by_event_csv, by_event)
    json_path.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "oracle_loaded_after_predictions": True,
                "method_specs": METHOD_SPECS,
                "split": args.split,
                "runs": args.runs,
                "seed": args.seed,
                "summary": summary,
                "by_event": by_event,
                "details": details,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = ["semantic-v2无模型baseline", ""]
    for row in summary:
        if row["semantic_type"] == "ALL":
            lines.append(
                f"[{row['method']}] Oracle={row['oracle_accuracy']:.2%} | "
                f"错选={row['wrong_selection_rate']:.2%} | "
                f"ABSTAIN={row['abstain_rate']:.2%}"
            )
    lines.extend(["", f"明细：{detail_csv}", f"汇总：{summary_csv}", f"按事件：{by_event_csv}", f"JSON：{json_path}"])
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
