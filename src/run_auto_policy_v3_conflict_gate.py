from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Conflict/uncertainty gate for Auto Policy V3.

This script does not call Qwen.  It reads existing Auto Policy V3 raw outputs
and applies a candidate-blind gate before candidate selection.  The gate is a
small, auditable rule layer for evidence uncertainty:

* missing key markers inserted by negative tests
* explicit conflict/alternative-source language
* dominant distractor language
* unresolved or missing generated canonical semantics
* multi-survivor candidate ambiguity remains handled by the selector

The goal is to measure whether an uncertainty gate can preserve normal-set
performance while improving fail-closed behavior on negative evidence.
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluate_auto_formal_policy_v2_semantic import auto_semantic_result
from run_auto_policy_v2_candidate_repair import (
    load_candidates,
    load_oracles_after_selection,
    select_candidate,
)
from run_auto_policy_v3_negative_safety import duplicate_matching_candidates, event_ids

import run_auto_formal_policy_batch_v3 as v3


ROOT = v3.ROOT
OUTPUT_DIR = ROOT / "output"

NORMAL_RAW = OUTPUT_DIR / "auto-policy-v3" / "raw"
NEGATIVE_RAW_BASE = OUTPUT_DIR / "auto-policy-v3-negative-safety"

NEGATIVE_VARIANTS = [
    "MISSING_KEY_FIELD",
    "CONFLICTING_EVIDENCE",
    "NO_MATCHING_CANDIDATE",
    "DISTRACTOR_DOMINATES",
    "MULTIPLE_MATCHING_CANDIDATES",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Policy V3 conflict/uncertainty gate evaluation")
    parser.add_argument("--prefix", default="auto-policy-v3-conflict-gate-r1-seed20260827")
    parser.add_argument("--negative-runs", type=int, default=1)
    parser.add_argument("--negative-seed", type=int, default=20260827)
    parser.add_argument("--normal-runs", type=int, default=5)
    parser.add_argument("--normal-seed", type=int, default=20260820)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def resolve_project_path(path_text: object) -> Path | None:
    text = str(path_text or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = ROOT / path
    return path


def load_evidence(record: dict[str, Any]) -> str:
    path = resolve_project_path(record.get("candidate_blind_file"))
    if path and path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def norm_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def explicit_conflict_reasons(evidence: str) -> list[str]:
    text = norm_text(evidence)
    reasons: list[str] = []
    markers = {
        "explicit_conflicting_note": "conflicting note",
        "primary_looking_distractor": "primary-looking note",
        "candidate_external_canonical_change": "current canonical change:",
        "alternative_excerpt": "alternative excerpt",
        "parallel_change_summary": "parallel change summary",
        "another_excerpt": "another excerpt",
        "another_note": "another note",
        "same_source_also_lists": "same source also lists",
    }
    for reason, marker in markers.items():
        if marker in text:
            reasons.append(reason)
    contradiction_patterns = {
        "deferred_or_not_integrated": r"\b(deferred to a future revision|not integrated|not currently applicable|previous revision remains authoritative)\b",
        "rejects_current_revision": r"\b(rejects the split|background only|not the current source)\b",
    }
    for reason, pattern in contradiction_patterns.items():
        if re.search(pattern, text):
            reasons.append(reason)
    return sorted(set(reasons))


def missing_key_reasons(evidence: str) -> list[str]:
    text = evidence
    reasons: list[str] = []
    markers = {
        "criterion_removed": "[CRITERION_REMOVED]",
        "number_removed": "[NUMBER_REMOVED]",
        "formula_term_removed": "[FORMULA_TERM_REMOVED]",
        "change_detail_removed": "[CHANGE_DETAIL_REMOVED]",
        "removed_level": "level [REMOVED]",
    }
    for reason, marker in markers.items():
        if marker in text:
            reasons.append(reason)
    return reasons


def generated_uncertainty_reasons(record: dict[str, Any], auto_result: str) -> list[str]:
    reasons: list[str] = []
    if str(record.get("status", "")).strip() != "GENERATED":
        reasons.append("not_generated")
    if str(record.get("canonical_status", "")).strip() not in {"", "ok"}:
        reasons.append("canonical_unresolved")
    if not auto_result.strip():
        reasons.append("semantic_result_missing")
    response = record.get("response")
    if isinstance(response, dict) and response.get("abstain", False):
        reasons.append("model_abstained")
    return reasons


def code_level_conflict_reasons(evidence: str, auto_result: str) -> list[str]:
    """Catch simple WCAG code/level ambiguity in evidence.

    This is intentionally conservative: it only fires when multiple criterion
    codes or multiple conformance levels are present together with an explicit
    conflict/distractor marker, to avoid rejecting normal WCAG text that merely
    contains a single target code and level.
    """

    text = norm_text(evidence)
    if not any(marker in text for marker in ("conflicting note", "primary-looking note", "alternative excerpt")):
        return []
    compact_auto = auto_result.lower()
    codes = set(re.findall(r"\b\d+\.\d+\.\d+\b", evidence))
    levels = set(re.findall(r"\b(?:level|conformance level)\s*(aaa|aa|a)\b", text))
    reasons: list[str] = []
    auto_code = re.search(r"(\d+\.\d+\.\d+)", compact_auto)
    if auto_code and any(code != auto_code.group(1) for code in codes):
        reasons.append("multiple_wcag_codes_with_conflict_marker")
    auto_level = re.search(r"level=(aaa|aa|a)\b", compact_auto)
    if auto_level and any(level.upper() != auto_level.group(1).upper() for level in levels):
        reasons.append("multiple_wcag_levels_with_conflict_marker")
    return reasons


def uncertainty_gate(record: dict[str, Any], evidence: str, auto_result: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    reasons.extend(generated_uncertainty_reasons(record, auto_result))
    reasons.extend(missing_key_reasons(evidence))
    reasons.extend(explicit_conflict_reasons(evidence))
    reasons.extend(code_level_conflict_reasons(evidence, auto_result))
    reasons = sorted(set(reasons))
    if reasons:
        return "ABSTAIN", reasons
    return "PASS", []


def normal_raw_path(event_id: str, run: int, seed_base: int) -> Path:
    seed = seed_base + run - 1
    return NORMAL_RAW / f"{event_id}-run{run}-seed{seed}.json"


def negative_raw_path(variant: str, event_id: str, run: int, seed_base: int) -> Path:
    if variant == "MULTIPLE_MATCHING_CANDIDATES":
        return normal_raw_path(event_id, run, 20260820)
    seed = seed_base + run - 1
    return NEGATIVE_RAW_BASE / variant.lower() / "raw" / f"{event_id}-run{run}-seed{seed}.json"


def load_records(dataset: str, runs: int, seed_base: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if dataset == "NORMAL":
        for event_id in event_ids():
            for run in range(1, runs + 1):
                records.append(load_json(normal_raw_path(event_id, run, seed_base)))
        return records
    for variant in NEGATIVE_VARIANTS:
        for event_id in event_ids():
            for run in range(1, runs + 1):
                record = load_json(negative_raw_path(variant, event_id, run, seed_base))
                record = dict(record)
                record["variant"] = variant
                records.append(record)
    return records


def evaluate_dataset(
    dataset: str,
    records: list[dict[str, Any]],
    events: dict[str, dict[str, str]],
    candidates_by_event: dict[str, list[dict[str, Any]]],
    oracles: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        event_id = str(record["event_id"])
        variant = str(record.get("variant", dataset))
        event = events[event_id]
        auto_result = auto_semantic_result(record)
        evidence = load_evidence(record)
        gate_decision, gate_reasons = uncertainty_gate(record, evidence, auto_result)

        selected = None
        selection_status = "ABSTAIN"
        selection_reason = "uncertainty gate abstain"
        match_evidence: list[dict[str, Any]] = []
        candidates = candidates_by_event[event_id]
        if variant == "MULTIPLE_MATCHING_CANDIDATES":
            candidates = duplicate_matching_candidates(event, candidates, auto_result)
        if gate_decision == "PASS":
            selected, selection_status, selection_reason, match_evidence = select_candidate(event, candidates, auto_result)

        oracle = oracles[event_id]
        oracle_correct = (
            selection_status == "SELECTED"
            and selected is not None
            and selected["candidate_id"] == oracle["oracle_candidate_id"]
        )
        expected_safe_abstain = dataset == "NEGATIVE"
        safe_abstain = expected_safe_abstain and selection_status != "SELECTED"
        unsafe_selection = expected_safe_abstain and selection_status == "SELECTED"
        unsafe_wrong_selection = unsafe_selection and not oracle_correct

        rows.append(
            {
                "dataset": dataset,
                "variant": variant,
                "event_id": event_id,
                "semantic_type": record["semantic_type"],
                "run": record["run"],
                "seed": record["seed"],
                "generation_status": record.get("status", ""),
                "auto_semantic_result": auto_result,
                "gate_decision": gate_decision,
                "gate_reasons": "|".join(gate_reasons),
                "selection_status": selection_status,
                "selected_candidate_id": selected["candidate_id"] if selected else "",
                "selected_value": selected["display_value"] if selected else "",
                "selection_reason": selection_reason,
                "selection_oracle_correct": oracle_correct,
                "expected_safe_abstain": expected_safe_abstain,
                "safe_abstain": safe_abstain,
                "unsafe_selection": unsafe_selection,
                "unsafe_wrong_selection": unsafe_wrong_selection,
                "candidate_match_evidence": json.dumps(match_evidence, ensure_ascii=False, sort_keys=True),
            }
        )
    return rows


def summarize_groups(details: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_variant_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_dataset_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        by_variant_groups[(row["dataset"], row["variant"])].append(row)
        by_dataset_groups[row["dataset"]].append(row)

    by_variant: list[dict[str, Any]] = []
    for (dataset, variant), items in sorted(by_variant_groups.items()):
        selected = sum(row["selection_status"] == "SELECTED" for row in items)
        oracle_correct = sum(bool(row["selection_oracle_correct"]) for row in items)
        safe_abstain = sum(bool(row["safe_abstain"]) for row in items)
        unsafe = sum(bool(row["unsafe_selection"]) for row in items)
        unsafe_wrong = sum(bool(row["unsafe_wrong_selection"]) for row in items)
        by_variant.append(
            {
                "dataset": dataset,
                "variant": variant,
                "attempts": len(items),
                "gate_abstains": sum(row["gate_decision"] == "ABSTAIN" for row in items),
                "gate_abstain_rate": sum(row["gate_decision"] == "ABSTAIN" for row in items) / len(items),
                "selected": selected,
                "selection_rate": selected / len(items),
                "oracle_correct": oracle_correct,
                "oracle_accuracy": oracle_correct / len(items),
                "safe_abstains": safe_abstain,
                "safe_abstain_rate": safe_abstain / len(items),
                "unsafe_selections": unsafe,
                "unsafe_selection_rate": unsafe / len(items),
                "unsafe_wrong_selections": unsafe_wrong,
                "unsafe_wrong_selection_rate": unsafe_wrong / len(items),
            }
        )

    by_dataset: list[dict[str, Any]] = []
    for dataset, items in sorted(by_dataset_groups.items()):
        selected = sum(row["selection_status"] == "SELECTED" for row in items)
        oracle_correct = sum(bool(row["selection_oracle_correct"]) for row in items)
        safe_abstain = sum(bool(row["safe_abstain"]) for row in items)
        unsafe = sum(bool(row["unsafe_selection"]) for row in items)
        unsafe_wrong = sum(bool(row["unsafe_wrong_selection"]) for row in items)
        by_dataset.append(
            {
                "dataset": dataset,
                "attempts": len(items),
                "gate_abstains": sum(row["gate_decision"] == "ABSTAIN" for row in items),
                "gate_abstain_rate": sum(row["gate_decision"] == "ABSTAIN" for row in items) / len(items),
                "selected": selected,
                "selection_rate": selected / len(items),
                "oracle_correct": oracle_correct,
                "oracle_accuracy": oracle_correct / len(items),
                "safe_abstains": safe_abstain,
                "safe_abstain_rate": safe_abstain / len(items) if dataset == "NEGATIVE" else "",
                "unsafe_selections": unsafe,
                "unsafe_selection_rate": unsafe / len(items) if dataset == "NEGATIVE" else "",
                "unsafe_wrong_selections": unsafe_wrong,
                "unsafe_wrong_selection_rate": unsafe_wrong / len(items) if dataset == "NEGATIVE" else "",
            }
        )
    return by_variant, by_dataset


def main() -> int:
    block_legacy_entrypoint(__file__)
    args = parse_args()
    events = {row["event_id"]: row for row in read_csv(v3.EVENT_CSV) if row.get("status", "").strip().upper() == "READY"}
    candidates_by_event = load_candidates()
    oracles = load_oracles_after_selection()
    normal_records = load_records("NORMAL", args.normal_runs, args.normal_seed)
    negative_records = load_records("NEGATIVE", args.negative_runs, args.negative_seed)
    details = [
        *evaluate_dataset("NORMAL", normal_records, events, candidates_by_event, oracles),
        *evaluate_dataset("NEGATIVE", negative_records, events, candidates_by_event, oracles),
    ]
    by_variant, by_dataset = summarize_groups(details)

    details_path = OUTPUT_DIR / f"{args.prefix}-details.csv"
    by_variant_path = OUTPUT_DIR / f"{args.prefix}-by-variant.csv"
    by_dataset_path = OUTPUT_DIR / f"{args.prefix}-by-dataset.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    write_csv(details_path, details)
    write_csv(by_variant_path, by_variant)
    write_csv(by_dataset_path, by_dataset)
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "details": str(details_path),
                "by_variant": str(by_variant_path),
                "by_dataset": str(by_dataset_path),
                "gate_boundary": (
                    "The gate is candidate-blind and Oracle-blind. It reads only generated "
                    "policy status, canonical semantic output, and the candidate-blind evidence "
                    "file referenced by each raw record."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"details={details_path}")
    print(f"by_variant={by_variant_path}")
    print(f"by_dataset={by_dataset_path}")
    for row in by_dataset:
        print(
            f"[{row['dataset']}] gate_abstain={float(row['gate_abstain_rate']):.2%} "
            f"selected={float(row['selection_rate']):.2%} "
            f"oracle={float(row['oracle_accuracy']):.2%} "
            f"safe_abstain={row['safe_abstain_rate']} "
            f"unsafe={row['unsafe_selection_rate']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
