from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path

from rfc213_direct_repair_ir_v2 import resolve_source_window
from semantic_v2_common import PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark/rfc-213-confirmatory-core"
RAW = PROJECT_DIR / "output/rfc-213-confirmatory-core/phase3-direct-ir/engineering-v1/raw-predicted-ir"


def raw_ir(event_id: str) -> dict:
    path = RAW / f"{event_id}-run1-seed20260829.json"
    return json.loads(path.read_text(encoding="utf-8"))["predicted_ir_raw"]


def evidence(event_id: str) -> str:
    return (BENCHMARK / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")


def gold_literal(event_id: str) -> str:
    with (BENCHMARK / "private/construct-audit/rfc213-gold-repair-construct-audit.csv").open(
        encoding="utf-8-sig", newline=""
    ) as file:
        gold_id = next(row["gold_candidate_id"] for row in csv.DictReader(file) if row["event_id"] == event_id)
    with (BENCHMARK / "repair-stage/candidates/external-real-candidate-template.csv").open(
        encoding="utf-8-sig", newline=""
    ) as file:
        operation = next(
            json.loads(row["operation_json"])
            for row in csv.DictReader(file)
            if row["event_id"] == event_id and row["candidate_id"] == gold_id
        )
    return operation["new_value"]["lexical"]


class DirectRepairIRV2Tests(unittest.TestCase):
    def test_full_window_resolution_recovers_boundary_failure(self) -> None:
        result = resolve_source_window(raw_ir("H5_E041"), evidence("H5_E041"))
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["canonical_ir"]["replacement"]["new_value"]["lexical"], gold_literal("H5_E041"))

    def test_wrong_evidence_region_is_not_silently_corrected(self) -> None:
        result = resolve_source_window(raw_ir("H5_E051"), evidence("H5_E051"))
        self.assertEqual(result["status"], "RESOLVED")
        self.assertNotEqual(result["canonical_ir"]["replacement"]["new_value"]["lexical"], gold_literal("H5_E051"))

    def test_different_containing_window_values_fail_closed(self) -> None:
        result = resolve_source_window(raw_ir("H5_E152"), evidence("H5_E152"))
        self.assertEqual(result["status"], "AMBIGUOUS_WINDOW")
        self.assertIsNone(result["canonical_ir"])

    def test_missing_span_fails_closed(self) -> None:
        ir = raw_ir("H5_E001")
        ir["evidence_spans"] = ["not present in any frozen evidence window"]
        result = resolve_source_window(ir, evidence("H5_E001"))
        self.assertEqual(result["status"], "NO_WINDOW_MATCH")


if __name__ == "__main__":
    unittest.main()
