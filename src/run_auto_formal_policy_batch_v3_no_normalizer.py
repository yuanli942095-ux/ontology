from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Run AUTO_POLICY_V3 prompt without deterministic canonical normalization.

This is a component ablation for V3.  It uses the same candidate-blind V3
prompt, but evaluates the model's own ``semantic_result`` exactly as emitted.
No post-generation canonical overwrite is applied.
"""

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3


OUTPUT_DIR = v3.ROOT / "output" / "auto-policy-v3-no-normalizer"
RAW_OUTPUT_DIR = OUTPUT_DIR / "raw"
CLEAN_EVIDENCE_DIR = OUTPUT_DIR / "candidate-blind-evidence"
DETAILS_FILE = OUTPUT_DIR / "auto-policy-v3-no-normalizer-generation-details.csv"
BY_EVENT_FILE = OUTPUT_DIR / "auto-policy-v3-no-normalizer-generation-by-event.csv"
SUMMARY_FILE = OUTPUT_DIR / "auto-policy-v3-no-normalizer-generation-summary.json"
LOG_FILE = OUTPUT_DIR / "auto-policy-v3-no-normalizer-generation.log"
PROMPT_VERSION = "AUTO_POLICY_V3_CANONICAL_CANDIDATE_BLIND_NO_NORMALIZER"

RAW_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CLEAN_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def validate_no_normalizer(parsed: Any, expected_semantic_type: str) -> tuple[bool, str]:
    if not isinstance(parsed, dict):
        return False, "not_json_object"
    if parsed.get("abstain", False):
        return True, "abstain"
    actual_type = str(parsed.get("semantic_type", "")).strip()
    if actual_type and actual_type != expected_semantic_type:
        return False, "semantic_type_mismatch"
    if not isinstance(parsed.get("facts"), dict):
        return False, "facts_missing"
    if not isinstance(parsed.get("canonical_result"), dict):
        return False, "canonical_result_missing"
    rules = parsed.get("rules")
    if not isinstance(rules, list) or not rules:
        return False, "rules_missing"
    if not any(
        isinstance(rule, dict) and str(rule.get("semantic_result", "")).strip()
        for rule in rules
    ):
        return False, "semantic_result_missing"
    return True, "ok"


def save_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(records: list[dict[str, Any]], expected_event_ids: list[str]) -> None:
    fieldnames = [
        "event_id",
        "semantic_type",
        "run",
        "seed",
        "status",
        "runtime_ms",
        "prompt_eval_count",
        "eval_count",
        "forbidden_marker_count",
        "forbidden_markers",
        "schema_valid",
        "validation_reason",
        "raw_output_file",
    ]
    save_csv(DETAILS_FILE, records, fieldnames)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["event_id"]].append(record)
    by_event: list[dict[str, Any]] = []
    for event_id in expected_event_ids:
        items = grouped[event_id]
        generated = sum(item["status"] == "GENERATED" for item in items)
        by_event.append(
            {
                "event_id": event_id,
                "semantic_type": items[0]["semantic_type"],
                "attempts": len(items),
                "generated": generated,
                "generated_rate": generated / len(items) if items else 0,
                "strict_all_runs_generated": generated == len(items),
                "invalid_schema": sum(not bool(item["schema_valid"]) for item in items),
                "forbidden_outputs": sum(item["forbidden_marker_count"] > 0 for item in items),
            }
        )
    save_csv(
        BY_EVENT_FILE,
        by_event,
        [
            "event_id",
            "semantic_type",
            "attempts",
            "generated",
            "generated_rate",
            "strict_all_runs_generated",
            "invalid_schema",
            "forbidden_outputs",
        ],
    )

    status_counts = Counter(record["status"] for record in records)
    summary = {
        "experiment": "AUTO_FORMAL_POLICY_BATCH_V3_NO_NORMALIZER",
        "prompt_version": PROMPT_VERSION,
        "model": v3.MODEL,
        "events": len(expected_event_ids),
        "runs": v3.RUNS,
        "attempts": len(records),
        "generated": status_counts.get("GENERATED", 0),
        "generated_rate": status_counts.get("GENERATED", 0) / len(records) if records else 0,
        "abstains": status_counts.get("ABSTAIN", 0),
        "forbidden_output": sum(record["forbidden_marker_count"] > 0 for record in records),
        "invalid_schema": sum(not bool(record["schema_valid"]) for record in records),
        "strict_all_runs_generated_events": sum(row["strict_all_runs_generated"] for row in by_event),
        "average_runtime_ms": sum(int(record["runtime_ms"]) for record in records) / len(records) if records else 0,
        "candidate_blind": True,
        "oracle_used": False,
        "candidate_used": False,
        "manual_formal_policy_used": False,
        "deterministic_normalization": False,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "AUTO FORMAL POLICY BATCH V3 NO NORMALIZER",
        f"generated={summary['generated']}/{summary['attempts']} ({summary['generated_rate']:.2%})",
        f"invalid_schema={summary['invalid_schema']}",
        f"forbidden_output={summary['forbidden_output']}",
        f"average_runtime_ms={summary['average_runtime_ms']:.2f}",
        f"summary={SUMMARY_FILE}",
        f"generated_at_utc={datetime.now(timezone.utc).isoformat()}",
    ]
    LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> int:
    block_legacy_entrypoint(__file__)
    events = v3.load_events()
    expected_event_ids = [f"EXT_E{i:03d}" for i in range(1, 31)]
    missing_events = [event_id for event_id in expected_event_ids if event_id not in events]
    if missing_events:
        raise RuntimeError("Missing event rows: " + ", ".join(missing_events))

    print("=" * 80)
    print("AUTO FORMAL POLICY BATCH V3 NO NORMALIZER")
    print("=" * 80)
    print(f"events={len(expected_event_ids)}, runs={v3.RUNS}, attempts={len(expected_event_ids) * v3.RUNS}")
    print(f"model={v3.MODEL}")
    print(f"prompt_version={PROMPT_VERSION}")
    print("=" * 80)

    records: list[dict[str, Any]] = []
    attempt_index = 0
    for event_id in expected_event_ids:
        event_row = events[event_id]
        semantic_type = event_row["semantic_type"].strip()
        evidence_file = v3.EXCERPT_DIR / f"{event_id}-evidence.md"
        original_evidence = evidence_file.read_text(encoding="utf-8")
        clean_evidence = v3.remove_candidate_sections(original_evidence)
        remaining_markers = v3.find_candidate_markers(clean_evidence)
        if remaining_markers:
            raise RuntimeError(f"{event_id}: candidate marker remained: {remaining_markers}")
        clean_file = CLEAN_EVIDENCE_DIR / f"{event_id}-candidate-blind.md"
        clean_file.write_text(clean_evidence, encoding="utf-8")
        prompt = v3.build_prompt(event_row, clean_evidence)

        for run in range(1, v3.RUNS + 1):
            seed = v3.SEED_BASE + run - 1
            attempt_index += 1
            print(f"[{attempt_index}/{len(expected_event_ids) * v3.RUNS}] {event_id} run={run} seed={seed}")
            raw_output_file = RAW_OUTPUT_DIR / f"{event_id}-run{run}-seed{seed}.json"
            status = "ERROR"
            runtime_ms = 0
            prompt_eval_count = 0
            eval_count = 0
            forbidden_markers: list[str] = []
            parsed: Any = None
            schema_valid = False
            validation_reason = "not_run"
            qwen_response: dict[str, Any] = {}
            try:
                qwen_response, runtime_ms = v3.call_qwen(prompt, seed)
                response_text = str(qwen_response.get("response", ""))
                prompt_eval_count = int(qwen_response.get("prompt_eval_count", 0) or 0)
                eval_count = int(qwen_response.get("eval_count", 0) or 0)
                forbidden_markers = v3.forbidden_output_markers(response_text)
                parsed = v3.extract_json(response_text)
                schema_valid, validation_reason = validate_no_normalizer(parsed, semantic_type)
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
                status = "ERROR"

            output_record = {
                "event_id": event_id,
                "semantic_type": semantic_type,
                "run": run,
                "seed": seed,
                "model": v3.MODEL,
                "prompt_version": PROMPT_VERSION,
                "source_type": "CANDIDATE_BLIND_PUBLIC_EVIDENCE",
                "source_file": str(evidence_file.relative_to(v3.ROOT)),
                "candidate_blind_file": str(clean_file.relative_to(v3.ROOT)),
                "oracle_used": False,
                "candidate_used": False,
                "manual_formal_policy_used": False,
                "deterministic_normalization": False,
                "status": status,
                "runtime_ms": runtime_ms,
                "done_reason": qwen_response.get("done_reason", ""),
                "prompt_eval_count": prompt_eval_count,
                "eval_count": eval_count,
                "forbidden_markers": forbidden_markers,
                "schema_valid": schema_valid,
                "validation_reason": validation_reason,
                "response": parsed,
            }
            raw_output_file.write_text(json.dumps(output_record, ensure_ascii=False, indent=2), encoding="utf-8")
            records.append(
                {
                    "event_id": event_id,
                    "semantic_type": semantic_type,
                    "run": run,
                    "seed": seed,
                    "status": status,
                    "runtime_ms": runtime_ms,
                    "prompt_eval_count": prompt_eval_count,
                    "eval_count": eval_count,
                    "forbidden_marker_count": len(forbidden_markers),
                    "forbidden_markers": "|".join(forbidden_markers),
                    "schema_valid": schema_valid,
                    "validation_reason": validation_reason,
                    "raw_output_file": str(raw_output_file.relative_to(v3.ROOT)),
                }
            )

    write_outputs(records, expected_event_ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
