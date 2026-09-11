from __future__ import annotations

"""Event-clustered paired bootstrap statistics for v5 blind-large.

The independent unit is the repair event. Each bootstrap sample draws events
with replacement and keeps all five runs for each selected event.
"""

import csv
import random
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "output" / "external-real-holdout-v5-blind-large"
RUN = ROOT / "final-blind-eval-r5-m16-deepseek"
OUT = ROOT / "statistics" / "v5-event-clustered-bootstrap.csv"
SEED = 20260901
BOOTSTRAPS = 10000


STAGES = {
    "M13_BASE": RUN
    / "m13-pilot"
    / "arm-d-rule-refinement"
    / "ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv",
    "M13_PLUS_M14": RUN / "m14-full-holdout-combined" / "m14-full-holdout-combined-details.csv",
    "M13_PLUS_M14_PLUS_M15": RUN / "m15-full-holdout-combined" / "m15-full-holdout-combined-details.csv",
    "FULL_M16": RUN / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def event_scores(path: Path) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in read_csv(path):
        success = row.get("closure_success")
        if success is None:
            success = row.get("full_closure_success")
        if success is None:
            success = row.get("oracle_correct")
        grouped[row["event_id"]].append(1 if truth(success) else 0)
    for event_id, values in grouped.items():
        if len(values) != 5:
            raise ValueError(f"{path}: expected 5 runs for {event_id}, got {len(values)}")
    return grouped


def mean_for_events(scores: dict[str, list[int]], events: list[str]) -> float:
    total = 0
    count = 0
    for event_id in events:
        values = scores[event_id]
        total += sum(values)
        count += len(values)
    return total / count if count else 0.0


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def compare(
    left_name: str,
    right_name: str,
    left: dict[str, list[int]],
    right: dict[str, list[int]],
) -> dict[str, Any]:
    events = sorted(set(left) & set(right))
    observed = mean_for_events(left, events) - mean_for_events(right, events)
    rng = random.Random(f"{SEED}|{left_name}|{right_name}")
    samples: list[float] = []
    for _ in range(BOOTSTRAPS):
        sampled = [rng.choice(events) for _ in events]
        samples.append(mean_for_events(left, sampled) - mean_for_events(right, sampled))
    return {
        "comparison": f"{left_name} vs {right_name}",
        "independent_unit": "event_cluster",
        "events": len(events),
        "runs_per_event": 5,
        "bootstrap_samples": BOOTSTRAPS,
        "observed_diff_pp": f"{observed * 100:.2f}",
        "cluster_bootstrap_ci_low_pp": f"{percentile(samples, 0.025) * 100:.2f}",
        "cluster_bootstrap_ci_high_pp": f"{percentile(samples, 0.975) * 100:.2f}",
    }


def main() -> int:
    scores = {name: event_scores(path) for name, path in STAGES.items()}
    rows = [
        compare("FULL_M16", "M13_BASE", scores["FULL_M16"], scores["M13_BASE"]),
        compare("M13_PLUS_M14", "M13_BASE", scores["M13_PLUS_M14"], scores["M13_BASE"]),
        compare(
            "M13_PLUS_M14_PLUS_M15",
            "M13_PLUS_M14",
            scores["M13_PLUS_M14_PLUS_M15"],
            scores["M13_PLUS_M14"],
        ),
        compare("FULL_M16", "M13_PLUS_M14_PLUS_M15", scores["FULL_M16"], scores["M13_PLUS_M14_PLUS_M15"]),
    ]
    write_csv(OUT, rows)
    for row in rows:
        print(row)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
