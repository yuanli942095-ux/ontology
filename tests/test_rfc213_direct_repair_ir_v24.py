from rfc213_direct_repair_ir_v24 import is_bibliographic_nonassertion_v24


def test_detects_named_key_doi_reference() -> None:
    text = '[KEYAGREEMENT] Barker, E., "Recommendation for pair-wise key-establishment schemes", DOI 10.6028/test, April 2018, <https://doi.org/10.6028/test>.'
    assert is_bibliographic_nonassertion_v24(text)


def test_detects_named_key_isbn_reference() -> None:
    text = '[C309] X/Open, "Remote Procedure Call", ISBN 1-85912-041-5, August 1994, <https://example.org/spec.pdf>.'
    assert is_bibliographic_nonassertion_v24(text)


def test_does_not_block_normative_sentence_with_citation_and_title() -> None:
    text = '[PROFILE] Clients MUST implement "Example Profile", RFC 9999, January 2025, <https://example.org/profile>.'
    assert not is_bibliographic_nonassertion_v24(text)


def test_does_not_block_bracket_led_cross_reference_prose() -> None:
    text = '[RFC3230] was intended to use what HTTP now defines as selected representation data.'
    assert not is_bibliographic_nonassertion_v24(text)


def test_month_may_is_not_an_rfc2119_keyword() -> None:
    text = '[JWA] Jones, M., "JSON Web Algorithms", RFC 7518, DOI 10.17487/RFC7518, May 2015, <https://www.rfc-editor.org/info/rfc7518>.'
    assert is_bibliographic_nonassertion_v24(text)
