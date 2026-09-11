from __future__ import annotations

"""Main-table statistics for the frozen external-real-v8-grounded experiment.

Reads ablation and AUTO_POLICY_V3 repair CSVs. Does not call the model and does
not rewrite benchmark metadata. LIGHT main numbers are overall; CLEAN-only is a
sensitivity slice that keeps LEAK_LIGHT events in the dataset.
"""

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

VARIANT_TO_PAPER = {
    "RAW_SHORT_WINDOW": "AUTO_POLICY_V3_RAW_WINDOW",
    "RAW_PROVENANCE_WINDOW": "AUTO_POLICY_V3_RAW_PROVENANCE",
    "RAW_WINDOW_METADATA_LIGHT": "AUTO_POLICY_V3_RAW_PROVENANCE_METADATA_LIGHT",
}

METHOD_POSITION = {
    "DIRECT_FREE": "baseline",
    "OPTION_DESCRIPTION": "aided baseline",
    "OPTION_VALUE_ONLY": "aided baseline",
    "OPTION_FORMAL_OPERATION": "aided baseline",
    "OPTION_FORMAL_POLICY": "policy-aided",
    "OPTION_FORMAL_POLICY_HARD_GATE": "symbolic upper bound",
    "AUTO_POLICY_V3_RAW_WINDOW": "fully automatic",
    "AUTO_POLICY_V3_RAW_PROVENANCE": "fully automatic",
    "AUTO_POLICY_V3_RAW_PROVENANCE_METADATA_LIGHT": "fully automatic",
}

PAPER_METHODS = (
    "DIRECT_FREE",
    "OPTION_DESCRIPTION",
    "OPTION_VALUE_ONLY",
    "OPTION_FORMAL_OPERATION",
    "OPTION_FORMAL_POLICY",
    "OPTION_FORMAL_POLICY_HARD_GATE",
    "AUTO_POLICY_V3_RAW_WINDOW",
    "AUTO_POLICY_V3_RAW_PROVENANCE",
    "AUTO_POLICY_V3_RAW_PROVENANCE_METADATA_LIGHT",
)

PAIRED_COMPARISONS = (
    ("AUTO_POLICY_V3_RAW_PROVENANCE", "DIRECT_FREE"),
    ("AUTO_POLICY_V3_RAW_PROVENANCE", "OPTION_DESCRIPTION"),
    ("AUTO_POLICY_V3_RAW_PROVENANCE", "AUTO_POLICY_V3_RAW_WINDOW"),
)


def paper_method_name(name: str) -> str:
    return VARIANT_TO_PAPER.get(str(name).strip(), str(name).strip())


def method_position(name: str) -> str:
    return METHOD_POSITION.get(paper_method_name(name), "unspecified")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    return max(0.0, (centre - spread) / denom), min(1.0, (centre + spread) / denom)


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


def rate_with_ci(successes: int, attempts: int) -> dict[str, float]:
    low, high = wilson(successes, attempts)
    return {
        "successes": successes,
        "attempts": attempts,
        "estimate": successes / attempts if attempts else 0.0,
        "ci95_low": low,
        "ci95_high": high,
    }


def load_event_meta(benchmark_dir: Path) -> dict[str, dict[str, str]]:
    event_csv = benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    if not event_csv.is_file():
        event_csv = benchmark_dir / "input" / "external-real-event-template.csv"
    leakage_csv = benchmark_dir / "public" / "retrieval" / "metadata-leakage-audit.csv"
    events = {
        row["event_id"]: {
            "domain": row.get("domain", ""),
            "semantic_type": row.get("semantic_type", ""),
            "leak_severity": "CLEAN",
        }
        for row in read_csv(event_csv)
        if str(row.get("status", "")).strip().upper() == "READY"
    }
    if leakage_csv.is_file():
        for row in read_csv(leakage_csv):
            event_id = row.get("event_id", "")
            if event_id in events:
                events[event_id]["leak_severity"] = row.get("severity", "CLEAN") or "CLEAN"
    return events


def normalize_ablation_row(row: dict[str, str], meta: dict[str, dict[str, str]]) -> dict[str, Any]:
    event_id = row["event_id"]
    info = meta.get(event_id, {})
    status = str(row.get("status", ""))
    return {
        "method": paper_method_name(row["method"]),
        "event_id": event_id,
        "domain": info.get("domain", ""),
        "semantic_type": row.get("semantic_type") or info.get("semantic_type", ""),
        "run": int(row.get("run", 1) or 1),
        "oracle_correct": bool_value(row.get("oracle_correct")),
        "abstain": status == "ABSTAIN",
        "invalid": status.startswith("REJECTED"),
        "selected_candidate_id": row.get("selected_candidate_id", ""),
        "runtime_ms": int(float(row.get("runtime_ms", 0) or 0)),
        "leak_severity": info.get("leak_severity", "CLEAN"),
    }


def normalize_repair_row(
    row: dict[str, str],
    meta: dict[str, dict[str, str]],
    method: str,
) -> dict[str, Any]:
    event_id = row["event_id"]
    info = meta.get(event_id, {})
    generation = str(row.get("generation_status", ""))
    selection = str(row.get("selection_status", ""))
    invalid = generation not in {"GENERATED", "ABSTAIN", ""} and generation != "SELECTED"
    if generation in {"INVALID_JSON", "INVALID_SCHEMA", "FORBIDDEN_OUTPUT", "ERROR", "RETRIEVAL_FAILED"}:
        invalid = True
    return {
        "method": paper_method_name(method),
        "event_id": event_id,
        "domain": info.get("domain", ""),
        "semantic_type": row.get("semantic_type") or info.get("semantic_type", ""),
        "run": int(row.get("run", 1) or 1),
        "oracle_correct": bool_value(row.get("selection_oracle_correct")),
        "full_closure_success": bool_value(row.get("full_closure_success")),
        "abstain": selection == "ABSTAIN",
        "invalid": invalid,
        "selected_candidate_id": row.get("selected_candidate_id", ""),
        "runtime_ms": int(float(row.get("runtime_ms", 0) or 0)),
        "leak_severity": info.get("leak_severity", "CLEAN"),
    }


def summarize_attempts(attempts: list[dict[str, Any]], clean_only: bool = False) -> dict[str, Any]:
    rows = list(attempts)
    notes = ""
    if clean_only:
        rows = [row for row in rows if row.get("leak_severity") == "CLEAN"]
        notes = "CLEAN-only sensitivity; LEAK_LIGHT events excluded"
    if not rows:
        return {
            "events": 0,
            "attempts": 0,
            "oracle_accuracy": 0.0,
            "notes": notes,
        }
    event_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    domain_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    type_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        event_groups[str(row["event_id"])].append(row)
        domain_groups[str(row.get("domain") or "unknown")].append(row)
        type_groups[str(row.get("semantic_type") or "unknown")].append(row)

    def group_accuracy(items: list[dict[str, Any]]) -> float:
        return sum(bool(item["oracle_correct"]) for item in items) / len(items)

    oracle_successes = sum(bool(row["oracle_correct"]) for row in rows)
    oracle = rate_with_ci(oracle_successes, len(rows))
    strict_events = sum(
        all(bool(item["oracle_correct"]) for item in items) for items in event_groups.values()
    )
    stable_events = sum(
        len({str(item.get("selected_candidate_id", "")) for item in items}) == 1
        for items in event_groups.values()
    )
    runtimes = [int(row.get("runtime_ms", 0) or 0) for row in rows]
    domain_acc = {name: group_accuracy(items) for name, items in sorted(domain_groups.items())}
    type_acc = {name: group_accuracy(items) for name, items in sorted(type_groups.items())}
    strict = rate_with_ci(strict_events, len(event_groups))
    abstain = rate_with_ci(sum(bool(row.get("abstain")) for row in rows), len(rows))
    invalid = rate_with_ci(sum(bool(row.get("invalid")) for row in rows), len(rows))
    return {
        "events": len(event_groups),
        "attempts": len(rows),
        "runs": max(len(items) for items in event_groups.values()),
        "oracle_accuracy": oracle["estimate"],
        "oracle_ci95_low": oracle["ci95_low"],
        "oracle_ci95_high": oracle["ci95_high"],
        "strict_event_success": strict["estimate"],
        "strict_event_successes": strict_events,
        "strict_ci95_low": strict["ci95_low"],
        "strict_ci95_high": strict["ci95_high"],
        "abstain_rate": abstain["estimate"],
        "invalid_rate": invalid["estimate"],
        "selection_stable_events": stable_events,
        "selection_stability": stable_events / len(event_groups),
        "mean_runtime_ms": sum(runtimes) / len(runtimes) if runtimes else 0.0,
        "by_domain_accuracy": domain_acc,
        "by_semantic_type_accuracy": type_acc,
        "macro_domain_average": sum(domain_acc.values()) / len(domain_acc) if domain_acc else 0.0,
        "macro_type_average": sum(type_acc.values()) / len(type_acc) if type_acc else 0.0,
        "notes": notes,
    }


def paired_mcnemar(
    a_label: str,
    b_label: str,
    a_rows: list[dict[str, Any]],
    b_rows: list[dict[str, Any]],
    metric: str = "oracle_correct",
) -> dict[str, Any]:
    a_map = {(str(row["event_id"]), int(row["run"])): bool(row[metric]) for row in a_rows}
    b_map = {(str(row["event_id"]), int(row["run"])): bool(row[metric]) for row in b_rows}
    keys = sorted(set(a_map) & set(b_map))
    both_success = sum(a_map[key] and b_map[key] for key in keys)
    a_only = sum(a_map[key] and not b_map[key] for key in keys)
    b_only = sum((not a_map[key]) and b_map[key] for key in keys)
    both_fail = sum((not a_map[key]) and (not b_map[key]) for key in keys)
    discordant = a_only + b_only
    return {
        "comparison": f"{a_label} vs {b_label}",
        "left_method": a_label,
        "right_method": b_label,
        "metric": metric,
        "pairs": len(keys),
        "both_success": both_success,
        "left_only": a_only,
        "right_only": b_only,
        "both_fail": both_fail,
        "discordant_pairs": discordant,
        "exact_mcnemar_p": binom_two_sided_p(min(a_only, b_only), discordant) if discordant else 1.0,
        "interpretation": "paired exact McNemar/binomial test on discordant matched (event, run) pairs",
    }


def bootstrap_mean_diff_ci(
    a_label: str,
    b_label: str,
    a_rows: list[dict[str, Any]],
    b_rows: list[dict[str, Any]],
    metric: str = "oracle_correct",
    boots: int = 2000,
    seed: int = 20260827,
) -> dict[str, Any]:
    a_map = {(str(row["event_id"]), int(row["run"])): float(bool(row[metric])) for row in a_rows}
    b_map = {(str(row["event_id"]), int(row["run"])): float(bool(row[metric])) for row in b_rows}
    keys = sorted(set(a_map) & set(b_map))
    diffs = [a_map[key] - b_map[key] for key in keys]
    observed = sum(diffs) / len(diffs) if diffs else 0.0
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(boots):
        draw = [diffs[rng.randrange(len(diffs))] for _ in diffs] if diffs else [0.0]
        samples.append(sum(draw) / len(draw))
    samples.sort()
    def pct(p: float) -> float:
        if not samples:
            return 0.0
        index = min(len(samples) - 1, max(0, int(round(p * (len(samples) - 1)))))
        return samples[index]
    return {
        "comparison": f"{a_label} vs {b_label}",
        "metric": metric,
        "pairs": len(keys),
        "mean_diff": observed,
        "boot_ci95_low": pct(0.025),
        "boot_ci95_high": pct(0.975),
        "boots": boots,
        "interpretation": "event-run paired bootstrap CI for mean accuracy difference",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="v8 main-table statistics")
    parser.add_argument("--benchmark-dir", type=Path, default=ROOT / "benchmark" / "external-real-v8-grounded")
    parser.add_argument("--ablation-details", type=Path)
    parser.add_argument(
        "--repair-details",
        action="append",
        default=[],
        help="METHOD=path, e.g. RAW_PROVENANCE_WINDOW=.../details.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "external-real-v8-grounded")
    parser.add_argument("--prefix", default="external-real-v8-main-table")
    return parser.parse_args()


def flatten_summary(method: str, slice_name: str, summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": method,
        "position": method_position(method),
        "slice": slice_name,
        "events": summary.get("events", 0),
        "attempts": summary.get("attempts", 0),
        "runs": summary.get("runs", 0),
        "oracle_accuracy": summary.get("oracle_accuracy", 0),
        "oracle_ci95_low": summary.get("oracle_ci95_low", 0),
        "oracle_ci95_high": summary.get("oracle_ci95_high", 0),
        "strict_event_success": summary.get("strict_event_success", 0),
        "strict_ci95_low": summary.get("strict_ci95_low", 0),
        "strict_ci95_high": summary.get("strict_ci95_high", 0),
        "abstain_rate": summary.get("abstain_rate", 0),
        "invalid_rate": summary.get("invalid_rate", 0),
        "selection_stability": summary.get("selection_stability", 0),
        "macro_domain_average": summary.get("macro_domain_average", 0),
        "macro_type_average": summary.get("macro_type_average", 0),
        "mean_runtime_ms": summary.get("mean_runtime_ms", 0),
        "notes": summary.get("notes", ""),
    }


def breakdown_rows(method: str, slice_name: str, summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for domain, acc in (summary.get("by_domain_accuracy") or {}).items():
        rows.append(
            {
                "method": method,
                "slice": slice_name,
                "axis": "domain",
                "group": domain,
                "oracle_accuracy": acc,
            }
        )
    for semantic_type, acc in (summary.get("by_semantic_type_accuracy") or {}).items():
        rows.append(
            {
                "method": method,
                "slice": slice_name,
                "axis": "semantic_type",
                "group": semantic_type,
                "oracle_accuracy": acc,
            }
        )
    return rows


def main() -> int:
    args = parse_args()
    meta = load_event_meta(args.benchmark_dir)
    by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if args.ablation_details:
        for row in read_csv(args.ablation_details):
            item = normalize_ablation_row(row, meta)
            by_method[item["method"]].append(item)
    for spec in args.repair_details:
        method, _, raw_path = spec.partition("=")
        if not raw_path:
            raise ValueError(f"--repair-details must be METHOD=path, got {spec!r}")
        for row in read_csv(Path(raw_path)):
            item = normalize_repair_row(row, meta, method)
            by_method[item["method"]].append(item)

    main_rows: list[dict[str, Any]] = []
    breakdown: list[dict[str, Any]] = []
    for method in PAPER_METHODS:
        attempts = by_method.get(method, [])
        if not attempts:
            continue
        overall = summarize_attempts(attempts, clean_only=False)
        main_rows.append(flatten_summary(method, "overall", overall))
        breakdown.extend(breakdown_rows(method, "overall", overall))
        if method == "AUTO_POLICY_V3_RAW_PROVENANCE_METADATA_LIGHT":
            clean = summarize_attempts(attempts, clean_only=True)
            main_rows.append(flatten_summary(method, "clean_only", clean))
            breakdown.extend(breakdown_rows(method, "clean_only", clean))

    paired_rows: list[dict[str, Any]] = []
    for left, right in PAIRED_COMPARISONS:
        if left not in by_method or right not in by_method:
            continue
        mcnemar = paired_mcnemar(left, right, by_method[left], by_method[right])
        boot = bootstrap_mean_diff_ci(left, right, by_method[left], by_method[right])
        paired_rows.append(
            {
                **mcnemar,
                "mean_diff": boot["mean_diff"],
                "boot_ci95_low": boot["boot_ci95_low"],
                "boot_ci95_high": boot["boot_ci95_high"],
                "boots": boot["boots"],
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    main_csv = args.output_dir / f"{args.prefix}-methods.csv"
    breakdown_csv = args.output_dir / f"{args.prefix}-by-group.csv"
    paired_csv = args.output_dir / f"{args.prefix}-paired.csv"
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": str(args.benchmark_dir),
        "git_binding": "47cfa4f",
        "methods": main_rows,
        "paired": paired_rows,
        "light_rule": (
            "RAW_PROVENANCE_METADATA_LIGHT main result is overall including "
            "LEAK_LIGHT; clean_only is a sensitivity slice. Do not drop those events."
        ),
    }
    write_csv_rows(main_csv, main_rows)
    write_csv_rows(breakdown_csv, breakdown)
    write_csv_rows(paired_csv, paired_rows)
    json_path = args.output_dir / f"{args.prefix}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"methods={main_csv}")
    print(f"by_group={breakdown_csv}")
    print(f"paired={paired_csv}")
    print(f"json={json_path}")
    for row in main_rows:
        print(
            f"{row['method']} [{row['slice']}] oracle={row['oracle_accuracy']:.2%} "
            f"strict={row['strict_event_success']:.2%} abstain={row['abstain_rate']:.2%} "
            f"invalid={row['invalid_rate']:.2%} n={row['events']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
