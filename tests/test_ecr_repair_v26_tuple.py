from ecr_repair_v26_tuple import NormativeTuple, compile_literal, gate_tuples, rank_tuples, tuple_entailment_score


def test_gate_detects_same_scope_value_conflict():
    left = NormativeTuple("allow", modality="MUST")
    right = NormativeTuple("deny", modality="MUST")
    assert gate_tuples([left, right])["reason"] == "CONFLICTING_EVIDENCE"


def test_numeric_mismatch_is_hard_incompatible():
    score, errors = tuple_entailment_score(NormativeTuple("limit", numeric_value="30", unit="days"), NormativeTuple("limit", numeric_value="60", unit="days"))
    assert score == 0 and errors == ["numeric_value"]


def test_ranking_abstains_without_margin():
    evidence = NormativeTuple("retain audit records", modality="MUST")
    candidates = [
        {"candidate_id": "C1", "tuple": NormativeTuple("retain audit records", modality="MUST"), "source": {}},
        {"candidate_id": "C2", "tuple": NormativeTuple("retain audit records", modality="MUST"), "source": {}},
    ]
    assert rank_tuples(evidence, candidates)["reason"] == "TUPLE_RANKING_AMBIGUOUS"


def test_literal_is_compiled_after_selection_with_fixed_precedence():
    candidate = {"tuple": NormativeTuple("fallback"), "source": {"surface_value": "surface", "core_value": "core", "scope_complete_value": "complete"}}
    assert compile_literal(candidate) == "complete"
