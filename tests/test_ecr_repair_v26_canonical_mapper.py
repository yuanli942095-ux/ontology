from ecr_repair_v26_canonical_mapper import build_predicate_vocabulary, canonical_gate, canonical_score, compose_tuples, map_tuple
from ecr_repair_v26_tuple import NormativeTuple


VOCAB = build_predicate_vocabulary("https://example.org#retention", "record retention requirement")


def test_global_aliases_map_required_and_mandatory_together():
    left = map_tuple(NormativeTuple("records are required"), VOCAB)
    right = map_tuple(NormativeTuple("records are mandatory"), VOCAB)
    assert "require" in left.core_concepts and left.core_concepts == right.core_concepts


def test_composition_merges_compatible_enumeration_fragments():
    left = map_tuple(NormativeTuple("allowed algorithms", enumeration=("A",)), VOCAB)
    right = map_tuple(NormativeTuple("allowed algorithms", enumeration=("B",)), VOCAB)
    merged = compose_tuples([left, right])
    assert len(merged) == 1 and merged[0].enumeration_ids == {"a", "b"}


def test_canonical_gate_keeps_polarity_conflict():
    positive = map_tuple(NormativeTuple("retain", polarity="POSITIVE"), VOCAB)
    negative = map_tuple(NormativeTuple("retain", polarity="NEGATIVE"), VOCAB)
    assert canonical_gate([positive, negative])["reason"] == "CONFLICTING_EVIDENCE"


def test_canonical_score_rejects_numeric_mismatch():
    left = map_tuple(NormativeTuple("retention", numeric_value="30", unit="days"), VOCAB)
    right = map_tuple(NormativeTuple("retention", numeric_value="60", unit="days"), VOCAB)
    assert canonical_score(left, right)[0] == 0
