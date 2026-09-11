from __future__ import annotations

"""Audit whether RFC-213 Gold literals are derivable from reviewed evidence.

This is an offline benchmark-suitability audit. It reads private Gold only for
scoring and must never feed its outputs into Direct-IR generation.
"""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from rfc213_direct_repair_ir import benchmark_literal_from_span, source_windows
from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "rfc-213-confirmatory-core"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "rfc-213-confirmatory-core" / "phase3-direct-ir" / "target-contract-audit"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    benchmark = args.benchmark_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    candidates = {
        (row["event_id"], row["candidate_id"]): json.loads(row["operation_json"])
        for row in read_csv(benchmark / "repair-stage/candidates/external-real-candidate-template.csv")
    }
    details: list[dict[str, Any]] = []
    for oracle in read_csv(benchmark / "private/oracle/external-real-oracle-template.csv"):
        operation = candidates[(oracle["event_id"], oracle["oracle_candidate_id"])]
        spans = json.loads(oracle["evidence_spans_json"])
        quote = str(spans[0].get("quote", "")) if spans else ""
        expected = operation["new_value"]["lexical"]
        excerpt_path = benchmark / "public/excerpts" / f"{oracle['event_id']}-evidence.md"
        evidence = excerpt_path.read_text(encoding="utf-8")
        public_windows = source_windows(evidence)
        attempts = [("oracle_quote", quote), *[(f"source_window_{index}", text) for index, text in public_windows]]
        derivations = [
            (label, text, benchmark_literal_from_span(operation["old_value"]["lexical"], text))
            for label, text in attempts
            if text
        ]
        match = next((item for item in derivations if item[2] == expected), None)
        derived = match[2] if match else (derivations[0][2] if derivations else "")
        details.append(
            {
                "event_id": oracle["event_id"],
                "derivable_from_reviewed_evidence": match is not None,
                "matching_public_span": match[0] if match else "",
                "derived_literal": derived,
                "gold_literal": expected,
                "generation_input_allowed": False,
                "purpose": "offline_target_contract_audit_only",
            }
        )

    matches = sum(row["derivable_from_reviewed_evidence"] for row in details)
    summary = {
        "events": len(details),
        "derivable": matches,
        "not_derivable": len(details) - matches,
        "derivable_rate": matches / len(details) if details else 0,
        "direct_ir_exact_target_contract_ready": matches == len(details),
        "candidate_or_oracle_visible_to_generator": False,
    }
    write_csv(output / "rfc213-direct-ir-target-contract-details.csv", details)
    (output / "rfc213-direct-ir-target-contract-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["direct_ir_exact_target_contract_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
