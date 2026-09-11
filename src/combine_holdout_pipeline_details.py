from __future__ import annotations

"""Combine M13/M14/M15/M16 hold-out pipeline detail CSVs for staged evaluation."""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv

M13_IR_PREFIX = "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827"
M14_IR_PREFIX = "m14-clause-level-gre-css-recovery-v4-ir"
M15_IR_PREFIX = "m15-temporal-anchor-recovery-v4-ir"

SUMMARY_COLUMNS = [
    "event_id",
    "semantic_type",
    "domain",
    "run",
    "seed",
    "closure_success",
    "oracle_correct",
    "selected_candidate_id",
    "used",
    "final_decision_path",
    "previous_decision_path",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def key_for(row: dict[str, str]) -> str:
    return f"{row['event_id']}|{row['run']}|{row['seed']}"


def closure_success(row: dict[str, str]) -> bool:
    return truth(row.get("full_closure_success")) or truth(row.get("closure_success"))


def oracle_correct(row: dict[str, str]) -> bool:
    if "oracle_correct" in row:
        return truth(row.get("oracle_correct"))
    return truth(row.get("selection_oracle_correct"))


def summary_row(
    row: dict[str, str],
    *,
    used: str,
    final_decision_path: str,
    previous_decision_path: str,
) -> dict[str, str]:
    return {
        "event_id": row["event_id"],
        "semantic_type": row.get("semantic_type", ""),
        "domain": row.get("domain", ""),
        "run": row["run"],
        "seed": row["seed"],
        "closure_success": str(closure_success(row)),
        "oracle_correct": str(oracle_correct(row)),
        "selected_candidate_id": row.get("selected_candidate_id", ""),
        "used": used,
        "final_decision_path": final_decision_path,
        "previous_decision_path": previous_decision_path,
    }


def combine_m14(base_details: list[dict[str, str]], recovery_details: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    recovery_by_key = {key_for(row): row for row in recovery_details}
    combined_full: list[dict[str, str]] = []
    combined_summary: list[dict[str, str]] = []
    for base in base_details:
        recovery = recovery_by_key.get(key_for(base))
        base_path = base.get("decision_path", "")
        if recovery and closure_success(recovery):
            chosen = recovery
            used = "M14_RECOVERY"
            final_path = recovery.get("decision_path", "")
            prev_path = base_path
        else:
            chosen = base
            used = "BASE"
            final_path = base_path
            prev_path = base_path
        combined_full.append(chosen)
        combined_summary.append(
            summary_row(chosen, used=used, final_decision_path=final_path, previous_decision_path=prev_path)
        )
    return combined_full, combined_summary


def combine_m15(
    m14_full: list[dict[str, str]],
    m14_summary: list[dict[str, str]],
    m15_recovery_details: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    m15_by_key = {key_for(row): row for row in m15_recovery_details}
    summary_by_key = {key_for(row): row for row in m14_summary}
    combined_full: list[dict[str, str]] = []
    combined_summary: list[dict[str, str]] = []
    for row in m14_full:
        k = key_for(row)
        prev_summary = summary_by_key[k]
        prev_path = prev_summary.get("final_decision_path") or row.get("decision_path", "")
        if row.get("semantic_type") == "TEMPORAL_VERSION":
            recovery = m15_by_key.get(k)
            if recovery and closure_success(recovery):
                chosen = recovery
                used = "M15_TEMPORAL"
                final_path = recovery.get("decision_path", "")
            elif closure_success(row):
                chosen = row
                used = "BASE"
                final_path = row.get("decision_path", "")
            else:
                chosen = recovery or row
                used = "M15_TEMPORAL" if recovery else "BASE"
                final_path = chosen.get("decision_path", "")
        else:
            chosen = row
            used = "M14_GRE_CSS"
            final_path = prev_summary.get("final_decision_path") or row.get("decision_path", "")
        combined_full.append(chosen)
        combined_summary.append(
            summary_row(chosen, used=used, final_decision_path=final_path, previous_decision_path=prev_path)
        )
    return combined_full, combined_summary


def combine_m16(
    m15_full: list[dict[str, str]],
    m15_summary: list[dict[str, str]],
    m16_details: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    m16_by_key = {key_for(row): row for row in m16_details}
    summary_by_key = {key_for(row): row for row in m15_summary}
    combined_full: list[dict[str, str]] = []
    combined_summary: list[dict[str, str]] = []
    for row in m15_full:
        k = key_for(row)
        prev_summary = summary_by_key[k]
        prev_path = prev_summary.get("final_decision_path") or row.get("decision_path", "")
        m16 = m16_by_key.get(k)
        final_path = prev_summary.get("final_decision_path") or row.get("decision_path", "")
        if (
            row.get("semantic_type") in {"GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}
            and m16
            and str(m16.get("selected_candidate_id", "")).strip()
        ):
            # M16 adds a candidate verdict but does not reconstruct Semantic IR.
            # Preserve the candidate-blind upstream IR audit fields in the final row.
            chosen = {**row, **m16}
            used = "M16_ENTAILMENT"
            final_path = m16.get("decision_path", "M16_ENTAILMENT_SELECT")
        else:
            chosen = row
            used = prev_summary.get("used", "BASE")
        combined_full.append(chosen)
        combined_summary.append(
            summary_row(chosen, used=used, final_decision_path=final_path, previous_decision_path=prev_path)
        )
    return combined_full, combined_summary


def summarize_attempts(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    from collections import Counter, defaultdict

    by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_type[row.get("semantic_type", "UNKNOWN")].append(row)
        by_type["ALL"].append(row)

    out: list[dict[str, Any]] = []
    for semantic_type in ("ALL", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"):
        subset = by_type.get(semantic_type, [])
        if not subset:
            continue
        attempts = len(subset)
        closure = sum(truth(row.get("closure_success", "")) for row in subset)
        oracle = sum(truth(row.get("oracle_correct", "")) for row in subset)
        events = len({row["event_id"] for row in subset})
        strict = sum(
            1
            for event_id in {row["event_id"] for row in subset}
            if all(truth(item.get("closure_success", "")) for item in subset if item["event_id"] == event_id)
        )
        out.append(
            {
                "semantic_type": semantic_type,
                "attempts": attempts,
                "closure_success": closure,
                "closure_accuracy": round(closure / attempts, 4) if attempts else 0.0,
                "oracle_success": oracle,
                "oracle_accuracy": round(oracle / attempts, 4) if attempts else 0.0,
                "events": events,
                "strict_event_successes": strict,
                "strict_event_accuracy": round(strict / events, 4) if events else 0.0,
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("m14", "m15", "m16"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-details", type=Path, required=True)
    parser.add_argument("--recovery-details", type=Path, default=None)
    parser.add_argument("--prior-full-details", type=Path, default=None)
    parser.add_argument("--prior-summary-details", type=Path, default=None)
    args = parser.parse_args()

    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    base_details = read_csv(args.base_details.resolve())

    if args.stage == "m14":
        if not args.recovery_details:
            raise SystemExit("--recovery-details required for m14 stage")
        full, summary = combine_m14(base_details, read_csv(args.recovery_details.resolve()))
        prefix = "m14-full-holdout-combined"
    elif args.stage == "m15":
        if not args.prior_summary_details or not args.recovery_details:
            raise SystemExit("--prior-summary-details and --recovery-details required for m15 stage")
        prior_full = read_csv(args.prior_full_details.resolve()) if args.prior_full_details else base_details
        full, summary = combine_m15(
            prior_full,
            read_csv(args.prior_summary_details.resolve()),
            read_csv(args.recovery_details.resolve()),
        )
        prefix = "m15-full-holdout-combined"
    else:
        if not args.prior_summary_details:
            raise SystemExit("--prior-summary-details required for m16 stage")
        prior_full = read_csv(args.prior_full_details.resolve()) if args.prior_full_details else base_details
        recovery = read_csv(args.recovery_details.resolve()) if args.recovery_details else []
        full, summary = combine_m16(prior_full, read_csv(args.prior_summary_details.resolve()), recovery)
        prefix = "m16-full-holdout-combined"

    full_path = args.output_dir / f"{prefix}-full-details.csv"
    summary_path = args.output_dir / f"{prefix}-details.csv"
    write_csv(full_path, full)
    write_csv(summary_path, summary)
    stats = summarize_attempts(summary)
    stats_path = args.output_dir / f"{prefix}-summary.csv"
    write_csv(stats_path, stats)
    binding = {
        "stage": args.stage,
        "output_dir": str(args.output_dir.relative_to(PROJECT_DIR)),
        "full_details": str(full_path.relative_to(PROJECT_DIR)),
        "summary_details": str(summary_path.relative_to(PROJECT_DIR)),
        "summary_stats": str(stats_path.relative_to(PROJECT_DIR)),
        "attempts": len(summary),
    }
    (args.output_dir / f"{prefix}-binding.json").write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(binding, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
