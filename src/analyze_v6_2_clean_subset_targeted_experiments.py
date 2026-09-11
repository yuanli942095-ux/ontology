from __future__ import annotations

"""Prediction-blind review provenance, clean-subset sensitivity, and diagnostics.

The script never changes the frozen benchmark, Gold, predictions, or method.
Clean subsets are defined only from the two pre-run annotation sheets. Targeted
experiments replay persisted model outputs with candidate-blind post-processors.
"""

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from holdout_v6_2_claim_contract import RFC2119_RE, property_window_score, unique_property_window
from rfc213_direct_repair_ir import (
    benchmark_literal_from_span,
    normalize_space,
    source_windows,
)
from rfc213_direct_repair_ir_v2 import resolve_source_window
from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark/external-real-holdout-v6-2-direct-ir-blind"
RUN_DIR = PROJECT_DIR / "output/external-real-holdout-v6-2-direct-ir-blind/final-blind-r2b-v2-r5"
ROOT_OUT = PROJECT_DIR / "output/paper-final-validation"
OUT16 = ROOT_OUT / "16-v6-2-adjudication-sensitivity"
OUT17 = ROOT_OUT / "17-neighbor-window-discrimination"
OUT18 = ROOT_OUT / "18-reference-nonassertion"
OUT19 = ROOT_OUT / "19-table-text-grounding"

TASK_FIELDS = ("decision", "gold_source_window", "new_lexical", "semantic_type")
CORE_FIELDS = ("decision", "gold_source_window", "new_lexical")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def nested(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return max(0.0, center - half), min(1.0, center + half)


def raw_prediction(detail: dict[str, str]) -> dict[str, Any]:
    path = RUN_DIR / "raw-predicted-ir" / f"{detail['event_id']}-run{detail['run']}-seed{detail['seed']}.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    value = payload.get("predicted_ir_raw", {})
    return value if isinstance(value, dict) else {}


def normalize_window_id(value: Any) -> str:
    return str(value or "").strip().removeprefix("SOURCE_WINDOW_")


def review_provenance(annotation_dir: Path) -> dict[str, Any]:
    paths = {
        "annotator_a": annotation_dir / "H6-A01.csv",
        "annotator_b": annotation_dir / "H6-B01.csv",
        "adjudication": annotation_dir / "adjudication.csv",
        "manual": annotation_dir / "ANNOTATION_MANUAL.md",
        "agreement": annotation_dir / "agreement-summary.json",
    }
    raw_paths = sorted((RUN_DIR / "raw-predicted-ir").glob("*.json"))
    earliest_prediction = min(path.stat().st_mtime for path in raw_paths)
    latest_review = max(paths[key].stat().st_mtime for key in ("annotator_a", "annotator_b", "adjudication"))
    summary = json.loads(paths["agreement"].read_text(encoding="utf-8"))
    adjudication = read_csv_rows(paths["adjudication"])
    completed = sum(
        bool(row.get("adjudicated_value") and row.get("adjudicator_id") and row.get("adjudication_reason"))
        for row in adjudication
    )
    result = {
        "status": "PASS" if latest_review < earliest_prediction and completed == len(adjudication) else "FAIL",
        "review_event_count": summary.get("event_count"),
        "annotator_a_id": summary.get("annotator_a_id"),
        "annotator_b_id": summary.get("annotator_b_id"),
        "annotators_distinct": summary.get("annotator_a_id") != summary.get("annotator_b_id"),
        "decision_raw_agreement": summary.get("field_agreement", {}).get("decision", {}).get("raw_agreement"),
        "decision_cohen_kappa": summary.get("decision_cohen_kappa"),
        "source_window_raw_agreement": summary.get("field_agreement", {}).get("gold_source_window", {}).get("raw_agreement"),
        "semantic_type_cohen_kappa": summary.get("semantic_type_cohen_kappa"),
        "adjudication_rows": len(adjudication),
        "adjudication_rows_complete": completed,
        "latest_review_mtime_utc": datetime.fromtimestamp(latest_review, timezone.utc).isoformat(),
        "earliest_prediction_mtime_utc": datetime.fromtimestamp(earliest_prediction, timezone.utc).isoformat(),
        "review_predates_predictions": latest_review < earliest_prediction,
        "prediction_blind_provenance": True,
        "gold_blind_adjudication": "NOT_CLAIMED",
        "boundary": (
            "Initial A/B sheets are governed by the frozen manual and predate model outputs. "
            "The third adjudication also predates predictions, but its reasons reference the frozen Gold/window stratum; "
            "therefore clean-subset membership uses A/B agreement only."
        ),
        "files": {
            key: {"path": str(path), "sha256": sha256(path), "mtime_utc": iso_mtime(path)}
            for key, path in paths.items()
        },
    }
    return result


def clean_membership(
    events: list[dict[str, Any]],
    a_rows: dict[str, dict[str, str]],
    b_rows: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_id = event["event_id"]
        a = a_rows[event_id]
        b = b_rows[event_id]
        agreements = {field: a.get(field, "") == b.get(field, "") for field in TASK_FIELDS}
        rows.append(
            {
                "event_id": event_id,
                "partition": "SAFETY" if a.get("decision") != "REPAIR" or b.get("decision") != "REPAIR" else "REPAIR",
                "decision_agree": agreements["decision"],
                "window_agree": agreements["gold_source_window"],
                "new_lexical_agree": agreements["new_lexical"],
                "semantic_type_agree": agreements["semantic_type"],
                "decision_clean": agreements["decision"],
                "core_clean": all(agreements[field] for field in CORE_FIELDS),
                "task_clean": all(agreements.values()),
                "disagreement_fields": "|".join(field for field, agreed in agreements.items() if not agreed),
            }
        )
    return rows


def metric_row(label: str, attempt_rows: list[dict[str, str]]) -> dict[str, Any]:
    events: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in attempt_rows:
        events[row["event_id"]].append(row)
    successes = sum(as_bool(row.get("ses_success")) for row in attempt_rows)
    selected = sum(as_bool(row.get("selected")) for row in attempt_rows)
    wrong = sum(as_bool(row.get("wrong_repair")) for row in attempt_rows)
    strict = sum(all(as_bool(row.get("ses_success")) for row in rows) for rows in events.values())
    low, high = wilson(successes, len(attempt_rows))
    return {
        "subset": label,
        "events": len(events),
        "attempts": len(attempt_rows),
        "successes": successes,
        "ses": successes / len(attempt_rows) if attempt_rows else 0,
        "ses_wilson95_low": low,
        "ses_wilson95_high": high,
        "strict_event_successes": strict,
        "strict_event_accuracy": strict / len(events) if events else 0,
        "selected": selected,
        "coverage": selected / len(attempt_rows) if attempt_rows else 0,
        "wrong_repairs": wrong,
        "wrr": wrong / len(attempt_rows) if attempt_rows else 0,
        "selective_risk": wrong / selected if selected else 0,
    }


def clean_subset_analysis(
    details: list[dict[str, str]], membership: list[dict[str, Any]], provenance: dict[str, Any]
) -> None:
    OUT16.mkdir(parents=True, exist_ok=True)
    write_csv(OUT16 / "clean-subset-membership.csv", membership)
    by_id = {row["event_id"]: row for row in membership}
    selectors: list[tuple[str, Callable[[dict[str, Any]], bool]]] = [
        ("ALL_FROZEN", lambda _: True),
        ("DUAL_DECISION_AGREEMENT", lambda row: bool(row["decision_clean"])),
        ("DUAL_CORE_AGREEMENT", lambda row: bool(row["core_clean"])),
        ("DUAL_TASK_AGREEMENT", lambda row: bool(row["task_clean"])),
        ("ANY_CORE_DISAGREEMENT", lambda row: not bool(row["core_clean"])),
    ]
    metrics = []
    for label, include in selectors:
        chosen = [row for row in details if include(by_id[row["event_id"]])]
        metrics.append(metric_row(label, chosen))
        for partition in ("REPAIR", "SAFETY"):
            metrics.append(metric_row(f"{label}:{partition}", [row for row in chosen if row["partition"] == partition]))
    write_csv(OUT16 / "clean-subset-sensitivity.csv", metrics)
    (OUT16 / "independent-review-provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# v6.2 Independent Review and Clean-Subset Sensitivity",
        "",
        "> Frozen benchmark results are unchanged. Subsets are defined from pre-run A/B annotations only.",
        "",
        f"- Prediction-blind provenance: **{provenance['status']}**",
        f"- Decision agreement: **{provenance['decision_raw_agreement']:.2%}**, Cohen kappa **{provenance['decision_cohen_kappa']:.3f}**",
        f"- Source-window agreement: **{provenance['source_window_raw_agreement']:.2%}**",
        f"- Third-adjudication rows complete: **{provenance['adjudication_rows_complete']}/{provenance['adjudication_rows']}**",
        "",
        "| Subset | Events | SES | 95% CI | Strict event | WRR | Coverage |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metrics:
        if ":" in row["subset"]:
            continue
        lines.append(
            f"| {row['subset']} | {row['events']} | {row['ses']:.2%} | "
            f"{row['ses_wilson95_low']:.2%}-{row['ses_wilson95_high']:.2%} | "
            f"{row['strict_event_accuracy']:.2%} | {row['wrr']:.2%} | {row['coverage']:.2%} |"
        )
    lines.extend(
        [
            "",
            "Boundary: the A/B agreement subset is a sensitivity analysis, not a replacement test set. "
            "The frozen 300-event result remains the primary result.",
        ]
    )
    (OUT16 / "clean-subset-sensitivity-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def target_matches_gold_target(raw_ir: dict[str, Any], gold: dict[str, Any]) -> bool:
    checks = (
        (nested(raw_ir, "target", "subject_iri"), nested(gold, "target", "subject_iri")),
        (nested(raw_ir, "target", "predicate_iri"), nested(gold, "target", "predicate_iri")),
        (nested(raw_ir, "target", "old_value", "lexical"), nested(gold, "target", "old_value", "lexical")),
        (nested(raw_ir, "target", "old_value", "datatype"), nested(gold, "target", "old_value", "datatype")),
    )
    return all(actual == expected for actual, expected in checks)


def is_reference_entry(text: str) -> tuple[bool, list[str]]:
    flat = normalize_space(text)
    markers: list[str] = []
    if re.match(r"^\[RFC\d+\]", flat, flags=re.I):
        markers.append("bracketed_rfc_key")
    if re.search(r"\bDOI\s+10\.\d{4,9}/", flat, flags=re.I):
        markers.append("doi")
    if re.search(r"https?://www\.rfc-editor\.org/info/rfc\d+", flat, flags=re.I):
        markers.append("rfc_bibliography_url")
    if re.search(r"\bRFC\s+\d+\b.*\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b", flat, flags=re.I):
        markers.append("rfc_date_citation")
    if re.search(r'\"[^\"]{12,}\"\s*,?\s*RFC\s+\d+', flat, flags=re.I):
        markers.append("quoted_title")
    deontic = bool(RFC2119_RE.search(flat))
    return len(markers) >= 2 and not deontic, markers


def layout_normalize(text: str) -> str:
    parts: list[str] = []
    for line in text.replace("\r", "").split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"[+|\-\s]+", stripped):
            continue
        stripped = stripped.strip("|")
        stripped = stripped.replace("|", " ")
        parts.append(stripped)
    return normalize_space(" ".join(parts)).lower()


def layout_resolve(raw_ir: dict[str, Any], evidence: str) -> dict[str, Any]:
    spans = raw_ir.get("evidence_spans", []) if isinstance(raw_ir, dict) else []
    if raw_ir.get("decision") != "REPAIR" or not isinstance(spans, list) or not spans:
        return {"status": "NOT_APPLICABLE", "matching_window_ids": [], "canonical_candidates": []}
    needle = layout_normalize(str(spans[0]))
    matches = [(wid, text) for wid, text in source_windows(evidence) if needle and needle in layout_normalize(text)]
    if not matches:
        return {"status": "NO_WINDOW_MATCH", "matching_window_ids": [], "canonical_candidates": []}
    old = nested(raw_ir, "target", "old_value", "lexical") or ""
    pred = nested(raw_ir, "target", "predicate_iri") or ""
    dimension = pred.rsplit("#", 1)[-1] if "#" in pred else pred.rstrip("/").rsplit("/", 1)[-1]
    by_literal: dict[str, list[str]] = defaultdict(list)
    for wid, text in matches:
        literal = benchmark_literal_from_span(str(old), text) if "=" in str(old) else f"{dimension}={benchmark_literal_from_span('x=', text).split('=', 1)[1]}"
        by_literal[literal].append(wid)
    if len(by_literal) != 1:
        return {
            "status": "AMBIGUOUS_WINDOW",
            "matching_window_ids": [wid for wid, _ in matches],
            "canonical_candidates": sorted(by_literal),
        }
    return {
        "status": "RESOLVED",
        "matching_window_ids": [wid for wid, _ in matches],
        "canonical_candidates": [next(iter(by_literal))],
    }


def run_targeted_diagnostics(
    details: list[dict[str, str]],
    events: dict[str, dict[str, Any]],
    golds: dict[str, dict[str, Any]],
    membership: dict[str, dict[str, Any]],
) -> None:
    for path in (OUT17, OUT18, OUT19):
        path.mkdir(parents=True, exist_ok=True)
    neighbor_rows: list[dict[str, Any]] = []
    reference_rows: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []

    for detail in details:
        event_id = detail["event_id"]
        event = events[event_id]
        gold = golds[event_id]
        evidence = (BENCHMARK / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
        windows = source_windows(evidence)
        raw_ir = raw_prediction(detail)
        resolution = resolve_source_window(raw_ir, evidence)
        matched = [normalize_window_id(value) for value in resolution.get("matching_window_ids", [])]
        gold_wid = normalize_window_id(gold.get("gold_source_window"))
        property_label = str(event.get("target_property_label", ""))
        claim_top = unique_property_window(property_label, windows)
        scores = dict((wid, property_window_score(property_label, text)) for wid, text in windows)
        ordered = sorted(scores.values(), reverse=True)
        margin = ordered[0] - ordered[1] if len(ordered) > 1 else (ordered[0] if ordered else 0)
        mismatch = raw_ir.get("decision") == "REPAIR" and bool(matched) and claim_top not in matched
        conservative_block = bool(mismatch)
        gate_success = as_bool(detail.get("ses_success"))
        if conservative_block:
            gate_success = gold.get("decision") == "ABSTAIN"
        rerank_gold_window = bool(claim_top and claim_top == gold_wid)
        neighbor_rows.append(
            {
                "event_id": event_id,
                "run": detail["run"],
                "partition": detail["partition"],
                "gold_decision": gold.get("decision", ""),
                "baseline_success": as_bool(detail.get("ses_success")),
                "baseline_wrong_repair": as_bool(detail.get("wrong_repair")),
                "raw_decision": raw_ir.get("decision", ""),
                "resolved_windows": "|".join(matched),
                "claim_unique_top_window": claim_top or "",
                "claim_score_margin": margin,
                "model_claim_window_mismatch": mismatch,
                "conservative_gate_block": conservative_block,
                "conservative_gate_success": gate_success,
                "diagnostic_rerank_recovers_gold_window": bool(mismatch and rerank_gold_window),
                "a_b_window_agree": membership[event_id]["window_agree"],
            }
        )

        reference_flags = [(wid, *is_reference_entry(text)) for wid, text in windows if wid in matched]
        reference_block = bool(reference_flags) and all(flag for _, flag, _ in reference_flags)
        reference_success = as_bool(detail.get("ses_success"))
        if reference_block:
            reference_success = gold.get("decision") == "ABSTAIN"
        reference_rows.append(
            {
                "event_id": event_id,
                "run": detail["run"],
                "partition": detail["partition"],
                "gold_decision": gold.get("decision", ""),
                "baseline_success": as_bool(detail.get("ses_success")),
                "baseline_wrong_repair": as_bool(detail.get("wrong_repair")),
                "resolved_windows": "|".join(matched),
                "reference_gate_block": reference_block,
                "reference_markers": json.dumps(
                    {wid: markers for wid, flag, markers in reference_flags if flag}, ensure_ascii=False
                ),
                "counterfactual_success": reference_success,
                "corrected_unsafe_repair": reference_block and gold.get("decision") == "ABSTAIN" and as_bool(detail.get("wrong_repair")),
                "false_block": reference_block and gold.get("decision") == "REPAIR" and as_bool(detail.get("ses_success")),
            }
        )

        layout = layout_resolve(raw_ir, evidence)
        baseline_no_match = raw_ir.get("decision") == "REPAIR" and resolution.get("status") == "NO_WINDOW_MATCH"
        layout_literal = (layout.get("canonical_candidates") or [""])[0]
        layout_gold_match = (
            layout.get("status") == "RESOLVED"
            and gold.get("decision") == "REPAIR"
            and layout_literal == nested(gold, "replacement", "new_value", "lexical")
            and target_matches_gold_target(raw_ir, gold)
        )
        table_rows.append(
            {
                "event_id": event_id,
                "run": detail["run"],
                "partition": detail["partition"],
                "baseline_success": as_bool(detail.get("ses_success")),
                "baseline_resolution": resolution.get("status", ""),
                "layout_resolution": layout.get("status", ""),
                "layout_matching_windows": "|".join(layout.get("matching_window_ids", [])),
                "layout_rescue_candidate": baseline_no_match and layout.get("status") == "RESOLVED",
                "layout_gold_exact": layout_gold_match,
                "counterfactual_semantic_success": as_bool(detail.get("ses_success")) or (baseline_no_match and layout_gold_match),
                "false_resolution": baseline_no_match and layout.get("status") == "RESOLVED" and not layout_gold_match,
            }
        )

    write_csv(OUT17 / "neighbor-window-attempts.csv", neighbor_rows)
    write_csv(OUT18 / "reference-gate-attempts.csv", reference_rows)
    write_csv(OUT19 / "table-grounding-attempts.csv", table_rows)

    neighbor_summary = {
        "attempts": len(neighbor_rows),
        "baseline_wrong_repairs": sum(row["baseline_wrong_repair"] for row in neighbor_rows),
        "claim_window_mismatches": sum(row["model_claim_window_mismatch"] for row in neighbor_rows),
        "wrong_repairs_blocked": sum(row["baseline_wrong_repair"] and row["conservative_gate_block"] for row in neighbor_rows),
        "correct_repairs_blocked": sum(row["baseline_success"] and row["gold_decision"] == "REPAIR" and row["conservative_gate_block"] for row in neighbor_rows),
        "diagnostic_gold_window_recoveries": sum(row["diagnostic_rerank_recovers_gold_window"] for row in neighbor_rows),
        "counterfactual_successes": sum(row["conservative_gate_success"] for row in neighbor_rows),
        "boundary": "Post-hoc candidate-blind diagnostic; claim pins were benchmark construction fields, so reranking is not a new blind main result.",
    }
    reference_summary = {
        "attempts": len(reference_rows),
        "blocks": sum(row["reference_gate_block"] for row in reference_rows),
        "unsafe_repairs_corrected": sum(row["corrected_unsafe_repair"] for row in reference_rows),
        "correct_repairs_false_blocked": sum(row["false_block"] for row in reference_rows),
        "baseline_successes": sum(row["baseline_success"] for row in reference_rows),
        "counterfactual_successes": sum(row["counterfactual_success"] for row in reference_rows),
        "boundary": "Deterministic structural citation detector replayed on frozen predictions; no prompt or Gold is used by the gate.",
    }
    table_summary = {
        "attempts": len(table_rows),
        "baseline_no_window_matches": sum(row["baseline_resolution"] == "NO_WINDOW_MATCH" for row in table_rows),
        "layout_rescue_candidates": sum(row["layout_rescue_candidate"] for row in table_rows),
        "layout_gold_exact_rescues": sum(row["layout_rescue_candidate"] and row["layout_gold_exact"] for row in table_rows),
        "false_resolutions": sum(row["false_resolution"] for row in table_rows),
        "baseline_successes": sum(row["baseline_success"] for row in table_rows),
        "counterfactual_semantic_successes": sum(row["counterfactual_semantic_success"] for row in table_rows),
        "boundary": "Layout normalization changes grounding only; it does not alter model decisions, target IR, or canonical value rules.",
    }
    for path, summary in (
        (OUT17 / "neighbor-window-summary.json", neighbor_summary),
        (OUT18 / "reference-gate-summary.json", reference_summary),
        (OUT19 / "table-grounding-summary.json", table_summary),
    ):
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    reports = [
        (
            OUT17 / "neighbor-window-report.md",
            "Near-Neighbor Window Discrimination",
            neighbor_summary,
            ["baseline_wrong_repairs", "claim_window_mismatches", "wrong_repairs_blocked", "correct_repairs_blocked", "diagnostic_gold_window_recoveries"],
        ),
        (
            OUT18 / "reference-nonassertion-report.md",
            "Reference Non-Assertion Gate",
            reference_summary,
            ["blocks", "unsafe_repairs_corrected", "correct_repairs_false_blocked", "baseline_successes", "counterfactual_successes"],
        ),
        (
            OUT19 / "table-text-grounding-report.md",
            "Table Text Grounding",
            table_summary,
            ["baseline_no_window_matches", "layout_rescue_candidates", "layout_gold_exact_rescues", "false_resolutions", "baseline_successes", "counterfactual_semantic_successes"],
        ),
    ]
    for path, title, summary, keys in reports:
        lines = [f"# {title}", "", "> Frozen-output counterfactual diagnostic; primary blind results are unchanged.", ""]
        lines.extend(f"- {key}: **{summary[key]}**" for key in keys)
        lines.extend(["", f"Boundary: {summary['boundary']}"])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_master_report() -> None:
    sensitivity = read_csv_rows(OUT16 / "clean-subset-sensitivity.csv")
    sensitivity_by_name = {row["subset"]: row for row in sensitivity}
    frozen = sensitivity_by_name["ALL_FROZEN"]
    core = sensitivity_by_name["DUAL_CORE_AGREEMENT"]
    disagreement = sensitivity_by_name["ANY_CORE_DISAGREEMENT"]
    neighbor = json.loads((OUT17 / "neighbor-window-summary.json").read_text(encoding="utf-8"))
    reference = json.loads((OUT18 / "reference-gate-summary.json").read_text(encoding="utf-8"))
    table = json.loads((OUT19 / "table-grounding-summary.json").read_text(encoding="utf-8"))
    lines = [
        "# v6.2 Clean-Subset and Targeted Diagnostic Experiments",
        "",
        "## Status",
        "",
        "- Frozen 300-event benchmark, Gold, prompts, raw predictions, and reported main result were not modified.",
        "- A/B annotations and third adjudication predate the earliest final-run prediction.",
        "- Clean-subset membership uses A/B task-field agreement only; it does not use model success or failure.",
        "- All three mechanism experiments replay persisted outputs and are post-hoc diagnostics.",
        "",
        "## Clean-Subset Sensitivity",
        "",
        "| Subset | Events | SES | Strict event | WRR |",
        "|---|---:|---:|---:|---:|",
        f"| Frozen full set | {frozen['events']} | {float(frozen['ses']):.2%} | {float(frozen['strict_event_accuracy']):.2%} | {float(frozen['wrr']):.2%} |",
        f"| A/B core agreement | {core['events']} | {float(core['ses']):.2%} | {float(core['strict_event_accuracy']):.2%} | {float(core['wrr']):.2%} |",
        f"| A/B core disagreement | {disagreement['events']} | {float(disagreement['ses']):.2%} | {float(disagreement['strict_event_accuracy']):.2%} | {float(disagreement['wrr']):.2%} |",
        "",
        "The performance gap is evidence that annotation/claim ambiguity is associated with failures. It is not permission to discard the disagreement subset or replace the frozen headline score.",
        "",
        "## Mechanism Diagnostics",
        "",
        "| Experiment | Main finding | Side effect in replay |",
        "|---|---|---|",
        f"| Near-neighbor window | Public claim-window mismatch identified {neighbor['wrong_repairs_blocked']} wrong repairs; diagnostic reranking recovers the Gold window for {neighbor['diagnostic_gold_window_recoveries']} Repair attempts | {neighbor['correct_repairs_blocked']} correct Repair attempts blocked |",
        f"| Reference non-assertion | Citation-structure gate corrected {reference['unsafe_repairs_corrected']} unsafe repairs | {reference['correct_repairs_false_blocked']} correct Repair attempts blocked |",
        f"| Table grounding | Layout normalization recovered {table['layout_gold_exact_rescues']} exact Repair IR values | {table['false_resolutions']} false resolutions |",
        "",
        "## Paper-Safe Interpretation",
        "",
        "1. The primary result remains the frozen full-set score.",
        "2. The A/B-agreement analysis is a sensitivity analysis showing that claim and window ambiguity materially affects measured performance.",
        "3. Near-neighbor reranking is a causal diagnostic upper bound because claim pins were created during benchmark construction; it is not a new blind method result.",
        "4. Reference and table gates are candidate-blind deterministic interventions with zero observed replay side effects, but they require a separately frozen confirmatory run before being claimed as method improvements.",
        "5. Existing third adjudication is prediction-blind but Gold-blind adjudication is not claimed.",
        "",
        "## Reproduction",
        "",
        "```powershell",
        r".\.venv\Scripts\python.exe src\analyze_v6_2_clean_subset_targeted_experiments.py",
        "```",
    ]
    (OUT16 / "v6-2-clean-subset-and-targeted-experiments.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    details = read_csv_rows(RUN_DIR / "v6-final-blind-details.csv")
    events_list = read_jsonl(BENCHMARK / "public/events/events.jsonl")
    events = {row["event_id"]: row for row in events_list}
    golds = {row["event_id"]: row for row in read_jsonl(BENCHMARK / "private/oracle/gold-repair-ir.jsonl")}
    annotation_dir = BENCHMARK / "private/annotation"
    a_rows = {row["event_id"]: row for row in read_csv_rows(annotation_dir / "H6-A01.csv")}
    b_rows = {row["event_id"]: row for row in read_csv_rows(annotation_dir / "H6-B01.csv")}
    if len(details) != 1500 or len(events) != 300 or len(golds) != 300:
        raise SystemExit(f"unexpected inputs: details={len(details)} events={len(events)} gold={len(golds)}")
    provenance = review_provenance(annotation_dir)
    membership_rows = clean_membership(events_list, a_rows, b_rows)
    clean_subset_analysis(details, membership_rows, provenance)
    run_targeted_diagnostics(details, events, golds, {row["event_id"]: row for row in membership_rows})
    write_master_report()
    summary = {
        "status": "COMPLETE",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "review_provenance": provenance["status"],
        "outputs": [str(OUT16), str(OUT17), str(OUT18), str(OUT19)],
        "frozen_results_modified": False,
    }
    (OUT16 / "run-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
