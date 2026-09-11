from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_policy_v45_generation import (  # noqa: E402
    ReliabilityMode,
    auto_policy_json_schema,
    budget_for_mode,
    mode_for_failure_bucket,
)


class V45GenerationTests(unittest.TestCase):
    def test_mode_mapping(self) -> None:
        self.assertEqual(mode_for_failure_bucket("TRUNCATED"), ReliabilityMode.EXTENDED_BUDGET)
        self.assertEqual(mode_for_failure_bucket("SEVERELY_DAMAGED"), ReliabilityMode.STRUCTURED_OUTPUT)
        self.assertEqual(mode_for_failure_bucket("WRONG_BOUNDARY"), ReliabilityMode.STRUCTURED_OUTPUT)

    def test_extended_budget_is_larger(self) -> None:
        predict, ctx = budget_for_mode(ReliabilityMode.EXTENDED_BUDGET)
        self.assertGreater(predict, 1000)
        self.assertGreater(ctx, 4096)

    def test_json_schema_has_required_policy_fields(self) -> None:
        schema = auto_policy_json_schema()
        self.assertIn("semantic_type", schema["required"])
        self.assertIn("facts", schema["required"])
        self.assertIn("rules", schema["required"])
        self.assertIn("canonical_result", schema["required"])


if __name__ == "__main__":
    unittest.main()
