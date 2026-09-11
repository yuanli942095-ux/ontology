from __future__ import annotations

"""Post-hoc failure decomposition for the frozen v6.1 Direct-IR blind run.

This script is diagnostic only. It reads persisted predictions and private Gold
after evaluation, and must not be used to relabel v6.1 as an independent blind
result after method development.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rfc213_direct_repair_ir import canonicalize_surface
from rfc213_direct_repair_ir_v2 import resolve_source_window
from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark/external-real-holdout-v6-1-direct-ir-blind"
RUN_DIR = PROJECT_DIR / "output/external-real-holdout-v6-1-direct-ir-blind/final-blind-r2b-v2-r5"
OUTPUT = RUN_DIR / "posthoc-failure-analysis"


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


def normalize_window_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("SOURCE_WINDOW_"):
        return text.removeprefix("SOURCE_WINDOW_")
    return text


def primary_attribution(
    detail: dict[str, str],
    raw_decision: str,
    resolution: dict[str, Any],
    differences: list[str],
    gold: dict[str, Any],
) -> tuple[str, str]:
    gold_decision = gold["decision"]
    if gold_decision == "REPAIR":
        if as_bool(detail["ses_success"]):
            return "CORRECT", "End-to-end repair succeeded"
        if detail["failure_stage"] in {"GENERATION", "TRANSPORT_OR_GENERATION"}:
            return "GENERATION_FAILURE", detail["failure_stage"]
        if raw_decision != "REPAIR":
            return "OVER_ABSTENTION", f"Gold REPAIR but model emitted {raw_decision or 'UNKNOWN'}"
        if resolution["status"] == "NO_WINDOW_MATCH":
            return "EVIDENCE_SPAN_NO_MATCH", "Predicted evidence span was not found verbatim in any frozen window"
        if resolution["status"] == "AMBIGUOUS_WINDOW":
            return "EVIDENCE_WINDOW_AMBIGUOUS", "Predicted span matched windows implying multiple canonical literals"
        gold_window = normalize_window_id(gold.get("gold_source_window"))
        matched_windows = {normalize_window_id(value) for value in resolution.get("matching_window_ids", [])}
        if resolution["status"] == "RESOLVED" and gold_window and gold_window not in matched_windows:
            return "WRONG_EVIDENCE_WINDOW", "Resolved evidence did not include the adjudicated Gold window"
        if detail["schema_status"] == "INVALID_IR":
            return "INVALID_IR", detail.get("validation_errors", "")
        if detail["schema_status"] == "PRECONDITION_FAILED":
            if "subject_iri" in differences or "predicate_iri" in differences:
                return "ONTOLOGY_TARGET_MISMATCH", ",".join(differences)
            return "OLD_VALUE_PRECONDITION_MISMATCH", ",".join(differences) or detail.get("validation_errors", "")
        if detail["failure_stage"] == "PRECONDITION":
            return "GAMMA_APPLY_PRECONDITION_FAILURE", ",".join(differences) or detail["precondition_status"]
        if as_bool(detail["selected"]) and not as_bool(detail["predicted_gamma_ir_exact"]):
            if "new_lexical" in differences:
                return "WRONG_EVIDENCE_WINDOW_REPAIR", ",".join(differences)
            if any(name in differences for name in ("subject_iri", "predicate_iri", "old_lexical")):
                return "WRONG_ONTOLOGY_TARGET_REPAIR", ",".join(differences)
            return "WRONG_REPAIR_IR", ",".join(differences)
        return "OTHER_REPAIR_FAILURE", detail["failure_stage"]

    if as_bool(detail["ses_success"]):
        return "CORRECT", f"Correct safety decision: {gold_decision}"
    if raw_decision == "REPAIR":
        if gold_decision == "NO_CHANGE":
            return "UNSAFE_FALSE_REPAIR_NO_CHANGE", "Gold NO_CHANGE but model emitted REPAIR"
        return "UNSAFE_FALSE_REPAIR_ABSTAIN", "Gold ABSTAIN but model emitted REPAIR"
    if gold_decision == "NO_CHANGE" and raw_decision == "ABSTAIN":
        return "NO_CHANGE_AS_ABSTAIN", "Safe non-execution but wrong decision label"
    return "OTHER_SAFETY_DECISION_FAILURE", f"Gold {gold_decision}, predicted {raw_decision}"


def build_attempt_rows(
    details: list[dict[str, str]], benchmark: Path, run_dir: Path, golds: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for detail in details:
        event_id = detail["event_id"]
        gold = golds[event_id]
        evidence = (benchmark / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
        payload_path = raw_path(run_dir, detail)
        payload = json.loads(payload_path.read_text(encoding="utf-8-sig")) if payload_path.is_file() else {}
        raw_ir = payload.get("predicted_ir_raw", {}) if isinstance(payload.get("predicted_ir_raw", {}), dict) else {}
        canonical_raw = canonicalize_surface(raw_ir)
        raw_decision = str(canonical_raw.get("decision", "ABSTAIN"))
        resolution = resolve_source_window(raw_ir, evidence)
        resolved_ir = resolution.get("canonical_ir")
        if not isinstance(resolved_ir, dict):
            resolved_ir = canonical_raw
        differences = target_differences(resolved_ir, gold) if raw_decision == "REPAIR" else []
        matching_windows = resolution.get("matching_window_ids", [])
        gold_window = gold.get("gold_source_window", "")
        normalized_matches = {normalize_window_id(value) for value in matching_windows}
        gold_window_recalled = bool(gold_window and normalize_window_id(gold_window) in normalized_matches)
        attribution, rationale = primary_attribution(detail, raw_decision, resolution, differences, gold)
        spans = canonical_raw.get("evidence_spans", [])
        first_span = spans[0] if isinstance(spans, list) and spans and isinstance(spans[0], str) else ""
        rows.append({
            **detail,
            "raw_model_decision": raw_decision,
            "gold_source_window": gold_window,
            "predicted_matching_windows": json.dumps(matching_windows, ensure_ascii=False),
            "gold_window_recalled": gold_window_recalled,
            "old_value_mismatch": "old_lexical" in differences or "old_datatype" in differences,
            "ontology_iri_mismatch": "subject_iri" in differences or "predicate_iri" in differences,
            "replacement_value_mismatch": "new_lexical" in differences or "new_datatype" in differences,
            "field_differences": json.dumps(differences, ensure_ascii=False),
            "primary_attribution": attribution,
            "attribution_rationale": rationale,
            "predicted_evidence_span": first_span,
            "gold_new_lexical": nested(gold, "replacement", "new_value", "lexical") or "",
            "predicted_new_lexical": nested(resolved_ir, "replacement", "new_value", "lexical") or "",
            "gold_subject_iri": nested(gold, "target", "subject_iri") or "",
            "predicted_subject_iri": nested(resolved_ir, "target", "subject_iri") or "",
            "gold_predicate_iri": nested(gold, "target", "predicate_iri") or "",
            "predicted_predicate_iri": nested(resolved_ir, "target", "predicate_iri") or "",
        })
    return rows


def summarize_attempts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        keys = (
            ("ALL", "ALL", "ALL"),
            ("PARTITION", row["partition"], "ALL"),
            ("SEMANTIC_TYPE", row["semantic_type"], row["partition"]),
            ("DOMAIN", row["domain"], row["partition"]),
            ("GOLD_WINDOW", row["gold_source_window"] or "NONE", row["partition"]),
        )
        for group_kind, group_value, partition in keys:
            groups[(group_kind, group_value, partition, row["primary_attribution"])].append(row)
    output: list[dict[str, Any]] = []
    for (kind, value, partition, attribution), items in sorted(groups.items()):
        denominator = sum(
            len(group_items)
            for (k, v, p, _), group_items in groups.items()
            if k == kind and v == value and p == partition
        )
        output.append({
            "group_kind": kind,
            "group_value": value,
            "partition": partition,
            "primary_attribution": attribution,
            "attempts": len(items),
            "share_within_group": len(items) / denominator if denominator else 0,
            "events": len({row["event_id"] for row in items}),
        })
    return output


def build_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(row)
    output: list[dict[str, Any]] = []
    for event_id, items in sorted(grouped.items()):
        counts = Counter(row["primary_attribution"] for row in items)
        success_runs = sum(row["primary_attribution"] == "CORRECT" for row in items)
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
            "strict_success": success_runs == len(items),
            "zero_success": success_runs == 0,
            "dominant_attribution": dominant,
            "dominant_attribution_runs": dominant_count,
            "attribution_counts": json.dumps(dict(sorted(counts.items())), ensure_ascii=False),
        })
    return output


def select_trace_sample(event_rows: list[dict[str, Any]], attempt_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred = (
        "CORRECT",
        "OVER_ABSTENTION",
        "OLD_VALUE_PRECONDITION_MISMATCH",
        "GAMMA_APPLY_PRECONDITION_FAILURE",
        "WRONG_EVIDENCE_WINDOW",
        "UNSAFE_FALSE_REPAIR_NO_CHANGE",
        "UNSAFE_FALSE_REPAIR_ABSTAIN",
    )
    selected_events: list[str] = []
    for attribution in preferred:
        candidates = [row for row in event_rows if row["dominant_attribution"] == attribution]
        candidates.sort(key=lambda row: (-int(row["dominant_attribution_runs"]), row["event_id"]))
        for row in candidates[:4]:
            if row["event_id"] not in selected_events:
                selected_events.append(row["event_id"])
    if len(selected_events) < 25:
        for row in sorted(event_rows, key=lambda item: (int(item["success_runs"]), item["event_id"])):
            if row["event_id"] not in selected_events:
                selected_events.append(row["event_id"])
            if len(selected_events) == 25:
                break
    selected = set(selected_events[:25])
    return [row for row in attempt_rows if row["event_id"] in selected]


def write_report(output: Path, attempts: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    repair = [row for row in attempts if row["partition"] == "REPAIR"]
    safety = [row for row in attempts if row["partition"] == "SAFETY"]
    repair_counts = Counter(row["primary_attribution"] for row in repair)
    safety_counts = Counter(row["primary_attribution"] for row in safety)
    zero_repair = sum(row["partition"] == "REPAIR" and bool(row["zero_success"]) for row in events)
    lines = [
        "# v6.1 Direct-IR Post-hoc Failure Analysis",
        "",
        "> Status: exploratory diagnostic analysis. The frozen v6.1 result remains the independent blind result; any method informed by this report is a new V3 method and requires a new hold-out.",
        "",
        "## Repair attempts",
        "",
        f"- Attempts: {len(repair)}",
        f"- Successful SES: {sum(row['primary_attribution'] == 'CORRECT' for row in repair)}",
        f"- Repair events with zero successful runs: {zero_repair}",
        "",
        "| Attribution | Attempts | Share | Events |",
        "|---|---:|---:|---:|",
    ]
    for label, count in repair_counts.most_common():
        event_count = len({row["event_id"] for row in repair if row["primary_attribution"] == label})
        lines.append(f"| {label} | {count} | {count / len(repair):.2%} | {event_count} |")
    lines.extend(["", "## Safety attempts", "", "| Attribution | Attempts | Share | Events |", "|---|---:|---:|---:|"])
    for label, count in safety_counts.most_common():
        event_count = len({row["event_id"] for row in safety if row["primary_attribution"] == label})
        lines.append(f"| {label} | {count} | {count / len(safety):.2%} | {event_count} |")
    lines.extend([
        "",
        "## V3 priorities",
        "",
        "1. Audit candidate-blind target answerability before changing the method. The companion audit checks whether public metadata uniquely identifies the adjudicated claim/window.",
        "2. Add an explicit evidence-grounded three-way decision layer for REPAIR / NO_CHANGE / ABSTAIN; the current generator never emitted NO_CHANGE.",
        "3. Replace first-span single-window resolution with order-invariant multi-window evidence aggregation and explicit source-window attribution.",
        "4. Ground subject, predicate, old value, and datatype against the public current ontology before Gamma; reject unresolved targets without inventing values.",
        "5. Calibrate selective execution only after the preceding changes. Do not lower the current threshold on v6.1 because selected-repair risk is already high.",
        "6. Keep Gamma frozen. Current evidence does not identify deterministic compilation as the primary bottleneck.",
        "",
        "## Output files",
        "",
        "- `attempt-failure-attribution.csv`: complete 1500-attempt chain with field-level differences.",
        "- `event-root-cause-summary.csv`: one row per event with five-run stability and dominant cause.",
        "- `failure-attribution-summary.csv`: grouped counts by partition, semantic type, domain, and Gold window.",
        "- `trace-sample-25-events.csv`: stratified 25-event trace sample (all five runs retained).",
    ])
    (output / "failure-analysis-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    summary = summarize_attempts(attempts)
    sample = select_trace_sample(events, attempts)
    write_csv(args.output_dir / "attempt-failure-attribution.csv", attempts)
    write_csv(args.output_dir / "event-root-cause-summary.csv", events)
    write_csv(args.output_dir / "failure-attribution-summary.csv", summary)
    write_csv(args.output_dir / "trace-sample-25-events.csv", sample)
    write_report(args.output_dir, attempts, events)
    print(json.dumps({
        "attempts": len(attempts),
        "events": len(events),
        "repair_attribution": dict(Counter(row["primary_attribution"] for row in attempts if row["partition"] == "REPAIR")),
        "safety_attribution": dict(Counter(row["primary_attribution"] for row in attempts if row["partition"] == "SAFETY")),
        "output_dir": str(args.output_dir),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
