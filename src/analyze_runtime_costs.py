from __future__ import annotations

r"""Summarize runtime, model-call, and token costs from experiment details CSVs."""

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DETAILS = [
    PROJECT_DIR / "output" / "final-main-table-test-r5-seed20260820-details.csv"
]
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output"


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_int(value: object) -> int:
    try:
        return int(float(str(value).strip() or "0"))
    except (TypeError, ValueError):
        return 0


def load_rows(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row["_source_details"] = str(path)
                rows.append(row)
    return rows


def summarize(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("method", "")].append(row)

    summaries: list[dict[str, Any]] = []
    for method, items in sorted(grouped.items()):
        attempts = len(items)
        qwen_calls = sum(parse_bool(row.get("qwen_called", "")) for row in items)
        direct_decisions = attempts - qwen_calls
        runtimes = [parse_int(row.get("runtime_ms", 0)) for row in items]
        qwen_runtimes = [
            parse_int(row.get("runtime_ms", 0))
            for row in items
            if parse_bool(row.get("qwen_called", ""))
        ]
        prompt_counts = [parse_int(row.get("prompt_eval_count", 0)) for row in items]
        eval_counts = [parse_int(row.get("eval_count", 0)) for row in items]
        qwen_prompt_counts = [
            parse_int(row.get("prompt_eval_count", 0))
            for row in items
            if parse_bool(row.get("qwen_called", ""))
        ]
        qwen_eval_counts = [
            parse_int(row.get("eval_count", 0))
            for row in items
            if parse_bool(row.get("qwen_called", ""))
        ]
        selected = sum(row.get("status") == "SELECTED" for row in items)
        correct = sum(parse_bool(row.get("oracle_correct", "")) for row in items)
        abstains = sum(row.get("status") == "ABSTAIN" for row in items)
        wrong = sum(
            row.get("status") == "SELECTED"
            and not parse_bool(row.get("oracle_correct", ""))
            for row in items
        )
        deterministic_selects = sum(
            row.get("decision_path", "") == "DETERMINISTIC_SELECT" for row in items
        )
        qwen_after_gate = sum(
            row.get("decision_path", "") == "QWEN_RANK_AFTER_GATE" for row in items
        )
        summaries.append(
            {
                "method": method,
                "attempts": attempts,
                "selected": selected,
                "oracle_successes": correct,
                "oracle_accuracy": correct / attempts if attempts else 0.0,
                "wrong_selection_rate": wrong / attempts if attempts else 0.0,
                "abstain_rate": abstains / attempts if attempts else 0.0,
                "qwen_calls": qwen_calls,
                "qwen_call_rate": qwen_calls / attempts if attempts else 0.0,
                "direct_decisions": direct_decisions,
                "direct_decision_rate": direct_decisions / attempts if attempts else 0.0,
                "deterministic_selects": deterministic_selects,
                "qwen_after_gate": qwen_after_gate,
                "total_runtime_ms": sum(runtimes),
                "mean_runtime_ms_all_attempts": mean(runtimes) if runtimes else 0.0,
                "mean_runtime_ms_qwen_calls": mean(qwen_runtimes) if qwen_runtimes else 0.0,
                "total_prompt_eval_count": sum(prompt_counts),
                "mean_prompt_eval_count_qwen_calls": (
                    mean(qwen_prompt_counts) if qwen_prompt_counts else 0.0
                ),
                "total_eval_count": sum(eval_counts),
                "mean_eval_count_qwen_calls": (
                    mean(qwen_eval_counts) if qwen_eval_counts else 0.0
                ),
                "total_token_count": sum(prompt_counts) + sum(eval_counts),
                "mean_total_tokens_qwen_calls": (
                    mean(
                        prompt + completion
                        for prompt, completion in zip(qwen_prompt_counts, qwen_eval_counts)
                    )
                    if qwen_prompt_counts
                    else 0.0
                ),
            }
        )
    return summaries


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="实验运行成本与调用次数汇总")
    parser.add_argument("--details", type=Path, nargs="+", default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default="runtime-costs")
    args = parser.parse_args()

    missing = [path for path in args.details if not path.exists()]
    if missing:
        raise SystemExit(f"details file not found: {missing}")

    rows = load_rows(args.details)
    summary = summarize(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = args.output_dir / f"{args.prefix}-summary.csv"
    json_path = args.output_dir / f"{args.prefix}.json"
    log_path = args.output_dir / f"{args.prefix}.log"
    write_csv(summary_csv, summary)
    json_path.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_details": [str(path) for path in args.details],
                "rows": len(rows),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        f"source={','.join(str(path) for path in args.details)}",
        f"rows={len(rows)}",
        f"summary={summary_csv}",
        f"json={json_path}",
        "",
    ]
    for row in summary:
        lines.append(
            "[{method}] Qwen={qwen_calls}/{attempts} ({qwen_call_rate:.2%}) | "
            "direct={direct_decisions}/{attempts} ({direct_decision_rate:.2%}) | "
            "mean_ms_all={mean_runtime_ms_all_attempts:.2f} | "
            "mean_ms_qwen={mean_runtime_ms_qwen_calls:.2f} | "
            "tokens={total_token_count}".format(**row)
        )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
