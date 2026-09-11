from __future__ import annotations

"""Statistical summaries for Auto Policy V2/V3 experiments.

Reads existing CSV/JSON outputs and computes Wilson 95% confidence intervals
plus paired exact McNemar/binomial tests for matched runs.  No model calls are
made and no benchmark artifacts are modified.
"""

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(r"G:\LearnAI\ontology-evolution")
OUT = ROOT / "output"
PREFIX = "auto-policy-v3-statistical-analysis"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (centre - spread) / denom, (centre + spread) / denom


def binom_two_sided_p(k: int, n: int, p: float = 0.5) -> float:
    if n <= 0:
        return 1.0
    observed = math.comb(n, k) * (p**k) * ((1 - p) ** (n - k))
    total = 0.0
    for i in range(n + 1):
        prob = math.comb(n, i) * (p**i) * ((1 - p) ** (n - i))
        if prob <= observed + 1e-15:
            total += prob
    return min(1.0, total)


def bool_value(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def metric_row(label: str, metric: str, k: int, n: int, notes: str = "") -> dict[str, Any]:
    low, high = wilson(k, n)
    return {
        "label": label,
        "metric": metric,
        "successes": k,
        "attempts": n,
        "estimate": k / n if n else 0,
        "ci95_low": low,
        "ci95_high": high,
        "notes": notes,
    }


def rows_from_summary_csv(path: Path, label_column: str, metric_columns: list[str]) -> list[dict[str, Any]]:
    success_columns = {
        "semantic_accuracy": "semantic_correct",
        "full_closure_accuracy": "full_closure_success",
        "oracle_accuracy": "oracle_correct",
        "safe_abstain_rate": "safe_abstains",
        "unsafe_selection_rate": "unsafe_selections",
        "unsafe_wrong_selection_rate": "unsafe_wrong_selections",
    }
    rows: list[dict[str, Any]] = []
    for row in read_csv(path):
        label = row[label_column]
        n = int(float(row.get("attempts", row.get("generation_successes", row.get("events", "0"))) or 0))
        for metric in metric_columns:
            if metric not in row or row[metric] == "":
                continue
            success_column = success_columns.get(metric)
            if success_column and success_column in row and row[success_column] != "":
                k = int(float(row[success_column]))
                if not n:
                    n = k
            else:
                value = float(row[metric])
                k = round(value * n) if value <= 1 else int(round(value))
            rows.append(metric_row(label, metric, k, n, str(path.name)))
    return rows


def paired_rows(
    a_path: Path,
    b_path: Path,
    a_label: str,
    b_label: str,
    metric: str,
    key_fields: tuple[str, str] = ("event_id", "run"),
) -> dict[str, Any]:
    a_rows = read_csv(a_path)
    b_rows = read_csv(b_path)
    a_map = {(row[key_fields[0]], row[key_fields[1]]): bool_value(row[metric]) for row in a_rows}
    b_map = {(row[key_fields[0]], row[key_fields[1]]): bool_value(row[metric]) for row in b_rows}
    keys = sorted(set(a_map) & set(b_map))
    both_success = sum(a_map[key] and b_map[key] for key in keys)
    a_only = sum(a_map[key] and not b_map[key] for key in keys)
    b_only = sum((not a_map[key]) and b_map[key] for key in keys)
    both_fail = sum((not a_map[key]) and (not b_map[key]) for key in keys)
    discordant = a_only + b_only
    p_value = binom_two_sided_p(min(a_only, b_only), discordant) if discordant else 1.0
    return {
        "comparison": f"{b_label} vs {a_label}",
        "metric": metric,
        "pairs": len(keys),
        "both_success": both_success,
        f"{a_label}_only": a_only,
        f"{b_label}_only": b_only,
        "both_fail": both_fail,
        "discordant_pairs": discordant,
        "exact_mcnemar_p": p_value,
        "interpretation": "paired exact binomial test on discordant pairs",
    }


def paired_gate_rows() -> dict[str, Any]:
    before = read_csv(OUT / "auto-policy-v3-negative-safety-r1-seed20260827-details.csv")
    after = read_csv(OUT / "auto-policy-v3-conflict-gate-r1-seed20260827-details.csv")
    before_map = {
        (row["variant"], row["event_id"], row["run"]): bool_value(row["safe_abstain"])
        for row in before
    }
    after_map = {
        (row["variant"], row["event_id"], row["run"]): bool_value(row["safe_abstain"])
        for row in after
        if row["dataset"] == "NEGATIVE"
    }
    keys = sorted(set(before_map) & set(after_map))
    both_success = sum(before_map[key] and after_map[key] for key in keys)
    before_only = sum(before_map[key] and not after_map[key] for key in keys)
    after_only = sum((not before_map[key]) and after_map[key] for key in keys)
    both_fail = sum((not before_map[key]) and (not after_map[key]) for key in keys)
    discordant = before_only + after_only
    return {
        "comparison": "CONFLICT_GATE vs V3_NEGATIVE_SAFETY",
        "metric": "safe_abstain",
        "pairs": len(keys),
        "both_success": both_success,
        "V3_NEGATIVE_SAFETY_only": before_only,
        "CONFLICT_GATE_only": after_only,
        "both_fail": both_fail,
        "discordant_pairs": discordant,
        "exact_mcnemar_p": binom_two_sided_p(min(before_only, after_only), discordant) if discordant else 1.0,
        "interpretation": "paired exact binomial test on safe abstain improvement",
    }


def main() -> int:
    metric_rows: list[dict[str, Any]] = []

    # V2/V3 component and repair closure summaries.
    metric_rows.extend(
        rows_from_summary_csv(
            OUT / "auto-policy-v3-component-ablation-r5-seed20260820-summary.csv",
            "variant",
            ["semantic_accuracy", "full_closure_accuracy"],
        )
    )
    metric_rows.extend(
        rows_from_summary_csv(
            OUT / "auto-policy-v3-robustness-r3-seed20260827-summary.csv",
            "variant",
            ["semantic_accuracy", "full_closure_accuracy"],
        )
    )
    metric_rows.extend(
        rows_from_summary_csv(
            OUT / "auto-policy-v3-negative-safety-r1-seed20260827-by-variant.csv",
            "variant",
            ["safe_abstain_rate", "unsafe_selection_rate", "unsafe_wrong_selection_rate"],
        )
    )
    metric_rows.extend(
        rows_from_summary_csv(
            OUT / "auto-policy-v3-conflict-gate-r1-seed20260827-by-dataset.csv",
            "dataset",
            ["oracle_accuracy", "safe_abstain_rate", "unsafe_selection_rate"],
        )
    )

    paired: list[dict[str, Any]] = [
        paired_rows(
            OUT / "auto-policy-v2-candidate-repair-r5-seed20260820-details.csv",
            OUT / "auto-policy-v3-candidate-repair-r5-seed20260820-details.csv",
            "V2",
            "V3",
            "full_closure_success",
        ),
        paired_gate_rows(),
    ]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metric_rows:
        grouped[row["label"]].append(row)

    metric_path = OUT / f"{PREFIX}-metrics.csv"
    paired_path = OUT / f"{PREFIX}-paired-tests.csv"
    json_path = OUT / f"{PREFIX}.json"
    write_csv(metric_path, metric_rows)
    write_csv(paired_path, paired)
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "metrics": str(metric_path),
                "paired_tests": str(paired_path),
                "methods": {
                    "ci": "Wilson 95% confidence interval for binomial proportions",
                    "paired_test": "Exact McNemar/binomial test on discordant matched pairs",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"metrics={metric_path}")
    print(f"paired_tests={paired_path}")
    for row in paired:
        print(
            f"{row['comparison']} {row['metric']}: discordant={row['discordant_pairs']} "
            f"p={row['exact_mcnemar_p']:.6g}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
