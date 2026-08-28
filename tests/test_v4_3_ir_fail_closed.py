from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyze_auto_policy_v4_3_ir_fail_closed import (  # noqa: E402
    classify_incomplete,
    classify_invalid,
    looks_truncated,
    semantic_payload,
)


class TruncationTests(unittest.TestCase):
    def test_done_reason_length_is_truncated(self) -> None:
        self.assertTrue(looks_truncated({"done_reason": "length", "eval_count": 400}))

    def test_eval_count_at_num_predict_is_truncated(self) -> None:
        self.assertTrue(looks_truncated({"done_reason": "stop", "eval_count": 1000}))

    def test_short_stop_is_not_truncated(self) -> None:
        self.assertFalse(looks_truncated({"done_reason": "stop", "eval_count": 542}))


class InvalidClassificationTests(unittest.TestCase):
    def test_retrieval_failed_is_not_json_error(self) -> None:
        payload = semantic_payload(None)
        code, _label, salvage = classify_invalid(
            {"status": "RETRIEVAL_FAILED", "response": None, "validation_reason": "not_run"},
            payload,
        )
        self.assertEqual(code, "R_RETRIEVAL")
        self.assertEqual(salvage, "RETRIEVAL_GAP")

    def test_null_response_without_raw_text_is_j1_j2(self) -> None:
        payload = semantic_payload(None)
        code, _label, salvage = classify_invalid(
            {"status": "INVALID_JSON", "response": None, "done_reason": "stop", "eval_count": 200},
            payload,
        )
        self.assertEqual(code, "J1_J2")
        self.assertEqual(salvage, "RAW_TEXT_LOST")

    def test_length_stop_is_j6(self) -> None:
        payload = semantic_payload(None)
        code, _label, salvage = classify_invalid(
            {"status": "INVALID_JSON", "response": None, "done_reason": "length", "eval_count": 1000},
            payload,
        )
        self.assertEqual(code, "J6")
        self.assertEqual(salvage, "RAW_TEXT_LOST")

    def test_semantic_type_mismatch_is_j5(self) -> None:
        response = {
            "semantic_type": "GENERAL_RULE_EXCEPTION",
            "facts": {},
            "rules": [{"semantic_result": "scope=listed_substances"}],
        }
        code, _label, salvage = classify_invalid(
            {"status": "INVALID_SCHEMA", "validation_reason": "semantic_type_mismatch", "response": response},
            semantic_payload(response),
        )
        self.assertEqual(code, "J5")
        self.assertEqual(salvage, "FORMAT_REPAIRABLE")

    def test_facts_as_list_is_j4(self) -> None:
        response = {"facts": ["a"], "rules": [{"semantic_result": "a=b"}]}
        code, _label, salvage = classify_invalid(
            {"status": "INVALID_SCHEMA", "validation_reason": "facts_missing", "response": response},
            semantic_payload(response),
        )
        self.assertEqual(code, "J4")
        self.assertEqual(salvage, "FORMAT_REPAIRABLE")

    def test_chinese_alias_keys_are_j3(self) -> None:
        response = {"事实": {"x": "y"}, "规则": [{"semantic_result": "family=updated"}]}
        code, _label, salvage = classify_invalid(
            {"status": "INVALID_SCHEMA", "validation_reason": "facts_missing", "response": response},
            semantic_payload(response),
        )
        self.assertEqual(code, "J3")
        self.assertEqual(salvage, "FORMAT_REPAIRABLE")

    def test_nested_semantic_result_without_canonical_is_j7(self) -> None:
        response = {
            "semantic_type": "TEMPORAL_VERSION",
            "facts": {"subject": "wcag"},
            "rules": [{"priority": 300, "semantic_result": "wcag22_scope=extends_wcag21_except_parsing"}],
        }
        code, _label, salvage = classify_invalid(
            {"status": "INVALID_SCHEMA", "validation_reason": "canonical_result_missing", "response": response},
            semantic_payload(response),
        )
        self.assertEqual(code, "J7")
        self.assertEqual(salvage, "FORMAT_REPAIRABLE")


class IncompleteClassificationTests(unittest.TestCase):
    def test_abstain_without_payload_is_semantic_gap(self) -> None:
        code, _label, salvage = classify_incomplete("model_abstained", {}, semantic_payload({"abstain": True}))
        self.assertEqual(code, "I_ABSTAIN")
        self.assertEqual(salvage, "TRUE_SEMANTIC_GAP")

    def test_missing_result_is_critical(self) -> None:
        code, _label, salvage = classify_incomplete("missing:result", {"subject": "x"}, semantic_payload({}))
        self.assertEqual(code, "I_CRITICAL")
        self.assertEqual(salvage, "TRUE_SEMANTIC_GAP")

    def test_missing_relation_only_is_noncritical_if_result_exists(self) -> None:
        code, _label, salvage = classify_incomplete(
            "missing:relation",
            {"subject": "x", "result": "scope=listed_substances"},
            semantic_payload({"rules": [{"semantic_result": "scope=listed_substances"}]}),
        )
        self.assertEqual(code, "I_NONCRITICAL")
        self.assertEqual(salvage, "DETERMINISTIC_COMPLETE")


if __name__ == "__main__":
    unittest.main()
