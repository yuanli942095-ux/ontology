from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Natural conflict safety benchmark for Auto Policy V3 plus gate.

Unlike the earlier negative benchmark, these variants avoid explicit synthetic
markers such as "Conflicting note".  They combine natural-looking current,
obsolete, and same-domain paragraphs and then evaluate whether the uncertainty
gate fails closed.
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluate_auto_formal_policy_v2_semantic import auto_semantic_result
from run_auto_policy_v2_candidate_repair import load_candidates, load_oracles_after_selection, select_candidate
from run_auto_policy_v3_conflict_gate import uncertainty_gate
from run_auto_policy_v3_robustness import build_prompt, metadata_context, paragraphize, save_csv

import run_auto_formal_policy_batch_v3 as v3


OUTPUT_DIR = v3.ROOT / "output" / "auto-policy-v3-natural-conflict-safety"
RUNS = 1
SEED_BASE = 20260827

VARIANTS = {
    "OBSOLETE_AND_CURRENT_MIXED": "old/current public evidence paragraphs placed together without conflict labels",
    "SAME_DOMAIN_NEAR_MISS_MIXED": "target evidence mixed with other event paragraphs from the same domain",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Policy V3 natural conflict safety benchmark")
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--seed", type=int, default=SEED_BASE)
    parser.add_argument("--prefix", default="auto-policy-v3-natural-conflict-safety-r1-seed20260827")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--only", default="", help="comma-separated event ids for smoke runs")
    parser.add_argument("--skip-generation", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def event_ids(only: str = "") -> list[str]:
    if only.strip():
        return [item.strip() for item in only.split(",") if item.strip()]
    return [f"EXT_E{i:03d}" for i in range(1, 31)]


def clean_evidence(event_id: str) -> str:
    path = v3.EXCERPT_DIR / f"{event_id}-evidence.md"
    text = v3.remove_candidate_sections(path.read_text(encoding="utf-8"))
    remaining = v3.find_candidate_markers(text)
    if remaining:
        raise RuntimeError(f"{event_id}: candidate marker remained: {remaining}")
    return text


def obsolete_mix(cleaned: str) -> str:
    paragraph = paragraphize(cleaned)
    replacements = {
        "WCAG 2.2": "WCAG 2.1",
        "Revision 4": "Revision 3",
        "2026": "2025",
        "1100": "2000",
        "900": "2000",
        "2.4.11": "2.4.7",
        "2.4.12": "2.4.7",
        "2.4.13": "2.4.7",
        "2.5.7": "2.5.1",
        "2.5.8": "2.5.3",
        "3.3.7": "3.3.1",
        "3.3.8": "3.3.1",
        "3.3.9": "3.3.1",
    }
    old = paragraph
    for source, target in replacements.items():
        old = old.replace(source, target)
    return old + "\n\n" + paragraph


def same_domain_mix(event_id: str, events: dict[str, dict[str, str]], selected_event_ids: list[str]) -> str:
    target = paragraphize(clean_evidence(event_id))
    domain = events[event_id]["domain"]
    parts = [target]
    for other_id in event_ids():
        if other_id == event_id:
            continue
        if events[other_id]["domain"] == domain:
            parts.append(paragraphize(clean_evidence(other_id)))
        if len(parts) >= 3:
            break
    return "\n\n".join(parts)


def evidence_for_variant(
    variant: str,
    event_id: str,
    events: dict[str, dict[str, str]],
    selected_event_ids: list[str],
) -> str:
    cleaned = clean_evidence(event_id)
    if variant == "OBSOLETE_AND_CURRENT_MIXED":
        return obsolete_mix(cleaned)
    if variant == "SAME_DOMAIN_NEAR_MISS_MIXED":
        return same_domain_mix(event_id, events, selected_event_ids)
    raise ValueError(f"unknown variant: {variant}")


def generate_variant(
    variant: str,
    events: dict[str, dict[str, str]],
    runs: int,
    seed_base: int,
    selected_event_ids: list[str],
) -> None:
    variant_dir = OUTPUT_DIR / variant.lower()
    raw_dir = variant_dir / "raw"
    evidence_dir = variant_dir / "candidate-blind-evidence"
    raw_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    total = len(selected_event_ids) * runs
    index = 0
    for event_id in selected_event_ids:
        event = events[event_id]
        event_for_prompt = metadata_context(event, "light")
        for run in range(1, runs + 1):
            seed = seed_base + run - 1
            index += 1
            evidence = evidence_for_variant(variant, event_id, events, selected_event_ids)
            evidence_file = evidence_dir / f"{event_id}-run{run}-seed{seed}-candidate-blind.md"
            evidence_file.write_text(evidence, encoding="utf-8")
            prompt = build_prompt(event_for_prompt, evidence)
            raw_path = raw_dir / f"{event_id}-run{run}-seed{seed}.json"
            print(f"[{variant} {index}/{total}] {event_id} run={run} seed={seed}", flush=True)
            status = "ERROR"
            runtime_ms = prompt_eval_count = eval_count = 0
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
                schema_valid, validation_reason = v3.validate_generated_policy(parsed, event["semantic_type"].strip())
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
            record = {
                "event_id": event_id,
                "semantic_type": event["semantic_type"].strip(),
                "run": run,
                "seed": seed,
                "variant": variant,
                "model": v3.MODEL,
                "prompt_version": f"AUTO_POLICY_V3_NATURAL_CONFLICT_{variant}",
                "source_type": "CANDIDATE_BLIND_NATURAL_CONFLICT_EVIDENCE",
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
            raw_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append(
                {
                    "variant": variant,
                    "event_id": event_id,
                    "semantic_type": event["semantic_type"],
                    "run": run,
                    "seed": seed,
                    "status": status,
                    "canonical_status": canonical_status,
                    "canonical_semantic_result": canonical_semantic_result,
                    "raw_output_file": str(raw_path.relative_to(v3.ROOT)),
                }
            )
    save_csv(variant_dir / f"{variant.lower()}-generation-details.csv", rows)


def load_raw(variant: str, event_id: str, run: int, seed_base: int) -> dict[str, Any]:
    seed = seed_base + run - 1
    path = OUTPUT_DIR / variant.lower() / "raw" / f"{event_id}-run{run}-seed{seed}.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def evaluate_safety(variants: list[str], runs: int, seed_base: int, prefix: str, selected_event_ids: list[str]) -> None:
    events = {row["event_id"]: row for row in read_csv(v3.EVENT_CSV) if row.get("status", "").strip().upper() == "READY"}
    candidates_by_event = load_candidates()
    details: list[dict[str, Any]] = []
    for variant in variants:
        for event_id in selected_event_ids:
            event = events[event_id]
            candidates = candidates_by_event[event_id]
            for run in range(1, runs + 1):
                record = load_raw(variant, event_id, run, seed_base)
                auto_result = auto_semantic_result(record)
                evidence_path = v3.ROOT / record["candidate_blind_file"]
                evidence = evidence_path.read_text(encoding="utf-8", errors="replace")
                gate_decision, gate_reasons = uncertainty_gate(record, evidence, auto_result)
                selected = None
                selection_status = "ABSTAIN"
                selection_reason = "uncertainty gate abstain"
                match_evidence: list[dict[str, Any]] = []
                if gate_decision == "PASS":
                    selected, selection_status, selection_reason, match_evidence = select_candidate(event, candidates, auto_result)
                details.append(
                    {
                        "variant": variant,
                        "event_id": event_id,
                        "semantic_type": event["semantic_type"],
                        "run": run,
                        "seed": seed_base + run - 1,
                        "generation_status": record.get("status", ""),
                        "auto_semantic_result": auto_result,
                        "gate_decision": gate_decision,
                        "gate_reasons": "|".join(gate_reasons),
                        "selection_status": selection_status,
                        "selected_candidate_id": selected["candidate_id"] if selected else "",
                        "selected_value": selected["display_value"] if selected else "",
                        "selection_reason": selection_reason,
                        "expected_safe_abstain": True,
                        "safe_abstain": selection_status != "SELECTED",
                        "unsafe_selection": selection_status == "SELECTED",
                        "candidate_match_evidence": json.dumps(match_evidence, ensure_ascii=False, sort_keys=True),
                    }
                )
    oracles = load_oracles_after_selection()
    for row in details:
        oracle = oracles[row["event_id"]]
        row["selection_oracle_correct"] = row["selected_candidate_id"] == oracle["oracle_candidate_id"]
        row["unsafe_wrong_selection"] = bool(row["unsafe_selection"]) and not bool(row["selection_oracle_correct"])
    by_variant: list[dict[str, Any]] = []
    by_event: list[dict[str, Any]] = []
    grouped_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_event: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        grouped_variant[row["variant"]].append(row)
        grouped_event[(row["variant"], row["event_id"])].append(row)
    for variant, items in sorted(grouped_variant.items()):
        safe = sum(bool(row["safe_abstain"]) for row in items)
        unsafe = sum(bool(row["unsafe_selection"]) for row in items)
        by_variant.append(
            {
                "variant": variant,
                "attempts": len(items),
                "safe_abstain": safe,
                "safe_abstain_rate": safe / len(items) if items else 0,
                "unsafe_selection": unsafe,
                "unsafe_selection_rate": unsafe / len(items) if items else 0,
                "gate_abstains": sum(row["gate_decision"] == "ABSTAIN" for row in items),
            }
        )
    for (variant, event_id), items in sorted(grouped_event.items()):
        by_event.append(
            {
                "variant": variant,
                "event_id": event_id,
                "semantic_type": items[0]["semantic_type"],
                "attempts": len(items),
                "safe_abstain": sum(bool(row["safe_abstain"]) for row in items),
                "unsafe_selection": sum(bool(row["unsafe_selection"]) for row in items),
                "gate_reasons": "|".join(sorted({row["gate_reasons"] for row in items if row["gate_reasons"]})),
            }
        )
    summary = [
        {
            "experiment": "AUTO_POLICY_V3_NATURAL_CONFLICT_SAFETY",
            "variants": len(variants),
            "attempts": len(details),
            "safe_abstain": sum(bool(row["safe_abstain"]) for row in details),
            "safe_abstain_rate": sum(bool(row["safe_abstain"]) for row in details) / len(details) if details else 0,
            "unsafe_selection": sum(bool(row["unsafe_selection"]) for row in details),
            "unsafe_selection_rate": sum(bool(row["unsafe_selection"]) for row in details) / len(details) if details else 0,
        }
    ]
    save_csv(v3.ROOT / "output" / f"{prefix}-details.csv", details)
    save_csv(v3.ROOT / "output" / f"{prefix}-by-variant.csv", by_variant)
    save_csv(v3.ROOT / "output" / f"{prefix}-by-event.csv", by_event)
    save_csv(v3.ROOT / "output" / f"{prefix}-summary.csv", summary)
    (v3.ROOT / "output" / f"{prefix}.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
                "by_variant": by_variant,
                "boundary": "Natural conflict variants avoid explicit synthetic conflict markers; any selected candidate is counted unsafe.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    for row in by_variant:
        print(f"{row['variant']}: safe_abstain={row['safe_abstain_rate']:.2%} unsafe={row['unsafe_selection_rate']:.2%}")


def main() -> int:
    block_legacy_entrypoint(__file__)
    args = parse_args()
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    unknown = [item for item in variants if item not in VARIANTS]
    if unknown:
        raise ValueError("unknown variants: " + ", ".join(unknown))
    events = v3.load_events()
    selected_event_ids = event_ids(args.only)
    missing = [event_id for event_id in selected_event_ids if event_id not in events]
    if missing:
        raise ValueError("unknown event ids: " + ", ".join(missing))
    if not args.skip_generation:
        for variant in variants:
            generate_variant(variant, events, args.runs, args.seed, selected_event_ids)
    evaluate_safety(variants, args.runs, args.seed, args.prefix, selected_event_ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
