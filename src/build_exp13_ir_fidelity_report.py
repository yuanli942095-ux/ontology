from __future__ import annotations

"""Score Exp13 GRE/CSS IR fidelity vs human gold; link temporal audit."""

import argparse
import json
import re
from pathlib import Path
from typing import Any

from exp9_baseline_common import ECR_CONTROL_DIR
from m15_semantic_audit_normalize import parse_ir_payload
from paper_final_validation_common import PAPER_VALIDATION_ROOT, read_csv, utc_now_iso, write_summary_json
from semantic_v2_common import PROJECT_DIR, write_csv


EXP13_ROOT = PAPER_VALIDATION_ROOT / "13-ir-fidelity"
TEMPORAL_GOLD = PAPER_VALIDATION_ROOT / "02-m15-semantic-audit" / "gold-temporal-anchors.csv"
M13_DETAILS = (
    ECR_CONTROL_DIR
    / "m13-pilot"
    / "arm-d-rule-refinement"
    / "ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
M14_FULL = ECR_CONTROL_DIR / "m14-full-holdout-combined" / "m14-full-holdout-combined-full-details.csv"
M15_DETAILS = (
    ECR_CONTROL_DIR / "m15-temporal-anchor-recovery" / "ir" / "m15-temporal-anchor-recovery-v4-ir-details.csv"
)

GRE_GOLD_FIELDS = [
    "general_rule",
    "exception_trigger",
    "exception_rule",
    "priority_relation",
    "result",
]
CSS_GOLD_FIELDS = ["qualifier", "scope_target", "scope_relation", "result"]
GRE_IR_MAP = {
    "general_rule": ("statement", "result"),
    "exception_trigger": ("exception_condition",),
    "exception_rule": ("result",),
    "priority_relation": ("priority", "relation"),
    "result": ("result",),
}
CSS_IR_MAP = {
    "qualifier": ("exception_condition", "statement"),
    "scope_target": ("subject",),
    "scope_relation": ("scope_relation", "relation"),
    "result": ("result",),
}


def norm(text: str) -> str:
    text = str(text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def token_recall(gold: str, candidate: str) -> float:
    g_tokens = {t for t in norm(gold).split() if len(t) > 2}
    c_tokens = {t for t in norm(candidate).split() if len(t) > 2}
    if not g_tokens:
        return 1.0
    if not c_tokens:
        return 0.0
    return len(g_tokens & c_tokens) / len(g_tokens)


def ir_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    parts: list[str] = []
    for key in keys:
        val = payload.get(key, "")
        if isinstance(val, list):
            parts.extend(str(x) for x in val)
        elif val:
            parts.append(str(val))
    return " ".join(parts)


def slot_status(gold_val: str, ir_val: str, threshold: float = 0.55) -> str:
    gold_val = str(gold_val or "").strip()
    ir_val = str(ir_val or "").strip()
    if not gold_val:
        return "n/a"
    if not ir_val:
        return "missing"
    gn, in_ = norm(gold_val), norm(ir_val)
    if gn in in_ or in_ in gn or token_recall(gold_val, ir_val) >= threshold:
        return "correct"
    return "incorrect"


def details_index(path: Path, run: str = "1") -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in read_csv(path):
        if str(row.get("run", "")) == run:
            out[row["event_id"]] = row
    return out


def score_gold_sheet(
    gold_rows: list[dict[str, str]],
    details: dict[str, dict[str, str]],
    field_map: dict[str, tuple[str, ...]],
    gold_fields: list[str],
    stage: str,
) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    for gold in gold_rows:
        event_id = gold["event_id"]
        detail = details.get(event_id)
        payload = parse_ir_payload(detail.get("ir_json", "")) if detail else {}
        row_out: dict[str, Any] = {"event_id": event_id, "stage": stage}
        statuses: list[str] = []
        for field in gold_fields:
            gold_val = gold.get(field, "")
            if not str(gold_val).strip() and field in ("exception_trigger", "exception_rule", "priority_relation"):
                status = "n/a"
            else:
                ir_val = ir_text(payload, field_map.get(field, (field,)))
                status = slot_status(gold_val, ir_val)
            row_out[f"{field}_status"] = status
            if status != "n/a":
                statuses.append(status)
        critical = [f for f in gold_fields if str(gold.get(f, "")).strip()]
        complete = all(row_out.get(f"{f}_status") == "correct" for f in critical)
        row_out["complete_frame"] = complete
        populated = [s for s in statuses if s != "n/a"]
        row_out["cmr"] = sum(1 for s in statuses if s == "missing") / len(critical) if critical else 0.0
        row_out["slot_correct_rate"] = sum(1 for s in populated if s == "correct") / len(populated) if populated else 0.0
        rows_out.append(row_out)
    return rows_out


def aggregate_metrics(rows: list[dict[str, Any]], gold_fields: list[str]) -> dict[str, Any]:
    n = len(rows)
    complete = sum(1 for r in rows if r.get("complete_frame"))
    cmr = sum(float(r.get("cmr", 0)) for r in rows) / n if n else 0.0
    slot_acc = sum(float(r.get("slot_correct_rate", 0)) for r in rows) / n if n else 0.0
    per_field: dict[str, Any] = {}
    for field in gold_fields:
        statuses = [r.get(f"{field}_status", "") for r in rows]
        scored = [s for s in statuses if s != "n/a"]
        per_field[field] = {
            "correct": sum(1 for s in scored if s == "correct"),
            "incorrect": sum(1 for s in scored if s == "incorrect"),
            "missing": sum(1 for s in scored if s == "missing"),
            "n/a": sum(1 for s in statuses if s == "n/a"),
        }
    return {
        "events": n,
        "complete_frame_accuracy": complete / n if n else 0.0,
        "mean_critical_missing_rate": cmr,
        "mean_slot_correct_rate": slot_acc,
        "per_field": per_field,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=EXP13_ROOT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    gre_gold = read_csv(args.output_dir / "gold-gre-ir-sheet.csv")
    css_gold = read_csv(args.output_dir / "gold-cross-sentence-ir-sheet.csv")
    m13 = details_index(M13_DETAILS)
    m14 = details_index(M14_FULL)

    gre_m13 = score_gold_sheet(gre_gold, m13, GRE_IR_MAP, GRE_GOLD_FIELDS, "m13")
    gre_m14 = score_gold_sheet(gre_gold, m14, GRE_IR_MAP, GRE_GOLD_FIELDS, "m13+m14")
    css_m13 = score_gold_sheet(css_gold, m13, CSS_IR_MAP, CSS_GOLD_FIELDS, "m13")
    css_m14 = score_gold_sheet(css_gold, m14, CSS_IR_MAP, CSS_GOLD_FIELDS, "m13+m14")

    write_csv(args.output_dir / "gre-fidelity-m13.csv", gre_m13)
    write_csv(args.output_dir / "gre-fidelity-m13-m14.csv", gre_m14)
    write_csv(args.output_dir / "css-fidelity-m13.csv", css_m13)
    write_csv(args.output_dir / "css-fidelity-m13-m14.csv", css_m14)

    gre_m13_agg = aggregate_metrics(gre_m13, GRE_GOLD_FIELDS)
    gre_m14_agg = aggregate_metrics(gre_m14, GRE_GOLD_FIELDS)
    css_m13_agg = aggregate_metrics(css_m13, CSS_GOLD_FIELDS)
    css_m14_agg = aggregate_metrics(css_m14, CSS_GOLD_FIELDS)

    temporal_note = ""
    if TEMPORAL_GOLD.is_file():
        temporal_note = str(TEMPORAL_GOLD.relative_to(PROJECT_DIR))
    m15_audit = PAPER_VALIDATION_ROOT / "02-m15-semantic-audit" / "m15-semantic-audit-summary.json"

    report = [
        "# Exp13 Semantic IR Fidelity Report",
        "",
        f"Generated: {utc_now_iso()}",
        "",
        "Scoring: human gold vs M13 / M13+M14 IR (`run=1`), deterministic normalized token recall ≥ 0.55.",
        "",
        "## GRE (30 events)",
        "",
        f"- M13 complete-frame accuracy: **{gre_m13_agg['complete_frame_accuracy']:.1%}**",
        f"- M13+M14 complete-frame accuracy: **{gre_m14_agg['complete_frame_accuracy']:.1%}**",
        f"- M13 mean slot correct rate: **{gre_m13_agg['mean_slot_correct_rate']:.1%}**",
        f"- M13+M14 mean slot correct rate: **{gre_m14_agg['mean_slot_correct_rate']:.1%}**",
        "",
        "## Cross-Sentence (30 events)",
        "",
        f"- M13 complete-frame accuracy: **{css_m13_agg['complete_frame_accuracy']:.1%}**",
        f"- M13+M14 complete-frame accuracy: **{css_m14_agg['complete_frame_accuracy']:.1%}**",
        f"- M13 mean slot correct rate: **{css_m13_agg['mean_slot_correct_rate']:.1%}**",
        f"- M13+M14 mean slot correct rate: **{css_m14_agg['mean_slot_correct_rate']:.1%}**",
        "",
        "## Temporal (88 events)",
        "",
        f"- Gold: `{temporal_note}`",
        f"- See `02-m15-semantic-audit/` for M13 vs M15 temporal anchor audit (existing run).",
        "",
    ]
    if m15_audit.is_file():
        audit = json.loads(m15_audit.read_text(encoding="utf-8"))
        report.append(f"- Cached temporal summary keys: {', '.join(sorted(audit.keys())[:8])}...")
    (args.output_dir / "ir-fidelity-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    summary = {
        "completed_at_utc": utc_now_iso(),
        "gre_m13": gre_m13_agg,
        "gre_m13_m14": gre_m14_agg,
        "css_m13": css_m13_agg,
        "css_m13_m14": css_m14_agg,
        "temporal_gold": temporal_note,
        "run": "1",
        "details_m13": str(M13_DETAILS.relative_to(PROJECT_DIR)),
        "details_m14_full": str(M14_FULL.relative_to(PROJECT_DIR)),
    }
    write_summary_json(args.output_dir / "ir-fidelity-results.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
