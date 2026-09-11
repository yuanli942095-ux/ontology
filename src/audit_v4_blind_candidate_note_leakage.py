from __future__ import annotations

"""Audit candidate note leakage and method candidate-field usage for v4-blind."""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v4-blind"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v4-blind" / "robustness"
METHOD_FILES = [
    PROJECT_DIR / "src" / "run_m13_rule_refinement_pilot.py",
    PROJECT_DIR / "src" / "run_m14_clause_level_gre_css_recovery.py",
    PROJECT_DIR / "src" / "run_m15_temporal_anchor_recovery.py",
    PROJECT_DIR / "src" / "run_m16_candidate_entailment_verifier.py",
    PROJECT_DIR / "src" / "run_auto_policy_v4_ir_candidate_repair.py",
    PROJECT_DIR / "src" / "auto_policy_v4_constraint_rerank.py",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path.resolve())


def scan_method_files() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in METHOD_FILES:
        text = path.read_text(encoding="utf-8", errors="replace")
        rows.append(
            {
                "file": relpath(path),
                "contains_row_notes_access": '".get("notes"' in text or ".get('notes'" in text or '["notes"]' in text,
                "contains_candidate_display_value_access": "display_value" in text,
                "contains_candidate_operation_json_access": "operation_json" in text or "operation" in text,
                "contains_candidate_id_access": "candidate_id" in text,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.benchmark_dir = args.benchmark_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    candidate_csv = args.benchmark_dir / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    candidates = read_csv(candidate_csv)
    leakage_rows = []
    leakage_tokens = ("oracle", "private oracle", "gold", "answer", "标准答案")
    for row in candidates:
        notes = row.get("notes", "")
        leaked = any(token in notes.lower() for token in leakage_tokens)
        leakage_rows.append(
            {
                "event_id": row.get("event_id", ""),
                "candidate_id": row.get("candidate_id", ""),
                "notes": notes,
                "leakage_detected": leaked,
            }
        )

    method_rows = scan_method_files()
    leakage_count = sum(1 for row in leakage_rows if row["leakage_detected"])
    if leakage_count:
        conclusion = (
            "Candidate notes contain oracle-leaking text and must be sanitized before public release. "
            "Scanned frozen-method files do not access candidate notes; they use candidate_id/display_value/operation only."
        )
    else:
        conclusion = (
            "Candidate notes contain no configured leakage tokens. "
            "Scanned frozen-method files do not access candidate notes; they use candidate_id/display_value/operation only."
        )

    summary = {
        "benchmark": args.benchmark_dir.name,
        "candidate_rows": len(candidates),
        "candidate_note_leakage_rows": leakage_count,
        "candidate_note_values": dict(Counter(row["notes"] for row in candidates)),
        "method_files_scanned": len(method_rows),
        "method_files_accessing_candidate_notes": [
            row["file"] for row in method_rows if row["contains_row_notes_access"]
        ],
        "conclusion": conclusion,
    }
    write_csv(args.output_dir / "v4-blind-candidate-note-leakage-details.csv", leakage_rows)
    write_csv(args.output_dir / "v4-blind-candidate-field-usage-audit.csv", method_rows)
    (args.output_dir / "v4-blind-candidate-note-leakage-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
