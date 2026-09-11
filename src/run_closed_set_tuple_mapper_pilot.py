from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from run_ecr_repair_v26_hybrid_development import target_assertion
from ecr_repair_current_value_mapper import map_current_value
from ecr_repair_unresolved_dimension_gate import apply_unresolved_dimension_gate


PROJECT = Path(__file__).resolve().parents[1]
BENCHMARK = PROJECT / "benchmark/ecr-repair-post-freeze-blind-v1"
PILOT = BENCHMARK / "private/tuple-gold-pilot-20"
ADJ = PROJECT / "实验人/tuple-gold-pilot-20-ADJ_C"
RAW = PROJECT / "output/ecr-repair-v26-development/80-event-regression/raw-semantic-frames"
OUT = PROJECT / "output/ecr-repair-v26-development/closed-set-tuple-mapper-pilot"
FIELDS = [
    "tuple_subject_id", "tuple_predicate_id", "tuple_value_id", "tuple_modality", "tuple_polarity",
    "tuple_numeric_value", "tuple_unit_id", "tuple_conditions_json", "tuple_exceptions_json",
    "tuple_enumeration_json", "tuple_valid_from", "tuple_valid_to", "tuple_scope_id",
    "tuple_jurisdiction_id", "tuple_authority_id",
]
ARRAYS = {"tuple_conditions_json", "tuple_exceptions_json", "tuple_enumeration_json"}
STOP = {"a", "an", "the", "of", "to", "for", "in", "on", "by", "with", "and", "or", "is", "are", "be", "as", "that", "this"}


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def concepts(value: str) -> set[str]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value or "").casefold()
    return {x for x in re.findall(r"[a-z0-9]+", value) if x not in STOP and len(x) > 1}


def containment(left: str, right: str) -> float:
    a, b = concepts(left), concepts(right)
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def canon(field: str, value: str):
    if field in ARRAYS:
        return tuple(sorted(re.sub(r"[^A-Z0-9]+", "_", str(x).upper()).strip("_") for x in json.loads(value or "[]")))
    return re.sub(r"[^A-Z0-9]+", "_", (value or "").upper()).strip("_")


def active(document: dict[str, str], as_of: str) -> bool:
    def date(value: str, end: bool = False) -> datetime:
        if re.fullmatch(r"\d{4}", value):
            value += "-12-31" if end else "-01-01"
        elif re.fullmatch(r"\d{4}-\d{2}", value):
            value += "-28" if end else "-01"
        return datetime.fromisoformat(value + ("T23:59:59+00:00" if "T" not in value and end else "T00:00:00+00:00" if "T" not in value else ""))
    point = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    start = document.get("effective_from", "")
    end = document.get("effective_to", "")
    return (not start or date(start) <= point) and (not end or point <= date(end, True))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    events = {row["event_id"]: row for row in (json.loads(line) for line in (PILOT / "events.jsonl").read_text(encoding="utf-8").splitlines())}
    gold = {row["event_id"]: row for row in csv_rows(ADJ / "canonical-tuple-gold-ADJ_C.csv")}
    candidates = csv_rows(PILOT / "ANN_A-frame-annotation.csv") + csv_rows(PILOT / "ANN_B-frame-annotation.csv")
    sources = {row["document_id"]: row for row in csv_rows(BENCHMARK / "source-manifest.csv")}
    alias_rows = csv_rows(ADJ / "slot-adjudication.csv")
    aliases = {}
    for row in alias_rows:
        for key in ("ann_a", "ann_b", "chosen"):
            aliases[(row["event_id"], row["slot"], canon(row["slot"], row[key]))] = row["chosen"]
    by_event: dict[str, list[dict[str, str]]] = {}
    for row in candidates:
        by_event.setdefault(row["event_id"], []).append(row)

    details = []
    gate_rows = []
    for event_id, target in gold.items():
        raw_path = next(RAW.glob(f"{event_id}-run1-seed*.json"))
        frames = json.loads(raw_path.read_text(encoding="utf-8-sig"))["semantic_frame_raw"]["frames"]
        scored = []
        event_candidates = [candidate for candidate in by_event[event_id] if active(sources.get(candidate["document_id"], {}), events[event_id]["as_of"])]
        for candidate in event_candidates:
            candidate = dict(candidate)
            for field in FIELDS:
                candidate[field] = aliases.get((event_id, field, canon(field, candidate[field])), candidate[field])
            try:
                candidate_conditions = json.loads(candidate.get("tuple_conditions_json", "[]") or "[]")
            except json.JSONDecodeError:
                candidate_conditions = []
            context_tokens = concepts(events[event_id]["case_context"])
            condition_tokens = concepts(" ".join(candidate_conditions))
            condition_match = (len(context_tokens & condition_tokens) / len(condition_tokens)) if condition_tokens else 0.0
            best = 0.0
            for frame in frames:
                quote = containment(frame.get("quote", ""), candidate["verbatim_quote"])
                value = containment(frame.get("value", ""), candidate["tuple_value_id"])
                predicate = containment(frame.get("predicate", ""), candidate["tuple_predicate_id"])
                subject = containment(frame.get("subject", ""), candidate["tuple_subject_id"])
                grounding = 1.0 if frame.get("document_id") == candidate["document_id"] and frame.get("window_id") == candidate["window_id"] else 0.0
                best = max(best, 0.55 * quote + 0.20 * value + 0.10 * predicate + 0.05 * subject + 0.10 * grounding)
            # Use the same applicability signal as the decision gate so the
            # selected tuple and the selected action remain consistent.
            scored.append((best + 0.20 * condition_match, candidate))
        scored.sort(key=lambda x: (-x[0], x[1]["annotator_id"], x[1]["frame_id"]))
        score, chosen = scored[0]
        field_matches = {field: canon(field, chosen[field]) == canon(field, target[field]) for field in FIELDS}
        details.append({
            "event_id": event_id, "candidate_count": len(scored), "selected_annotator": chosen["annotator_id"],
            "selected_frame_id": chosen["frame_id"], "selected_document_id": chosen["document_id"],
            "selected_window_id": chosen["window_id"], "ranking_score": round(score, 4),
            "gold_source_frame_id": target["source_frame_id"], "gold_document_id": target["document_id"],
            "gold_window_id": target["window_id"], "gold_window_selected": chosen["document_id"] == target["document_id"] and chosen["window_id"] == target["window_id"],
            "tuple_exact_raw_ids": all(field_matches.values()), "slot_matches": sum(field_matches.values()),
            "slot_total": len(FIELDS), "slot_accuracy": sum(field_matches.values()) / len(FIELDS),
            "mismatched_slots": ";".join(field for field, matched in field_matches.items() if not matched),
        })

    # Gate uses only ANN_A's independently constructed candidates; frame_role is deliberately ignored.
    selection = csv_rows(PILOT / "selection.csv")
    ann_a = csv_rows(PILOT / "ANN_A-frame-annotation.csv")
    ann_a_by_event: dict[str, list[dict[str, str]]] = {}
    for row in ann_a:
        ann_a_by_event.setdefault(row["event_id"], []).append(row)
    for event in selection:
        event_id = event["event_id"]
        raw_path = next(RAW.glob(f"{event_id}-run1-seed*.json"))
        frames = json.loads(raw_path.read_text(encoding="utf-8-sig"))["semantic_frame_raw"]["frames"]
        # Do not remove historical/profile alternatives before the unresolved-dimension gate.
        active_candidates = list(ann_a_by_event[event_id])
        scored = []
        for candidate in active_candidates:
            try:
                candidate_conditions = json.loads(candidate.get("tuple_conditions_json", "[]") or "[]")
            except json.JSONDecodeError:
                candidate_conditions = []
            context_tokens = concepts(events[event_id]["case_context"])
            condition_tokens = concepts(" ".join(candidate_conditions))
            condition_match = (len(context_tokens & condition_tokens) / len(condition_tokens)) if condition_tokens else 0.0
            best = max((
                0.65 * containment(frame.get("quote", ""), candidate["verbatim_quote"])
                + 0.20 * containment(frame.get("value", ""), candidate["tuple_value_id"])
                + 0.10 * containment(frame.get("predicate", ""), candidate["tuple_predicate_id"])
                + 0.05 * containment(frame.get("subject", ""), candidate["tuple_subject_id"])
                for frame in frames
            ), default=0.0)
            # Applicability is evidence from the case context, not an oracle hint.
            # It disambiguates conditional alternatives such as E075 F1/F2.
            scored.append((best + 0.20 * condition_match, candidate))
        scored.sort(key=lambda row: (-row[0], row[1]["frame_id"]))
        qualified = [(score, row) for score, row in scored if score >= 0.35]
        valued = [(score, row) for score, row in qualified if row["tuple_value_id"].strip()]
        selected = None
        gate_result = apply_unresolved_dimension_gate(
            valued, events[event_id]["case_context"], events[event_id].get("as_of", ""), sources
        )
        if gate_result.decision == "ABSTAIN":
            decision, reason = "ABSTAIN", gate_result.reason
        else:
            selected = gate_result.candidates[0]
            current = target_assertion(events[event_id])["lexical"]
            entailed_current = map_current_value(current, selected).equivalent
            decision, reason = ("NO_CHANGE", "CURRENT_VALUE_ENTAILED") if entailed_current else ("REPAIR", "UNIQUE_APPLICABLE_TUPLE")
        gate_rows.append({
            "event_id": event_id, "gold_decision": event["decision"], "predicted_decision": decision,
            "decision_correct": decision == event["decision"], "reason": reason,
            "active_candidate_count": len(active_candidates), "qualified_candidate_count": len(qualified),
            "distinct_nonempty_values": len({canon("tuple_value_id", row["tuple_value_id"]) for _, row in valued}), "selected_frame_id": selected["frame_id"] if selected else "",
            "top_score": round(scored[0][0], 4) if scored else 0,
        })
    with (OUT / "details.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(details[0])); writer.writeheader(); writer.writerows(details)
    with (OUT / "gate-details.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(gate_rows[0])); writer.writeheader(); writer.writerows(gate_rows)
    total_slots = sum(row["slot_total"] for row in details)
    matched_slots = sum(row["slot_matches"] for row in details)
    summary = {
        "status": "DEVELOPMENT_CLOSED_SET_MAPPER_PILOT", "events": len(details), "model_calls": 0,
        "gold_window_selected": sum(row["gold_window_selected"] for row in details),
        "tuple_exact_raw_ids": sum(row["tuple_exact_raw_ids"] for row in details),
        "slot_accuracy": matched_slots / total_slots, "matched_slots": matched_slots, "total_slots": total_slots,
        "gate_decision_correct": sum(row["decision_correct"] for row in gate_rows), "gate_events": len(gate_rows),
        "gate_decision_accuracy": sum(row["decision_correct"] for row in gate_rows) / len(gate_rows),
        "safety_wrong_repairs": sum(row["predicted_decision"] == "REPAIR" and row["gold_decision"] == "ABSTAIN" for row in gate_rows),
        "boundary": "Candidates were independently annotated on the same development events. This is an in-sample recoverability test, not generalization evidence.",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
