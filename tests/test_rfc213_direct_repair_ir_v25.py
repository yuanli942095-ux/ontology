from rfc213_direct_repair_ir_v25 import resolve_source_window_v25


def repair_ir(new_value: str, span: str) -> dict:
    return {
        "schema_version": "predicted-repair-ir-v1",
        "decision": "REPAIR",
        "operation": "UPDATE_LITERAL",
        "target": {
            "subject_iri": "https://example.org#subject",
            "predicate_iri": "https://example.org#requirement",
            "old_value": {"kind": "literal", "lexical": "old natural value", "datatype": "http://www.w3.org/2001/XMLSchema#string"},
        },
        "replacement": {"new_value": {"kind": "literal", "lexical": new_value, "datatype": "http://www.w3.org/2001/XMLSchema#string"}},
        "evidence_spans": [span],
        "confidence": 0.9,
    }


def test_preserves_natural_literal_after_unique_grounding() -> None:
    span = "The field is residentKey, of type DOMString."
    result = resolve_source_window_v25(repair_ir("residentKey, of type DOMString", span), f"[SOURCE_WINDOW_1]\n{span}")
    assert result["status"] == "RESOLVED"
    assert result["canonical_ir"]["replacement"]["new_value"]["lexical"] == "residentKey, of type DOMString"


def test_keeps_v24_bibliography_fail_closed() -> None:
    reference = '[KEY] Author, "A title", RFC 9999, 2025, https://example.org.'
    result = resolve_source_window_v25(repair_ir("new value", reference), f"[SOURCE_WINDOW_1]\n{reference}")
    assert result["status"] == "REFERENCE_NON_ASSERTION"


def test_does_not_resolve_missing_span() -> None:
    result = resolve_source_window_v25(repair_ir("new value", "not present"), "[SOURCE_WINDOW_1]\nDifferent evidence")
    assert result["status"] == "NO_WINDOW_MATCH"
