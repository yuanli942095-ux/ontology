from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_policy_v4_constraint_rerank import (  # noqa: E402
    StructuredRepair,
    parse_candidate_structure,
    parse_ir_structure,
    rank_candidates,
    score_structured,
    select_ranked,
)
from run_auto_policy_v4_ir_candidate_repair import SemanticIR  # noqa: E402


def ir(
    *,
    semantic_type: str = "TEMPORAL_VERSION",
    relation: str = "REPLACES",
    result: str = "wcag22_scope=extends_wcag21_except_parsing",
    source_family: str = "wcag22_added",
    numbers: list[str] | None = None,
) -> SemanticIR:
    return SemanticIR(
        event_id="EXT_TEST",
        semantic_type=semantic_type,
        ir_status="OK",
        ir_reason="ok",
        fields={"subject": "wcag 2.2", "relation": relation, "result": result},
        normalized_terms=["wcag", "22", "scope", "extends"],
        numbers=numbers or ["2.2"],
        dates=[],
        codes=["2.4.11"],
        source_family=source_family,
    )


class RoleAwareMatchingTests(unittest.TestCase):
    def test_previous_target_loses_when_ir_is_current(self) -> None:
        structured_ir = parse_ir_structure(ir())
        previous = parse_candidate_structure(
            "wcag21_status=previous_target",
            {"operator": "REPLACE_PROPERTY_VALUE", "new_value": {"lexical": "wcag21_status=previous_target"}},
        )
        current = parse_candidate_structure(
            "wcag22_scope=extends_wcag21_except_parsing",
            {"operator": "REPLACE_PROPERTY_VALUE", "new_value": {"lexical": "wcag22_scope=extends_wcag21_except_parsing"}},
        )
        previous_score = score_structured(structured_ir, previous, semantic_type="CROSS_SENTENCE_SCOPE")
        current_score = score_structured(structured_ir, current, semantic_type="CROSS_SENTENCE_SCOPE")
        self.assertGreater(current_score["score"], previous_score["score"])
        self.assertGreater(previous_score["contradiction_penalty"], 0)

    def test_previous_or_not_encoded_loses_to_concrete_new_value(self) -> None:
        structured_ir = parse_ir_structure(
            ir(relation="AMENDS", result="annex_vi=updated_entry", source_family="eu_regulation_amendment")
        )
        stale = parse_candidate_structure("clp_annex_vi_update=previous_or_not_encoded", {})
        fresh = parse_candidate_structure("annex_vi=updated_entry", {})
        self.assertGreater(
            score_structured(structured_ir, fresh, "TEMPORAL_VERSION")["score"],
            score_structured(structured_ir, stale, "TEMPORAL_VERSION")["score"],
        )

    def test_deferred_date_is_not_treated_as_previous_role(self) -> None:
        structured_ir = parse_ir_structure(
            ir(relation="AMENDS", result="application=deferred_date", source_family="eu_regulation_effective_date")
        )
        stale = parse_candidate_structure("transitional_application_date=previous_or_not_encoded", {})
        deferred = parse_candidate_structure("application=deferred_date", {})
        self.assertEqual(deferred.new_status, "CURRENT")
        self.assertGreater(
            score_structured(structured_ir, deferred, "TEMPORAL_VERSION")["score"],
            score_structured(structured_ir, stale, "TEMPORAL_VERSION")["score"],
        )


class StructuredValueTests(unittest.TestCase):
    def test_insurance_split_beats_total_only(self) -> None:
        structured_ir = parse_ir_structure(
            ir(
                relation="EFFECTIVE_FROM",
                result="insurance_amount_split:2026:leaf",
                source_family="insurance_amount_split",
                numbers=["1100", "900", "2000", "2026"],
            )
        )
        total_only = parse_candidate_structure("total_only=2000", {"new_value": {"lexical": "total_only=2000"}})
        split = parse_candidate_structure(
            "spring=1100;summer_autumn=900;total=2000",
            {"new_value": {"lexical": "spring=1100;summer_autumn=900;total=2000"}},
        )
        self.assertGreater(
            score_structured(structured_ir, split, "TEMPORAL_VERSION")["score"],
            score_structured(structured_ir, total_only, "TEMPORAL_VERSION")["score"],
        )

    def test_old_revision_loses_to_expanded_current_requirement(self) -> None:
        structured_ir = parse_ir_structure(
            ir(
                semantic_type="GENERAL_RULE_EXCEPTION",
                relation="UNKNOWN",
                result="fraud_requirements=expanded_identity_proofing",
                source_family="fraud_requirements",
            )
        )
        old = parse_candidate_structure("nist63_3_status=old_revision", {})
        expanded = parse_candidate_structure("nist63_process=assurance_level_selection", {})
        fraud = parse_candidate_structure("fraud_requirements=expanded_identity_proofing", {})
        ranked = rank_candidates(
            structured_ir,
            [
                {"candidate_id": "CAND_001", "display_value": old.raw, "operation": {}},
                {"candidate_id": "CAND_002", "display_value": expanded.raw, "operation": {}},
                {"candidate_id": "CAND_003", "display_value": fraud.raw, "operation": {}},
            ],
            "GENERAL_RULE_EXCEPTION",
        )
        self.assertEqual(ranked[0]["candidate_id"], "CAND_003")


class TiePolicyTests(unittest.TestCase):
    def test_equal_scores_abstain_instead_of_cand001(self) -> None:
        structured = StructuredRepair(
            subject="",
            predicate="",
            old_value="",
            new_value="",
            old_status="UNSPECIFIED",
            new_status="UNSPECIFIED",
            effective_time="",
            relation_direction="UNKNOWN",
            operation_type="",
            assignments={},
            raw="",
        )
        rows = [
            {"candidate_id": "CAND_001", "score": 0.0, "structure": structured},
            {"candidate_id": "CAND_002", "score": 0.0, "structure": structured},
            {"candidate_id": "CAND_003", "score": 0.0, "structure": structured},
        ]
        selected, status, reason, path = select_ranked(rows, min_score=0.0, min_margin=0.0)
        self.assertIsNone(selected)
        self.assertEqual(status, "ABSTAIN")
        self.assertEqual(path, "IR_RANK_TIE_ABSTAIN")
        self.assertIn("tie", reason)

    def test_unique_top_score_is_selected(self) -> None:
        rows = [
            {"candidate_id": "CAND_001", "score": 0.12, "structure": parse_candidate_structure("a=previous_or_not_encoded", {})},
            {"candidate_id": "CAND_002", "score": 0.44, "structure": parse_candidate_structure("b=updated", {})},
            {"candidate_id": "CAND_003", "score": 0.10, "structure": parse_candidate_structure("c=unmodeled", {})},
        ]
        selected, status, reason, path = select_ranked(rows, min_score=0.30, min_margin=0.0)
        self.assertEqual(selected["candidate_id"], "CAND_002")
        self.assertEqual(status, "SELECTED")
        self.assertEqual(path, "IR_CONSTRAINT_RANK")

    def test_temporal_unique_top1_selects_below_min_score_without_contradiction(self) -> None:
        rows = [
            {"candidate_id": "CAND_002", "score": 0.13, "contradiction_penalty": 0.0},
            {"candidate_id": "CAND_003", "score": 0.00, "contradiction_penalty": 0.0},
            {"candidate_id": "CAND_001", "score": 0.00, "contradiction_penalty": 0.55},
        ]
        selected, status, reason, path = select_ranked(
            rows, min_score=0.30, min_margin=0.0, allow_unique_top1=True
        )
        self.assertEqual(selected["candidate_id"], "CAND_002")
        self.assertEqual(status, "SELECTED")
        self.assertEqual(path, "IR_TEMPORAL_UNIQUE_TOP1")

    def test_temporal_unique_top1_still_abstains_on_contradiction(self) -> None:
        rows = [
            {"candidate_id": "CAND_001", "score": 0.13, "contradiction_penalty": 0.55},
            {"candidate_id": "CAND_002", "score": 0.00, "contradiction_penalty": 0.0},
        ]
        selected, status, _reason, path = select_ranked(
            rows, min_score=0.30, min_margin=0.0, allow_unique_top1=True
        )
        self.assertIsNone(selected)
        self.assertEqual(status, "ABSTAIN")
        self.assertEqual(path, "IR_RANK_ABSTAIN")


if __name__ == "__main__":
    unittest.main()
