from __future__ import annotations

import copy

from auto_policy_v45_semantic_completion import merge_semantic_completion, should_attempt_completion
from run_auto_policy_v4_ir_candidate_repair import parse_semantic_ir


def test_merge_semantic_completion_fills_rules():
    base = {
        "semantic_type": "TEMPORAL_VERSION",
        "facts": {"domain": "us_regulation"},
        "canonical_result": {"family": "nist_revision_change"},
        "rules": [],
    }
    completion = {
        "semantic_result": "sp800_63b_replaced_by_sp800_63b_4",
        "relation": "REPLACES",
        "canonical_result": {"change_value": "sp800_63b_replaced_by_sp800_63b_4"},
    }
    merged = merge_semantic_completion(base, completion)
    assert merged["rules"][0]["semantic_result"] == "sp800_63b_replaced_by_sp800_63b_4"
    assert merged["facts"]["relation"] == "REPLACES"


def test_should_skip_when_ir_ok():
    event = {"event_id": "EXT_E058", "semantic_type": "GENERAL_RULE_EXCEPTION", "subject_label": "x", "predicate_label": "y"}
    record = {
        "status": "INVALID_SCHEMA",
        "response": {
            "semantic_type": "GENERAL_RULE_EXCEPTION",
            "facts": {},
            "canonical_result": {"semantic_result": "EXCEPTION_OVERRIDES"},
            "rules": [{"priority": 300, "conditions": [], "semantic_result": "EXCEPTION_OVERRIDES"}],
        },
    }
    ir = parse_semantic_ir(record, event, robust_ir=True)
    triggered, _, reason = should_attempt_completion(record, ir)
    assert not triggered
    assert reason == "ir_already_ok"
