from __future__ import annotations

from auto_policy_task_formulation import (
    ExtractionMode,
    audit_atomic_slots,
    infer_repair_dimension,
    route_task_formulation,
)


def test_gre_pseudo_events_route_to_normative_attribute():
    event = {
        "event_id": "EXT_E120",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "subject_label": "Cybersecurity Framework 2.0 :: CSF 2.0 reference tool",
        "predicate_label": "tool status",
        "case_context": "CSF 2.0 reference tool",
    }
    evidence = "The CSF 2.0 Reference Tool allows users to explore and export resources."
    formulation = route_task_formulation(event, evidence)
    assert formulation.extraction_mode is ExtractionMode.NORMATIVE_ATTRIBUTE
    assert formulation.route_reason == "predicate_normative_attribute"
    assert infer_repair_dimension(event["predicate_label"]) == "tool"


def test_gre_with_exception_cue_routes_to_rule_exception():
    event = {
        "event_id": "EXT_E003",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "subject_label": "梨种植保险",
        "predicate_label": "冻害损失赔偿公式",
        "case_context": "legacy orchard",
    }
    evidence = "Unless the insured proves otherwise, the general rule applies except when frost damage is documented."
    formulation = route_task_formulation(event, evidence)
    assert formulation.extraction_mode is ExtractionMode.RULE_EXCEPTION


def test_css_scope_predicate_routes_to_predicate_value_scope():
    event = {
        "event_id": "EXT_E197",
        "semantic_type": "CROSS_SENTENCE_SCOPE",
        "subject_label": "IFRS S1 General Requirements :: IFRS S1 cash-flow effect scope",
        "predicate_label": "scope",
    }
    formulation = route_task_formulation(event, "cash-flow effect disclosures under IFRS S1")
    assert formulation.extraction_mode is ExtractionMode.PREDICATE_VALUE_SCOPE
    assert formulation.repair_dimension == "scope"


def test_normative_attribute_slot_audit():
    facts = {
        "subject_entity": "CSF 2.0 Reference Tool",
        "attribute_name": "tool",
        "stated_value": "reference export",
        "evidence_spans": ["Reference Tool"],
    }
    complete, required, filled, missing = audit_atomic_slots(ExtractionMode.NORMATIVE_ATTRIBUTE, facts)
    assert complete
    assert not missing
    assert len(filled) == len(required)
