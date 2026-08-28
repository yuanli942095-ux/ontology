from __future__ import annotations

"""Fine-grained triage of V4.3 IR_FAIL_CLOSED attempts.

Reads frozen LIGHT raw files and the V4.3 details table. Does not call Qwen,
does not change gates, and does not run OWL closure. Offline ranking replay is
oracle-only and labeled as such.
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from run_auto_policy_v4_ir_candidate_repair import (
    PROJECT_DIR,
    configure_paths,
    load_candidates,
    load_json,
    parse_semantic_ir,
    read_csv,
    ready,
    select_candidate_v4,
)
from semantic_v2_common import write_csv


DETAILS_CSV = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.3-ir"
    / "auto-policy-v4.3-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
RAW_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-v8-grounded"
    / "preformal-r3"
    / "raw_window_metadata_light"
    / "raw"
)
OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v4.3-ir-fail-closed"
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
NUM_PREDICT = 1000

J_LABELS = {
    "R_RETRIEVAL": "retrieval failed; Qwen never ran",
    "J1": "JSON syntax error",
    "J2": "extra text around JSON",
    "J3": "illegal schema field names",
    "J4": "field type error",
    "J5": "illegal enum value",
    "J6": "output truncated",
    "J7": "semantic present but structure parse failed",
    "J1_J2": "JSON unparseable; raw Qwen text not saved so syntax vs extra-text cannot be split",
}

I_LABELS = {
    "I_ABSTAIN": "model abstained",
    "I_NONCRITICAL": "missing only deterministically completable fields",
    "I_CRITICAL": "missing critical semantic fields; must stay fail-closed",
}

ASSIGNMENT_RE = re.compile(r"\b[a-z][a-z0-9_]{2,}=[a-z0-9][a-z0-9_.-]{1,}\b", re.I)
SEMANTIC_HINT_KEYS = {
    "semantic_result",
    "canonical_result",
    "canonical_semantic_result",
    "new_value",
    "old_value",
    "current_value",
    "change_value",
    "family",
    "result",
    "scope",
    "relation",
}
ALIAS_KEYS = {
    "fact",
    "facts_list",
    "rule",
    "规则",
    "事实",
    "semanticresult",
    "canonicalresult",
    "canonical",
    "policy",
    "output",
}
NONCRITICAL_MISSING = {"relation", "scope_relation", "priority", "statement", "subject"}
CRITICAL_MISSING = {"result", "new_value", "current_value"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="triage V4.3 IR_FAIL_CLOSED attempts")
    parser.add_argument("--details", type=Path, default=DETAILS_CSV)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--replay-rank", action="store_true", default=True)
    parser.add_argument("--no-replay-rank", action="store_false", dest="replay_rank")
    return parser.parse_args()


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def flatten_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            found.extend(flatten_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(flatten_strings(item))
    elif value not in (None, "", [], {}):
        found.append(str(value))
    return found


def flatten_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(str(key))
            keys.extend(flatten_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(flatten_keys(item))
    return keys


def collect_semantic_results(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            compact = str(key).strip().lower().replace("_", "")
            if compact in {"semanticresult", "canonicalsemanticresult", "result"} and item not in (None, "", [], {}):
                if not isinstance(item, (dict, list)):
                    found.append(str(item).strip())
            found.extend(collect_semantic_results(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_semantic_results(item))
    return [item for item in found if item]


def has_assignment_payload(text: str) -> bool:
    return bool(ASSIGNMENT_RE.search(text or ""))


def semantic_payload(response: Any) -> dict[str, Any]:
    strings = flatten_strings(response)
    blob = " ".join(strings)
    results = collect_semantic_results(response)
    keys = {key.lower() for key in flatten_keys(response)}
    return {
        "semantic_results": results,
        "has_semantic_result": bool(results),
        "has_assignment": has_assignment_payload(blob),
        "has_hint_key": bool(keys & SEMANTIC_HINT_KEYS),
        "has_alias_key": bool(keys & ALIAS_KEYS),
        "nonempty_strings": len([item for item in strings if item.strip()]),
        "blob": blob[:500],
    }


def looks_truncated(record: dict[str, Any]) -> bool:
    done = str(record.get("done_reason") or "").strip().lower()
    if done in {"length", "max_tokens", "max_length"}:
        return True
    try:
        eval_count = int(record.get("eval_count") or 0)
    except (TypeError, ValueError):
        eval_count = 0
    return eval_count >= NUM_PREDICT


def classify_invalid(record: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str, str]:
    status = str(record.get("status") or "")
    reason = str(record.get("validation_reason") or "")
    response = record.get("response")
    if status == "RETRIEVAL_FAILED":
        return "R_RETRIEVAL", J_LABELS["R_RETRIEVAL"], "RETRIEVAL_GAP"
    if looks_truncated(record):
        salvage = "FORMAT_REPAIRABLE" if payload["has_semantic_result"] or payload["has_assignment"] else "RAW_OR_TRUNCATED"
        if response in (None, "", {}, []):
            salvage = "RAW_TEXT_LOST" if salvage != "FORMAT_REPAIRABLE" else salvage
        return "J6", J_LABELS["J6"], salvage
    if status == "INVALID_JSON" or response in (None, "") or not isinstance(response, dict):
        return "J1_J2", J_LABELS["J1_J2"], "RAW_TEXT_LOST"
    if reason == "semantic_type_mismatch":
        salvage = "FORMAT_REPAIRABLE" if payload["has_semantic_result"] or payload["has_assignment"] else "TRUE_SEMANTIC_GAP"
        return "J5", J_LABELS["J5"], salvage
    if reason in {"facts_missing", "rules_missing", "not_json_object"} and _is_type_error(response, reason):
        salvage = "FORMAT_REPAIRABLE" if payload["has_semantic_result"] or payload["has_assignment"] or payload["has_hint_key"] else "TRUE_SEMANTIC_GAP"
        return "J4", J_LABELS["J4"], salvage
    if payload["has_alias_key"] and reason in {"facts_missing", "rules_missing", "canonical_result_missing", "semantic_result_missing", "not_json_object"}:
        return "J3", J_LABELS["J3"], "FORMAT_REPAIRABLE"
    if payload["has_semantic_result"] or payload["has_assignment"] or payload["has_hint_key"]:
        return "J7", J_LABELS["J7"], "FORMAT_REPAIRABLE"
    if reason in {"facts_missing", "rules_missing", "canonical_result_missing", "semantic_result_missing"}:
        return "J4", J_LABELS["J4"], "TRUE_SEMANTIC_GAP"
    return "J7", J_LABELS["J7"], "TRUE_SEMANTIC_GAP"


def _is_type_error(response: dict[str, Any], reason: str) -> bool:
    if reason == "facts_missing":
        return "facts" in response and not isinstance(response.get("facts"), dict)
    if reason == "rules_missing":
        return "rules" in response and not isinstance(response.get("rules"), list)
    if reason == "not_json_object":
        return True
    return False


def classify_incomplete(ir_reason: str, fields: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str, str]:
    if ir_reason == "model_abstained":
        if payload["has_semantic_result"] or payload["has_assignment"]:
            return "I_ABSTAIN", I_LABELS["I_ABSTAIN"], "FORMAT_REPAIRABLE"
        return "I_ABSTAIN", I_LABELS["I_ABSTAIN"], "TRUE_SEMANTIC_GAP"
    missing = []
    if ir_reason.startswith("missing:"):
        missing = [part for part in ir_reason.split(":", 1)[1].split("|") if part]
    critical = [key for key in missing if key in CRITICAL_MISSING or key == "result"]
    noncritical = [key for key in missing if key in NONCRITICAL_MISSING]
    if critical:
        return "I_CRITICAL", I_LABELS["I_CRITICAL"] + ":" + "|".join(critical), "TRUE_SEMANTIC_GAP"
    if missing and set(missing) <= set(NONCRITICAL_MISSING):
        salvage = "DETERMINISTIC_COMPLETE" if fields.get("result") or payload["has_semantic_result"] else "TRUE_SEMANTIC_GAP"
        return "I_NONCRITICAL", I_LABELS["I_NONCRITICAL"] + ":" + "|".join(noncritical or missing), salvage
    if not missing and (payload["has_semantic_result"] or payload["has_assignment"]):
        return "I_NONCRITICAL", I_LABELS["I_NONCRITICAL"], "FORMAT_REPAIRABLE"
    return "I_CRITICAL", I_LABELS["I_CRITICAL"], "TRUE_SEMANTIC_GAP"


def salvage_ir(record: dict[str, Any], event: dict[str, str]) -> dict[str, Any]:
    cloned = dict(record)
    if cloned.get("status") in {"INVALID_JSON", "INVALID_SCHEMA"}:
        cloned["status"] = "GENERATED"
    ir = parse_semantic_ir(cloned, event)
    return {
        "salvage_ir_status": ir.ir_status,
        "salvage_ir_reason": ir.ir_reason,
        "salvage_result": str(ir.fields.get("result") or ""),
        "salvage_relation": str(ir.fields.get("relation") or ""),
        "salvage_new_value": str(ir.fields.get("new_value") or ""),
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["bucket"], row["fine_class"])].append(row)
    summary = []
    for (bucket, fine_class), items in sorted(groups.items()):
        summary.append(
            {
                "bucket": bucket,
                "fine_class": fine_class,
                "label": items[0]["fine_label"],
                "n": len(items),
                "format_repairable": sum(item["salvage_family"] == "FORMAT_REPAIRABLE" for item in items),
                "deterministic_complete": sum(item["salvage_family"] == "DETERMINISTIC_COMPLETE" for item in items),
                "raw_text_lost": sum(item["salvage_family"] == "RAW_TEXT_LOST" for item in items),
                "retrieval_gap": sum(item["salvage_family"] == "RETRIEVAL_GAP" for item in items),
                "true_semantic_gap": sum(item["salvage_family"] == "TRUE_SEMANTIC_GAP" for item in items),
                "salvage_ir_ok": sum(item["salvage_ir_status"] == "OK" for item in items),
                "replay_selected": sum(item.get("replay_status") == "SELECTED" for item in items),
                "replay_oracle_correct": sum(as_bool(item.get("replay_oracle_correct")) for item in items),
            }
        )
    return summary


def main() -> int:
    args = parse_args()
    details = [row for row in read_csv(args.details) if row.get("decision_path") == "IR_FAIL_CLOSED"]
    paths = configure_paths(args.benchmark_dir.resolve())
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    oracles = {row["event_id"]: row for row in read_csv(paths["oracle_csv"]) if ready(row.get("status", ""))}
    candidates_by_event = load_candidates(paths["candidate_csv"]) if args.replay_rank else {}

    rows: list[dict[str, Any]] = []
    for detail in details:
        raw_path = PROJECT_DIR / str(detail.get("raw_output_file") or "")
        if not raw_path.is_file():
            raw_path = args.raw_dir / f"{detail['event_id']}-run{detail['run']}-seed{detail['seed']}.json"
        record = load_json(raw_path) if raw_path.is_file() else {}
        event = events[detail["event_id"]]
        payload = semantic_payload(record.get("response"))
        ir_status = detail.get("ir_status", "")
        generation_status = str(record.get("status") or detail.get("generation_status") or "")
        if generation_status == "RETRIEVAL_FAILED" or ir_status == "INVALID_OUTPUT":
            fine_class, fine_label, salvage_family = classify_invalid(record, payload)
            bucket = "RETRIEVAL_FAILED" if fine_class == "R_RETRIEVAL" else "INVALID_OUTPUT"
        else:
            fields = {}
            try:
                fields = json.loads(detail.get("ir_json") or "{}")
            except json.JSONDecodeError:
                fields = {}
            fine_class, fine_label, salvage_family = classify_incomplete(
                str(detail.get("ir_reason") or ""),
                fields if isinstance(fields, dict) else {},
                payload,
            )
            bucket = "INCOMPLETE_IR"
        salvage = salvage_ir(record, event)
        if salvage["salvage_ir_status"] == "OK" and salvage_family in {"RAW_TEXT_LOST", "TRUE_SEMANTIC_GAP"}:
            salvage_family = "FORMAT_REPAIRABLE"
        replay_status = ""
        replay_path = ""
        replay_candidate = ""
        replay_oracle_correct = False
        if args.replay_rank and salvage["salvage_ir_status"] == "OK":
            cloned = dict(record)
            if cloned.get("status") in {"INVALID_JSON", "INVALID_SCHEMA"}:
                cloned["status"] = "GENERATED"
            ir = parse_semantic_ir(cloned, event)
            selected, status, _reason, _scores, path = select_candidate_v4(
                event,
                candidates_by_event.get(detail["event_id"], []),
                ir,
                min_score=0.30,
                min_margin=0.00,
                reranker="constraint",
                temporal_unique_top1=True,
            )
            replay_status = status
            replay_path = path
            replay_candidate = selected["candidate_id"] if selected else ""
            oracle_id = oracles[detail["event_id"]]["oracle_candidate_id"]
            replay_oracle_correct = bool(selected) and selected["candidate_id"] == oracle_id
        missing_reason = str(detail.get("ir_reason") or "")
        rows.append(
            {
                "event_id": detail["event_id"],
                "run": detail["run"],
                "seed": detail["seed"],
                "semantic_type": detail["semantic_type"],
                "generation_status": record.get("status", detail.get("generation_status", "")),
                "validation_reason": record.get("validation_reason", ""),
                "done_reason": record.get("done_reason", ""),
                "eval_count": record.get("eval_count", ""),
                "ir_status": ir_status,
                "ir_reason": missing_reason,
                "bucket": bucket,
                "fine_class": fine_class,
                "fine_label": fine_label,
                "salvage_family": salvage_family,
                "has_semantic_result": payload["has_semantic_result"],
                "has_assignment": payload["has_assignment"],
                "response_is_object": isinstance(record.get("response"), dict),
                **salvage,
                "replay_status": replay_status,
                "replay_path": replay_path,
                "replay_candidate": replay_candidate,
                "replay_oracle_correct": replay_oracle_correct,
            }
        )

    summary = summarize(rows)
    totals = {
        "n_fail_closed": len(rows),
        "unique_events": len({row["event_id"] for row in rows}),
        "invalid_output": sum(row["bucket"] == "INVALID_OUTPUT" for row in rows),
        "retrieval_failed": sum(row["bucket"] == "RETRIEVAL_FAILED" for row in rows),
        "incomplete_ir": sum(row["bucket"] == "INCOMPLETE_IR" for row in rows),
        "j_counts": dict(Counter(row["fine_class"] for row in rows if row["bucket"] == "INVALID_OUTPUT")),
        "retrieval_unique_events": len({row["event_id"] for row in rows if row["bucket"] == "RETRIEVAL_FAILED"}),
        "incomplete_unique_events": len({row["event_id"] for row in rows if row["bucket"] == "INCOMPLETE_IR"}),
        "j7_unique_events": len({row["event_id"] for row in rows if row["fine_class"] == "J7"}),
        "invalid_json_attempts": sum(row["fine_class"] in {"J1_J2", "J6"} for row in rows),
        "i_counts": dict(Counter(row["fine_class"] for row in rows if row["bucket"] == "INCOMPLETE_IR")),
        "salvage_family_counts": dict(Counter(row["salvage_family"] for row in rows)),
        "salvage_ir_ok": sum(row["salvage_ir_status"] == "OK" for row in rows),
        "replay_selected": sum(row["replay_status"] == "SELECTED" for row in rows),
        "replay_oracle_correct": sum(as_bool(row["replay_oracle_correct"]) for row in rows),
        "format_or_complete": sum(
            row["salvage_family"] in {"FORMAT_REPAIRABLE", "DETERMINISTIC_COMPLETE"} for row in rows
        ),
        "true_semantic_gap": sum(row["salvage_family"] == "TRUE_SEMANTIC_GAP" for row in rows),
        "raw_text_lost": sum(row["salvage_family"] == "RAW_TEXT_LOST" for row in rows),
        "retrieval_gap": sum(row["salvage_family"] == "RETRIEVAL_GAP" for row in rows),
        "next_cut": "",
    }
    if totals["retrieval_gap"] >= max(totals["format_or_complete"], totals["raw_text_lost"], 1):
        totals["next_cut"] = "retrieval_then_robust_parser_for_rules_missing"
    elif totals["raw_text_lost"] >= totals["format_or_complete"]:
        totals["next_cut"] = "robust_parser_then_save_raw_qwen_text"
    elif totals["format_or_complete"] > totals["true_semantic_gap"]:
        totals["next_cut"] = "robust_parser_schema_repair"
    else:
        totals["next_cut"] = "structured_slot_filling_extraction"

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "ir-fail-closed-details.csv", rows)
    write_csv(args.output_dir / "ir-fail-closed-summary.csv", summary)
    (args.output_dir / "ir-fail-closed.json").write_text(
        json.dumps({"totals": totals, "summary": summary}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    print(f"details={args.output_dir / 'ir-fail-closed-details.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
