from __future__ import annotations

"""Build Exp8 formal tables: by semantic type + event-clustered paired bootstrap."""

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


EXP8_ROOT = PROJECT_DIR / "output" / "paper-final-validation" / "08-evidence-dependence"
BOOTSTRAPS = 10_000
SEED = 20260903

CONDITION_ORDER = (
    "normal",
    "no-evidence",
    "no-evidence-masked-source",
    "shuffled-evidence",
    "irrelevant-same-source",
)

COUNTERFACTUALS = (
    "no-evidence",
    "no-evidence-masked-source",
    "shuffled-evidence",
    "irrelevant-same-source",
)

TYPE_ORDER = (
    "TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE",
)

TYPE_SHORT = {
    "TEMPORAL_VERSION": "TEMP",
    "GENERAL_RULE_EXCEPTION": "GRE",
    "CROSS_SENTENCE_SCOPE": "CSS",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def truth(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def event_attempt_scores(rows: list[dict[str, str]], field: str) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(1 if truth(row[field]) else 0)
    return grouped


def closure_accuracy_for_events(scores: dict[str, list[int]], events: list[str]) -> float:
    total = 0
    count = 0
    for event_id in events:
        total += sum(scores[event_id])
        count += len(scores[event_id])
    return total / count if count else 0.0


def bootstrap_closure_diff_pp(
    normal_scores: dict[str, list[int]],
    counter_scores: dict[str, list[int]],
    events: list[str],
    *,
    label: str,
) -> dict[str, Any]:
    observed_pp = (closure_accuracy_for_events(normal_scores, events) - closure_accuracy_for_events(counter_scores, events)) * 100
    rng = random.Random(f"{SEED}|closure|{label}")
    samples_pp: list[float] = []
    for _ in range(BOOTSTRAPS):
        sampled = [rng.choice(events) for _ in events]
        diff = closure_accuracy_for_events(normal_scores, sampled) - closure_accuracy_for_events(counter_scores, sampled)
        samples_pp.append(diff * 100)
    return {
        "comparison": label,
        "metric": "closure_accuracy_pp",
        "events": len(events),
        "runs_per_event": len(next(iter(normal_scores.values()))),
        "bootstrap_replicates": BOOTSTRAPS,
        "observed_diff_pp": f"{observed_pp:.2f}",
        "ci_low_pp": f"{percentile(samples_pp, 0.025):.2f}",
        "ci_high_pp": f"{percentile(samples_pp, 0.975):.2f}",
    }


def build_by_type_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for condition in CONDITION_ORDER:
        for semantic_type in TYPE_ORDER:
            subset = [row for row in rows if row["condition"] == condition and row["semantic_type"] == semantic_type]
            if not subset:
                continue
            total = len(subset)
            closure_correct = sum(truth(row["closure_pass"]) for row in subset)
            abstain = sum(truth(row["abstain"]) for row in subset)
            wrong_select = sum(truth(row["wrong_select"]) for row in subset)
            out.append(
                {
                    "condition": condition,
                    "semantic_type": semantic_type,
                    "semantic_type_short": TYPE_SHORT.get(semantic_type, semantic_type),
                    "total": total,
                    "closure_correct": closure_correct,
                    "closure_accuracy": f"{closure_correct / total:.6f}",
                    "abstain": abstain,
                    "wrong_select": wrong_select,
                }
            )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=EXP8_ROOT / "evidence-dependence-details-formal.csv")
    parser.add_argument("--output-dir", type=Path, default=EXP8_ROOT)
    args = parser.parse_args()

    rows = [row for row in read_csv(args.input) if row.get("pilot", "").lower() not in {"true", "1", "yes"}]
    by_condition: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_condition[row["condition"]].append(row)

    normal = by_condition.get("normal", [])
    if not normal:
        raise SystemExit("missing normal rows in formal details CSV")

    by_type_rows = build_by_type_rows(rows)
    out_dir = args.output_dir.resolve()
    write_csv(out_dir / "evidence-dependence-by-type-formal.csv", by_type_rows)

    normal_scores = event_attempt_scores(normal, "closure_pass")
    events = sorted(normal_scores.keys())
    bootstrap_rows: list[dict[str, Any]] = []
    comparison_labels = {
        "no-evidence": "Normal vs No Evidence",
        "no-evidence-masked-source": "Normal vs Masked",
        "shuffled-evidence": "Normal vs Shuffled",
        "irrelevant-same-source": "Normal vs Irrelevant",
    }
    for condition in COUNTERFACTUALS:
        subset = by_condition.get(condition, [])
        if not subset:
            raise SystemExit(f"missing condition rows: {condition}")
        shared_events = sorted({row["event_id"] for row in normal} & {row["event_id"] for row in subset})
        counter_scores = event_attempt_scores(subset, "closure_pass")
        bootstrap_rows.append(
            bootstrap_closure_diff_pp(
                normal_scores,
                counter_scores,
                shared_events,
                label=comparison_labels[condition],
            )
        )

    write_csv(out_dir / "evidence-dependence-bootstrap-formal.csv", bootstrap_rows)

    lines = [
        "# Exp8 Evidence-Dependence Formal Report",
        "",
        f"Input: `{args.input.relative_to(PROJECT_DIR)}`",
        "",
        "## By semantic type (formal ×5)",
        "",
        "| Condition | Type | Total | Closure | Abstain | Wrong-select |",
        "|-----------|------|-------|---------|---------|--------------|",
    ]
    for row in by_type_rows:
        lines.append(
            f"| {row['condition']} | {row['semantic_type_short']} | {row['total']} | "
            f"{row['closure_correct']}/{row['total']} ({float(row['closure_accuracy']):.2%}) | "
            f"{row['abstain']} | {row['wrong_select']} |"
        )

    lines.extend(
        [
            "",
            "## Paired event-clustered bootstrap (Normal − counterfactual)",
            "",
            "264 events as clusters; each replicate resamples events with replacement and pools all 5 runs per event.",
            f"{BOOTSTRAPS:,} bootstrap replicates; seed={SEED}.",
            "",
            "| Comparison | Δ closure (pp) | 95% CI |",
            "|------------|----------------|--------|",
        ]
    )
    for row in bootstrap_rows:
        lines.append(
            f"| {row['comparison']} | {row['observed_diff_pp']} | {row['ci_low_pp']} – {row['ci_high_pp']} |"
        )

    (out_dir / "evidence-dependence-report-formal.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    seal = {
        "exp8_formal_sealed": True,
        "input": str(args.input.relative_to(PROJECT_DIR)),
        "by_type_csv": str((out_dir / "evidence-dependence-by-type-formal.csv").relative_to(PROJECT_DIR)),
        "bootstrap_csv": str((out_dir / "evidence-dependence-bootstrap-formal.csv").relative_to(PROJECT_DIR)),
        "report_md": str((out_dir / "evidence-dependence-report-formal.md").relative_to(PROJECT_DIR)),
        "bootstrap_replicates": BOOTSTRAPS,
        "bootstrap_seed": SEED,
        "by_type_rows": len(by_type_rows),
        "bootstrap_comparisons": len(bootstrap_rows),
    }
    (out_dir / "exp8-formal-seal.json").write_text(json.dumps(seal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(seal, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
