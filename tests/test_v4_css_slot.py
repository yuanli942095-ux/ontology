from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_policy_v4_css_slot import complete_css_scope_relation  # noqa: E402
from run_auto_policy_v4_ir_candidate_repair import parse_semantic_ir  # noqa: E402


def css_event() -> dict[str, str]:
    return {
        "event_id": "EXT_TEST",
        "semantic_type": "CROSS_SENTENCE_SCOPE",
        "domain": "sustainability_reporting",
        "subject_label": "IFRS S1",
        "predicate_label": "document scope status",
        "title": "IFRS S1 general requirements",
        "case_context": "",
    }


class CssSlotCompletionTests(unittest.TestCase):
    def test_except_where_and_applies_is_ambiguous(self) -> None:
        response = {
            "facts": {
                "scope_condition": "Reporting only climate-related disclosures",
                "applicability_limitation": "not applicable if S2 already includes corresponding requirements",
            },
            "canonical_result": {
                "change_value": "apply_s1_general_requirements_except_where_s2_specific_climate_requirements_exist"
            },
        }
        chosen, klass, _reason = complete_css_scope_relation(response, {"result": "apply_s1 except where s2"})
        self.assertIsNone(chosen)
        self.assertEqual(klass, "S2")

    def test_wcag_cross_scope_without_exception_is_applies_to(self) -> None:
        response = {
            "facts": {"criterion_id": "2.4.11", "level": "AA"},
            "canonical_result": {"family": "wcag21_cross_scope", "criterion_id": "2.4.11", "level": "AA"},
        }
        chosen, klass, _reason = complete_css_scope_relation(
            response, {"result": "wcag21_cross_scope=2.4.11;level=AA"}
        )
        self.assertEqual(chosen, "APPLIES_TO")
        self.assertEqual(klass, "S1")

    def test_wcag_cross_scope_with_exception_condition_is_ambiguous(self) -> None:
        response = {
            "facts": {"exception_condition": "cognitive function test", "criterion_id": "3.3.8"},
            "canonical_result": {"family": "wcag21_cross_scope"},
        }
        chosen, klass, _reason = complete_css_scope_relation(
            response, {"result": "wcag21_cross_scope=1.1.1;level=aaa"}
        )
        self.assertIsNone(chosen)
        self.assertEqual(klass, "S2")

    def test_scope_target_uniquely_applies_to(self) -> None:
        chosen, klass, _reason = complete_css_scope_relation(
            {"facts": {}, "canonical_result": {"family": "regulatory_guidance_non_binding"}},
            {"result": "postmarket cybersecurity management", "scope_target": "postmarket_cybersecurity_management"},
        )
        self.assertEqual(chosen, "APPLIES_TO")
        self.assertEqual(klass, "S1")

    def test_revision_supersession_without_css_marker_is_s2(self) -> None:
        chosen, klass, reason = complete_css_scope_relation(
            {
                "facts": {"supersedes": "NIST SP 800-63A"},
                "canonical_result": {
                    "family": "nist_revision_change",
                    "change_value": "expanded_identity_proofing",
                },
            },
            {"result": "fraud_requirements=expanded_identity_proofing"},
        )
        self.assertIsNone(chosen)
        self.assertEqual(klass, "S2")
        self.assertIn("no unique", reason)

    def test_missing_result_is_s3(self) -> None:
        chosen, klass, _reason = complete_css_scope_relation({"facts": {}}, {})
        self.assertEqual(klass, "S3")
        self.assertIsNone(chosen)

    def test_event_type_name_does_not_force_applies_to(self) -> None:
        response = {
            "facts": {"domain": "digital_identity"},
            "canonical_result": {"family": "nist_revision_change", "change_value": "expanded_identity_proofing"},
        }
        chosen, klass, _reason = complete_css_scope_relation(
            response,
            {"result": "fraud_requirements=expanded_identity_proofing", "statement": "CROSS_SENTENCE_SCOPE"},
        )
        self.assertIsNone(chosen)
        self.assertEqual(klass, "S2")

    def test_does_not_read_candidate_or_oracle_fields(self) -> None:
        response = {
            "facts": {"oracle_candidate_id": "CAND_002", "criterion_id": "2.4.11"},
            "canonical_result": {"family": "wcag21_cross_scope"},
        }
        chosen, klass, _reason = complete_css_scope_relation(
            response, {"result": "wcag21_cross_scope=2.4.11"}
        )
        self.assertEqual(klass, "S1")
        self.assertEqual(chosen, "APPLIES_TO")

    def test_parser_completes_unique_scope_target_when_bundle_misses(self) -> None:
        record = {
            "status": "GENERATED",
            "response": {
                "facts": {
                    "scope_target": "postmarket_cybersecurity_management",
                    "supersedes": "2023 guidance",
                },
                "canonical_result": {
                    "family": "regulatory_guidance_non_binding",
                    "change_value": "supersedes 2023 guidance for postmarket cybersecurity",
                },
                "rules": [{"semantic_result": "supersedes 2023 guidance for postmarket cybersecurity"}],
            },
        }
        ir = parse_semantic_ir(record, css_event(), robust_ir=True)
        self.assertEqual(ir.ir_status, "OK")
        self.assertEqual(ir.fields.get("scope_relation"), "APPLIES_TO")

    def test_parser_does_not_complete_ambiguous_applies_and_exception(self) -> None:
        record = {
            "status": "GENERATED",
            "response": {
                "facts": {
                    "scope_condition": "Reporting only climate-related disclosures",
                    "applicability_limitation": "not applicable if S2 already includes corresponding requirements",
                },
                "canonical_result": {
                    "family": "nist_revision_change",
                    "change_value": "apply_s1_except_where_s2",
                },
                "rules": [{"semantic_result": "apply s1 except where s2"}],
            },
        }
        ir = parse_semantic_ir(record, css_event(), robust_ir=True)
        self.assertEqual(ir.ir_status, "INCOMPLETE_IR")
        self.assertNotIn("scope_relation", ir.fields)


if __name__ == "__main__":
    unittest.main()
