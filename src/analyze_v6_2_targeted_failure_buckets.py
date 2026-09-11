from __future__ import annotations

"""Targeted failure analysis for the three remaining v6.2 buckets.

Diagnostic only. Does not modify predictions, Gold, prompt, Gamma, or the frozen method.
"""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rfc213_direct_repair_ir import (
    benchmark_literal_from_span,
    normalize_space,
    source_windows,
)
from rfc213_direct_repair_ir_v2 import resolve_source_window
from semantic_v2_common import PROJECT_DIR, write_csv

BENCHMARK = PROJECT_DIR / "benchmark/external-real-holdout-v6-2-direct-ir-blind"
RUN_DIR = PROJECT_DIR / "output/external-real-holdout-v6-2-direct-ir-blind/final-blind-r2b-v2-r5"
OUTPUT = RUN_DIR / "targeted-failure-analysis"


def read_csv_rows(path: Path) -> list[dict[str, str]]:
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


def window_id(value: Any) -> str:
    return str(value or "").strip().removeprefix("SOURCE_WINDOW_")


def raw_path(row: dict[str, str]) -> Path:
    return RUN_DIR / "raw-predicted-ir" / f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"


def load_annotators() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    a = {row["event_id"]: row for row in read_csv_rows(BENCHMARK / "private/annotation/H6-A01.csv")}
    b = {row["event_id"]: row for row in read_csv_rows(BENCHMARK / "private/annotation/H6-B01.csv")}
    return a, b


def containing_windows(span: str, windows: list[tuple[str, str]]) -> list[str]:
    needle = normalize_space(span).lower()
    if not needle:
        return []
    return [wid for wid, text in windows if needle in normalize_space(text).lower()]


def gold_window_literal(gold: dict[str, Any], windows: list[tuple[str, str]]) -> str:
    gold_wid = window_id(gold.get("gold_source_window"))
    old = nested(gold, "target", "old_value", "lexical") or ""
    for wid, text in windows:
        if wid == gold_wid:
            if "=" in str(old):
                return benchmark_literal_from_span(str(old), text)
            pred = nested(gold, "target", "predicate_iri") or ""
            dim = pred.rsplit("#", 1)[-1] if "#" in pred else pred.rstrip("/").rsplit("/", 1)[-1]
            return f"{dim}={benchmark_literal_from_span('x=', text).split('=', 1)[1]}"
    return ""


def classify_attempt(
    detail: dict[str, str],
    gold: dict[str, Any],
    raw_ir: dict[str, Any],
    resolution: dict[str, Any],
    windows: list[tuple[str, str]],
    annot_a: dict[str, str],
    annot_b: dict[str, str],
) -> dict[str, Any]:
    event_id = detail["event_id"]
    gold_decision = gold["decision"]
    gold_type = gold["semantic_type"]
    gold_wid = window_id(gold.get("gold_source_window"))
    raw_decision = str(raw_ir.get("decision") or "UNKNOWN")
    final_decision = detail.get("model_decision", "")
    spans = raw_ir.get("evidence_spans") if isinstance(raw_ir.get("evidence_spans"), list) else []
    span = normalize_space(str(spans[0])) if spans else ""
    matched = [window_id(v) for v in resolution.get("matching_window_ids", [])]
    span_windows = containing_windows(span, windows)
    predicted_new = nested(resolution.get("canonical_ir") if isinstance(resolution.get("canonical_ir"), dict) else {}, "replacement", "new_value", "lexical") or ""
    gold_new = nested(gold, "replacement", "new_value", "lexical") or ""
    reconstructed_gold = gold_window_literal(gold, windows)
    annotator_decision_disagree = annot_a.get("decision") != annot_b.get("decision")
    annotator_window_disagree = annot_a.get("gold_source_window") != annot_b.get("gold_source_window")
    gold_literal_inconsistent = bool(gold_wid) and reconstructed_gold and gold_new and reconstructed_gold != gold_new

    user_class = "OTHER"
    mechanism = ""

    if gold_type == "CONFLICTING_EVIDENCE" and as_bool(detail.get("wrong_repair")):
        if annotator_decision_disagree:
            user_class = "DATA_AMBIGUITY"
            mechanism = "Annotators already disagreed on ABSTAIN vs REPAIR; Gold is ABSTAIN but a unique window is executable"
        else:
            user_class = "CONFLICT_JUDGMENT_FAILURE"
            mechanism = "Gold ABSTAIN on conflicting windows, but model uniquely resolved one window and executed REPAIR"
        if not matched:
            user_class = "SEMANTIC_EXTRACTION_ERROR"
            mechanism = "Conflict item executed a repair without a resolved source window"
    elif gold_decision == "REPAIR" and as_bool(detail.get("wrong_repair")):
        if gold_literal_inconsistent:
            user_class = "DATA_AMBIGUITY"
            mechanism = "Gold new_value is not the V2 canonical literal of the Gold window"
        elif resolution["status"] == "AMBIGUOUS_WINDOW":
            user_class = "DATA_AMBIGUITY"
            mechanism = "Predicted span maps to multiple windows with different canonical literals"
        elif gold_wid and gold_wid not in matched:
            if span and gold_wid in span_windows:
                user_class = "SEMANTIC_EXTRACTION_ERROR"
                mechanism = "Span occurs in Gold window and another window; resolver bound the non-Gold unique literal"
            else:
                user_class = "WRONG_WINDOW"
                mechanism = f"Resolved window(s) {matched or ['NONE']} do not include Gold window {gold_wid}"
        elif gold_wid and gold_wid in matched and predicted_new and gold_new and predicted_new != gold_new:
            user_class = "CANONICALIZATION_ERROR"
            mechanism = "Gold window matched but resolved new_value still differs from Gold"
        elif predicted_new == gold_new:
            user_class = "SEMANTIC_EXTRACTION_ERROR"
            mechanism = "Canonical value matches Gold but another IR field or OWL/CQ closure failed"
        else:
            user_class = "WRONG_WINDOW"
            mechanism = f"Selected repair with unresolved Gold-window relationship; status={resolution['status']}"
    elif gold_decision == "REPAIR" and final_decision == "ABSTAIN":
        if raw_decision != "REPAIR":
            if annotator_decision_disagree:
                user_class = "DATA_AMBIGUITY"
                mechanism = "Model abstained; annotators also disagreed on whether this event is REPAIR"
            else:
                user_class = "OVER_ABSTENTION"
                mechanism = "Model itself output ABSTAIN on a Gold REPAIR event"
        elif resolution["status"] == "NO_WINDOW_MATCH":
            user_class = "SEMANTIC_EXTRACTION_ERROR"
            mechanism = "Model tried REPAIR but evidence_span is not a verbatim substring of any frozen window"
        elif resolution["status"] == "AMBIGUOUS_WINDOW":
            if gold_wid in matched and len(set(resolution.get("canonical_candidates") or [])) > 1:
                user_class = "SEMANTIC_EXTRACTION_ERROR"
                mechanism = "Repair span is too coarse and matches multiple windows with different literals"
            else:
                user_class = "WRONG_WINDOW"
                mechanism = "Repair span matched non-unique windows and V2 failed closed to ABSTAIN"
        elif detail.get("schema_status") == "INVALID_IR":
            user_class = "SEMANTIC_EXTRACTION_ERROR"
            mechanism = "Model tried REPAIR but schema/IR was invalid, counted as ABSTAIN"
        else:
            user_class = "OVER_ABSTENTION"
            mechanism = f"Final ABSTAIN after {detail.get('failure_stage') or resolution['status']}"

    return {
        "event_id": event_id,
        "run": detail["run"],
        "seed": detail["seed"],
        "domain": detail["domain"],
        "semantic_type": gold_type,
        "partition": detail["partition"],
        "gold_decision": gold_decision,
        "raw_model_decision": raw_decision,
        "final_model_decision": final_decision,
        "failure_stage": detail.get("failure_stage", ""),
        "schema_status": detail.get("schema_status", ""),
        "window_resolution_status": resolution["status"],
        "selected": as_bool(detail.get("selected")),
        "wrong_repair": as_bool(detail.get("wrong_repair")),
        "ses_success": as_bool(detail.get("ses_success")),
        "gold_source_window": gold.get("gold_source_window", ""),
        "predicted_matching_windows": json.dumps(matched, ensure_ascii=False),
        "span_containing_windows": json.dumps(span_windows, ensure_ascii=False),
        "gold_window_in_match": gold_wid in matched if gold_wid else "",
        "annotator_a_decision": annot_a.get("decision", ""),
        "annotator_b_decision": annot_b.get("decision", ""),
        "annotator_a_window": annot_a.get("gold_source_window", ""),
        "annotator_b_window": annot_b.get("gold_source_window", ""),
        "annotator_decision_disagree": annotator_decision_disagree,
        "annotator_window_disagree": annotator_window_disagree,
        "gold_literal_inconsistent": gold_literal_inconsistent,
        "gold_new_lexical": gold_new,
        "predicted_new_lexical": predicted_new,
        "reconstructed_gold_window_literal": reconstructed_gold,
        "predicted_evidence_span": span,
        "user_class": user_class,
        "mechanism": mechanism,
        "bucket": "",
    }


def main() -> int:
    details = read_csv_rows(RUN_DIR / "v6-final-blind-details.csv")
    golds = {row["event_id"]: row for row in read_jsonl(BENCHMARK / "private/oracle/gold-repair-ir.jsonl")}
    annot_a, annot_b = load_annotators()
    OUTPUT.mkdir(parents=True, exist_ok=True)

    classified: list[dict[str, Any]] = []
    for detail in details:
        gold = golds[detail["event_id"]]
        path = raw_path(detail)
        payload = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        raw_ir = payload.get("predicted_ir_raw", {}) if isinstance(payload.get("predicted_ir_raw"), dict) else {}
        evidence = (BENCHMARK / "public/excerpts" / f"{detail['event_id']}-evidence.md").read_text(encoding="utf-8")
        windows = source_windows(evidence)
        resolution = resolve_source_window(raw_ir, evidence)
        row = classify_attempt(detail, gold, raw_ir, resolution, windows, annot_a[detail["event_id"]], annot_b[detail["event_id"]])
        if gold["semantic_type"] == "CONFLICTING_EVIDENCE" and as_bool(detail.get("wrong_repair")):
            row["bucket"] = "CE_WRONG_REPAIR"
        elif gold["decision"] == "REPAIR" and as_bool(detail.get("wrong_repair")):
            row["bucket"] = "REPAIR_WRONG_REPAIR"
        elif gold["decision"] == "REPAIR" and detail.get("model_decision") == "ABSTAIN":
            row["bucket"] = "REPAIR_ABSTAIN"
        else:
            continue
        classified.append(row)

    write_csv(OUTPUT / "targeted-failure-attempts.csv", classified)

    summaries: list[dict[str, Any]] = []
    event_summaries: list[dict[str, Any]] = []
    for bucket in ("CE_WRONG_REPAIR", "REPAIR_WRONG_REPAIR", "REPAIR_ABSTAIN"):
        items = [row for row in classified if row["bucket"] == bucket]
        class_counts = Counter(row["user_class"] for row in items)
        events = defaultdict(list)
        for row in items:
            events[row["event_id"]].append(row)
        for label, count in class_counts.most_common():
            event_n = len({row["event_id"] for row in items if row["user_class"] == label})
            summaries.append({
                "bucket": bucket,
                "user_class": label,
                "attempts": count,
                "share_of_bucket": count / len(items) if items else 0,
                "events": event_n,
            })
        for event_id, rows in sorted(events.items()):
            counts = Counter(row["user_class"] for row in rows)
            dominant, n = counts.most_common(1)[0]
            event_summaries.append({
                "bucket": bucket,
                "event_id": event_id,
                "domain": rows[0]["domain"],
                "semantic_type": rows[0]["semantic_type"],
                "failed_runs": len(rows),
                "dominant_class": dominant,
                "dominant_runs": n,
                "class_counts": json.dumps(dict(counts), ensure_ascii=False),
                "raw_decisions": json.dumps(dict(Counter(row["raw_model_decision"] for row in rows)), ensure_ascii=False),
                "matched_windows": json.dumps(sorted({tuple(json.loads(row["predicted_matching_windows"])) for row in rows}), ensure_ascii=False),
                "gold_source_window": rows[0]["gold_source_window"],
                "annotator_a_decision": rows[0]["annotator_a_decision"],
                "annotator_b_decision": rows[0]["annotator_b_decision"],
                "annotator_decision_disagree": rows[0]["annotator_decision_disagree"],
                "annotator_window_disagree": rows[0]["annotator_window_disagree"],
                "mechanism": rows[0]["mechanism"],
                "example_span": rows[0]["predicted_evidence_span"][:240],
            })

    write_csv(OUTPUT / "targeted-failure-class-summary.csv", summaries)
    write_csv(OUTPUT / "targeted-failure-events.csv", event_summaries)

    lines = [
        "# v6.2 Targeted Failure Analysis",
        "",
        "> Diagnostic only. Frozen method / Gold / windows / prompt are unchanged.",
        "",
        f"- CE wrong repairs: {sum(1 for r in classified if r['bucket']=='CE_WRONG_REPAIR')}",
        f"- Repair wrong repairs: {sum(1 for r in classified if r['bucket']=='REPAIR_WRONG_REPAIR')}",
        f"- Repair abstains: {sum(1 for r in classified if r['bucket']=='REPAIR_ABSTAIN')}",
        "",
        "## Class counts",
        "",
        "| Bucket | Class | Attempts | Share | Events |",
        "|---|---|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['bucket']} | {row['user_class']} | {row['attempts']} | {row['share_of_bucket']:.1%} | {row['events']} |"
        )
    (OUTPUT / "targeted-failure-analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "output_dir": str(OUTPUT),
        "bucket_sizes": dict(Counter(row["bucket"] for row in classified)),
        "classes": summaries,
        "unique_events": {bucket: len({row['event_id'] for row in classified if row['bucket']==bucket}) for bucket in ("CE_WRONG_REPAIR", "REPAIR_WRONG_REPAIR", "REPAIR_ABSTAIN")},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
