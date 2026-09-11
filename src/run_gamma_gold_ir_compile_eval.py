from __future__ import annotations

"""Compile RFC-213 private gold repair constructs into the frozen Gamma MVP IR.

This is an offline confirmatory audit. It reads the private gold candidate ID
only to test whether the frozen repair fragment can express the benchmark gold
repairs. It does not call an LLM, perform retrieval, or use sampling.
"""

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "rfc-213-confirmatory-core"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "rfc-213-confirmatory-core" / "gamma"
SUPPORTED_CONSTRUCTS = {"UPDATE_LITERAL"}
SUPPORTED_OPERATORS = {"REPLACE_PROPERTY_VALUE"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RFC-213 Gamma gold-IR compile eval")
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--prefix", default="rfc213-gamma-gold-ir-compile")
    return parser.parse_args()


def load_candidates(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_csv(path):
        if str(row.get("status", "")).strip().upper() != "READY":
            continue
        parsed = json.loads(row["operation_json"])
        result[(row["event_id"], row["candidate_id"])] = {**row, "operation": parsed}
    return result


def compile_update_literal(operation: dict[str, Any]) -> dict[str, Any]:
    required = ["subject_iri", "predicate_iri", "old_value", "new_value"]
    missing = [field for field in required if field not in operation]
    if missing:
        return {"status": "INVALID_IR", "error": f"missing_fields={missing}"}
    if operation.get("operator") not in SUPPORTED_OPERATORS:
        return {"status": "UNSUPPORTED", "error": f"operator={operation.get('operator')}"}
    old_value = operation.get("old_value")
    new_value = operation.get("new_value")
    if not isinstance(old_value, dict) or not isinstance(new_value, dict):
        return {"status": "INVALID_IR", "error": "old_value/new_value must be objects"}
    if old_value.get("kind") != "literal" or new_value.get("kind") != "literal":
        return {"status": "UNSUPPORTED", "error": "non_literal_update"}
    for label, spec in (("old_value", old_value), ("new_value", new_value)):
        if not spec.get("lexical") or not spec.get("datatype"):
            return {"status": "INVALID_IR", "error": f"{label}_missing_lexical_or_datatype"}
    return {
        "status": "COMPILED",
        "gamma_operator": "UPDATE_LITERAL",
        "subject_iri": operation["subject_iri"],
        "predicate_iri": operation["predicate_iri"],
        "remove_kind": old_value["kind"],
        "remove_lexical": old_value["lexical"],
        "remove_datatype": old_value["datatype"],
        "add_kind": new_value["kind"],
        "add_lexical": new_value["lexical"],
        "add_datatype": new_value["datatype"],
        "triples_removed": 1,
        "triples_added": 1,
    }


def main() -> int:
    args = parse_args()
    benchmark_dir = args.benchmark_dir.resolve()
    output_dir = args.output_dir.resolve()
    construct_path = benchmark_dir / "private" / "construct-audit" / "rfc213-gold-repair-construct-audit.csv"
    candidate_path = benchmark_dir / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    candidates = load_candidates(candidate_path)

    rows: list[dict[str, Any]] = []
    for construct in read_csv(construct_path):
        event_id = construct["event_id"]
        candidate_id = construct["gold_candidate_id"]
        construct_type = construct["construct_type"]
        key = (event_id, candidate_id)
        row: dict[str, Any] = {
            "event_id": event_id,
            "gold_candidate_id": candidate_id,
            "construct_type": construct_type,
            "supported_by_gamma_mvp": construct["supported_by_gamma_mvp"],
            "status": "",
            "error": "",
            "llm_allowed": False,
            "retrieval_allowed": False,
            "sampling_allowed": False,
        }
        if construct_type not in SUPPORTED_CONSTRUCTS or construct["supported_by_gamma_mvp"] != "TRUE":
            row.update({"status": "UNSUPPORTED", "error": f"construct_type={construct_type}"})
        elif key not in candidates:
            row.update({"status": "INVALID_IR", "error": "missing_gold_candidate_operation"})
        else:
            compiled = compile_update_literal(candidates[key]["operation"])
            row.update(compiled)
        rows.append(row)

    counts = Counter(row["status"] for row in rows)
    total = len(rows)
    compiled = counts.get("COMPILED", 0)
    summary = {
        "benchmark": benchmark_dir.name,
        "prefix": args.prefix,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "events": total,
        "compiled": compiled,
        "unsupported": counts.get("UNSUPPORTED", 0),
        "invalid_ir": counts.get("INVALID_IR", 0),
        "compile_success_rate": compiled / total if total else 0,
        "supported_constructs": sorted(SUPPORTED_CONSTRUCTS),
        "llm_allowed": False,
        "retrieval_allowed": False,
        "sampling_allowed": False,
    }

    write_csv(output_dir / f"{args.prefix}-details.csv", rows)
    write_csv(output_dir / f"{args.prefix}-summary.csv", [summary])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{args.prefix}-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("RFC213 Gamma gold-IR compile eval")
    print(f"details={output_dir / f'{args.prefix}-details.csv'}")
    print(f"summary={output_dir / f'{args.prefix}-summary.csv'}")
    print(f"[ALL] compiled={compiled}/{total} ({summary['compile_success_rate']:.2%}) unsupported={summary['unsupported']} invalid_ir={summary['invalid_ir']}")
    return 0 if compiled == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
