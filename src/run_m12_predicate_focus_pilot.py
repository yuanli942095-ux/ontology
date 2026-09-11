from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""6-slot predicate-aware evidence focus pilot for GRE/CSS residual failures.

This pilot does not retrieve new documents and does not read candidates or Oracle
before extraction. It only reranks/crops the already frozen candidate-blind
evidence window using public subject/predicate metadata.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from auto_policy_m12_task_formulation import apply_task_formulated_extraction
from auto_policy_task_formulation import route_task_formulation
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from run_m12_decomposed_extraction_pilot import (
    ARM_A_RAW,
    BENCHMARK_DIR,
    IR_PREFIX,
    SEED_BASE,
    load_evidence,
    run_ir,
)
from semantic_v2_common import PROJECT_DIR, write_csv


OUTPUT_ROOT = PROJECT_DIR / "output" / "m12-predicate-focus-pilot"
DEFAULT_EVENT_IDS = ("EXT_E129", "EXT_E144", "EXT_E145", "EXT_E149", "EXT_E197", "EXT_E199")
TASK_SUMMARY = PROJECT_DIR / "output" / "m12-task-formulation-pilot" / "task-formulation-summary.csv"


def normalize_for_match(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff.]+", " ", str(text or "").lower()).strip()


def token_set(text: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9.]+|[\u4e00-\u9fff]+", normalize_for_match(text))
        if len(token) > 1
    }
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "current",
        "previous",
        "status",
        "scope",
        "rule",
        "value",
        "policy",
    }
    return tokens - stop


def split_evidence_units(evidence: str) -> list[str]:
    units: list[str] = []
    for raw_line in str(evidence or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("=====") or line == "---":
            continue
        if line.lower().startswith("source url:"):
            units.append(line)
            continue
        if len(line) <= 260:
            units.append(line)
            continue
        pieces = re.split(r"(?<=[.;:!?。；！？])\s+", line)
        units.extend(piece.strip() for piece in pieces if piece.strip())
    compacted: list[str] = []
    seen: set[str] = set()
    for unit in units:
        key = normalize_for_match(unit)
        if key and key not in seen:
            compacted.append(unit)
            seen.add(key)
    return compacted


def focus_terms(event: dict[str, str]) -> set[str]:
    terms = token_set(
        " ".join(
            str(event.get(key, ""))
            for key in ("subject_label", "predicate_label", "title", "case_context", "domain", "semantic_type")
        )
    )
    predicate = str(event.get("predicate_label", "")).lower()
    if "scope" in predicate:
        terms.update({"scope", "applies", "applicable", "include", "includes", "范围", "适用"})
    if "topic" in predicate:
        terms.update({"topic", "disclosure", "report", "报告", "披露", "主题"})
    if "basis" in predicate:
        terms.update({"basis", "based", "basis", "依据", "基础"})
    if "resource" in predicate:
        terms.update({"resource", "repository", "reference", "mapping", "spreadsheet", "available", "webpage", "tool", "资源", "可用"})
    return terms


def score_unit(unit: str, event: dict[str, str], terms: set[str]) -> tuple[float, dict[str, Any]]:
    unit_norm = normalize_for_match(unit)
    unit_tokens = token_set(unit)
    predicate_tokens = token_set(event.get("predicate_label", ""))
    subject_tokens = token_set(event.get("subject_label", ""))
    context_tokens = token_set(event.get("case_context", ""))
    predicate = normalize_for_match(event.get("predicate_label", ""))
    overlap = unit_tokens & terms
    predicate_overlap = unit_tokens & predicate_tokens
    subject_overlap = unit_tokens & subject_tokens
    context_overlap = unit_tokens & context_tokens
    exact_predicate = predicate in unit_norm
    exact_subject = normalize_for_match(event.get("subject_label", "")) in unit_norm
    value_cue = bool(re.search(r"\b(is|are|means|includes|available|consists|applies|shall|must|=|->)\b", unit, re.I))
    response_cue = bool(re.search(r"\b(in response to|responds? to|stakeholder desire|stakeholder feedback)\b", unit, re.I))
    disclosure_value_cue = bool(
        re.search(r"\b(disclose|discloses|disclosure|provide)\s+(?:information\s+)?(?:about|on|related to)\b", unit, re.I)
    )
    repository_mapping_cue = bool(re.search(r"\b(resource repository|informative reference|mapping to|mapping spreadsheet|spreadsheet)\b", unit, re.I))
    source_url_cue = unit_norm.startswith("source url") or bool(re.search(r"https?://", unit, re.I))
    resource_value_cue = bool(re.search(r"\b(available|spreadsheet|webpage|resource|repository|download|free of charge|found at)\b", unit, re.I))
    scope_value_cue = bool(re.search(r"\b(applies to|used to|intended to|help(?:s)? organizations|consists of|includes)\b", unit, re.I))
    ghg_scope_noise = bool(re.search(r"\bscope\s+[123]\b|\bscope\s+1,\s*scope\s+2\b|\bghg\b", unit, re.I))
    cover_page_noise = bool(
        re.search(r"\b(table of contents|list of tables|list of figures|doi\.org|available free of charge|certain equipment)\b", unit, re.I)
    )
    score = (
        2.0 * len(predicate_overlap)
        + 1.5 * len(subject_overlap)
        + 0.8 * len(context_overlap)
        + 0.5 * len(overlap)
        + (3.0 if exact_predicate else 0.0)
        + (2.0 if exact_subject else 0.0)
        + (1.0 if value_cue else 0.0)
    )
    if "change basis" in predicate and response_cue:
        score += 7.0
    if "disclosure topic" in predicate and disclosure_value_cue:
        score += 8.0
    if "resource status" in predicate and resource_value_cue:
        score += 4.0
    if "resource status" in predicate and repository_mapping_cue:
        score += 7.0
    if "resource status" in predicate and source_url_cue:
        score += 2.0
    if "scope" == predicate and scope_value_cue:
        score += 3.0
    if "scope" in predicate and ghg_scope_noise and "greenhouse" not in normalize_for_match(event.get("subject_label", "")):
        score -= 8.0
    if cover_page_noise and len(predicate_overlap) == 0:
        score -= 4.0
    return score, {
        "score": round(score, 4),
        "predicate_overlap": "|".join(sorted(predicate_overlap)),
        "subject_overlap": "|".join(sorted(subject_overlap)),
        "context_overlap": "|".join(sorted(context_overlap)),
        "value_cue": value_cue,
        "response_cue": response_cue,
        "disclosure_value_cue": disclosure_value_cue,
        "resource_value_cue": resource_value_cue,
        "repository_mapping_cue": repository_mapping_cue,
        "source_url_cue": source_url_cue,
        "scope_value_cue": scope_value_cue,
        "ghg_scope_noise": ghg_scope_noise,
        "cover_page_noise": cover_page_noise,
    }


def context_expanded_text(units: list[str], index: int, *, after: int = 1) -> str:
    selected = [units[index]]
    for offset in range(1, after + 1):
        next_index = index + offset
        if next_index < len(units):
            next_unit = units[next_index]
            # Keep list continuations and nearby explanatory text, but avoid swallowing
            # another high-level document header far away from the matched sentence.
            if len(next_unit) <= 420 or re.search(r"^\s*(?:[-•]|\d+\.|[a-z]\))", next_unit, re.I):
                selected.append(next_unit)
    return " ".join(selected)


def expansion_window_for_event(event: dict[str, str], unit: str) -> int:
    predicate = normalize_for_match(event.get("predicate_label", ""))
    if "change basis" in predicate and re.search(r"\b(in response to|that:|stakeholder desire)\b", unit, re.I):
        return 4
    if "resource status" in predicate and re.search(r"\b(resource repository|mapping|webpage|found at|source url)\b", unit, re.I):
        return 3
    if "disclosure topic" in predicate and re.search(r"\bdisclose\s+(?:information\s+)?(?:about|on)\b", unit, re.I):
        return 2
    return 2


def build_focused_evidence(event: dict[str, str], evidence: str, *, top_n: int) -> tuple[str, list[dict[str, Any]]]:
    units = split_evidence_units(evidence)
    terms = focus_terms(event)
    scored: list[dict[str, Any]] = []
    for index, unit in enumerate(units):
        score, meta = score_unit(unit, event, terms)
        if score <= 0:
            continue
        scored.append({
            "index": index,
            "text": context_expanded_text(units, index, after=expansion_window_for_event(event, unit)),
            **meta,
        })
    ranked = sorted(scored, key=lambda row: (-float(row["score"]), int(row["index"])))
    selected = ranked[:top_n]
    focus_header = [
        "Predicate-aware focus over frozen public evidence.",
        f"event_id: {event.get('event_id', '')}",
        f"subject_label: {event.get('subject_label', '')}",
        f"predicate_label: {event.get('predicate_label', '')}",
        "The spans below are selected only from the supplied frozen evidence window.",
        "",
    ]
    body = []
    for row in selected:
        body.append(f"[FOCUS_SCORE={row['score']} SOURCE_UNIT={row['index']}] {row['text']}")
    focused = "\n".join(focus_header + body)
    return focused, selected


def load_task_manifest(path: Path, event_ids: set[str]) -> list[dict[str, str]]:
    rows = [
        row
        for row in csv.DictReader(path.open(encoding="utf-8-sig"))
        if not event_ids or row.get("event_id") in event_ids
    ]
    if event_ids and len({row.get("event_id", "") for row in rows}) != len(event_ids):
        missing = sorted(event_ids - {row.get("event_id", "") for row in rows})
        raise RuntimeError(f"missing pilot task rows: {missing}")
    return rows


def semantic_mapping_consistency(row: dict[str, str]) -> str:
    derived = str(row.get("ir_semantic_string", ""))
    selected = str(row.get("selected_value", ""))
    if not selected:
        return "NO_SELECTION"
    selected_key = selected.split("=", 1)[0] if "=" in selected else selected
    selected_value = selected.split("=", 1)[1] if "=" in selected else selected
    if selected in derived:
        return "EXACT_DERIVED_LITERAL"
    if selected_key and selected_key in derived and selected_value and selected_value in derived:
        return "KEY_AND_VALUE_IN_IR"
    if selected_key and selected_key in derived:
        return "KEY_ONLY_MATCH_WEAK"
    return "RANKING_ONLY_WEAK"


def main() -> int:
    block_legacy_entrypoint(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", default=",".join(DEFAULT_EVENT_IDS))
    parser.add_argument("--manifest", type=Path, default=TASK_SUMMARY)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--top-n", type=int, default=4)
    parser.add_argument("--qwen-timeout", type=int, default=600)
    parser.add_argument("--focus-only", action="store_true")
    parser.add_argument("--skip-extraction", action="store_true")
    parser.add_argument("--skip-ir", action="store_true")
    parser.add_argument("--no-force-trigger", action="store_true")
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()

    event_ids = set()
    if args.events.strip().upper() not in {"", "ALL", "*"}:
        event_ids = {item.strip().upper() for item in args.events.split(",") if item.strip()}
    paths = configure_paths(BENCHMARK_DIR)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    manifest = load_task_manifest(args.manifest, event_ids)
    event_ids = {row["event_id"] for row in manifest}

    arm_a_raw = args.output_dir / "arm-a-task-router" / "raw_window_metadata_light" / "raw"
    arm_b_raw = args.output_dir / "arm-b-predicate-focus" / "raw_window_metadata_light" / "raw"
    focus_dir = args.output_dir / "focused-evidence"
    arm_a_raw.mkdir(parents=True, exist_ok=True)
    arm_b_raw.mkdir(parents=True, exist_ok=True)
    focus_dir.mkdir(parents=True, exist_ok=True)

    focus_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for item in manifest:
        event_id = item["event_id"]
        event = events[event_id]
        run = int(item["run"])
        seed = int(item.get("seed") or SEED_BASE + run - 1)
        filename = f"{event_id}-run{run}-seed{seed}.json"
        source = ARM_A_RAW / filename
        record = json.loads(source.read_text(encoding="utf-8-sig"))
        evidence = load_evidence(record)
        focused, selected_units = build_focused_evidence(event, evidence, top_n=args.top_n)
        focus_path = focus_dir / f"{event_id}-run{run}-seed{seed}-focused.md"
        focus_path.write_text(focused, encoding="utf-8")

        existing_task_record = (
            PROJECT_DIR
            / "output"
            / "m12-task-formulation-pilot"
            / "arm-b-task-formulation"
            / "raw_window_metadata_light"
            / "raw"
            / filename
        )
        if existing_task_record.is_file():
            arm_a_raw.joinpath(filename).write_text(existing_task_record.read_text(encoding="utf-8-sig"), encoding="utf-8")
        else:
            arm_a_raw.joinpath(filename).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        formulation = route_task_formulation(event, focused)
        if args.focus_only:
            out_record = record
            result = None
        elif args.skip_extraction and (arm_b_raw / filename).is_file():
            out_record = json.loads((arm_b_raw / filename).read_text(encoding="utf-8-sig"))
            result = None
        else:
            out_record, result = apply_task_formulated_extraction(
                record,
                event,
                focused,
                subtype=item.get("missing_subtype", ""),
                seed=seed,
                qwen_timeout=args.qwen_timeout,
                relaxed_css_scope=True,
                force_trigger=not args.no_force_trigger,
            )
            out_record["m13_predicate_focus"] = {
                "focused_evidence_file": str(focus_path.relative_to(PROJECT_DIR)),
                "focus_top_n": args.top_n,
                "selected_units": selected_units,
                "candidate_used": False,
                "oracle_used": False,
                "manual_policy_used": False,
                "relaxed_css_scope": True,
            }
        if not args.focus_only:
            arm_b_raw.joinpath(filename).write_text(json.dumps(out_record, ensure_ascii=False, indent=2), encoding="utf-8")

        for unit in selected_units:
            focus_rows.append(
                {
                    "event_id": event_id,
                    "run": run,
                    "seed": seed,
                    "semantic_type": event.get("semantic_type", ""),
                    "domain": event.get("domain", ""),
                    "predicate_label": event.get("predicate_label", ""),
                    "unit_index": unit["index"],
                    "focus_score": unit["score"],
                    "predicate_overlap": unit["predicate_overlap"],
                    "subject_overlap": unit["subject_overlap"],
                    "context_overlap": unit["context_overlap"],
                    "value_cue": unit["value_cue"],
                    "text": unit["text"],
                }
            )
        summary_rows.append(
            {
                "event_id": event_id,
                "run": run,
                "seed": seed,
                "semantic_type": event.get("semantic_type", ""),
                "domain": event.get("domain", ""),
                "baseline_missing_slots": item.get("missing_slots", ""),
                "missing_subtype": item.get("missing_subtype", ""),
                "extraction_mode": formulation.extraction_mode.value,
                "route_reason": formulation.route_reason,
                "repair_dimension": formulation.repair_dimension,
                "focused_unit_count": len(selected_units),
                "extraction_status": result.extraction_status if result else "resumed",
                "atomic_complete": str(result.atomic_complete).lower() if result else "",
                "missing_slots": "|".join(result.missing_slots) if result else "",
                "derived_result": result.derivation.derived_result if result and result.derivation else "",
                "failure_layer": result.derivation.failure_layer if result and result.derivation else "",
                "runtime_ms": result.runtime_ms if result else 0,
                "raw_output_file": str((arm_b_raw / filename).relative_to(PROJECT_DIR)),
                "focused_evidence_file": str(focus_path.relative_to(PROJECT_DIR)),
            }
        )
        print(
            f"{event_id} focus_units={len(selected_units)} mode={formulation.extraction_mode.value} "
            f"status={(result.extraction_status if result else 'resumed')}",
            flush=True,
        )

    write_csv(args.output_dir / "predicate-focus-units.csv", focus_rows)
    write_csv(args.output_dir / "predicate-focus-extraction-summary.csv", summary_rows)

    if not args.focus_only:
        if not args.skip_ir:
            run_ir(
                arm_a_raw,
                args.output_dir / "arm-a-task-router" / "ir",
                event_ids=event_ids,
                method_name="M12_Task_Formulation",
            )
            run_ir(
                arm_b_raw,
                args.output_dir / "arm-b-predicate-focus" / "ir",
                event_ids=event_ids,
                method_name="M13_Predicate_Focus",
            )
            details_path = args.output_dir / "arm-b-predicate-focus" / "ir" / f"{IR_PREFIX}-details.csv"
            detail_rows = read_csv(details_path)
            for row in detail_rows:
                row["semantic_mapping_consistency"] = semantic_mapping_consistency(row)
            write_csv(details_path, detail_rows)
            write_csv(
                args.output_dir / "predicate-focus-ir-summary.csv",
                [
                    {
                        "attempts": len(detail_rows),
                        "selected": sum(row.get("selection_status") == "SELECTED" for row in detail_rows),
                        "oracle_correct": sum(str(row.get("selection_oracle_correct", "")).lower() == "true" for row in detail_rows),
                        "closure_success": sum(str(row.get("full_closure_success", "")).lower() == "true" for row in detail_rows),
                        "abstains": sum(row.get("selection_status") == "ABSTAIN" for row in detail_rows),
                        "mapping_consistency_counts": json.dumps(dict(Counter(row["semantic_mapping_consistency"] for row in detail_rows)), ensure_ascii=False, sort_keys=True),
                    }
                ],
            )

    (args.output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "experiment": "M13 predicate-aware evidence focus pilot",
                "event_ids": sorted(event_ids),
                "qwen_called": not args.skip_extraction and not args.focus_only,
                "new_retrieval": False,
                "candidate_blind_focus": True,
                "oracle_blind_focus": True,
                "relaxed_css_scope": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
