from __future__ import annotations

"""Classify schema/IR failures for V4.5 adaptive arm (C1–C7 taxonomy)."""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_v4_schema_contract import classify_contract_failure, prepare_policy_response
from run_auto_policy_v4_ir_candidate_repair import configure_paths, parse_semantic_ir, read_csv, ready
from semantic_v2_common import PROJECT_DIR, write_csv

DEFAULT_FROZEN_RAW = (
    PROJECT_DIR
    / "output"
    / "schema-contract-repair-ablation"
    / "frozen-v45-adaptive-raw"
    / "raw_window_metadata_light"
    / "raw"
)
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "schema-contract-repair-ablation"


def load_evidence_map(evidence_dir: Path) -> dict[tuple[str, int, int], str]:
    mapping: dict[tuple[str, int, int], str] = {}
    if not evidence_dir.is_dir():
        return mapping
    for path in evidence_dir.glob("*-candidate-blind.md"):
        name = path.name
        event_id = name.split("-run", 1)[0]
        tail = name.split("-run", 1)[1]
        run = int(tail.split("-seed", 1)[0])
        seed = int(tail.split("-seed", 1)[1].split("-candidate-blind", 1)[0])
        mapping[(event_id, run, seed)] = path.read_text(encoding="utf-8")
    return mapping


def generation_status_from_record(record: dict[str, Any], schema_valid: bool, parsed: Any) -> str:
    if record.get("status") == "RETRIEVAL_FAILED":
        return "RETRIEVAL_FAILED"
    if record.get("status") == "INPUT_CONSTRUCTION_ERROR":
        return "INPUT_CONSTRUCTION_ERROR"
    if parsed is None:
        return "INVALID_JSON"
    if isinstance(parsed, dict) and parsed.get("abstain") is True:
        return "ABSTAIN"
    if schema_valid:
        return "GENERATED"
    return "INVALID_SCHEMA"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=PROJECT_DIR / "benchmark" / "external-real-v8-grounded")
    parser.add_argument("--frozen-raw-dir", type=Path, default=DEFAULT_FROZEN_RAW)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed-base", type=int, default=20260827)
    args = parser.parse_args()

    paths = configure_paths(args.benchmark_dir)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    evidence_dir = args.frozen_raw_dir.parent.parent / "candidate-blind-evidence"
    evidence_map = load_evidence_map(evidence_dir)

    rows: list[dict[str, Any]] = []
    for event_id, event in sorted(events.items()):
        for run in range(1, args.runs + 1):
            seed = args.seed_base + run - 1
            raw_path = args.frozen_raw_dir / f"{event_id}-run{run}-seed{seed}.json"
            if not raw_path.is_file():
                continue
            record = json.loads(raw_path.read_text(encoding="utf-8-sig"))
            evidence = evidence_map.get((event_id, run, seed), "")
            if not evidence and record.get("candidate_blind_file"):
                evidence_path = PROJECT_DIR / str(record["candidate_blind_file"])
                if evidence_path.is_file():
                    evidence = evidence_path.read_text(encoding="utf-8")

            response = record.get("response")
            parsed, _canonical_status, _canonical_result, schema_valid, validation_reason, _repair = prepare_policy_response(
                response if isinstance(response, dict) else None,
                event,
                evidence,
                apply_contract_repair=False,
            )
            generation_status = generation_status_from_record(record, schema_valid, parsed)
            ir_record = {**record, "status": generation_status, "response": parsed}
            ir = parse_semantic_ir(ir_record, event, robust_ir=True)
            failure_class, failure_reason = classify_contract_failure(
                parsed if isinstance(parsed, dict) else None,
                event["semantic_type"].strip(),
                schema_valid=schema_valid,
                validation_reason=validation_reason,
            )
            needs_review = generation_status in {"INVALID_SCHEMA", "INVALID_JSON"} or ir.ir_status != "OK"
            rows.append(
                {
                    "event_id": event_id,
                    "semantic_type": event["semantic_type"],
                    "domain": event.get("domain", ""),
                    "run": run,
                    "seed": seed,
                    "generation_status": generation_status,
                    "schema_valid": schema_valid,
                    "validation_reason": validation_reason,
                    "ir_status": ir.ir_status,
                    "ir_reason": ir.ir_reason,
                    "failure_class": failure_class if needs_review else "OK",
                    "failure_reason": failure_reason if needs_review else "ok",
                    "repairable": failure_class in {"C1", "C2", "C3", "C4"} if needs_review else False,
                    "raw_output_file": str(raw_path.relative_to(PROJECT_DIR)),
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "failure-classification.csv", rows)
    summary = {
        "attempts": len(rows),
        "schema_invalid": sum(not row["schema_valid"] for row in rows),
        "ir_incomplete": sum(row["ir_status"] != "OK" for row in rows),
        "failure_class_counts": dict(Counter(row["failure_class"] for row in rows if row["failure_class"] != "OK")),
        "repairable_counts": dict(
            Counter(row["failure_class"] for row in rows if row["repairable"])
        ),
    }
    (args.output_dir / "failure-classification-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
