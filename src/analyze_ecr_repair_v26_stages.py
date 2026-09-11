from __future__ import annotations

"""Mutually exclusive stage attribution for the V2.6 80-event development regression."""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ecr_repair_v26 import align_target_frames, normalize, validate_frame_bundle
from run_ecr_repair_v26_development import BENCHMARK, OUTPUT, inject_frozen_metadata, jsonl, source_metadata, window_document_maps


LABELS = {
    "SUCCESS": "SUCCESS",
    "FRAME_RULE_MISSED": "1_FRAME_RULE_MISSED",
    "TARGET_FRAME_ALIGNMENT_ERROR": "2_TARGET_FRAME_ALIGNMENT_ERROR",
    "POLICY_APPLICABILITY_SCOPE_ERROR": "3_TIME_CONDITION_EXCEPTION_COMPARATOR_ERROR",
    "VALUE_LITERAL_MISMATCH": "4_FRAME_VALUE_LITERAL_MISMATCH",
    "SAFETY_GATE_ERROR": "5_SAFETY_GATE_ERROR",
    "CLOSURE_FAILURE": "6_GAMMA_REASONER_CQ_FAILURE",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def truth(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def main() -> int:
    output = OUTPUT.resolve()
    analysis = output / "stage-attribution"
    analysis.mkdir(parents=True, exist_ok=True)
    events = {row["event_id"]: row for row in jsonl(BENCHMARK / "public/events/events.jsonl")}
    gold = {row["event_id"]: row for row in jsonl(BENCHMARK / "private/oracle/gold.jsonl")}
    staged = {(row["event_id"], row["run"]): row for row in read_csv(output / "v26-staged-decisions.csv")}
    closure = {(row["event_id"], row["run"]): row for row in read_csv(output / "closure-evaluation/v6-final-blind-details.csv")}
    locators, sources = window_document_maps(), source_metadata()
    rows = []
    for key, detail in closure.items():
        event_id, run = key; event, answer, stage = events[event_id], gold[event_id], staged[key]
        raw_path = output / "raw-semantic-frames" / f"{event_id}-run{run}-seed{stage['seed']}.json"
        payload = json.loads(raw_path.read_text(encoding="utf-8-sig"))
        evidence = (BENCHMARK / event["evidence_file"]).read_text(encoding="utf-8")
        enriched = inject_frozen_metadata(payload.get("semantic_frame_raw", {}), event, locators.get(event_id, {}), sources)
        validated = validate_frame_bundle(enriched, evidence)
        grounded = validated["frames"]
        aligned = align_target_frames(grounded, event["target"]["subject_label"], event["target"]["predicate_label"])
        gold_windows = set(answer.get("supporting_window_ids", []))
        grounded_windows = {frame["window_id"] for frame in grounded}
        aligned_windows = {frame["window_id"] for frame in aligned}
        predicted = json.loads(stage.get("predicted_ir_json") or "{}")
        predicted_value = predicted.get("replacement", {}).get("new_value", {}).get("lexical", "")
        gold_value = (answer.get("replacement") or {}).get("new_value", {}).get("lexical", "")

        if truth(detail["ses_success"]): cause = "SUCCESS"
        elif not (grounded_windows & gold_windows): cause = "FRAME_RULE_MISSED"
        elif not (aligned_windows & gold_windows): cause = "TARGET_FRAME_ALIGNMENT_ERROR"
        elif answer["decision"] != "REPAIR" and detail["model_decision"] == "REPAIR": cause = "SAFETY_GATE_ERROR"
        elif answer["decision"] == "REPAIR" and detail["model_decision"] != "REPAIR": cause = "POLICY_APPLICABILITY_SCOPE_ERROR"
        elif answer["decision"] != detail["model_decision"]: cause = "POLICY_APPLICABILITY_SCOPE_ERROR"
        elif answer["decision"] == "REPAIR" and normalize(predicted_value) != normalize(gold_value): cause = "VALUE_LITERAL_MISMATCH"
        elif truth(detail.get("predicted_gamma_ir_exact")) and not truth(detail["ses_success"]): cause = "CLOSURE_FAILURE"
        else: cause = "VALUE_LITERAL_MISMATCH" if answer["decision"] == "REPAIR" else "POLICY_APPLICABILITY_SCOPE_ERROR"

        rows.append({
            "event_id": event_id, "run": run, "domain": event["domain"], "semantic_type": answer["semantic_type"],
            "gold_partition": answer["partition"], "gold_decision": answer["decision"],
            "model_decision": detail["model_decision"], "primary_cause": LABELS[cause],
            "raw_frame_count": len(enriched.get("frames", [])), "grounded_frame_count": len(grounded),
            "aligned_frame_count": len(aligned), "gold_support_windows": ";".join(sorted(gold_windows)),
            "grounded_gold_window_hit": bool(grounded_windows & gold_windows),
            "aligned_gold_window_hit": bool(aligned_windows & gold_windows),
            "predicted_value": predicted_value, "gold_value": gold_value,
            "gamma_exact": truth(detail.get("predicted_gamma_ir_exact")), "gamma_status": detail["gamma_status"],
            "reasoner_status": detail["reasoner_status"], "target_cq_pass": truth(detail["target_cq_pass"]),
            "ses_success": truth(detail["ses_success"]), "original_failure_stage": detail["failure_stage"],
        })
    write_csv(analysis / "v26-stage-attribution-details.csv", rows)

    overall = Counter(row["primary_cause"] for row in rows)
    by_partition: dict[str, Counter] = defaultdict(Counter)
    for row in rows: by_partition[row["gold_partition"]][row["primary_cause"]] += 1
    summary_rows = []
    for group, counts in [("ALL", overall), *sorted(by_partition.items())]:
        total = sum(counts.values())
        for cause in LABELS.values():
            summary_rows.append({"group": group, "primary_cause": cause, "count": counts[cause],
                                 "rate": counts[cause] / total if total else 0})
    write_csv(analysis / "v26-stage-attribution-summary.csv", summary_rows)

    failures = len(rows) - overall["SUCCESS"]
    value_related = overall[LABELS["VALUE_LITERAL_MISMATCH"]]
    recommendation = (
        "KEEP_FRAME_AND_SAFETY_FRONT_END_BUT_RESTORE_INDEPENDENT_FINITE_CANDIDATES_AND_EVIDENCE_ENTAILMENT_RANKING"
        if failures and value_related / failures >= 0.25
        else "KEEP_DYNAMIC_CANDIDATES_AND_PRIORITIZE_UPSTREAM_FRAME_OR_POLICY_REPAIR"
    )
    report = {"status": "DEVELOPMENT_STAGE_ATTRIBUTION", "events": len(rows), "successes": overall["SUCCESS"],
              "failures": failures, "primary_causes": dict(overall), "recommendation": recommendation,
              "interpretation_boundary": "Automated attribution uses adjudicated supporting windows and exact Gold literals; manually review borderline semantic equivalence before publication."}
    (analysis / "v26-stage-attribution-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    event_lists = {cause: [row["event_id"] for row in rows if row["primary_cause"] == cause] for cause in LABELS.values()}
    md = [
        "# V2.6 80-event development stage attribution", "",
        "> Development regression only. The 80 events informed V2.6 design and are not blind confirmatory evidence.", "",
        "## Mutually exclusive primary causes", "",
        "| Stage | Count | Event IDs |", "|---|---:|---|",
    ]
    for cause in LABELS.values():
        ids = ", ".join(event_lists[cause]) or "None"
        md.append(f"| `{cause}` | {overall[cause]} | {ids} |")
    md += [
        "", "## Decision", "",
        "Retain the candidate-independent locator, semantic-frame representation, deterministic applicability/conflict front end, and three-state safety decision.",
        "Restore an independently constructed finite candidate space after the gate, then rank candidates by evidence entailment. Do not compile the free-form frame `value` directly into OWL.",
        "", "## Evidence", "",
        f"- Exact end-to-end successes: {overall['SUCCESS']}/80.",
        f"- Free-form frame value mismatches: {overall[LABELS['VALUE_LITERAL_MISMATCH']]}/80 and {overall[LABELS['VALUE_LITERAL_MISMATCH']]}/{failures} failures.",
        f"- Applicability, condition, exception, or scope comparator errors: {overall[LABELS['POLICY_APPLICABILITY_SCOPE_ERROR']]}/80.",
        f"- Unsafe repairs on safety events: {overall[LABELS['SAFETY_GATE_ERROR']]}/80.",
        f"- Independent Gamma/reasoner/CQ failures after exact operation selection: {overall[LABELS['CLOSURE_FAILURE']]}/80.",
        "", "## Interpretation boundary", "",
        "Supporting-window membership and exact Gold literals are used only by this post-run attribution. Borderline semantic equivalence should receive a separate blinded human review before publication.",
    ]
    (analysis / "V26-STAGE-ATTRIBUTION.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
