from ecr_repair_v26_canonical_mapper import build_predicate_vocabulary, map_tuple
from ecr_repair_v26_tuple import NormativeTuple
from ecr_repair_v26_typed_reasoner import infer_profile, typed_gate, typed_score


V=build_predicate_vocabulary("https://example.org#p","maximum retention duration")


def test_numeric_profile_requires_number_and_unit():
    complete=map_tuple(NormativeTuple("retention",numeric_value="30",unit="days"),V)
    profile=infer_profile(V.predicate_id,"maximum retention duration",[complete])
    assert profile.type_id=="NUMERIC_THRESHOLD"
    assert typed_gate([complete],profile,"")["decision"]=="CANDIDATE_EVALUATION_REQUIRED"


def test_numeric_candidate_with_wrong_value_scores_zero():
    left=map_tuple(NormativeTuple("retention",numeric_value="30",unit="days"),V)
    right=map_tuple(NormativeTuple("retention",numeric_value="60",unit="days"),V)
    profile=infer_profile(V.predicate_id,"maximum retention duration",[left])
    assert typed_score(left,right,profile)[0]==0
