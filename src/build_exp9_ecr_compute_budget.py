from __future__ import annotations

"""Derive frozen ECR compute budget from candidate-id-permute structural pipeline counts."""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from exp9_baseline_common import DEFAULT_BENCHMARK, ECR_CONTROL_DIR, EXP9_ROOT, read_csv, sha256_file, utc_now_iso
from semantic_v2_common import PROJECT_DIR


def estimate_calls_for_row(row: dict[str, str]) -> int:
    semantic = row.get("semantic_type", "")
    calls = 2  # M13 draft + refine
    if semantic in {"GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}:
        calls += 1  # M14 recovery attempt
    if semantic == "TEMPORAL_VERSION":
        calls += 1  # M15 anchor recovery
    supported = row.get("supported_count", "")
    if supported and str(supported).strip() and str(supported).strip() != "0":
        calls += int(str(supported).strip())
    elif row.get("decision_path", "").startswith("IR_") and semantic in {"GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}:
        calls += 3  # upper bound M16 entailment calls on abstain fallback
    return calls


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ecr-details", type=Path, default=ECR_CONTROL_DIR / "m16-full-holdout-combined" / "m16-full-holdout-combined-full-details.csv")
    parser.add_argument("--output", type=Path, default=EXP9_ROOT / "ecr-compute-budget.json")
    args = parser.parse_args()

    rows = read_csv(args.ecr_details)
    per_attempt_calls = [estimate_calls_for_row(row) for row in rows]
    per_event: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        per_attempt_calls_row = estimate_calls_for_row(row)
        per_event[row["event_id"]].append(per_attempt_calls_row)

    event_means = [sum(items) / len(items) for items in per_event.values()]
    budget: dict[str, Any] = {
        "generated_at_utc": utc_now_iso(),
        "source_ecr_details": str(args.ecr_details.relative_to(PROJECT_DIR)),
        "source_sha256": sha256_file(args.ecr_details),
        "benchmark": str(DEFAULT_BENCHMARK.relative_to(PROJECT_DIR)),
        "attempts": len(rows),
        "mean_calls_per_attempt": sum(per_attempt_calls) / len(per_attempt_calls),
        "median_calls_per_attempt": percentile(per_attempt_calls, 0.5),
        "p95_calls_per_attempt": percentile(per_attempt_calls, 0.95),
        "mean_calls_per_event": sum(event_means) / len(event_means),
        "median_calls_per_event": percentile(event_means, 0.5),
        "p95_calls_per_event": percentile(event_means, 0.95),
        "max_calls_per_attempt": int(max(per_attempt_calls)),
        "max_calls_per_event": int(max(sum(items) for items in per_event.values())),
        "baseline_budget": {
            "max_calls_per_attempt": min(4, int(round(sum(per_attempt_calls) / len(per_attempt_calls)) + 1)),
            "max_total_tokens_per_attempt": 20000,
            "note": "B3 uses 3 deliberation stages; budget capped at structural ECR mean+1 calls and 20k tokens.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(budget, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(budget, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
