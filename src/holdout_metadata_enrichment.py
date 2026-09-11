from __future__ import annotations

"""Derive event-specific predicate_label and case_context for hold-out benchmarks."""

import re
from pathlib import Path

STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "from",
    "are",
    "not",
    "must",
    "should",
    "shall",
    "may",
    "can",
    "when",
    "where",
    "which",
    "into",
    "than",
    "then",
    "been",
    "have",
    "has",
    "its",
    "their",
    "section",
    "rfc",
    "http",
    "tls",
    "dns",
    "uri",
    "json",
    "oauth",
}


def keywords(text: str, limit: int = 5) -> tuple[str, ...]:
    raw = [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text)]
    scored: dict[str, int] = {}
    for token in raw:
        if token in STOPWORDS or token.isdigit():
            continue
        scored[token] = scored.get(token, 0) + 1
    ranked = sorted(scored, key=lambda token: (-scored[token], raw.index(token)))
    return tuple(ranked[:limit] or raw[:limit] or ("requirement",))


def first_window(evidence: str) -> str:
    match = re.search(r"(?s)\[SOURCE_WINDOW_1\]\s*(.*?)(?:\n\n\[SOURCE_WINDOW_2\]|\Z)", evidence)
    return match.group(1).strip() if match else evidence.strip()


def subject_tail(subject_label: str, source_title: str = "") -> str:
    subject = str(subject_label or "").strip()
    title = str(source_title or "").strip()
    if title and subject.lower().startswith(title.lower()):
        tail = subject[len(title) :].strip(" -:")
        if tail:
            return tail
    return subject


def predicate_label_for(
    semantic_type: str,
    *,
    subject_label: str,
    source_title: str = "",
    evidence_window: str = "",
) -> str:
    tail = subject_tail(subject_label, source_title)
    if not tail:
        focus = " ".join(keywords(evidence_window, limit=3))
        tail = focus or "normative claim"
    if semantic_type == "TEMPORAL_VERSION":
        return f"{tail} versioned normative status"
    if semantic_type == "GENERAL_RULE_EXCEPTION":
        return f"{tail} rule exception or constraint"
    if semantic_type == "CROSS_SENTENCE_SCOPE":
        return f"{tail} cross-sentence scope attachment"
    return f"{tail} normative attribute"


SEMANTIC_TYPE_ZH = {
    "TEMPORAL_VERSION": "时序/版本",
    "GENERAL_RULE_EXCEPTION": "规则例外",
    "CROSS_SENTENCE_SCOPE": "跨句范围",
}

DOMAIN_ZH = {
    "http_semantics": "HTTP 语义",
    "http_messaging": "HTTP 消息",
    "tls_security": "TLS 安全",
    "dns_security": "DNS 安全",
    "uri_templates": "URI 模板",
    "json_formats": "JSON 格式",
    "oauth_authorization": "OAuth 授权",
    "email_authentication": "邮件认证",
    "internationalized_identifiers": "国际化标识符",
    "privacy_considerations": "隐私考量",
}

PREDICATE_SUFFIX_ZH = {
    " versioned normative status": "的版本化规范状态",
    " rule exception or constraint": "的规则例外或约束",
    " cross-sentence scope attachment": "的跨句范围归属",
    " normative attribute": "的规范属性",
}


def semantic_type_zh(semantic_type: str) -> str:
    return SEMANTIC_TYPE_ZH.get(semantic_type, semantic_type)


def domain_zh(domain: str) -> str:
    return DOMAIN_ZH.get(domain, domain)


def predicate_label_zh(predicate_label: str) -> str:
    label = str(predicate_label or "").strip()
    for suffix_en, suffix_zh in PREDICATE_SUFFIX_ZH.items():
        if label.endswith(suffix_en):
            return label[: -len(suffix_en)] + suffix_zh
    return label


def case_context_for_zh(
    *,
    semantic_type: str,
    domain: str,
    subject_label: str,
    predicate_label: str,
    source_family: str,
    source_title: str,
    source_url: str,
    evidence_window: str,
) -> str:
    window = re.sub(r"\s+", " ", evidence_window).strip()
    lead = window[:140].rstrip()
    if len(window) > 140:
        lead = lead.rsplit(" ", 1)[0] + "..."
    type_phrase = {
        "TEMPORAL_VERSION": "版本化规范状态",
        "GENERAL_RULE_EXCEPTION": "规则/例外约束",
        "CROSS_SENTENCE_SCOPE": "跨句范围归属",
    }.get(semantic_type, "规范主张")
    domain_label = domain_zh(domain)
    predicate_zh = predicate_label_zh(predicate_label)
    return (
        f"保留集事件，领域为「{domain_label}」（{domain}）。仅依据公开引用来源"
        f"（{source_family}：{source_title}，{source_url}），判断主体「{subject_label}」、"
        f"谓词「{predicate_zh}」所对应的证据支撑{type_phrase}。"
        f"请将答案锚定在以如下规范性段落开头的证据：「{lead}」"
    )


def bool_zh(value: bool) -> str:
    return "是" if value else "否"


def case_context_for(
    *,
    semantic_type: str,
    domain: str,
    subject_label: str,
    predicate_label: str,
    source_family: str,
    source_title: str,
    source_url: str,
    evidence_window: str,
) -> str:
    window = re.sub(r"\s+", " ", evidence_window).strip()
    lead = window[:140].rstrip()
    if len(window) > 140:
        lead = lead.rsplit(" ", 1)[0] + "..."
    type_phrase = {
        "TEMPORAL_VERSION": "versioned normative status",
        "GENERAL_RULE_EXCEPTION": "rule/exception constraint",
        "CROSS_SENTENCE_SCOPE": "cross-sentence scope attachment",
    }.get(semantic_type, "normative claim")
    return (
        f"Hold-out event in domain {domain}. Using only the cited public source "
        f"({source_family}: {source_title}, {source_url}), determine the evidence-grounded "
        f"{type_phrase} for subject \"{subject_label}\" and predicate \"{predicate_label}\". "
        f"Anchor the answer to the normative passage that begins: \"{lead}\""
    )


def parse_display_value(display_value: str) -> tuple[str, str]:
    if "=" not in display_value:
        return "", display_value
    left, right = display_value.split("=", 1)
    return left.strip(), right.strip()


def display_source_title(event: dict[str, str], documents: dict[str, dict[str, str]]) -> str:
    title = str(event.get("title", "")).strip()
    if " normative claim" in title:
        return title.split(" normative claim", 1)[0].strip()
    return source_title_from_event(event, documents)


def source_title_from_event(event: dict[str, str], documents: dict[str, dict[str, str]]) -> str:
    doc_id = str(event.get("document_ids", "")).strip()
    doc = documents.get(doc_id, {})
    issuer = str(doc.get("issuer", "")).strip()
    if issuer:
        return issuer
    file_name = str(doc.get("file_name", ""))
    match = re.search(r"DOC_HOX_E\d+_NEW_(.+)\.txt$", file_name)
    if match:
        return match.group(1).replace("_", " ")
    return event.get("domain", "").replace("_", " ")


def display_source_title(event: dict[str, str], documents: dict[str, dict[str, str]]) -> str:
    title = str(event.get("title", "")).strip()
    if " normative claim" in title:
        return title.split(" normative claim", 1)[0].strip()
    return source_title_from_event(event, documents)


def source_family_from_event(event: dict[str, str], families_by_domain: dict[str, str]) -> str:
    return families_by_domain.get(event.get("domain", ""), event.get("domain", ""))


def load_source_family_by_event(benchmark_dir: Path) -> dict[str, str]:
    root = Path(benchmark_dir)
    retrieval_csv = root / "public" / "retrieval" / "external-real-v8-event-retrieval.csv"
    family_csv = root / "public" / "retrieval" / "external-real-v8-grounded-source-families.csv"
    families_by_source = {
        row["source_id"]: row.get("source_family", row["source_id"])
        for row in _read_csv_rows(family_csv)
    }
    return {
        row["event_id"]: families_by_source.get(row["source_id"], row["source_id"])
        for row in _read_csv_rows(retrieval_csv)
    }


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def enrich_event_row(
    event: dict[str, str],
    *,
    evidence: str,
    source_title: str,
    source_family: str,
    subject_source_title: str | None = None,
) -> dict[str, str]:
    window1 = first_window(evidence)
    predicate = predicate_label_for(
        event.get("semantic_type", ""),
        subject_label=event.get("subject_label", ""),
        source_title=subject_source_title or source_title,
        evidence_window=window1,
    )
    case_context = case_context_for(
        semantic_type=event.get("semantic_type", ""),
        domain=event.get("domain", ""),
        subject_label=event.get("subject_label", ""),
        predicate_label=predicate,
        source_family=source_family,
        source_title=source_title,
        source_url=event.get("source_url", ""),
        evidence_window=window1,
    )
    out = dict(event)
    out["predicate_label"] = predicate
    out["case_context"] = case_context
    return out
