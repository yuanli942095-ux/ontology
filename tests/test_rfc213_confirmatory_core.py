from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import build_rfc213_confirmatory_core as builder  # noqa: E402
import validate_rfc213_confirmatory_core as validator  # noqa: E402


class SelectionProtocolTests(unittest.TestCase):
    def test_type_quota_solver_reaches_71_each(self) -> None:
        domains = [f"domain-{index:02d}" for index in range(11)]
        quotas = {
            domain: (20 if index < 4 else 19)
            for index, domain in enumerate(domains)
        }
        assignment = builder.type_quota_by_domain(quotas)
        totals = Counter()
        for domain, type_counts in assignment.items():
            self.assertEqual(sum(type_counts.values()), quotas[domain])
            totals.update(type_counts)
        self.assertEqual(
            dict(totals),
            {semantic_type: 71 for semantic_type in builder.SEMANTIC_TYPES},
        )

    def test_candidate_mapping_is_deterministic_permutation(self) -> None:
        first = builder.candidate_id_mapping("H5_E001")
        second = builder.candidate_id_mapping("H5_E001")
        self.assertEqual(first, second)
        self.assertEqual(
            set(first),
            {"CAND_001", "CAND_002", "CAND_003"},
        )
        self.assertEqual(
            set(first.values()),
            {"CAND_001", "CAND_002", "CAND_003"},
        )
        forced = builder.candidate_id_mapping("H5_E001", "CAND_003")
        self.assertEqual(forced["CAND_002"], "CAND_003")

    def test_protocol_prohibits_model_result_access(self) -> None:
        protocol = builder.exclusion_protocol()
        self.assertEqual(protocol["model_result_access"], "PROHIBITED")
        e06 = next(
            row
            for row in protocol["ordered_exclusion_rules"]
            if row["code"] == "E06_UNSUPPORTED_GAMMA_CONSTRUCT"
        )
        self.assertIn("DO_NOT_EXCLUDE", e06["action"])


class GeneratedCoreTests(unittest.TestCase):
    def test_generated_core_passes_validator(self) -> None:
        if not builder.CORE_DIR.is_dir():
            self.skipTest("generated core not built yet")
        result = validator.validate_core(write_outputs=False)
        self.assertEqual(result["error_count"], 0)

    def test_lineage_is_identical_in_output_and_private(self) -> None:
        output = builder.OUTPUT_DIR / "rfc264-to-rfc213-lineage.csv"
        private = (
            builder.CORE_DIR
            / "private/construction/rfc264-to-rfc213-lineage.csv"
        )
        if not output.is_file():
            self.skipTest("generated core not built yet")
        self.assertEqual(builder.sha256_file(output), builder.sha256_file(private))


if __name__ == "__main__":
    unittest.main()
