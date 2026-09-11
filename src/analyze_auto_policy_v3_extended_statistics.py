from __future__ import annotations

"""Extended statistics for the latest Auto Policy V3 repairs.

Adds confidence intervals and paired exact tests for:

* naive raw-source retrieval vs provenance-aware retrieval
* provenance-aware retrieval with full vs hidden target metadata
* original natural-conflict gate vs enhanced natural-conflict gate

No model calls are made.
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
PREFIX = "auto-policy-v3-extended-statistical-analysis"


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


def bool_value(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


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


def metric_row(label: str, metric: str, k: int, n: int, source: str, notes: str = "") -> dict[str, Any]:
    low, high = wilson(k, n)
    return {
        "label": label,
        "metric": metric,
        "successes": k,
        "attempts": n,
        "estimate": k / n if n else 0,
        "ci95_low": low,
        "ci95_high": high,
        "source": source,
        "notes": notes,
    }


def summarize_repair_details(label: str, path: Path) -> list[dict[str, Any]]:
    rows = read_csv(path)
    closure = sum(bool_value(row["full_closure_success"]) for row in rows)
    oracle = sum(bool_value(row["selection_oracle_correct"]) for row in rows)
    selected = sum(row["selection_status"] == "SELECTED" for row in rows)
    return [
        metric_row(label, "full_closure_accuracy", closure, len(rows), path.name),
        metric_row(label, "oracle_accuracy", oracle, len(rows), path.name),
        metric_row(label, "selection_rate", selected, len(rows), path.name),
    ]


def summarize_natural_conflict(label: str, path: Path, dataset: str | None = None) -> list[dict[str, Any]]:
    rows = read_csv(path)
    if dataset is not None:
        rows = [row for row in rows if row.get("dataset") == dataset]
    safe = sum(bool_value(row["safe_abstain"]) for row in rows)
    unsafe = sum(bool_value(row["unsafe_selection"]) for row in rows)
    selected = sum(row["selection_status"] == "SELECTED" for row in rows)
    return [
        metric_row(label, "safe_abstain_rate", safe, len(rows), path.name),
        metric_row(label, "unsafe_selection_rate", unsafe, len(rows), path.name),
        metric_row(label, "selection_rate", selected, len(rows), path.name),
    ]


def paired_success(
    a_label: str,
    b_label: str,
    a_rows: list[dict[str, str]],
    b_rows: list[dict[str, str]],
    metric: str,
    key_fields: tuple[str, ...],
) -> dict[str, Any]:
    a_map = {tuple(row[key] for key in key_fields): bool_value(row[metric]) for row in a_rows}
    b_map = {tuple(row[key] for key in key_fields): bool_value(row[metric]) for row in b_rows}
    keys = sorted(set(a_map) & set(b_map))
    both_success = sum(a_map[key] and b_map[key] for key in keys)
    a_only = sum(a_map[key] and not b_map[key] for key in keys)
    b_only = sum((not a_map[key]) and b_map[key] for key in keys)
    both_fail = sum((not a_map[key]) and (not b_map[key]) for key in keys)
    discordant = a_only + b_only
    return {
        "comparison": f"{b_label} vs {a_label}",
        "metric": metric,
        "pairs": len(keys),
        "both_success": both_success,
        f"{a_label}_only": a_only,
        f"{b_label}_only": b_only,
        "both_fail": both_fail,
        "discordant_pairs": discordant,
        "exact_mcnemar_p": binom_two_sided_p(min(a_only, b_only), discordant) if discordant else 1.0,
        "interpretation": "paired exact binomial test on discordant matched pairs",
    }


def paired_raw_retrieval() -> dict[str, Any]:
    raw_short = read_csv(OUT / "auto-policy-v3-natural-evidence-raw_short_context-candidate-repair-r1-seed20260827-details.csv")
    raw_prov = read_csv(OUT / "auto-policy-v3-natural-evidence-raw_provenance_context-candidate-repair-r1-seed20260827-details.csv")
    return paired_success(
        "RAW_SHORT_CONTEXT",
        "RAW_PROVENANCE_CONTEXT",
        raw_short,
        raw_prov,
        "full_closure_success",
        ("event_id", "run"),
    )


def paired_provenance_metadata() -> dict[str, Any]:
    full = read_csv(OUT / "auto-policy-v3-natural-evidence-raw_provenance_context-candidate-repair-r1-seed20260827-details.csv")
    light = read_csv(OUT / "auto-policy-v3-natural-evidence-raw_provenance_metadata_light-candidate-repair-r1-seed20260827-details.csv")
    return paired_success(
        "RAW_PROVENANCE_METADATA_LIGHT",
        "RAW_PROVENANCE_CONTEXT",
        light,
        full,
        "full_closure_success",
        ("event_id", "run"),
    )


def paired_natural_conflict_gate() -> dict[str, Any]:
    original = read_csv(OUT / "auto-policy-v3-natural-conflict-safety-r1-seed20260827-details.csv")
    enhanced = [
        row
        for row in read_csv(OUT / "auto-policy-v3-natural-conflict-gate-r1-seed20260827-details.csv")
        if row["dataset"] == "NATURAL_CONFLICT"
    ]
    return paired_success(
        "ORIGINAL_NATURAL_GATE",
        "ENHANCED_NATURAL_GATE",
        original,
        enhanced,
        "safe_abstain",
        ("variant", "event_id", "run"),
    )


def by_type_rows(label: str, path: Path) -> list[dict[str, Any]]:
    rows = read_csv(path)
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["semantic_type"]].append(row)
    result: list[dict[str, Any]] = []
    for semantic_type, items in sorted(groups.items()):
        closure = sum(bool_value(row["full_closure_success"]) for row in items)
        result.append(metric_row(label, f"full_closure_accuracy:{semantic_type}", closure, len(items), path.name))
    return result


def main() -> int:
    metric_rows: list[dict[str, Any]] = []
    metric_rows.extend(
        summarize_repair_details(
            "RAW_SHORT_CONTEXT",
            OUT / "auto-policy-v3-natural-evidence-raw_short_context-candidate-repair-r1-seed20260827-details.csv",
        )
    )
    metric_rows.extend(
        summarize_repair_details(
            "RAW_PROVENANCE_CONTEXT",
            OUT / "auto-policy-v3-natural-evidence-raw_provenance_context-candidate-repair-r1-seed20260827-details.csv",
        )
    )
    metric_rows.extend(
        summarize_repair_details(
            "RAW_PROVENANCE_METADATA_LIGHT",
            OUT / "auto-policy-v3-natural-evidence-raw_provenance_metadata_light-candidate-repair-r1-seed20260827-details.csv",
        )
    )
    metric_rows.extend(
        summarize_natural_conflict(
            "ORIGINAL_NATURAL_GATE",
            OUT / "auto-policy-v3-natural-conflict-safety-r1-seed20260827-details.csv",
        )
    )
    metric_rows.extend(
        summarize_natural_conflict(
            "ENHANCED_NATURAL_GATE",
            OUT / "auto-policy-v3-natural-conflict-gate-r1-seed20260827-details.csv",
            dataset="NATURAL_CONFLICT",
        )
    )
    metric_rows.extend(
        summarize_natural_conflict(
            "ENHANCED_NATURAL_GATE_NORMAL_SET",
            OUT / "auto-policy-v3-natural-conflict-gate-r1-seed20260827-details.csv",
            dataset="NORMAL",
        )
    )
    metric_rows.extend(
        by_type_rows(
            "RAW_SHORT_CONTEXT",
            OUT / "auto-policy-v3-natural-evidence-raw_short_context-candidate-repair-r1-seed20260827-details.csv",
        )
    )
    metric_rows.extend(
        by_type_rows(
            "RAW_PROVENANCE_CONTEXT",
            OUT / "auto-policy-v3-natural-evidence-raw_provenance_context-candidate-repair-r1-seed20260827-details.csv",
        )
    )
    metric_rows.extend(
        by_type_rows(
            "RAW_PROVENANCE_METADATA_LIGHT",
            OUT / "auto-policy-v3-natural-evidence-raw_provenance_metadata_light-candidate-repair-r1-seed20260827-details.csv",
        )
    )
    paired_rows = [paired_raw_retrieval(), paired_provenance_metadata(), paired_natural_conflict_gate()]

    metric_path = OUT / f"{PREFIX}-metrics.csv"
    paired_path = OUT / f"{PREFIX}-paired-tests.csv"
    json_path = OUT / f"{PREFIX}.json"
    write_csv(metric_path, metric_rows)
    write_csv(paired_path, paired_rows)
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
    for row in paired_rows:
        print(
            f"{row['comparison']} {row['metric']}: pairs={row['pairs']} "
            f"discordant={row['discordant_pairs']} p={row['exact_mcnemar_p']:.6g}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
