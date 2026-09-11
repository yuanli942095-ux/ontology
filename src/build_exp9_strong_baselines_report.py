from __future__ import annotations

"""Build Exp9 paired bootstrap report (ECR control vs baselines)."""

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


EXP9_ROOT = PROJECT_DIR / "output" / "paper-final-validation" / "09-strong-baselines"
BOOTSTRAPS = 10_000
SEED = 20260903


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def truth(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def event_scores(rows: list[dict[str, str]], field: str) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(1 if truth(row[field]) else 0)
    return grouped


def mean_for_events(scores: dict[str, list[int]], events: list[str]) -> float:
    total = 0
    count = 0
    for event_id in events:
        total += sum(scores[event_id])
        count += len(scores[event_id])
    return total / count if count else 0.0


def mean_strict(rows: list[dict[str, str]]) -> dict[str, float]:
    by_event: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        by_event[row["event_id"]].append(truth(row["closure_pass"]))
    return {event_id: 1.0 if all(values) else 0.0 for event_id, values in by_event.items()}


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def bootstrap_diff(left, right, events, label: str) -> dict[str, Any]:
    observed = mean_for_events(left, events) - mean_for_events(right, events)
    rng = random.Random(f"{SEED}|{label}")
    samples: list[float] = []
    for _ in range(BOOTSTRAPS):
        sampled = [rng.choice(events) for _ in events]
        samples.append(mean_for_events(left, sampled) - mean_for_events(right, sampled))
    return {
        "comparison": label,
        "metric": "closure_accuracy",
        "events": len(events),
        "observed_diff_pp": f"{observed * 100:.2f}",
        "ci_low_pp": f"{percentile(samples, 0.025) * 100:.2f}",
        "ci_high_pp": f"{percentile(samples, 0.975) * 100:.2f}",
    }


def bootstrap_strict(left_rows, right_rows, label: str) -> dict[str, Any]:
    left = mean_strict(left_rows)
    right = mean_strict(right_rows)
    events = sorted(set(left) & set(right))
    observed = sum(left[e] for e in events) / len(events) - sum(right[e] for e in events) / len(events)
    rng = random.Random(f"{SEED}|strict|{label}")
    samples: list[float] = []
    for _ in range(BOOTSTRAPS):
        sampled = [rng.choice(events) for _ in events]
        samples.append(sum(left[e] for e in sampled) / len(sampled) - sum(right[e] for e in sampled) / len(sampled))
    return {
        "comparison": label,
        "metric": "strict_event_accuracy",
        "events": len(events),
        "observed_diff_pp": f"{observed * 100:.2f}",
        "ci_low_pp": f"{percentile(samples, 0.025) * 100:.2f}",
        "ci_high_pp": f"{percentile(samples, 0.975) * 100:.2f}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=EXP9_ROOT / "strong-baselines-details-formal.csv")
    parser.add_argument("--output-dir", type=Path, default=EXP9_ROOT)
    args = parser.parse_args()

    rows = [row for row in read_csv(args.input) if row.get("pilot", "").lower() not in {"true", "1", "yes"}]
    by_condition: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_condition[row["condition"]].append(row)

    ecr = by_condition.get("ecr-control", [])
    if not ecr:
        raise SystemExit("missing ecr-control rows")

    bootstrap_rows: list[dict[str, Any]] = []
    lines = [
        "# Exp9 Strong Baselines Report",
        "",
        f"Input: `{args.input.relative_to(PROJECT_DIR)}`",
        "",
        "## Method summary",
        "",
        "| Method | Closure | Strict events | Abstain | Wrong-select | Mean calls | Mean tokens |",
        "|--------|---------|---------------|---------|--------------|------------|-------------|",
    ]

    for condition in sorted(by_condition):
        subset = by_condition[condition]
        attempts = len(subset)
        closure = sum(truth(row["closure_pass"]) for row in subset)
        abstain = sum(truth(row["abstain"]) for row in subset)
        wrong = sum(truth(row["wrong_select"]) for row in subset)
        strict_map = mean_strict(subset)
        strict = int(sum(strict_map.values()))
        calls = [float(row["llm_calls"] or 0) for row in subset if row.get("llm_calls")]
        tokens = [float(row["total_tokens"] or 0) for row in subset if row.get("total_tokens")]
        mean_calls = sum(calls) / len(calls) if calls else 0.0
        mean_tokens = sum(tokens) / len(tokens) if tokens else 0.0
        lines.append(
            f"| {condition} | {closure}/{attempts} ({closure/attempts:.2%}) | "
            f"{strict}/{len(strict_map)} | {abstain} | {wrong} | {mean_calls:.2f} | {mean_tokens:.0f} |"
        )
        if condition == "ecr-control":
            continue
        events = sorted({row["event_id"] for row in ecr} & {row["event_id"] for row in subset})
        bootstrap_rows.append(
            bootstrap_diff(
                event_scores(ecr, "closure_pass"),
                event_scores(subset, "closure_pass"),
                events,
                label=f"ECR vs {condition}",
            )
        )
        bootstrap_rows.append(bootstrap_strict(ecr, subset, label=f"ECR vs {condition}"))

    lines.extend(["", "## Paired bootstrap (ECR − baseline)", ""])
    for row in bootstrap_rows:
        lines.append(
            f"- {row['comparison']} [{row['metric']}]: {row['observed_diff_pp']} pp "
            f"(95% CI {row['ci_low_pp']} – {row['ci_high_pp']})"
        )

    out_dir = args.output_dir.resolve()
    write_csv(out_dir / "strong-baselines-bootstrap-formal.csv", bootstrap_rows)
    (out_dir / "strong-baselines-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"bootstrap_rows": len(bootstrap_rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
