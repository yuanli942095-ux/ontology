from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Evaluate an enhanced natural-conflict gate for Auto Policy V3.

The original conflict gate catches explicit synthetic uncertainty markers.  This
script adds candidate-blind natural evidence checks for mixed evidence blocks:
multiple event paragraphs, duplicated event paragraphs with different version
signals, and WCAG/NIST/insurance value ambiguity.  It reuses existing raw Qwen
outputs and does not call the model.
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
from run_auto_policy_v2_candidate_repair import load_candidates, load_oracles_after_selection, select_candidate
from run_auto_policy_v3_conflict_gate import uncertainty_gate

import run_auto_formal_policy_batch_v3 as v3


ROOT = v3.ROOT
OUTPUT = ROOT / "output"
NATURAL_CONFLICT_RAW_BASE = OUTPUT / "auto-policy-v3-natural-conflict-safety"
NORMAL_RAW = OUTPUT / "auto-policy-v3" / "raw"
NATURAL_VARIANTS = [
    "OBSOLETE_AND_CURRENT_MIXED",
    "SAME_DOMAIN_NEAR_MISS_MIXED",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Policy V3 enhanced natural-conflict gate")
    parser.add_argument("--prefix", default="auto-policy-v3-natural-conflict-gate-r1-seed20260827")
    parser.add_argument("--normal-runs", type=int, default=5)
    parser.add_argument("--normal-seed", type=int, default=20260820)
    parser.add_argument("--natural-runs", type=int, default=1)
    parser.add_argument("--natural-seed", type=int, default=20260827)
    parser.add_argument("--variants", default=",".join(NATURAL_VARIANTS))
    return parser.parse_args()


def event_ids() -> list[str]:
    return [f"EXT_E{i:03d}" for i in range(1, 31)]


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


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def evidence_block_count(evidence: str) -> int:
    return len(re.findall(r"\bEXT_E\d{3}\s+Evidence Note\b", evidence, flags=re.I))


def natural_conflict_reasons(event: dict[str, str], evidence: str, auto_result: str) -> list[str]:
    text = norm(evidence)
    reasons: list[str] = []

    event_refs = re.findall(r"\bEXT_E\d{3}\b", evidence)
    distinct_event_refs = sorted(set(event_refs))
    blocks = evidence_block_count(evidence)
    if len(distinct_event_refs) > 1:
        reasons.append("multiple_event_evidence_blocks")
    if blocks > 1 and len(distinct_event_refs) == 1:
        reasons.append("duplicated_event_evidence_blocks")

    domain = event.get("domain", "")
    if domain == "web_accessibility":
        codes = set(re.findall(r"\b\d+\.\d+\.\d+\b", evidence))
        revisions = set(re.findall(r"\bWCAG\s*2\.[012]\b", evidence, flags=re.I))
        levels = set(re.findall(r"\b(?:conformance level|level)\s*(AAA|AA|A)\b", evidence, flags=re.I))
        auto_code = re.search(r"\b\d+\.\d+\.\d+\b", auto_result)
        if blocks > 1 and len(codes) > 1:
            reasons.append("multiple_wcag_criteria_in_mixed_evidence")
        if blocks > 1 and len(revisions) > 1:
            reasons.append("multiple_wcag_revisions_in_mixed_evidence")
        if auto_code and len(codes) > 1 and any(code != auto_code.group(0) for code in codes):
            reasons.append("auto_wcag_result_not_unique_in_evidence")
        if blocks > 1 and len(levels) > 1:
            reasons.append("multiple_wcag_levels_in_mixed_evidence")

    if domain == "digital_identity":
        revisions = set(re.findall(r"\bRevision\s*[34]\b", evidence, flags=re.I))
        nist_docs = set(re.findall(r"\b800[- ]63[- ][34]\b", evidence, flags=re.I))
        if blocks > 1 and (len(revisions) > 1 or len(nist_docs) > 1):
            reasons.append("multiple_nist_revisions_in_mixed_evidence")

    if domain == "insurance":
        numbers = set(re.findall(r"\b(?:900|1000|1100|2000|30|45|50|60)\b", evidence))
        formula_terms = [
            "effective insured amount",
            "accident-date",
            "accident date",
            "loss rate",
            "50 percent",
            "赔偿限额",
            "损失率",
            "出险日期",
            "有效保险金额",
        ]
        formula_hits = [term for term in formula_terms if term.lower() in text]
        if blocks > 1 and len(numbers) >= 3:
            reasons.append("multiple_insurance_values_in_mixed_evidence")
        if blocks > 1 and len(formula_hits) >= 3:
            reasons.append("multiple_insurance_formula_terms_in_mixed_evidence")

    return sorted(set(reasons))


def enhanced_gate(record: dict[str, Any], event: dict[str, str], evidence: str, auto_result: str) -> tuple[str, list[str]]:
    decision, reasons = uncertainty_gate(record, evidence, auto_result)
    reasons = list(reasons)
    reasons.extend(natural_conflict_reasons(event, evidence, auto_result))
    reasons = sorted(set(reasons))
    if reasons:
        return "ABSTAIN", reasons
    return decision, []


def normal_raw_path(event_id: str, run: int, seed_base: int) -> Path:
    seed = seed_base + run - 1
    return NORMAL_RAW / f"{event_id}-run{run}-seed{seed}.json"


def natural_raw_path(variant: str, event_id: str, run: int, seed_base: int) -> Path:
    seed = seed_base + run - 1
    return NATURAL_CONFLICT_RAW_BASE / variant.lower() / "raw" / f"{event_id}-run{run}-seed{seed}.json"


def load_records(dataset: str, variants: list[str], runs: int, seed_base: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if dataset == "NORMAL":
        for event_id in event_ids():
            for run in range(1, runs + 1):
                record = load_json(normal_raw_path(event_id, run, seed_base))
                record["variant"] = "NORMAL"
                rows.append(record)
        return rows
    for variant in variants:
        for event_id in event_ids():
            for run in range(1, runs + 1):
                record = load_json(natural_raw_path(variant, event_id, run, seed_base))
                record["variant"] = variant
                rows.append(record)
    return rows


def evaluate_records(
    dataset: str,
    records: list[dict[str, Any]],
    events: dict[str, dict[str, str]],
    candidates_by_event: dict[str, list[dict[str, Any]]],
    oracles: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    expected_safe_abstain = dataset == "NATURAL_CONFLICT"
    for record in records:
        event_id = str(record["event_id"])
        event = events[event_id]
        auto_result = auto_semantic_result(record)
        evidence = load_evidence(record)
        gate_decision, gate_reasons = enhanced_gate(record, event, evidence, auto_result)
        selected = None
        selection_status = "ABSTAIN"
        selection_reason = "uncertainty gate abstain"
        match_evidence: list[dict[str, Any]] = []
        if gate_decision == "PASS":
            selected, selection_status, selection_reason, match_evidence = select_candidate(
                event,
                candidates_by_event[event_id],
                auto_result,
            )
        oracle = oracles[event_id]
        oracle_correct = (
            selection_status == "SELECTED"
            and selected is not None
            and selected["candidate_id"] == oracle["oracle_candidate_id"]
        )
        details.append(
            {
                "dataset": dataset,
                "variant": record.get("variant", dataset),
                "event_id": event_id,
                "semantic_type": event["semantic_type"],
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
                "safe_abstain": expected_safe_abstain and selection_status != "SELECTED",
                "unsafe_selection": expected_safe_abstain and selection_status == "SELECTED",
                "normal_false_abstain": (not expected_safe_abstain) and selection_status != "SELECTED",
                "candidate_match_evidence": json.dumps(match_evidence, ensure_ascii=False, sort_keys=True),
            }
        )
    return details


def summarize(details: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dataset_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    variant_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        dataset_groups[row["dataset"]].append(row)
        variant_groups[(row["dataset"], row["variant"])].append(row)

    by_dataset: list[dict[str, Any]] = []
    for dataset, items in sorted(dataset_groups.items()):
        safe = sum(bool(row["safe_abstain"]) for row in items)
        unsafe = sum(bool(row["unsafe_selection"]) for row in items)
        false_abstain = sum(bool(row["normal_false_abstain"]) for row in items)
        correct = sum(bool(row["selection_oracle_correct"]) for row in items)
        by_dataset.append(
            {
                "dataset": dataset,
                "attempts": len(items),
                "selected": sum(row["selection_status"] == "SELECTED" for row in items),
                "oracle_correct": correct,
                "oracle_accuracy": correct / len(items) if items else 0,
                "safe_abstain": safe,
                "safe_abstain_rate": safe / len(items) if dataset == "NATURAL_CONFLICT" and items else "",
                "unsafe_selection": unsafe,
                "unsafe_selection_rate": unsafe / len(items) if dataset == "NATURAL_CONFLICT" and items else "",
                "normal_false_abstain": false_abstain,
                "normal_false_abstain_rate": false_abstain / len(items) if dataset == "NORMAL" and items else "",
            }
        )

    by_variant: list[dict[str, Any]] = []
    for (dataset, variant), items in sorted(variant_groups.items()):
        safe = sum(bool(row["safe_abstain"]) for row in items)
        unsafe = sum(bool(row["unsafe_selection"]) for row in items)
        false_abstain = sum(bool(row["normal_false_abstain"]) for row in items)
        by_variant.append(
            {
                "dataset": dataset,
                "variant": variant,
                "attempts": len(items),
                "safe_abstain": safe,
                "safe_abstain_rate": safe / len(items) if dataset == "NATURAL_CONFLICT" and items else "",
                "unsafe_selection": unsafe,
                "unsafe_selection_rate": unsafe / len(items) if dataset == "NATURAL_CONFLICT" and items else "",
                "normal_false_abstain": false_abstain,
                "normal_false_abstain_rate": false_abstain / len(items) if dataset == "NORMAL" and items else "",
                "gate_abstains": sum(row["gate_decision"] == "ABSTAIN" for row in items),
                "top_gate_reasons": "|".join(
                    sorted({reason for row in items for reason in str(row["gate_reasons"]).split("|") if reason})[:12]
                ),
            }
        )
    return by_dataset, by_variant


def main() -> int:
    block_legacy_entrypoint(__file__)
    args = parse_args()
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    unknown = [item for item in variants if item not in NATURAL_VARIANTS]
    if unknown:
        raise ValueError("unknown variants: " + ", ".join(unknown))

    events = {row["event_id"]: row for row in read_csv(v3.EVENT_CSV) if row.get("status", "").strip().upper() == "READY"}
    candidates = load_candidates()
    oracles = load_oracles_after_selection()
    normal = load_records("NORMAL", [], args.normal_runs, args.normal_seed)
    natural = load_records("NATURAL_CONFLICT", variants, args.natural_runs, args.natural_seed)
    details = [
        *evaluate_records("NORMAL", normal, events, candidates, oracles),
        *evaluate_records("NATURAL_CONFLICT", natural, events, candidates, oracles),
    ]
    by_dataset, by_variant = summarize(details)
    details_path = OUTPUT / f"{args.prefix}-details.csv"
    by_dataset_path = OUTPUT / f"{args.prefix}-by-dataset.csv"
    by_variant_path = OUTPUT / f"{args.prefix}-by-variant.csv"
    json_path = OUTPUT / f"{args.prefix}.json"
    write_csv(details_path, details)
    write_csv(by_dataset_path, by_dataset)
    write_csv(by_variant_path, by_variant)
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "details": str(details_path),
                "by_dataset": by_dataset,
                "by_variant": by_variant,
                "boundary": "Enhanced gate reuses existing candidate-blind raw outputs and adds natural mixed-evidence checks.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"details={details_path}")
    print(f"by_dataset={by_dataset_path}")
    print(f"by_variant={by_variant_path}")
    for row in by_dataset:
        print(
            f"[{row['dataset']}] attempts={row['attempts']} selected={row['selected']} "
            f"oracle={float(row['oracle_accuracy']):.2%} safe={row['safe_abstain_rate']} unsafe={row['unsafe_selection_rate']} "
            f"false_abstain={row['normal_false_abstain_rate']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
