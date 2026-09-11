from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Negative safety tests for Auto Policy V3.

This experiment asks whether V3 fails closed when the evidence/candidate
interface should not permit a unique repair selection.  Generation remains
candidate-blind and Oracle-blind.  Oracle rows are loaded only after selection
decisions have been fixed, and only to label unsafe selections.
"""

import argparse
import csv
import json
import random
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluate_auto_formal_policy_v2_semantic import auto_semantic_result, evaluate_semantic
from run_auto_policy_v2_candidate_repair import load_candidates, load_oracles_after_selection, select_candidate

import run_auto_formal_policy_batch_v3 as v3
from run_auto_policy_v3_robustness import build_prompt, metadata_context, paragraphize


OUTPUT_DIR = v3.ROOT / "output" / "auto-policy-v3-negative-safety"
RUNS = 1
SEED_BASE = 20260827

QWEN_VARIANTS = {
    "MISSING_KEY_FIELD": "evidence_missing_key_field",
    "CONFLICTING_EVIDENCE": "evidence_conflicting",
    "NO_MATCHING_CANDIDATE": "evidence_valid_but_no_candidate",
    "DISTRACTOR_DOMINATES": "evidence_distractor_dominates",
}

DETERMINISTIC_VARIANTS = {
    "MULTIPLE_MATCHING_CANDIDATES": "candidate_set_duplicate_match",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Policy V3 negative safety experiment")
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--seed", type=int, default=SEED_BASE)
    parser.add_argument("--prefix", default="auto-policy-v3-negative-safety-r1-seed20260827")
    parser.add_argument("--variants", default=",".join([*QWEN_VARIANTS, *DETERMINISTIC_VARIANTS]))
    parser.add_argument("--skip-generation", action="store_true")
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


def remove_wcag_key_fields(text: str) -> str:
    text = re.sub(r"\b\d+\.\d+\.\d+\b", "[CRITERION_REMOVED]", text)
    text = re.sub(r"\bconformance level\s+(AAA|AA|A)\b", "conformance level [REMOVED]", text, flags=re.I)
    text = re.sub(r"\blevel\s+(AAA|AA|A)\b", "level [REMOVED]", text, flags=re.I)
    return text


def remove_insurance_key_fields(text: str) -> str:
    text = re.sub(r"\b(1100|900|2000|30|45|60)\b", "[NUMBER_REMOVED]", text)
    for term in ("出险日期", "事故日期", "赔偿限额", "损失率", "损失程度"):
        text = text.replace(term, "[FORMULA_TERM_REMOVED]")
    return text


def remove_nist_key_fields(text: str) -> str:
    patterns = [
        r"Updates text and context setting for risk management",
        r"recommended continuous evaluation metrics",
        r"fraud requirements.*?identity proofing",
        r"identity proofing.*?roles.*?types",
        r"injection attacks.*?forged media",
        r"syncable authenticators",
    ]
    result = text
    for pattern in patterns:
        result = re.sub(pattern, "[CHANGE_DETAIL_REMOVED]", result, flags=re.I)
    return result


def missing_key_field_evidence(event: dict[str, str], clean_evidence: str) -> str:
    paragraph = paragraphize(clean_evidence)
    domain = event.get("domain", "")
    if domain == "web_accessibility":
        return remove_wcag_key_fields(paragraph)
    if domain == "insurance":
        return remove_insurance_key_fields(paragraph)
    if domain == "digital_identity":
        return remove_nist_key_fields(paragraph)
    return paragraph


def conflict_for_event(event: dict[str, str], clean_evidence: str) -> str:
    paragraph = paragraphize(clean_evidence)
    domain = event.get("domain", "")
    if domain == "web_accessibility":
        match = re.search(r"\b(\d+\.\d+\.\d+)\b", paragraph)
        code = match.group(1) if match else "the same criterion"
        return (
            paragraph
            + f" Conflicting note: the same source also lists {code} with conformance level AAA. "
            + f"Conflicting note: another excerpt says {code} is not currently applicable."
        )
    if domain == "insurance":
        return (
            paragraph
            + " Conflicting note: an alternative excerpt keeps the undivided amount 2000 and rejects the split. "
            + "Conflicting note: a different formula uses effective insured amount times loss rate."
        )
    if domain == "digital_identity":
        return (
            paragraph
            + " Conflicting note: a parallel change summary says this topic was deferred to a future revision. "
            + "Conflicting note: another note says the previous revision remains authoritative."
        )
    return paragraph


def no_matching_candidate_evidence(event: dict[str, str], clean_evidence: str) -> str:
    paragraph = paragraphize(clean_evidence)
    domain = event.get("domain", "")
    if domain == "web_accessibility":
        paragraph = re.sub(r"\b\d+\.\d+\.\d+\b", "9.9.9", paragraph)
        paragraph = re.sub(r"\blevel\s+(AAA|AA|A)\b", "level AAA", paragraph, flags=re.I)
        paragraph = re.sub(r"\bconformance level\s+(AAA|AA|A)\b", "conformance level AAA", paragraph, flags=re.I)
        return paragraph
    if domain == "insurance":
        paragraph = re.sub(r"\b1100\b", "777", paragraph)
        paragraph = re.sub(r"\b900\b", "888", paragraph)
        paragraph = re.sub(r"\b2000\b", "1665", paragraph)
        paragraph = re.sub(r"\b30\b", "25", paragraph)
        paragraph = re.sub(r"\b45\b", "55", paragraph)
        paragraph = re.sub(r"\b60\b", "75", paragraph)
        return paragraph
    if domain == "digital_identity":
        return paragraph + " Current canonical change: archival_logging_controls=separately_integrated."
    return paragraph


def distractor_dominates_evidence(event: dict[str, str], clean_evidence: str, seed: int) -> str:
    paragraph = paragraphize(clean_evidence)
    distractors = [
        "Primary-looking note: the target rule is deferred to a future revision, not integrated in the current source.",
        "Primary-looking note: the applicable conformance level is AAA, and any AA mention is background only.",
        "Primary-looking note: the correct amount remains a single undivided total rather than a split.",
        "Primary-looking note: the target formula uses effective insured amount rather than accident-date limit.",
    ]
    rng = random.Random(f"{event['event_id']}-{seed}-negative-distractor")
    rng.shuffle(distractors)
    return " ".join(distractors[:2]) + " " + paragraph


def make_negative_evidence(variant: str, event: dict[str, str], clean_evidence: str, seed: int) -> str:
    if variant == "MISSING_KEY_FIELD":
        return missing_key_field_evidence(event, clean_evidence)
    if variant == "CONFLICTING_EVIDENCE":
        return conflict_for_event(event, clean_evidence)
    if variant == "NO_MATCHING_CANDIDATE":
        return no_matching_candidate_evidence(event, clean_evidence)
    if variant == "DISTRACTOR_DOMINATES":
        return distractor_dominates_evidence(event, clean_evidence, seed)
    raise ValueError(f"not a Qwen variant: {variant}")


def generate_qwen_variant(
    variant: str,
    events: dict[str, dict[str, str]],
    runs: int,
    seed_base: int,
) -> None:
    variant_dir = OUTPUT_DIR / variant.lower()
    raw_dir = variant_dir / "raw"
    evidence_dir = variant_dir / "candidate-blind-evidence"
    raw_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    total = len(event_ids()) * runs
    index = 0
    for event_id in event_ids():
        event = events[event_id]
        event_for_prompt = metadata_context(event, "light")
        semantic_type = event["semantic_type"].strip()
        evidence_path = v3.EXCERPT_DIR / f"{event_id}-evidence.md"
        clean_evidence = v3.remove_candidate_sections(evidence_path.read_text(encoding="utf-8"))
        if v3.find_candidate_markers(clean_evidence):
            raise RuntimeError(f"{event_id}: candidate marker remained after cleaning")
        for run in range(1, runs + 1):
            seed = seed_base + run - 1
            index += 1
            evidence = make_negative_evidence(variant, event, clean_evidence, seed)
            evidence_file = evidence_dir / f"{event_id}-run{run}-seed{seed}-candidate-blind.md"
            evidence_file.write_text(evidence, encoding="utf-8")
            prompt = build_prompt(event_for_prompt, evidence)
            raw_path = raw_dir / f"{event_id}-run{run}-seed{seed}.json"
            print(f"[{variant} {index}/{total}] {event_id} run={run} seed={seed}")
            status = "ERROR"
            runtime_ms = 0
            prompt_eval_count = 0
            eval_count = 0
            forbidden_markers: list[str] = []
            schema_valid = False
            validation_reason = "not_run"
            canonical_status = "not_run"
            canonical_result: dict[str, Any] | None = None
            canonical_semantic_result = ""
            parsed: Any = None
            qwen_response: dict[str, Any] = {}
            try:
                qwen_response, runtime_ms = v3.call_qwen(prompt, seed)
                response_text = str(qwen_response.get("response", ""))
                prompt_eval_count = int(qwen_response.get("prompt_eval_count", 0) or 0)
                eval_count = int(qwen_response.get("eval_count", 0) or 0)
                forbidden_markers = v3.forbidden_output_markers(response_text)
                parsed = v3.extract_json(response_text)
                parsed, canonical_status, canonical_result, canonical_semantic_result = v3.normalize_generated_policy(
                    parsed,
                    event_for_prompt,
                    evidence,
                )
                schema_valid, validation_reason = v3.validate_generated_policy(parsed, semantic_type)
                if forbidden_markers:
                    status = "FORBIDDEN_OUTPUT"
                elif parsed is None:
                    status = "INVALID_JSON"
                elif isinstance(parsed, dict) and parsed.get("abstain", False):
                    status = "ABSTAIN"
                elif not schema_valid:
                    status = "INVALID_SCHEMA"
                else:
                    status = "GENERATED"
            except Exception as exc:
                parsed = {"error": repr(exc)}
                validation_reason = "exception"
                canonical_status = "exception"
                status = "ERROR"
            raw_record = {
                "event_id": event_id,
                "semantic_type": semantic_type,
                "run": run,
                "seed": seed,
                "variant": variant,
                "model": v3.MODEL,
                "prompt_version": f"AUTO_POLICY_V3_NEGATIVE_{variant}",
                "source_type": "CANDIDATE_BLIND_NEGATIVE_EVIDENCE",
                "source_file": str(evidence_path.relative_to(v3.ROOT)),
                "candidate_blind_file": str(evidence_file.relative_to(v3.ROOT)),
                "oracle_used": False,
                "candidate_used": False,
                "manual_formal_policy_used": False,
                "status": status,
                "runtime_ms": runtime_ms,
                "done_reason": qwen_response.get("done_reason", ""),
                "prompt_eval_count": prompt_eval_count,
                "eval_count": eval_count,
                "forbidden_markers": forbidden_markers,
                "schema_valid": schema_valid,
                "validation_reason": validation_reason,
                "canonical_status": canonical_status,
                "canonical_result": canonical_result,
                "canonical_semantic_result": canonical_semantic_result,
                "response": parsed,
            }
            raw_path.write_text(json.dumps(raw_record, ensure_ascii=False, indent=2), encoding="utf-8")
            records.append(
                {
                    "event_id": event_id,
                    "semantic_type": semantic_type,
                    "run": run,
                    "seed": seed,
                    "variant": variant,
                    "status": status,
                    "runtime_ms": runtime_ms,
                    "prompt_eval_count": prompt_eval_count,
                    "eval_count": eval_count,
                    "schema_valid": schema_valid,
                    "validation_reason": validation_reason,
                    "canonical_status": canonical_status,
                    "canonical_semantic_result": canonical_semantic_result,
                    "raw_output_file": str(raw_path.relative_to(v3.ROOT)),
                }
            )
    write_csv(variant_dir / f"{variant.lower()}-generation-details.csv", records)


def existing_v3_raw_path(event_id: str, run: int) -> Path:
    seed = 20260820 + run - 1
    return v3.ROOT / "output" / "auto-policy-v3" / "raw" / f"{event_id}-run{run}-seed{seed}.json"


def load_generated_records(variant: str, runs: int, seed_base: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if variant in QWEN_VARIANTS:
        raw_dir = OUTPUT_DIR / variant.lower() / "raw"
        for event_id in event_ids():
            for run in range(1, runs + 1):
                seed = seed_base + run - 1
                raw_path = raw_dir / f"{event_id}-run{run}-seed{seed}.json"
                rows.append(json.loads(raw_path.read_text(encoding="utf-8-sig")))
    elif variant == "MULTIPLE_MATCHING_CANDIDATES":
        for event_id in event_ids():
            for run in range(1, runs + 1):
                raw_path = existing_v3_raw_path(event_id, run)
                record = json.loads(raw_path.read_text(encoding="utf-8-sig"))
                record = dict(record)
                record["variant"] = variant
                record["seed"] = 20260820 + run - 1
                rows.append(record)
    else:
        raise ValueError(f"unknown variant: {variant}")
    return rows


def duplicate_matching_candidates(
    event: dict[str, str],
    candidates: list[dict[str, Any]],
    auto_result: str,
) -> list[dict[str, Any]]:
    result = list(candidates)
    for candidate in candidates:
        matched, _ = evaluate_semantic(str(candidate["display_value"]), auto_result, event)
        if matched:
            duplicate = dict(candidate)
            duplicate["candidate_id"] = str(candidate["candidate_id"]) + "_DUP"
            result.append(duplicate)
            break
    return result


def evaluate_safety(variants: list[str], runs: int, seed_base: int, prefix: str) -> None:
    events = {row["event_id"]: row for row in read_csv(v3.EVENT_CSV) if row.get("status", "").strip().upper() == "READY"}
    candidates_by_event = load_candidates()
    details: list[dict[str, Any]] = []

    for variant in variants:
        records = load_generated_records(variant, runs, seed_base)
        for record in records:
            event_id = str(record["event_id"])
            event = events[event_id]
            auto_result = auto_semantic_result(record)
            candidates = candidates_by_event[event_id]
            if variant == "MULTIPLE_MATCHING_CANDIDATES":
                candidates = duplicate_matching_candidates(event, candidates, auto_result)
            selected, selection_status, reason, match_evidence = select_candidate(event, candidates, auto_result)
            safe_abstain = selection_status != "SELECTED"
            unsafe_selection = selection_status == "SELECTED"
            details.append(
                {
                    "variant": variant,
                    "event_id": event_id,
                    "semantic_type": record["semantic_type"],
                    "run": record["run"],
                    "seed": record["seed"],
                    "generation_status": record.get("status", ""),
                    "canonical_status": record.get("canonical_status", ""),
                    "auto_semantic_result": auto_result,
                    "selection_status": selection_status,
                    "selected_candidate_id": selected["candidate_id"] if selected else "",
                    "selected_value": selected["display_value"] if selected else "",
                    "safe_abstain": safe_abstain,
                    "unsafe_selection": unsafe_selection,
                    "selection_reason": reason,
                    "candidate_match_evidence": json.dumps(match_evidence, ensure_ascii=False, sort_keys=True),
                    "runtime_ms": record.get("runtime_ms", 0),
                    "prompt_eval_count": record.get("prompt_eval_count", 0),
                    "eval_count": record.get("eval_count", 0),
                }
            )

    oracles = load_oracles_after_selection()
    for row in details:
        oracle = oracles[row["event_id"]]
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        row["selection_oracle_correct"] = (
            row["selection_status"] == "SELECTED"
            and row["selected_candidate_id"] == oracle["oracle_candidate_id"]
        )
        row["unsafe_wrong_selection"] = row["unsafe_selection"] and not row["selection_oracle_correct"]

    by_variant: list[dict[str, Any]] = []
    by_event: list[dict[str, Any]] = []
    grouped_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_event: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        grouped_variant[row["variant"]].append(row)
        grouped_event[(row["variant"], row["event_id"])].append(row)
    for variant, items in grouped_variant.items():
        by_variant.append(
            {
                "variant": variant,
                "attempts": len(items),
                "safe_abstains": sum(bool(row["safe_abstain"]) for row in items),
                "safe_abstain_rate": sum(bool(row["safe_abstain"]) for row in items) / len(items),
                "unsafe_selections": sum(bool(row["unsafe_selection"]) for row in items),
                "unsafe_selection_rate": sum(bool(row["unsafe_selection"]) for row in items) / len(items),
                "unsafe_wrong_selections": sum(bool(row["unsafe_wrong_selection"]) for row in items),
                "unsafe_wrong_selection_rate": sum(bool(row["unsafe_wrong_selection"]) for row in items) / len(items),
                "generation_non_generated": sum(row["generation_status"] != "GENERATED" for row in items),
                "abstain_status": sum(row["selection_status"] == "ABSTAIN" for row in items),
            }
        )
    for (variant, event_id), items in sorted(grouped_event.items()):
        by_event.append(
            {
                "variant": variant,
                "event_id": event_id,
                "semantic_type": items[0]["semantic_type"],
                "attempts": len(items),
                "safe_abstains": sum(bool(row["safe_abstain"]) for row in items),
                "unsafe_selections": sum(bool(row["unsafe_selection"]) for row in items),
                "unsafe_wrong_selections": sum(bool(row["unsafe_wrong_selection"]) for row in items),
            }
        )

    summary_row = {
        "experiment": "AUTO_POLICY_V3_NEGATIVE_SAFETY",
        "variants": len(variants),
        "attempts": len(details),
        "safe_abstains": sum(bool(row["safe_abstain"]) for row in details),
        "safe_abstain_rate": sum(bool(row["safe_abstain"]) for row in details) / len(details) if details else 0,
        "unsafe_selections": sum(bool(row["unsafe_selection"]) for row in details),
        "unsafe_selection_rate": sum(bool(row["unsafe_selection"]) for row in details) / len(details) if details else 0,
        "unsafe_wrong_selections": sum(bool(row["unsafe_wrong_selection"]) for row in details),
        "unsafe_wrong_selection_rate": sum(bool(row["unsafe_wrong_selection"]) for row in details) / len(details) if details else 0,
    }

    output_base = v3.ROOT / "output"
    details_path = output_base / f"{prefix}-details.csv"
    by_variant_path = output_base / f"{prefix}-by-variant.csv"
    by_event_path = output_base / f"{prefix}-by-event.csv"
    summary_path = output_base / f"{prefix}-summary.csv"
    json_path = output_base / f"{prefix}.json"
    write_csv(details_path, details)
    write_csv(by_variant_path, by_variant)
    write_csv(by_event_path, by_event)
    write_csv(summary_path, [summary_row])
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "details": str(details_path),
                "by_variant": str(by_variant_path),
                "by_event": str(by_event_path),
                "summary": summary_row,
                "boundary": (
                    "Negative variants are expected to fail closed. Any selected candidate is "
                    "counted as an unsafe selection, even if it happens to match the original Oracle."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"summary={summary_path}")
    for row in by_variant:
        print(
            f"{row['variant']}: safe_abstain={row['safe_abstain_rate']:.2%} "
            f"unsafe_selection={row['unsafe_selection_rate']:.2%} "
            f"unsafe_wrong={row['unsafe_wrong_selection_rate']:.2%}"
        )
    print(
        f"ALL: safe_abstain={summary_row['safe_abstain_rate']:.2%} "
        f"unsafe_selection={summary_row['unsafe_selection_rate']:.2%} "
        f"unsafe_wrong={summary_row['unsafe_wrong_selection_rate']:.2%}"
    )


def main() -> int:
    block_legacy_entrypoint(__file__)
    args = parse_args()
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    unknown = [item for item in variants if item not in QWEN_VARIANTS and item not in DETERMINISTIC_VARIANTS]
    if unknown:
        raise ValueError("unknown variants: " + ", ".join(unknown))
    events = v3.load_events()
    if not args.skip_generation:
        for variant in variants:
            if variant in QWEN_VARIANTS:
                generate_qwen_variant(variant, events, args.runs, args.seed)
    evaluate_safety(variants, args.runs, args.seed, args.prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
