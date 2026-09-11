from ecr_repair_v26 import align_target_frames, construct_finite_candidates, decide_policy, decide_policy_v3_experimental, ground_frame, select_candidate, validate_frame_bundle


def frame(value="required", **changes):
    item = {
        "document_id": "DOC_1", "window_id": "SOURCE_WINDOW_1", "quote": "The field is required.",
        "subject": "field", "predicate": "requirement", "value": value, "modality": "MUST",
        "conditions": [], "exceptions": [], "valid_from": None, "valid_to": None,
        "jurisdiction": None, "assertion_kind": "NORMATIVE", "authority": 100, "confidence": 0.9,
    }
    item.update(changes)
    return item


def test_grounding_records_exact_offset():
    evidence = "[SOURCE_WINDOW_1]\nThe field is required."
    result = ground_frame(frame(), evidence)
    assert result["status"] == "GROUNDED"
    assert evidence[result["frame"]["char_start"]:result["frame"]["char_end"]] == "The field is required."


def test_reference_is_not_an_assertion():
    result = decide_policy([frame(assertion_kind="REFERENCE")], "2026-09-01T00:00:00Z", "old")
    assert result == {"decision": "ABSTAIN", "reason": "INSUFFICIENT_EVIDENCE"}


def test_equal_authority_same_scope_conflict_abstains():
    result = decide_policy([frame("required"), frame("optional")], "2026-09-01T00:00:00Z", "old")
    assert result == {"decision": "ABSTAIN", "reason": "CONFLICTING_EVIDENCE"}


def test_conditions_keep_population_specific_rules_separate():
    result = decide_policy_v3_experimental(
        [frame("required", conditions=["uses insulin"]), frame("not required", conditions=["does not use insulin"])],
        "2026-09-01T00:00:00Z", "old",
    )
    assert result["decision"] == "ABSTAIN"
    assert result["reason"] == "INSUFFICIENT_EVIDENCE"


def test_candidate_is_selected_only_after_repair_gate():
    policy = decide_policy([frame("required")], "2026-09-01T00:00:00Z", "old")
    candidate = {
        "candidate_id": "CAND_1", "display_value": "required",
        "operation_json": {"subject_iri": "https://example.org/s", "predicate_iri": "https://example.org/p",
                           "old_value": {"kind": "literal", "lexical": "old", "datatype": "xsd:string"},
                           "new_value": {"kind": "literal", "lexical": "required", "datatype": "xsd:string"}},
    }
    result = select_candidate(policy, [candidate], {})
    assert result["decision"] == "REPAIR"
    assert result["candidate_revealed_after_gate"] is True


def test_candidates_cannot_override_terminal_gate():
    result = select_candidate({"decision": "ABSTAIN", "reason": "CONFLICTING_EVIDENCE"}, [{"candidate_id": "C1"}], {})
    assert result == {"decision": "ABSTAIN", "reason": "CONFLICTING_EVIDENCE"}


def test_bundle_rejects_non_verbatim_grounding():
    result = validate_frame_bundle(
        {"schema_version": "semantic-frame-v1", "event_id": "E1", "frames": [frame(quote="paraphrase")]},
        "[SOURCE_WINDOW_1]\nThe field is required.",
    )
    assert result["status"] == "INVALID_FRAME_SET"
    assert "QUOTE_NOT_VERBATIM" in result["errors"][0]


def test_bundle_keeps_other_exact_frames_when_one_quote_fails():
    result = validate_frame_bundle(
        {"schema_version": "semantic-frame-v1", "frames": [frame(), frame(quote="paraphrase")]},
        "[SOURCE_WINDOW_1]\nThe field is required.",
    )
    assert result["status"] == "VALID_WITH_REJECTED_FRAMES"
    assert len(result["frames"]) == 1


def test_target_alignment_removes_neighbor_rule():
    target = frame(subject="WebAuthn authenticatorSelection", predicate="resident-key requirement field")
    neighbor = frame(subject="client platform", predicate="ignore unknown values")
    assert align_target_frames([target, neighbor], "WebAuthn authenticatorSelection", "resident-key requirement field") == [target]


def test_candidate_constructor_is_empty_before_repair_gate():
    target = {"subject_iri": "https://example.org/s", "predicate_iri": "https://example.org/p",
              "old_value": {"kind": "literal", "lexical": "old", "datatype": "xsd:string"}}
    assert construct_finite_candidates({"decision": "ABSTAIN"}, [frame()], target) == []


def test_candidate_constructor_uses_frames_not_static_oracle_pool():
    policy = decide_policy([frame("required")], "2026-09-01T00:00:00Z", "old")
    target = {"subject_iri": "https://example.org/s", "predicate_iri": "https://example.org/p",
              "old_value": {"kind": "literal", "lexical": "old", "datatype": "xsd:string"}}
    rows = construct_finite_candidates(policy, [frame("required"), frame("optional", conditions=["other scope"])], target)
    assert [row["display_value"] for row in rows] == ["required", "optional", "old"]


def test_date_only_validity_is_supported():
    result = decide_policy([frame("new", valid_from="2026-01-01")], "2026-09-01T00:00:00Z", "old")
    assert result["decision"] == "REPAIR_CANDIDATES_REQUIRED"


def test_latest_applicable_version_wins_within_same_scope():
    result = decide_policy_v3_experimental(
        [frame("old", valid_from="2020-01-01"), frame("new", valid_from="2025-01-01")],
        "2026-09-01T00:00:00Z", "old",
    )
    assert result["decision"] == "REPAIR_CANDIDATES_REQUIRED"
    assert result["supporting_frame"]["value"] == "new"


def test_case_context_selects_matching_conditional_scope():
    result = decide_policy_v3_experimental(
        [frame("monitor", conditions=["uses insulin"]), frame("do not monitor", conditions=["does not use insulin"])],
        "2026-09-01T00:00:00Z", "old", "The patient uses insulin.",
    )
    assert result["decision"] == "REPAIR_CANDIDATES_REQUIRED"
    assert result["supporting_frame"]["value"] == "monitor"
