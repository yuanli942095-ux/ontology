from __future__ import annotations

from auto_policy_m12_decomposed_extraction import (
    atomic_complete,
    derive_gre,
    derive_temporal,
)


def test_temporal_derivation_success():
    facts = {
        "subject": "nist sp 800-63",
        "previous_value": "sp800_63b",
        "current_value": "sp800_63b_4",
        "effective_time": "2025-08",
        "change_relation": "supersedes",
        "previous_still_valid": False,
        "evidence_spans": ["superseded"],
    }
    audit = derive_temporal(facts)
    assert audit.derivation_status == "DERIVED"
    assert audit.derived_result.startswith("supersedes:")


def test_gre_derivation_exception_branch():
    facts = {
        "general_rule": "default encryption required",
        "exception_condition": "legacy system",
        "exception_rule": "legacy exemption applies",
        "condition_satisfied": True,
        "priority_cue": "except",
        "evidence_spans": ["except legacy"],
    }
    audit = derive_gre(facts, {"case_context": "legacy system"})
    assert audit.derivation_status == "DERIVED"
    assert "EXCEPTION_OVERRIDES" in audit.derived_result


def test_atomic_complete_requires_fields():
    ok, missing = atomic_complete("TEMPORAL_VERSION", {"current_value": "", "change_relation": "replace"})
    assert not ok
    assert "current_value" in missing
