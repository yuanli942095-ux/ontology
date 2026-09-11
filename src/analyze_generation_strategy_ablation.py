from __future__ import annotations

"""Analyze generation strategy ablation: metrics, paired comparison, semantic fidelity."""

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from generation_strategy_ablation import PILOT_EVENTS_FILE, PILOT_OUTPUT_DIR
from semantic_v2_common import PROJECT_DIR, write_csv

PILOT_EVENT_IDS = set()


def load_details(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not path.is_file():
        return {}
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    return {(row["event_id"], row["run"]): row for row in rows}


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def valid_json_status(status: str) -> bool:
    return status in {"GENERATED", "INVALID_SCHEMA", "ABSTAIN"}


def summarize_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    if not rows:
        return {}
    selected = [row for row in rows if row.get("selection_status") == "SELECTED"]
    first_ok = [row for row in rows if row.get("first_attempt_status") == "GENERATED"]
    return {
        "attempts": len(rows),
        "valid_json_rate": sum(valid_json_status(row.get("generation_status", "")) for row in rows) / len(rows),
        "schema_valid_rate": sum(truthy(row.get("schema_valid", "")) or row.get("generation_status") == "GENERATED" for row in rows) / len(rows),
        "generated_rate": sum(row.get("generation_status") == "GENERATED" for row in rows) / len(rows),
        "ir_ok_rate": sum(row.get("ir_status") == "OK" for row in rows) / len(rows),
        "selected_rate": len(selected) / len(rows),
        "oracle_accuracy": sum(truthy(row.get("selection_oracle_correct", "")) for row in rows) / len(rows),
        "closure_accuracy": sum(truthy(row.get("full_closure_success", "")) for row in rows) / len(rows),
        "precision_given_select": (
            sum(truthy(row.get("selection_oracle_correct", "")) for row in selected) / len(selected) if selected else 0.0
        ),
        "abstain_rate": sum(row.get("selection_status") == "ABSTAIN" for row in rows) / len(rows),
        "avg_calls": sum(float(row.get("generation_call_count") or 1) for row in rows) / len(rows),
        "avg_runtime_ms": sum(float(row.get("runtime_ms") or 0) for row in rows) / len(rows),
        "first_call_success_rate": len(first_ok) / len(rows),
        "retry_rate": sum(float(row.get("generation_call_count") or 1) > 1 for row in rows) / len(rows),
    }


def strict_events(rows: list[dict[str, str]]) -> tuple[int, int]:
    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_event[row["event_id"]].append(row)
    successes = 0
    for event_rows in by_event.values():
        if event_rows and all(truthy(row.get("full_closure_success", "")) for row in event_rows):
            successes += 1
    return successes, len(by_event)


def run_ir_if_needed(arm_dir: Path, event_ids: set[str], *, skip_ir: bool) -> Path:
    raw_dir = arm_dir / "raw_window_metadata_light" / "raw"
    ir_dir = arm_dir / "ir"
    details = ir_dir / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
    if skip_ir and details.is_file():
        return details
    ir_dir.mkdir(parents=True, exist_ok=True)
    only = ",".join(sorted(event_ids))
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_auto_policy_v4_ir_candidate_repair.py"),
        "--benchmark-dir",
        str(PROJECT_DIR / "benchmark" / "external-real-v8-grounded"),
        "--raw-dir",
        str(raw_dir),
        "--output-dir",
        str(ir_dir),
        "--method-name",
        arm_dir.name,
        "--min-score",
        "0.30",
        "--min-margin",
        "0.00",
        "--reranker",
        "constraint",
        "--temporal-unique-top1",
        "--skip-missing-raw",
        "--only",
        only,
    ]
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR)
    return details


def enrich_with_generation(rows: dict[tuple[str, str], dict[str, str]], raw_dir: Path) -> None:
    for key, row in rows.items():
        event_id, run = key
        seed = row.get("seed") or str(20260827 + int(run) - 1)
        raw_path = raw_dir / f"{event_id}-run{run}-seed{seed}.json"
        if not raw_path.is_file():
            continue
        record = json.loads(raw_path.read_text(encoding="utf-8-sig"))
        row["generation_call_count"] = str(record.get("generation_call_count", 1))
        row["first_attempt_status"] = record.get("first_attempt_status", "")
        row["runtime_ms"] = str(record.get("runtime_ms", 0))
        row["schema_valid"] = str(record.get("schema_valid", ""))


def decision_hint(delta_closure: float, delta_p_select: float, delta_valid_json: float) -> str:
    if delta_closure >= 0.03 and delta_p_select >= -0.02 and delta_valid_json > 0:
        return "A_always_structured_better_run_600"
    if abs(delta_closure) <= 0.03 and delta_valid_json > 0:
        return "B_comparable_consider_always_structured"
    if delta_valid_json > 0 and delta_closure <= -0.05:
        return "C_semantic_fidelity_risk_keep_adaptive"
    return "D_mixed_review_by_type"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PILOT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=PILOT_EVENTS_FILE)
    parser.add_argument("--skip-ir", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    event_ids = {row["event_id"] for row in manifest["events"]}
    slot_keys = {(slot["event_id"], str(slot["run"])) for slot in manifest["slots"]}

    adaptive_dir = args.output_dir / "adaptive"
    always_dir = args.output_dir / "always-structured"
    adaptive_details = run_ir_if_needed(adaptive_dir, event_ids, skip_ir=args.skip_ir)
    always_details = run_ir_if_needed(always_dir, event_ids, skip_ir=args.skip_ir)

    adaptive_rows_map = load_details(adaptive_details)
    always_rows_map = load_details(always_details)
    enrich_with_generation(adaptive_rows_map, adaptive_dir / "raw_window_metadata_light" / "raw")
    enrich_with_generation(always_rows_map, always_dir / "raw_window_metadata_light" / "raw")

    adaptive_rows = [adaptive_rows_map[key] for key in sorted(slot_keys) if key in adaptive_rows_map]
    always_rows = [always_rows_map[key] for key in sorted(slot_keys) if key in always_rows_map]

    summary_rows = []
    for label, rows in (("V4.5 Adaptive", adaptive_rows), ("Always Schema", always_rows)):
        metrics = summarize_rows(rows)
        strict_ok, strict_total = strict_events(rows)
        metrics["strategy"] = label
        metrics["strict_events"] = f"{strict_ok}/{strict_total}"
        summary_rows.append(metrics)

    if len(summary_rows) == 2:
        delta = {
            "strategy": "Delta (Always - Adaptive)",
            "valid_json_rate": summary_rows[1]["valid_json_rate"] - summary_rows[0]["valid_json_rate"],
            "schema_valid_rate": summary_rows[1]["schema_valid_rate"] - summary_rows[0]["schema_valid_rate"],
            "ir_ok_rate": summary_rows[1]["ir_ok_rate"] - summary_rows[0]["ir_ok_rate"],
            "closure_accuracy": summary_rows[1]["closure_accuracy"] - summary_rows[0]["closure_accuracy"],
            "precision_given_select": summary_rows[1]["precision_given_select"] - summary_rows[0]["precision_given_select"],
            "abstain_rate": summary_rows[1]["abstain_rate"] - summary_rows[0]["abstain_rate"],
            "avg_calls": summary_rows[1]["avg_calls"] - summary_rows[0]["avg_calls"],
            "avg_runtime_ms": summary_rows[1]["avg_runtime_ms"] - summary_rows[0]["avg_runtime_ms"],
        }
        summary_rows.append(delta)
        hint = decision_hint(
            delta["closure_accuracy"],
            delta["precision_given_select"],
            delta["valid_json_rate"],
        )
    else:
        hint = "insufficient_data"

    by_type_rows = []
    for semantic_type in ("TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE", "ALL"):
        for label, rows in (("V4.5 Adaptive", adaptive_rows), ("Always Schema", always_rows)):
            subset = rows if semantic_type == "ALL" else [row for row in rows if row.get("semantic_type") == semantic_type]
            metrics = summarize_rows(subset)
            metrics["semantic_type"] = semantic_type
            metrics["strategy"] = label
            by_type_rows.append(metrics)

    paired_rows = []
    fidelity_rows = []
    for key in sorted(slot_keys):
        adaptive = adaptive_rows_map.get(key, {})
        always = always_rows_map.get(key, {})
        paired_rows.append(
            {
                "event_id": key[0],
                "run": key[1],
                "semantic_type": adaptive.get("semantic_type") or always.get("semantic_type", ""),
                "domain": adaptive.get("domain") or always.get("domain", ""),
                "adaptive_generation_status": adaptive.get("generation_status", ""),
                "always_generation_status": always.get("generation_status", ""),
                "adaptive_ir_status": adaptive.get("ir_status", ""),
                "always_ir_status": always.get("ir_status", ""),
                "adaptive_selected": adaptive.get("selected_candidate_id", ""),
                "always_selected": always.get("selected_candidate_id", ""),
                "adaptive_oracle": adaptive.get("selection_oracle_correct", ""),
                "always_oracle": always.get("selection_oracle_correct", ""),
                "adaptive_closure": adaptive.get("full_closure_success", ""),
                "always_closure": always.get("full_closure_success", ""),
                "adaptive_calls": adaptive.get("generation_call_count", ""),
                "always_calls": always.get("generation_call_count", ""),
                "adaptive_first_status": adaptive.get("first_attempt_status", ""),
            }
        )
        if adaptive.get("first_attempt_status") == "GENERATED":
            changed_candidate = adaptive.get("selected_candidate_id", "") != always.get("selected_candidate_id", "")
            oracle_flip = truthy(adaptive.get("selection_oracle_correct", "")) and not truthy(
                always.get("selection_oracle_correct", "")
            )
            closure_flip = truthy(adaptive.get("full_closure_success", "")) and not truthy(
                always.get("full_closure_success", "")
            )
            if changed_candidate or oracle_flip or closure_flip:
                fidelity_rows.append(
                    {
                        **paired_rows[-1],
                        "candidate_changed": changed_candidate,
                        "oracle_correct_to_wrong": oracle_flip,
                        "closure_pass_to_fail": closure_flip,
                        "adaptive_ir_json": adaptive.get("ir_json", ""),
                        "always_ir_json": always.get("ir_json", ""),
                    }
                )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "summary.csv", summary_rows)
    write_csv(args.output_dir / "by-type.csv", by_type_rows)
    write_csv(args.output_dir / "paired-attempt-comparison.csv", paired_rows)
    write_csv(args.output_dir / "semantic-fidelity-disagreements.csv", fidelity_rows)
    (args.output_dir / "analysis-summary.json").write_text(
        json.dumps(
            {
                "attempts_per_arm": len(slot_keys),
                "decision_hint": hint,
                "summary": summary_rows,
                "semantic_fidelity_slots": len(fidelity_rows),
                "semantic_fidelity_by_type": dict(
                    Counter(row["semantic_type"] for row in fidelity_rows)
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"decision_hint": hint, "summary": summary_rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
