from ecr_repair_v26_typed_extraction import detect_construct, validate_typed_record


def event(label="maximum retention period"):
    return {"event_id":"E1","case_context":"Determine the current value.","target":{"subject_label":"records","predicate_label":label}}


def test_numeric_detection_precedes_condition_words():
    assert detect_construct(event(),"If applicable, retain for 30 days.")=="NUMERIC_THRESHOLD"


def test_enumeration_detection():
    assert detect_construct(event("available enumeration values"),"Values are A and B.")=="ENUMERATION"


def test_conditional_record_requires_explicit_condition_or_exception():
    payload={"schema_version":"typed-frame-v1","event_id":"E1","construct":"CONDITIONAL_RULE","status":"COMPLETE","record":{"window_id":"SOURCE_WINDOW_1","quote":"Users MUST act.","core_action":"act","polarity":"POSITIVE","modality":"MUST","population":"users","conditions":[],"exceptions":[]}}
    assert "conditions_or_exceptions" in validate_typed_record(payload,event("action requirement"),"CONDITIONAL_RULE","[SOURCE_WINDOW_1]\nUsers MUST act.")
