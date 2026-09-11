from __future__ import annotations

from holdout_metadata_enrichment import (
    case_context_for,
    case_context_for_zh,
    predicate_label_for,
    predicate_label_zh,
    subject_tail,
)


def test_predicate_label_uses_subject_tail_not_generic_template():
    predicate = predicate_label_for(
        "TEMPORAL_VERSION",
        subject_label="HTTP Semantics independent client purpose",
        source_title="HTTP Semantics",
        evidence_window="HTTP hides the details of how a service is implemented.",
    )
    assert predicate == "independent client purpose versioned normative status"
    assert predicate != "versioned normative claim"


def test_predicate_label_zh_translates_suffix():
    assert predicate_label_zh("independent client purpose versioned normative status") == (
        "independent client purpose的版本化规范状态"
    )


def test_case_context_zh_is_chinese():
    context = case_context_for_zh(
        semantic_type="GENERAL_RULE_EXCEPTION",
        domain="http_semantics",
        subject_label="HTTP Semantics communication cannot interface",
        predicate_label="communication cannot interface rule exception or constraint",
        source_family="IETF RFC 9110",
        source_title="HTTP Semantics",
        source_url="https://www.rfc-editor.org/rfc/rfc9110.txt",
        evidence_window="One consequence of this flexibility is that the protocol cannot be defined in terms of what occurs behind the interface.",
    )
    assert "保留集事件" in context
    assert "HTTP 语义" in context
    assert "规则/例外约束" in context
    assert "One consequence of this flexibility" in context


def test_subject_tail_strips_source_title():
    assert subject_tail("HTTP Semantics independent client purpose", "HTTP Semantics") == "independent client purpose"
