from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from run_auto_policy_v4_ir_candidate_repair import (  # noqa: E402
    parse_semantic_ir,
    schema_payload_usable,
)


def event(semantic_type: str = "TEMPORAL_VERSION") -> dict[str, str]:
    return {
        "event_id": "EXT_TEST",
        "semantic_type": semantic_type,
        "domain": "digital_identity",
        "subject_label": "NIST SP 800-63B",
        "predicate_label": "revision status",
        "title": "SP 800-63B superseded by 800-63B-4",
        "case_context": "",
    }


class SchemaPayloadTests(unittest.TestCase):
    def test_empty_rules_with_canonical_result_is_usable(self) -> None:
        self.assertTrue(
            schema_payload_usable(
                {
                    "facts": {"supersedes": "NIST SP 800-63B"},
                    "canonical_result": {
                        "family": "nist_revision_change",
                        "change_key": "document_supersession",
                        "change_value": "sp800_63b_replaced_by_sp800_63b_4",
                    },
                    "rules": [],
                }
            )
        )

    def test_empty_facts_and_canonical_is_not_usable(self) -> None:
        self.assertFalse(schema_payload_usable({"facts": {}, "canonical_result": {}, "rules": []}))


class RobustIrJ7Tests(unittest.TestCase):
    def test_rules_missing_does_not_fail_closed_when_canonical_result_exists(self) -> None:
        record = {
            "status": "INVALID_SCHEMA",
            "validation_reason": "rules_missing",
            "response": {
                "semantic_type": "TEMPORAL_VERSION",
                "facts": {"supersedes": "NIST SP 800-63B"},
                "canonical_result": {
                    "family": "nist_revision_change",
                    "change_key": "document_supersession",
                    "change_value": "sp800_63b_replaced_by_sp800_63b_4",
                },
                "rules": [],
            },
        }
        ir = parse_semantic_ir(record, event(), robust_ir=True)
        self.assertEqual(ir.ir_status, "OK")
        self.assertIn("sp800_63b_replaced_by_sp800_63b_4", str(ir.fields.get("result")))

    def test_invalid_schema_without_payload_still_fail_closed(self) -> None:
        record = {
            "status": "INVALID_SCHEMA",
            "validation_reason": "rules_missing",
            "response": {"facts": {}, "canonical_result": {}, "rules": []},
        }
        ir = parse_semantic_ir(record, event(), robust_ir=True)
        self.assertEqual(ir.ir_status, "INVALID_OUTPUT")

    def test_strict_v43_mode_still_kills_invalid_schema(self) -> None:
        record = {
            "status": "INVALID_SCHEMA",
            "validation_reason": "rules_missing",
            "response": {
                "facts": {"supersedes": "NIST SP 800-63B"},
                "canonical_result": {"change_value": "sp800_63b_replaced_by_sp800_63b_4"},
                "rules": [],
            },
        }
        ir = parse_semantic_ir(record, event(), robust_ir=False)
        self.assertEqual(ir.ir_status, "INVALID_OUTPUT")

    def test_incomplete_after_salvage_stays_fail_closed_status(self) -> None:
        record = {
            "status": "INVALID_SCHEMA",
            "validation_reason": "rules_missing",
            "response": {
                "semantic_type": "CROSS_SENTENCE_SCOPE",
                "facts": {"note": "no scope relation marker"},
                "canonical_result": {"family": "other"},
                "rules": [],
            },
        }
        ir = parse_semantic_ir(record, event("CROSS_SENTENCE_SCOPE"), robust_ir=True)
        self.assertEqual(ir.ir_status, "INCOMPLETE_IR")

    def test_retrieval_failed_is_labeled_before_null_response(self) -> None:
        record = {"status": "RETRIEVAL_FAILED", "response": None}
        ir = parse_semantic_ir(record, event(), robust_ir=True)
        self.assertEqual(ir.ir_status, "RETRIEVAL_FAILED")


if __name__ == "__main__":
    unittest.main()
