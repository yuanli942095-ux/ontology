from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_policy_robust_json_recovery import (  # noqa: E402
    classify_failure,
    recover_json_object,
)


class RobustJsonRecoveryTests(unittest.TestCase):
    def test_strip_code_fence(self) -> None:
        text = '```json\n{"facts": {}, "rules": []}\n```'
        result = recover_json_object(text)
        self.assertTrue(result.salvageable)
        self.assertEqual(result.bucket, "FENCE_OR_PREAMBLE")
        self.assertIsInstance(result.parsed, dict)

    def test_preamble_before_json(self) -> None:
        text = 'Here is the result:\n{"facts": {"a": 1}, "rules": []}'
        result = recover_json_object(text)
        self.assertTrue(result.salvageable)
        self.assertEqual(result.bucket, "FENCE_OR_PREAMBLE")
        self.assertEqual(result.parsed["facts"]["a"], 1)

    def test_trailing_comma(self) -> None:
        text = '{"facts": {"a": 1,}, "rules": [],}'
        result = recover_json_object(text)
        self.assertTrue(result.salvageable)
        self.assertEqual(result.bucket, "MINOR_SYNTAX")

    def test_multiple_json_picks_first(self) -> None:
        text = '{"facts": {"first": true}, "rules": []}\n{"facts": {"second": true}, "rules": []}'
        result = recover_json_object(text)
        self.assertTrue(result.salvageable)
        self.assertEqual(result.bucket, "MULTIPLE_JSON")
        self.assertTrue(result.parsed["facts"]["first"])

    def test_truncated_unclosed(self) -> None:
        text = '{"facts": {"a": 1}, "rules": ['
        result = recover_json_object(text)
        self.assertFalse(result.salvageable)
        self.assertEqual(result.bucket, "TRUNCATED")

    def test_metadata_only_truncated_proxy(self) -> None:
        record = {"done_reason": "length", "eval_count": 1000, "response": None}
        result = classify_failure(None, record)
        self.assertEqual(result.bucket, "TRUNCATED_PROXY")
        self.assertFalse(result.salvageable)

    def test_metadata_only_non_truncated_proxy(self) -> None:
        record = {"done_reason": "stop", "eval_count": 876, "response": None}
        result = classify_failure(None, record)
        self.assertEqual(result.bucket, "NON_TRUNCATED_PROXY")
        self.assertFalse(result.salvageable)

    def test_does_not_invent_fields(self) -> None:
        text = '{"facts": {}}'
        result = recover_json_object(text)
        self.assertIsInstance(result.parsed, dict)
        self.assertNotIn("rules", result.parsed)


if __name__ == "__main__":
    unittest.main()
