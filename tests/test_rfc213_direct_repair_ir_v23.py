from rfc213_direct_repair_ir_v23 import (
    is_reference_nonassertion,
    normalize_ascii_table_layout,
    resolve_source_window_v23,
)


def repair_ir(span: str) -> dict:
    return {
        "decision": "REPAIR",
        "target": {
            "predicate_iri": "https://example.org/ontology#requirement",
            "old_value": {"lexical": "requirement=unmodeled"},
        },
        "replacement": {"new_value": {"lexical": span}},
        "evidence_spans": [span],
    }


def test_reference_entry_requires_structural_markers_and_no_deontic_language() -> None:
    entry = '[RFC5019] Cooper, D., "OCSP Requirements", RFC 5019, September 2007. https://www.rfc-editor.org/info/rfc5019'
    assert is_reference_nonassertion(entry)


def test_normative_sentence_with_inline_rfc_citation_is_not_a_reference_entry() -> None:
    sentence = "Clients MUST reject this algorithm as specified in [RFC5019]."
    assert not is_reference_nonassertion(sentence)


def test_ascii_table_layout_normalization_removes_borders_only() -> None:
    table = "+------+----------+\n| Name | REQUIRED |\n+------+----------+"
    assert normalize_ascii_table_layout(table) == "name required"


def test_v23_resolves_span_missing_only_ascii_table_decoration() -> None:
    span = "Name REQUIRED"
    evidence = "[SOURCE_WINDOW_1]\n+------+----------+\n| Name | REQUIRED |\n+------+----------+\n"
    result = resolve_source_window_v23(repair_ir(span), evidence)
    assert result["status"] == "RESOLVED"
    assert result["resolution_path"] == "ASCII_TABLE"


def test_v23_fails_closed_on_reference_entry() -> None:
    entry = '[RFC5019] Cooper, D., "OCSP Requirements", RFC 5019, September 2007. https://www.rfc-editor.org/info/rfc5019'
    result = resolve_source_window_v23(repair_ir(entry), f"[SOURCE_WINDOW_1]\n{entry}\n")
    assert result["status"] == "REFERENCE_NON_ASSERTION"
    assert result["canonical_ir"] is None
