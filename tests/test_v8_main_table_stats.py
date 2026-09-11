from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyze_external_real_v8_main_table import (  # noqa: E402
    bootstrap_mean_diff_ci,
    method_position,
    paired_mcnemar,
    paper_method_name,
    rate_with_ci,
    summarize_attempts,
)


class NamingTests(unittest.TestCase):
    def test_variant_maps_to_paper_method(self) -> None:
        self.assertEqual(
            paper_method_name("RAW_PROVENANCE_WINDOW"),
            "AUTO_POLICY_V3_RAW_PROVENANCE",
        )
        self.assertEqual(paper_method_name("DIRECT_FREE"), "DIRECT_FREE")

    def test_positioning_is_frozen(self) -> None:
        self.assertEqual(method_position("DIRECT_FREE"), "baseline")
        self.assertEqual(method_position("OPTION_DESCRIPTION"), "aided baseline")
        self.assertEqual(method_position("OPTION_FORMAL_POLICY"), "policy-aided")
        self.assertEqual(
            method_position("OPTION_FORMAL_POLICY_HARD_GATE"),
            "symbolic upper bound",
        )
        self.assertEqual(
            method_position("AUTO_POLICY_V3_RAW_PROVENANCE"),
            "fully automatic",
        )


class RateTests(unittest.TestCase):
    def test_rate_with_ci_all_success(self) -> None:
        row = rate_with_ci(10, 10)
        self.assertEqual(row["estimate"], 1.0)
        self.assertGreater(row["ci95_low"], 0.6)
        self.assertGreaterEqual(row["ci95_high"], 0.99)

    def test_summarize_attempts_splits_clean_only(self) -> None:
        attempts = [
            {
                "event_id": "EXT_E001",
                "domain": "insurance",
                "semantic_type": "TEMPORAL_VERSION",
                "run": 1,
                "oracle_correct": True,
                "strict_event_success": True,
                "abstain": False,
                "invalid": False,
                "selection_stable": True,
                "runtime_ms": 100,
                "leak_severity": "CLEAN",
            },
            {
                "event_id": "EXT_E093",
                "domain": "eu_regulation",
                "semantic_type": "TEMPORAL_VERSION",
                "run": 1,
                "oracle_correct": False,
                "strict_event_success": False,
                "abstain": True,
                "invalid": False,
                "selection_stable": True,
                "runtime_ms": 200,
                "leak_severity": "LEAK_LIGHT",
            },
        ]
        overall = summarize_attempts(attempts, clean_only=False)
        clean = summarize_attempts(attempts, clean_only=True)
        self.assertEqual(overall["events"], 2)
        self.assertEqual(overall["oracle_accuracy"], 0.5)
        self.assertEqual(clean["events"], 1)
        self.assertEqual(clean["oracle_accuracy"], 1.0)
        self.assertEqual(clean["notes"], "CLEAN-only sensitivity; LEAK_LIGHT events excluded")


class PairedTests(unittest.TestCase):
    def test_mcnemar_all_tied_is_one(self) -> None:
        a = [{"event_id": "E1", "run": 1, "oracle_correct": True}]
        b = [{"event_id": "E1", "run": 1, "oracle_correct": True}]
        row = paired_mcnemar("A", "B", a, b)
        self.assertEqual(row["pairs"], 1)
        self.assertEqual(row["discordant_pairs"], 0)
        self.assertEqual(row["exact_mcnemar_p"], 1.0)

    def test_bootstrap_ci_contains_observed_diff(self) -> None:
        a = [
            {"event_id": f"E{i}", "run": 1, "oracle_correct": True}
            for i in range(8)
        ] + [
            {"event_id": "E8", "run": 1, "oracle_correct": False},
            {"event_id": "E9", "run": 1, "oracle_correct": False},
        ]
        b = [
            {"event_id": f"E{i}", "run": 1, "oracle_correct": False}
            for i in range(10)
        ]
        row = bootstrap_mean_diff_ci("A", "B", a, b, seed=1)
        self.assertGreater(row["mean_diff"], 0)
        self.assertLessEqual(row["boot_ci95_low"], row["mean_diff"])
        self.assertGreaterEqual(row["boot_ci95_high"], row["mean_diff"])


if __name__ == "__main__":
    unittest.main()
