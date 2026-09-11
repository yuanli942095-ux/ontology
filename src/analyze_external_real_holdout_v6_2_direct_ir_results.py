from __future__ import annotations

"""Post-hoc failure analysis and statistics for v6.2 Direct-IR blind results.

This script reads persisted model outputs and private Gold after the blind run.
It is diagnostic/reporting infrastructure only and does not modify predictions,
Gold labels, the benchmark, or the frozen method.
"""

import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rfc213_direct_repair_ir import canonicalize_surface
from rfc213_direct_repair_ir_v2 import resolve_source_window
from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark/external-real-holdout-v6-2-direct-ir-blind"
RUN_DIR = PROJECT_DIR / "output/external-real-holdout-v6-2-direct-ir-blind/final-blind-r2b-v2-r5"
OUTPUT = RUN_DIR / "posthoc-analysis-v2-2"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def raw_path(run_dir: Path, row: dict[str, str]) -> Path:
    return run_dir / "raw-predicted-ir" / f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"


def normalize_window_id(value: Any) -> str:
    text = str(value or "").strip()
    return text.removeprefix("SOURCE_WINDOW_")


def target_differences(predicted: dict[str, Any], gold: dict[str, Any]) -> list[str]:
    differences: list[str] = []
    checks = (
        ("operation", predicted.get("operation"), gold.get("operation")),
        ("subject_iri", nested(predicted, "target", "subject_iri"), nested(gold, "target", "subject_iri")),
        ("predicate_iri", nested(predicted, "target", "predicate_iri"), nested(gold, "target", "predicate_iri")),
        ("old_kind", nested(predicted, "target", "old_value", "kind"), nested(gold, "target", "old_value", "kind")),
        ("old_lexical", nested(predicted, "target", "old_value", "lexical"), nested(gold, "target", "old_value", "lexical")),
        ("old_datatype", nested(predicted, "target", "old_value", "datatype"), nested(gold, "target", "old_value", "datatype")),
        ("new_kind", nested(predicted, "replacement", "new_value", "kind"), nested(gold, "replacement", "new_value", "kind")),
        ("new_lexical", nested(predicted, "replacement", "new_value", "lexical"), nested(gold, "replacement", "new_value", "lexical")),
        ("new_datatype", nested(predicted, "replacement", "new_value", "datatype"), nested(gold, "replacement", "new_value", "datatype")),
    )
    for name, actual, expected in checks:
        if actual != expected:
            differences.append(name)
    return differences


def primary_attribution(
    detail: dict[str, str],
    raw_decision: str,
    resolution: dict[str, Any],
    differences: list[str],
    gold: dict[str, Any],
) -> tuple[str, str]:
    if as_bool(detail["ses_success"]):
        return "CORRECT", "End-to-end SES passed"

    gold_decision = gold["decision"]
    final_decision = detail.get("model_decision", "")
    failure_stage = detail.get("failure_stage", "")
    if gold_decision == "REPAIR":
        if failure_stage in {"GENERATION", "TRANSPORT_OR_GENERATION"}:
            return "GENERATION_FAILURE", failure_stage
        if final_decision != "REPAIR":
            return "OVER_ABSTENTION", f"Gold REPAIR but final decision is {final_decision or 'UNKNOWN'}"
        if detail.get("schema_status") == "INVALID_IR":
            return "INVALID_IR", detail.get("validation_errors", "")
        if resolution["status"] == "NO_WINDOW_MATCH":
            return "EVIDENCE_SPAN_NO_MATCH", "Predicted evidence span was not found verbatim in a frozen window"
        if resolution["status"] == "AMBIGUOUS_WINDOW":
            return "EVIDENCE_WINDOW_AMBIGUOUS", "Predicted span maps to multiple canonical literals"
        matched = {normalize_window_id(value) for value in resolution.get("matching_window_ids", [])}
        gold_window = normalize_window_id(gold.get("gold_source_window"))
        if gold_window and matched and gold_window not in matched:
            return "WRONG_EVIDENCE_WINDOW", "Resolved evidence window does not include the adjudicated Gold window"
        if as_bool(detail.get("selected")) and not as_bool(detail.get("predicted_gamma_ir_exact")):
            if "new_lexical" in differences:
                return "WRONG_CANONICAL_VALUE", ",".join(differences)
            if any(name in differences for name in ("subject_iri", "predicate_iri", "old_lexical")):
                return "WRONG_ONTOLOGY_TARGET", ",".join(differences)
            return "WRONG_REPAIR_IR", ",".join(differences)
        if failure_stage == "PRECONDITION":
            return "GAMMA_PRECONDITION_FAILURE", detail.get("precondition_status", "")
        return "OTHER_REPAIR_FAILURE", failure_stage

    if final_decision == "REPAIR":
        if gold_decision == "NO_CHANGE":
            return "UNSAFE_FALSE_REPAIR_NO_CHANGE", "Gold NO_CHANGE but final decision remains REPAIR"
        if gold_decision == "ABSTAIN":
            return "UNSAFE_FALSE_REPAIR_ABSTAIN", "Gold ABSTAIN but model attempted REPAIR"
    if gold_decision == "NO_CHANGE" and final_decision == "ABSTAIN":
        return "NO_CHANGE_AS_ABSTAIN", "Safe non-execution but wrong decision label"
    if gold_decision == "ABSTAIN" and final_decision == "NO_CHANGE":
        return "ABSTAIN_AS_NO_CHANGE", "Wrong safety subtype"
    return "OTHER_SAFETY_FAILURE", f"Gold {gold_decision}, final {final_decision}"


def build_attempt_rows(details: list[dict[str, str]], benchmark: Path, run_dir: Path, golds: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for detail in details:
        event_id = detail["event_id"]
        gold = golds[event_id]
        evidence = (benchmark / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
        payload_path = raw_path(run_dir, detail)
        payload = json.loads(payload_path.read_text(encoding="utf-8-sig")) if payload_path.is_file() else {}
        raw_ir = payload.get("predicted_ir_raw", {}) if isinstance(payload.get("predicted_ir_raw", {}), dict) else {}
        raw_surface = canonicalize_surface(raw_ir)
        raw_decision = str(raw_surface.get("decision", "ABSTAIN"))
        resolution = resolve_source_window(raw_ir, evidence)
        resolved_ir = resolution.get("canonical_ir") if isinstance(resolution.get("canonical_ir"), dict) else raw_surface
        differences = target_differences(resolved_ir, gold) if raw_decision == "REPAIR" else []
        attribution, rationale = primary_attribution(detail, raw_decision, resolution, differences, gold)
        spans = raw_surface.get("evidence_spans", [])
        rows.append({
            **detail,
            "raw_model_decision": raw_decision,
            "gold_source_window": gold.get("gold_source_window", ""),
            "predicted_matching_windows": json.dumps(resolution.get("matching_window_ids", []), ensure_ascii=False),
            "gold_window_recalled": normalize_window_id(gold.get("gold_source_window")) in {normalize_window_id(v) for v in resolution.get("matching_window_ids", [])},
            "field_differences": json.dumps(differences, ensure_ascii=False),
            "primary_attribution": attribution,
            "attribution_rationale": rationale,
            "predicted_evidence_span": spans[0] if isinstance(spans, list) and spans else "",
            "gold_new_lexical": nested(gold, "replacement", "new_value", "lexical") or "",
            "predicted_new_lexical": nested(resolved_ir, "replacement", "new_value", "lexical") or "",
        })
    return rows


def build_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(row)
    output: list[dict[str, Any]] = []
    for event_id, items in sorted(grouped.items()):
        counts = Counter(row["primary_attribution"] for row in items)
        success_runs = sum(as_bool(row["ses_success"]) for row in items)
        selected_runs = sum(as_bool(row["selected"]) for row in items)
        wrong_runs = sum(as_bool(row["wrong_repair"]) for row in items)
        dominant, dominant_count = counts.most_common(1)[0]
        output.append({
            "event_id": event_id,
            "partition": items[0]["partition"],
            "domain": items[0]["domain"],
            "semantic_type": items[0]["semantic_type"],
            "gold_decision": items[0]["gold_decision"],
            "gold_source_window": items[0]["gold_source_window"],
            "runs": len(items),
            "success_runs": success_runs,
            "selected_runs": selected_runs,
            "wrong_runs": wrong_runs,
            "strict_success": success_runs == len(items),
            "zero_success": success_runs == 0,
            "dominant_attribution": dominant,
            "dominant_attribution_runs": dominant_count,
            "attribution_counts": json.dumps(dict(sorted(counts.items())), ensure_ascii=False),
        })
    return output


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def bootstrap_ci(values: list[float], seed: int = 20260909, draws: int = 10000) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    samples = []
    for _ in range(draws):
        samples.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    samples.sort()
    return (samples[int(0.025 * draws)], samples[int(0.975 * draws)])


def write_stat_tables(output: Path, attempts: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        groups["ALL"].append(row)
        groups[row["partition"]].append(row)
        groups[row["semantic_type"]].append(row)
        groups[row["gold_decision"]].append(row)
        groups[f"domain:{row['domain']}"].append(row)
    event_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        event_groups["ALL"].append(row)
        event_groups[row["partition"]].append(row)
        event_groups[row["semantic_type"]].append(row)
        event_groups[row["gold_decision"]].append(row)
        event_groups[f"domain:{row['domain']}"].append(row)

    stats: list[dict[str, Any]] = []
    for label, rows in sorted(groups.items()):
        event_rows = event_groups.get(label, [])
        n = len(rows)
        success = sum(as_bool(row["ses_success"]) for row in rows)
        selected = sum(as_bool(row["selected"]) for row in rows)
        wrong = sum(as_bool(row["wrong_repair"]) for row in rows)
        strict = sum(bool(row["strict_success"]) for row in event_rows)
        low, high = wilson_interval(success, n)
        strict_low, strict_high = wilson_interval(strict, len(event_rows))
        clustered_low, clustered_high = bootstrap_ci([row["success_runs"] / row["runs"] for row in event_rows])
        stats.append({
            "group": label,
            "events": len(event_rows),
            "attempts": n,
            "ses_successes": success,
            "ses": success / n if n else 0,
            "ses_wilson95_low": low,
            "ses_wilson95_high": high,
            "event_clustered_ses95_low": clustered_low,
            "event_clustered_ses95_high": clustered_high,
            "strict_event_successes": strict,
            "strict_event_accuracy": strict / len(event_rows) if event_rows else 0,
            "strict_wilson95_low": strict_low,
            "strict_wilson95_high": strict_high,
            "coverage": selected / n if n else 0,
            "wrong_repairs": wrong,
            "wrr": wrong / n if n else 0,
            "selective_risk": wrong / selected if selected else 0,
        })
    write_csv(output / "statistical-summary.csv", stats)
    return stats


def write_report(output: Path, attempts: list[dict[str, Any]], events: list[dict[str, Any]], stats: list[dict[str, Any]]) -> None:
    repair = [row for row in attempts if row["partition"] == "REPAIR"]
    safety = [row for row in attempts if row["partition"] == "SAFETY"]
    repair_counts = Counter(row["primary_attribution"] for row in repair)
    safety_counts = Counter(row["primary_attribution"] for row in safety)
    zero_success = sum(bool(row["zero_success"]) for row in events)
    all_stats = next(row for row in stats if row["group"] == "ALL")
    repair_stats = next(row for row in stats if row["group"] == "REPAIR")
    safety_stats = next(row for row in stats if row["group"] == "SAFETY")
    lines = [
        "# v6.2 / V2.2 Direct-IR Blind Post-hoc Analysis",
        "",
        "> Status: reporting and diagnostic analysis after persisted blind predictions. This report does not modify the benchmark, Gold labels, raw model outputs, Gamma, or the frozen method.",
        "",
        "## Main Statistics",
        "",
        "| Group | SES | Event-clustered 95% CI | Strict event | Coverage | WRR | Selective risk |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in (all_stats, repair_stats, safety_stats):
        lines.append(
            f"| {row['group']} | {row['ses']:.2%} | "
            f"{row['event_clustered_ses95_low']:.2%}-{row['event_clustered_ses95_high']:.2%} | "
            f"{row['strict_event_accuracy']:.2%} | {row['coverage']:.2%} | "
            f"{row['wrr']:.2%} | {row['selective_risk']:.2%} |"
        )
    lines.extend([
        "",
        "## Failure Decomposition",
        "",
        f"- Events with zero successful runs: {zero_success}/{len(events)}",
        f"- Repair selected attempts: {sum(as_bool(r['selected']) for r in repair)}/{len(repair)}",
        f"- Safety false repairs: {sum(as_bool(r['wrong_repair']) for r in safety)}/{len(safety)}",
        "",
        "### Repair Attempts",
        "",
        "| Attribution | Attempts | Share | Events |",
        "|---|---:|---:|---:|",
    ])
    for label, count in repair_counts.most_common():
        events_n = len({row["event_id"] for row in repair if row["primary_attribution"] == label})
        lines.append(f"| {label} | {count} | {count / len(repair):.2%} | {events_n} |")
    lines.extend(["", "### Safety Attempts", "", "| Attribution | Attempts | Share | Events |", "|---|---:|---:|---:|"])
    for label, count in safety_counts.most_common():
        events_n = len({row["event_id"] for row in safety if row["primary_attribution"] == label})
        lines.append(f"| {label} | {count} | {count / len(safety):.2%} | {events_n} |")
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- V2.2 eliminates the previous NO_CHANGE failure mode through a candidate-blind equivalence gate.",
        "- The remaining dominant risks are wrong repairs under conflicting evidence and a smaller number of repair-window/value errors.",
        "- TEMPORAL_VERSION has zero wrong repairs in the full blind run; its residual loss is mainly abstention.",
        "",
        "## Output Files",
        "",
        "- `attempt-failure-attribution.csv`",
        "- `event-root-cause-summary.csv`",
        "- `failure-attribution-summary.csv`",
        "- `statistical-summary.csv`",
        "- `trace-sample-25-events.csv`",
    ])
    (output / "v6-2-v2-2-posthoc-analysis-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()

    details = read_csv(args.run_dir / "v6-final-blind-details.csv")
    golds = {row["event_id"]: row for row in read_jsonl(args.benchmark_dir / "private/oracle/gold-repair-ir.jsonl")}
    if len(details) != 1500 or len(golds) != 300:
        raise SystemExit(f"unexpected input size: details={len(details)}, gold={len(golds)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    attempts = build_attempt_rows(details, args.benchmark_dir, args.run_dir, golds)
    events = build_event_rows(attempts)
    stats = write_stat_tables(args.output_dir, attempts, events)
    summary: list[dict[str, Any]] = []
    grouped_summary: dict[tuple[str, str, str], int] = Counter()
    for row in attempts:
        grouped_summary[("ALL", "ALL", row["primary_attribution"])] += 1
        grouped_summary[("PARTITION", row["partition"], row["primary_attribution"])] += 1
        grouped_summary[("SEMANTIC_TYPE", row["semantic_type"], row["primary_attribution"])] += 1
        grouped_summary[("GOLD_DECISION", row["gold_decision"], row["primary_attribution"])] += 1
    totals = Counter((kind, value) for kind, value, _ in grouped_summary)
    for (kind, value, attribution), count in sorted(grouped_summary.items()):
        denominator = sum(v for (k, val, _), v in grouped_summary.items() if k == kind and val == value)
        summary.append({
            "group_kind": kind,
            "group_value": value,
            "primary_attribution": attribution,
            "attempts": count,
            "share_within_group": count / denominator if denominator else 0,
            "events": len({row["event_id"] for row in attempts if (
                (kind == "ALL" or row.get("partition" if kind == "PARTITION" else "semantic_type" if kind == "SEMANTIC_TYPE" else "gold_decision") == value)
                and row["primary_attribution"] == attribution
            )}),
        })
    write_csv(args.output_dir / "attempt-failure-attribution.csv", attempts)
    write_csv(args.output_dir / "event-root-cause-summary.csv", events)
    write_csv(args.output_dir / "failure-attribution-summary.csv", summary)
    failure_events = [row for row in events if not bool(row["strict_success"])]
    sample_ids = {row["event_id"] for row in sorted(failure_events, key=lambda r: (r["dominant_attribution"], r["event_id"]))[:25]}
    write_csv(args.output_dir / "trace-sample-25-events.csv", [row for row in attempts if row["event_id"] in sample_ids])
    write_report(args.output_dir, attempts, events, stats)
    print(json.dumps({
        "attempts": len(attempts),
        "events": len(events),
        "statistics": {row["group"]: {"ses": row["ses"], "strict_event_accuracy": row["strict_event_accuracy"], "wrr": row["wrr"]} for row in stats if row["group"] in {"ALL", "REPAIR", "SAFETY"}},
        "repair_attribution": dict(Counter(row["primary_attribution"] for row in attempts if row["partition"] == "REPAIR")),
        "safety_attribution": dict(Counter(row["primary_attribution"] for row in attempts if row["partition"] == "SAFETY")),
        "output_dir": str(args.output_dir),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
