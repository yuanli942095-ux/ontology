from __future__ import annotations

"""Compute statistical CIs and paired tests for the v4-blind final pipeline."""

import argparse
import csv
import json
import math
import random
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_RUN_DIR = PROJECT_DIR / "output" / "external-real-holdout-v4-blind" / "final-blind-eval-r5-m16-deepseek"
DEFAULT_POST_SANITIZE = PROJECT_DIR / "output" / "external-real-holdout-v4-blind" / "post-sanitize-offline-scoring"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v4-blind" / "statistics"


STAGES = [
    (
        "M13_BASE_ONLY",
        DEFAULT_RUN_DIR
        / "main-method"
        / "m13-pilot"
        / "arm-d-rule-refinement"
        / "ir"
        / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv",
    ),
    ("M13_PLUS_M14", DEFAULT_POST_SANITIZE / "m14-full-holdout-combined-details.csv"),
    ("M13_PLUS_M14_PLUS_M15", DEFAULT_POST_SANITIZE / "m15-full-holdout-combined-details.csv"),
    ("FULL_M16", DEFAULT_POST_SANITIZE / "m16-full-holdout-combined-details.csv"),
]


COMPARISONS = [
    ("FULL_M16", "M13_BASE_ONLY"),
    ("M13_PLUS_M14", "M13_BASE_ONLY"),
    ("M13_PLUS_M14_PLUS_M15", "M13_PLUS_M14"),
    ("FULL_M16", "M13_PLUS_M14_PLUS_M15"),
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def closure(row: dict[str, str]) -> bool:
    return truth(row.get("closure_success")) or truth(row.get("full_closure_success"))


def oracle(row: dict[str, str]) -> bool:
    return truth(row.get("oracle_correct")) or truth(row.get("selection_oracle_correct"))


def key(row: dict[str, str]) -> tuple[str, str]:
    return (row["event_id"], str(row.get("run", "")))


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def binom_two_sided_p(k: int, n: int) -> float:
    if n <= 0:
        return 1.0
    observed = math.comb(n, k) * (0.5**n)
    total = 0.0
    for i in range(n + 1):
        prob = math.comb(n, i) * (0.5**n)
        if prob <= observed + 1e-15:
            total += prob
    return min(1.0, total)


def load_stage_maps() -> dict[str, dict[tuple[str, str], dict[str, str]]]:
    maps: dict[str, dict[tuple[str, str], dict[str, str]]] = {}
    for label, path in STAGES:
        if not path.is_file():
            raise FileNotFoundError(path)
        rows = read_csv(path)
        maps[label] = {key(row): row for row in rows}
    return maps


def event_success(rows: list[dict[str, str]], metric: str) -> dict[str, bool]:
    fn = closure if metric == "closure" else oracle
    by_event: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_event.setdefault(row["event_id"], []).append(row)
    return {event_id: all(fn(row) for row in subset) for event_id, subset in by_event.items()}


def ci_rows(stage_maps: dict[str, dict[tuple[str, str], dict[str, str]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label, rows_by_key in stage_maps.items():
        rows = list(rows_by_key.values())
        for semantic_type in ["ALL", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"]:
            subset = rows if semantic_type == "ALL" else [row for row in rows if row.get("semantic_type") == semantic_type]
            if not subset:
                continue
            for metric_name, fn in [("closure", closure), ("oracle", oracle)]:
                k = sum(1 for row in subset if fn(row))
                n = len(subset)
                low, high = wilson(k, n)
                out.append(
                    {
                        "stage": label,
                        "semantic_type": semantic_type,
                        "metric": f"attempt_{metric_name}",
                        "successes": k,
                        "n": n,
                        "estimate": k / n if n else 0,
                        "ci95_low": low,
                        "ci95_high": high,
                    }
                )
            event_map = event_success(subset, "closure")
            k = sum(1 for ok in event_map.values() if ok)
            n = len(event_map)
            low, high = wilson(k, n)
            out.append(
                {
                    "stage": label,
                    "semantic_type": semantic_type,
                    "metric": "strict_event_closure",
                    "successes": k,
                    "n": n,
                    "estimate": k / n if n else 0,
                    "ci95_low": low,
                    "ci95_high": high,
                }
            )
    return out


def paired_mcnemar(
    a_label: str,
    b_label: str,
    a_rows: dict[tuple[str, str], dict[str, str]],
    b_rows: dict[tuple[str, str], dict[str, str]],
    *,
    metric: str,
) -> dict[str, Any]:
    fn = closure if metric == "closure" else oracle
    shared = sorted(set(a_rows) & set(b_rows))
    a_only = 0
    b_only = 0
    both_success = 0
    both_fail = 0
    for item in shared:
        a_ok = fn(a_rows[item])
        b_ok = fn(b_rows[item])
        if a_ok and b_ok:
            both_success += 1
        elif a_ok and not b_ok:
            a_only += 1
        elif b_ok and not a_ok:
            b_only += 1
        else:
            both_fail += 1
    discordant = a_only + b_only
    return {
        "comparison": f"{a_label}_vs_{b_label}",
        "metric": f"attempt_{metric}",
        "matched_pairs": len(shared),
        "a_success_b_fail": a_only,
        "b_success_a_fail": b_only,
        "both_success": both_success,
        "both_fail": both_fail,
        "discordant_pairs": discordant,
        "exact_mcnemar_p": binom_two_sided_p(min(a_only, b_only), discordant) if discordant else 1.0,
        "a_accuracy": sum(1 for item in shared if fn(a_rows[item])) / len(shared) if shared else 0,
        "b_accuracy": sum(1 for item in shared if fn(b_rows[item])) / len(shared) if shared else 0,
    }


def bootstrap_diff(
    a_label: str,
    b_label: str,
    a_rows: dict[tuple[str, str], dict[str, str]],
    b_rows: dict[tuple[str, str], dict[str, str]],
    *,
    metric: str,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    fn = closure if metric == "closure" else oracle
    shared = sorted(set(a_rows) & set(b_rows))
    if not shared:
        return {}
    diffs = [(1 if fn(a_rows[item]) else 0) - (1 if fn(b_rows[item]) else 0) for item in shared]
    rng = random.Random(seed)
    samples: list[float] = []
    n = len(diffs)
    for _ in range(iterations):
        total = sum(diffs[rng.randrange(n)] for _ in range(n))
        samples.append(total / n)
    samples.sort()
    low = samples[int(0.025 * (iterations - 1))]
    high = samples[int(0.975 * (iterations - 1))]
    return {
        "comparison": f"{a_label}_vs_{b_label}",
        "metric": f"attempt_{metric}",
        "matched_pairs": n,
        "mean_diff": sum(diffs) / n,
        "mean_diff_pp": 100 * sum(diffs) / n,
        "boot_ci95_low": low,
        "boot_ci95_high": high,
        "boot_ci95_low_pp": 100 * low,
        "boot_ci95_high_pp": 100 * high,
        "bootstrap_iterations": iterations,
        "seed": seed,
    }


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def write_markdown(path: Path, ci: list[dict[str, Any]], tests: list[dict[str, Any]], boot: list[dict[str, Any]]) -> None:
    final_ci = [
        row
        for row in ci
        if row["stage"] == "FULL_M16" and row["metric"] in {"attempt_closure", "strict_event_closure"}
    ]
    lines = [
        "# v4-blind Statistical Significance and Confidence Intervals",
        "",
        "All statistics are computed offline from frozen/post-sanitize scoring artifacts. No LLM calls are made.",
        "",
        "## Final M16 Wilson 95% CI",
        "",
        "| Semantic type | Metric | Successes | Estimate | Wilson 95% CI |",
        "|---|---|---:|---:|---:|",
    ]
    for row in final_ci:
        lines.append(
            f"| {row['semantic_type']} | {row['metric']} | {row['successes']}/{row['n']} | "
            f"{pct(row['estimate'])} | {pct(row['ci95_low'])}-{pct(row['ci95_high'])} |"
        )

    lines.extend(
        [
            "",
            "## Paired Exact McNemar Tests",
            "",
            "| Comparison | Metric | A accuracy | B accuracy | A-only | B-only | Discordant | p-value |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in tests:
        lines.append(
            f"| {row['comparison']} | {row['metric']} | {pct(row['a_accuracy'])} | {pct(row['b_accuracy'])} | "
            f"{row['a_success_b_fail']} | {row['b_success_a_fail']} | {row['discordant_pairs']} | "
            f"{row['exact_mcnemar_p']:.6g} |"
        )

    lines.extend(
        [
            "",
            "## Paired Bootstrap Difference CI",
            "",
            "| Comparison | Metric | Mean diff | 95% CI | Iterations |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in boot:
        lines.append(
            f"| {row['comparison']} | {row['metric']} | {row['mean_diff_pp']:.2f} pp | "
            f"{row['boot_ci95_low_pp']:.2f} to {row['boot_ci95_high_pp']:.2f} pp | "
            f"{row['bootstrap_iterations']} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260831)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stage_maps = load_stage_maps()
    ci = ci_rows(stage_maps)
    tests: list[dict[str, Any]] = []
    boot: list[dict[str, Any]] = []
    for a_label, b_label in COMPARISONS:
        for metric in ("closure", "oracle"):
            tests.append(paired_mcnemar(a_label, b_label, stage_maps[a_label], stage_maps[b_label], metric=metric))
            boot.append(
                bootstrap_diff(
                    a_label,
                    b_label,
                    stage_maps[a_label],
                    stage_maps[b_label],
                    metric=metric,
                    iterations=args.bootstrap_iterations,
                    seed=args.seed,
                )
            )

    write_csv(args.output_dir / "v4-blind-confidence-intervals.csv", ci)
    write_csv(args.output_dir / "v4-blind-paired-mcnemar-tests.csv", tests)
    write_csv(args.output_dir / "v4-blind-bootstrap-diff-ci.csv", boot)
    payload = {"confidence_intervals": ci, "paired_mcnemar": tests, "bootstrap_diff_ci": boot}
    (args.output_dir / "v4-blind-statistical-significance.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(args.output_dir / "v4-blind-statistical-significance.md", ci, tests, boot)
    print(json.dumps({"output_dir": str(args.output_dir.relative_to(PROJECT_DIR)), "ci_rows": len(ci), "tests": len(tests)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
