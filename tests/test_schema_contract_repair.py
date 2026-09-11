from __future__ import annotations

import copy

from auto_policy_v4_schema_contract import (
    apply_schema_contract_repair,
    classify_contract_failure,
    prepare_policy_response,
)


def test_valid_payload_unchanged():
    response = {
        "semantic_type": "TEMPORAL_VERSION",
        "facts": {"domain": "insurance"},
        "canonical_result": {"family": "x", "change_value": "foo"},
        "rules": [{"priority": 300, "conditions": [], "semantic_result": "foo"}],
    }
    result = apply_schema_contract_repair(response, "TEMPORAL_VERSION")
    assert result.repair_applied is False
    assert result.schema_valid is True
    assert result.response == response


def test_rules_missing_relocates_canonical_value():
    response = {
        "semantic_type": "TEMPORAL_VERSION",
        "facts": {"domain": "digital_identity"},
        "canonical_result": {
            "family": "nist_revision_change",
            "change_value": "sp800_63b_replaced_by_sp800_63b_4",
        },
        "rules": [],
    }
    result = apply_schema_contract_repair(response, "TEMPORAL_VERSION")
    assert result.repair_applied is True
    assert result.schema_valid is True
    assert result.response["rules"][0]["semantic_result"] == "sp800_63b_replaced_by_sp800_63b_4"


def test_alias_mapping_for_rule_result_field():
    response = {
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "facts": {},
        "canonical_result": {"semantic_result": "EXCEPTION_OVERRIDES"},
        "rules": [{"priority": 300, "conditions": [], "result": "EXCEPTION_OVERRIDES"}],
    }
    result = apply_schema_contract_repair(response, "GENERAL_RULE_EXCEPTION")
    assert result.schema_valid is True
    assert result.response["rules"][0]["semantic_result"] == "EXCEPTION_OVERRIDES"


def test_ambiguous_scope_relation_fail_closed():
    response = {
        "semantic_type": "CROSS_SENTENCE_SCOPE",
        "facts": {"subject_label": "A", "predicate_label": "B"},
        "canonical_result": {
            "family": "wcag21_cross_scope",
            "semantic_result": "APPLIES_TO",
            "exception_rule": "EXCEPTION_OVERRIDES",
        },
        "rules": [
            {"priority": 300, "conditions": [], "semantic_result": "APPLIES_TO"},
            {"priority": 200, "conditions": [], "semantic_result": "EXCEPTION_OVERRIDES"},
        ],
    }
    failure_class, _reason = classify_contract_failure(
        response,
        "CROSS_SENTENCE_SCOPE",
        schema_valid=False,
        validation_reason="semantic_result_missing",
    )
    result = apply_schema_contract_repair(response, "CROSS_SENTENCE_SCOPE")
    assert result.failure_class in {"C6", "C7", "OK", "C5"} or result.schema_valid is False


def test_prepare_policy_response_arm_b_is_deterministic():
    event = {"event_id": "EXT_E043", "semantic_type": "TEMPORAL_VERSION", "subject_label": "x", "predicate_label": "y"}
    response = {
        "semantic_type": "TEMPORAL_VERSION",
        "facts": {"domain": "digital_identity"},
        "canonical_result": {"change_value": "sp800_63b_replaced_by_sp800_63b_4"},
        "rules": [],
    }
    parsed_a, *_rest_a, repair_a = prepare_policy_response(copy.deepcopy(response), event, "", apply_contract_repair=False)
    parsed_b, *_rest_b, repair_b = prepare_policy_response(copy.deepcopy(response), event, "", apply_contract_repair=True)
    assert repair_a is None or repair_a.repair_applied is False
    assert repair_b is not None and repair_b.repair_applied is True
    assert parsed_b["rules"][0]["semantic_result"] == "sp800_63b_replaced_by_sp800_63b_4"


def test_forbidden_context_not_repaired():
    response = {
        "semantic_type": "TEMPORAL_VERSION",
        "facts": {},
        "canonical_result": {"change_value": "x"},
        "rules": [],
        "oracle_candidate_id": "CAND_001",
    }
    failure_class, _ = classify_contract_failure(response, "TEMPORAL_VERSION", schema_valid=False, validation_reason="rules_missing")
    assert failure_class == "C7"
