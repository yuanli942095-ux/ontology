from __future__ import annotations

"""Build a fresh public-source hold-out benchmark draft.

This builder intentionally creates a new benchmark directory instead of editing
external-real-v8 or revised-m7. Sources are fetched from public normative
documents, stored with hashes, and event evidence windows are cut from the
fetched text. Oracle/candidates are produced after source material exists, but
the public construction files do not contain oracle or candidate data.
"""

import argparse
import csv
import hashlib
import html
import json
import re
import time
import urllib.request
import xml.sax.saxutils as xml_escape
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-draft"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-draft"
CACHE = PROJECT_DIR / "data" / "external-real-holdout-v1-cache"
BASE_IRI = "file:///G:/LearnAI/ontology-evolution/external-real-holdout-v1#"
SUPPORT_GATE_VERSION = "holdout-v1-source-window-v1"


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    domain: str
    family: str
    title: str
    url: str


@dataclass(frozen=True)
class EventSpec:
    event_id: str
    semantic_type: str
    domain: str
    source_id: str
    title: str
    subject_label: str
    predicate_label: str
    case_context: str
    query_terms: tuple[str, ...]
    dimension: str
    value: str
    distractor: str


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("HO_HTTP_RFC9110", "http_semantics", "IETF RFC 9110", "HTTP Semantics", "https://www.rfc-editor.org/rfc/rfc9110.txt"),
    SourceSpec("HO_HTTP_RFC9111", "http_semantics", "IETF RFC 9111", "HTTP Caching", "https://www.rfc-editor.org/rfc/rfc9111.txt"),
    SourceSpec("HO_HTTP_MSG_RFC9112", "http_messaging", "IETF RFC 9112", "HTTP/1.1", "https://www.rfc-editor.org/rfc/rfc9112.txt"),
    SourceSpec("HO_HTTP_MSG_RFC9113", "http_messaging", "IETF RFC 9113", "HTTP/2", "https://www.rfc-editor.org/rfc/rfc9113.txt"),
    SourceSpec("HO_TLS_RFC8446", "tls_security", "IETF RFC 8446", "TLS 1.3", "https://www.rfc-editor.org/rfc/rfc8446.txt"),
    SourceSpec("HO_TLS_RFC9325", "tls_security", "IETF RFC 9325", "TLS and DTLS Recommendations", "https://www.rfc-editor.org/rfc/rfc9325.txt"),
    SourceSpec("HO_OAUTH_RFC6749", "oauth_authorization", "IETF RFC 6749", "OAuth 2.0 Authorization Framework", "https://www.rfc-editor.org/rfc/rfc6749.txt"),
    SourceSpec("HO_OAUTH_RFC6750", "oauth_authorization", "IETF RFC 6750", "OAuth 2.0 Bearer Token Usage", "https://www.rfc-editor.org/rfc/rfc6750.txt"),
    SourceSpec("HO_EMAIL_RFC7208", "email_authentication", "IETF RFC 7208", "Sender Policy Framework", "https://www.rfc-editor.org/rfc/rfc7208.txt"),
    SourceSpec("HO_EMAIL_RFC7489", "email_authentication", "IETF RFC 7489", "DMARC", "https://www.rfc-editor.org/rfc/rfc7489.txt"),
    SourceSpec("HO_DNS_RFC4033", "dns_security", "IETF RFC 4033", "DNSSEC Introduction", "https://www.rfc-editor.org/rfc/rfc4033.txt"),
    SourceSpec("HO_DNS_RFC4035", "dns_security", "IETF RFC 4035", "DNSSEC Protocol Modifications", "https://www.rfc-editor.org/rfc/rfc4035.txt"),
    SourceSpec("HO_JSON_RFC8259", "json_formats", "IETF RFC 8259", "JSON Data Interchange Format", "https://www.rfc-editor.org/rfc/rfc8259.txt"),
    SourceSpec("HO_JSON_RFC6902", "json_formats", "IETF RFC 6902", "JSON Patch", "https://www.rfc-editor.org/rfc/rfc6902.txt"),
    SourceSpec("HO_URI_RFC3986", "uri_templates", "IETF RFC 3986", "URI Generic Syntax", "https://www.rfc-editor.org/rfc/rfc3986.txt"),
    SourceSpec("HO_URI_RFC6570", "uri_templates", "IETF RFC 6570", "URI Template", "https://www.rfc-editor.org/rfc/rfc6570.txt"),
    SourceSpec("HO_IDNA_RFC5890", "internationalized_identifiers", "IETF RFC 5890", "IDNA Definitions", "https://www.rfc-editor.org/rfc/rfc5890.txt"),
    SourceSpec("HO_IDNA_RFC5891", "internationalized_identifiers", "IETF RFC 5891", "IDNA Protocol", "https://www.rfc-editor.org/rfc/rfc5891.txt"),
    SourceSpec("HO_PRIV_RFC6973", "privacy_considerations", "IETF RFC 6973", "Privacy Considerations", "https://www.rfc-editor.org/rfc/rfc6973.txt"),
    SourceSpec("HO_PRIV_RFC7258", "privacy_considerations", "IETF RFC 7258", "Pervasive Monitoring Is an Attack", "https://www.rfc-editor.org/rfc/rfc7258.txt"),
)


EVENTS: tuple[EventSpec, ...] = (
    EventSpec("HO_E001", "CROSS_SENTENCE_SCOPE", "http_semantics", "HO_HTTP_RFC9110", "HTTP GET semantics", "HTTP GET", "method retrieval semantics", "Determine the normative retrieval role of the GET method.", ("GET", "retrieval"), "method_semantics", "primary_mechanism_of_information_retrieval", "state_changing_submission"),
    EventSpec("HO_E002", "GENERAL_RULE_EXCEPTION", "http_semantics", "HO_HTTP_RFC9110", "HTTP status code 404 meaning", "HTTP 404", "status code meaning", "Determine the status meaning associated with 404.", ("404", "Not Found"), "status_meaning", "not_found", "temporarily_unavailable"),
    EventSpec("HO_E003", "TEMPORAL_VERSION", "http_semantics", "HO_HTTP_RFC9111", "HTTP cache freshness model", "HTTP cache", "freshness model", "Determine how HTTP caching treats freshness.", ("fresh", "cache"), "cache_model", "freshness_based_reuse", "no_store_only"),
    EventSpec("HO_E004", "GENERAL_RULE_EXCEPTION", "http_messaging", "HO_HTTP_MSG_RFC9112", "HTTP/1.1 transfer coding rule", "HTTP/1.1 message", "transfer coding constraint", "Determine the constraint on Transfer-Encoding usage.", ("Transfer-Encoding", "MUST NOT"), "transfer_coding_rule", "restricted_by_message_framing_rules", "always_allowed"),
    EventSpec("HO_E005", "CROSS_SENTENCE_SCOPE", "http_messaging", "HO_HTTP_MSG_RFC9112", "HTTP Host field requirement", "HTTP/1.1 request", "host field requirement", "Determine the request scope of the Host header field requirement.", ("Host", "header field"), "host_requirement", "required_for_http_1_1_requests", "optional_for_all_requests"),
    EventSpec("HO_E006", "TEMPORAL_VERSION", "http_messaging", "HO_HTTP_MSG_RFC9113", "HTTP/2 connection preface", "HTTP/2 connection", "connection preface", "Determine the connection preface requirement for HTTP/2.", ("connection preface", "HTTP/2"), "connection_preface", "required_http2_preface", "http1_upgrade_only"),
    EventSpec("HO_E007", "TEMPORAL_VERSION", "tls_security", "HO_TLS_RFC8446", "TLS 1.3 protocol version", "TLS", "protocol version", "Determine the protocol version defined by the document.", ("TLS", "1.3"), "tls_version", "tls_1_3", "tls_1_2"),
    EventSpec("HO_E008", "GENERAL_RULE_EXCEPTION", "tls_security", "HO_TLS_RFC8446", "TLS early data replay limits", "TLS early data", "replay protection caution", "Determine the caution associated with early data.", ("early data", "replay"), "early_data_risk", "replay_protection_required", "confidentiality_only"),
    EventSpec("HO_E009", "CROSS_SENTENCE_SCOPE", "tls_security", "HO_TLS_RFC9325", "TLS minimum recommendations", "TLS deployment", "protocol recommendation", "Determine the recommended TLS protocol baseline.", ("TLS 1.2", "TLS 1.3"), "protocol_recommendation", "use_tls_1_2_or_later", "allow_ssl_3_0"),
    EventSpec("HO_E010", "GENERAL_RULE_EXCEPTION", "oauth_authorization", "HO_OAUTH_RFC6749", "OAuth authorization code expiration", "OAuth authorization code", "expiration rule", "Determine the rule for authorization code lifetime.", ("authorization code", "MUST expire"), "code_lifetime", "must_expire_shortly_after_issue", "never_expires"),
    EventSpec("HO_E011", "CROSS_SENTENCE_SCOPE", "oauth_authorization", "HO_OAUTH_RFC6749", "OAuth redirect URI validation", "OAuth redirect URI", "redirection endpoint matching", "Determine the matching requirement for redirect URIs.", ("redirection URI", "MUST compare"), "redirect_uri_rule", "must_compare_registered_redirect_uri", "may_ignore_redirect_uri"),
    EventSpec("HO_E012", "GENERAL_RULE_EXCEPTION", "oauth_authorization", "HO_OAUTH_RFC6750", "Bearer token transport", "OAuth bearer token", "token transport method", "Determine a supported bearer token transmission method.", ("Authorization", "Bearer"), "bearer_transport", "authorization_header_bearer_scheme", "password_query_only"),
    EventSpec("HO_E013", "CROSS_SENTENCE_SCOPE", "email_authentication", "HO_EMAIL_RFC7208", "SPF DNS record scope", "SPF record", "policy expression location", "Determine where SPF policy is published.", ("DNS", "SPF"), "spf_policy_location", "dns_txt_record", "http_endpoint"),
    EventSpec("HO_E014", "GENERAL_RULE_EXCEPTION", "email_authentication", "HO_EMAIL_RFC7208", "SPF result fail", "SPF fail result", "authorization meaning", "Determine the meaning of SPF fail.", ("fail", "not authorized"), "spf_fail_meaning", "client_not_authorized", "domain_temporarily_unknown"),
    EventSpec("HO_E015", "CROSS_SENTENCE_SCOPE", "email_authentication", "HO_EMAIL_RFC7489", "DMARC alignment", "DMARC", "identifier alignment", "Determine the identifier alignment role in DMARC.", ("Identifier Alignment", "DMARC"), "dmarc_alignment", "identifier_alignment_required_for_policy", "signature_format_only"),
    EventSpec("HO_E016", "TEMPORAL_VERSION", "dns_security", "HO_DNS_RFC4033", "DNSSEC origin authentication", "DNSSEC", "security service", "Determine the service DNSSEC adds to DNS.", ("origin authentication", "DNSSEC"), "dnssec_service", "origin_authentication_and_integrity", "transport_encryption"),
    EventSpec("HO_E017", "CROSS_SENTENCE_SCOPE", "dns_security", "HO_DNS_RFC4035", "DNSSEC RRSIG scope", "RRSIG", "signature coverage", "Determine what an RRSIG covers.", ("RRSIG", "RRset"), "rrsig_scope", "covers_rrset", "covers_tcp_session"),
    EventSpec("HO_E018", "GENERAL_RULE_EXCEPTION", "dns_security", "HO_DNS_RFC4035", "DNSSEC validation failure", "DNSSEC validation", "bogus response handling", "Determine the outcome of failed DNSSEC validation.", ("bogus", "validation"), "validation_outcome", "treat_as_bogus", "treat_as_authenticated"),
    EventSpec("HO_E019", "GENERAL_RULE_EXCEPTION", "json_formats", "HO_JSON_RFC8259", "JSON duplicate names guidance", "JSON object", "member name uniqueness", "Determine the interoperability guidance for object member names.", ("names", "unique"), "member_name_rule", "names_should_be_unique", "duplicates_required"),
    EventSpec("HO_E020", "TEMPORAL_VERSION", "json_formats", "HO_JSON_RFC8259", "JSON text encoding", "JSON text", "encoding requirement", "Determine the required encoding for open ecosystem JSON exchange.", ("UTF-8", "encoded"), "json_encoding", "utf_8", "utf_16_required"),
    EventSpec("HO_E021", "CROSS_SENTENCE_SCOPE", "json_formats", "HO_JSON_RFC6902", "JSON Patch operation object", "JSON Patch", "operation member", "Determine the required operation member in a patch object.", ("operation object", "op"), "patch_operation_member", "op_member_required", "path_only"),
    EventSpec("HO_E022", "CROSS_SENTENCE_SCOPE", "uri_templates", "HO_URI_RFC3986", "URI generic syntax components", "URI", "generic syntax components", "Determine the high-level URI component structure.", ("scheme", "authority", "path"), "uri_components", "scheme_authority_path_query_fragment", "host_port_only"),
    EventSpec("HO_E023", "GENERAL_RULE_EXCEPTION", "uri_templates", "HO_URI_RFC3986", "URI fragment identifier", "URI fragment", "fragment semantics", "Determine where fragment identifier semantics are defined.", ("fragment", "media type"), "fragment_semantics", "defined_by_media_type", "defined_by_transport"),
    EventSpec("HO_E024", "TEMPORAL_VERSION", "uri_templates", "HO_URI_RFC6570", "URI Template expression", "URI Template", "expression syntax", "Determine the delimiter used by URI Template expressions.", ("expression", "{", "}"), "template_expression", "curly_brace_expression", "angle_bracket_expression"),
    EventSpec("HO_E025", "TEMPORAL_VERSION", "internationalized_identifiers", "HO_IDNA_RFC5890", "IDNA label forms", "IDNA label", "label form taxonomy", "Determine the label form taxonomy used by IDNA.", ("A-label", "U-label"), "idna_label_forms", "a_label_and_u_label", "ascii_only_label"),
    EventSpec("HO_E026", "GENERAL_RULE_EXCEPTION", "internationalized_identifiers", "HO_IDNA_RFC5891", "IDNA registration lookup", "IDNA domain name", "lookup protocol", "Determine the protocol context for IDNA domain lookup.", ("lookup", "domain name"), "idna_lookup", "lookup_protocol_context", "email_header_only"),
    EventSpec("HO_E027", "CROSS_SENTENCE_SCOPE", "internationalized_identifiers", "HO_IDNA_RFC5891", "IDNA registration protocol", "IDNA registry", "registration protocol", "Determine the protocol context for IDNA registration.", ("registration", "protocol"), "idna_registration", "registration_protocol_context", "display_only"),
    EventSpec("HO_E028", "CROSS_SENTENCE_SCOPE", "privacy_considerations", "HO_PRIV_RFC6973", "Privacy threat model", "Internet protocol privacy", "privacy threat analysis", "Determine the purpose of privacy threat analysis.", ("privacy", "threat"), "privacy_analysis", "identify_privacy_threats_and_mitigations", "encrypt_every_packet_only"),
    EventSpec("HO_E029", "GENERAL_RULE_EXCEPTION", "privacy_considerations", "HO_PRIV_RFC6973", "Data minimization guidance", "Protocol design", "data minimization", "Determine the privacy design guidance around data minimization.", ("minimization", "data"), "data_minimization", "minimize_disclosure_of_personal_data", "maximize_logging"),
    EventSpec("HO_E030", "TEMPORAL_VERSION", "privacy_considerations", "HO_PRIV_RFC7258", "Pervasive monitoring status", "Pervasive monitoring", "attack classification", "Determine how pervasive monitoring is classified.", ("Pervasive Monitoring", "attack"), "monitoring_status", "pervasive_monitoring_is_an_attack", "pervasive_monitoring_is_required"),
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_url(url: str, timeout: int) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ontology-evolution-holdout-builder/1.0",
            "Accept": "text/plain,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


def clean_text(raw: bytes, content_type: str) -> str:
    text = raw.decode("utf-8", errors="replace")
    if "html" in content_type or re.search(r"<html|<body|<p[ >]", text[:5000], flags=re.I):
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?is)<br\s*/?>", "\n", text)
        text = re.sub(r"(?is)</(p|div|li|h[1-6]|tr)>", "\n", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", text)}


def split_units(text: str) -> list[str]:
    paragraphs: list[str] = []
    buf: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        paragraphs.append(" ".join(buf))

    units: list[str] = []
    for paragraph in paragraphs:
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if len(paragraph) <= 900:
            units.append(paragraph)
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])", paragraph):
            sentence = sentence.strip()
            if sentence:
                units.append(sentence)
    return [chunk for chunk in units if 40 <= len(chunk) <= 1600]


def extract_windows(text: str, query_terms: tuple[str, ...], limit: int = 5) -> tuple[list[str], float]:
    query = tokens(" ".join(query_terms))
    units = split_units(text)
    scored: list[tuple[float, int, str]] = []
    for index, unit in enumerate(units):
        unit_tokens = tokens(unit)
        overlap = len(query & unit_tokens)
        phrase_bonus = sum((12 if len(term) > 30 else 2) for term in query_terms if term.lower() in unit.lower())
        normative_bonus = 6 if re.search(r"\b(MUST|SHOULD|REQUIRED|NOT RECOMMENDED|RECOMMENDED)\b", unit) else 0
        score = overlap + phrase_bonus + normative_bonus
        if score > 0:
            scored.append((score, index, unit))
    scored.sort(key=lambda item: (-item[0], item[1]))
    windows = [unit for _score, _index, unit in scored[:limit]]
    best = scored[0][0] if scored else 0.0
    return windows, float(best)


def xml(value: str) -> str:
    return xml_escape.escape(value, {'"': "&quot;"})


def event_local_id(event_id: str) -> str:
    return event_id.replace("-", "_")


def property_name(event: EventSpec) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", event.dimension)


def subject_iri(event: EventSpec) -> str:
    return BASE_IRI + event_local_id(event.event_id)


def predicate_iri(event: EventSpec) -> str:
    return BASE_IRI + property_name(event)


def owl_text(event: EventSpec, value: str) -> str:
    pred = property_name(event)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF
   xmlns:hold="file:///G:/LearnAI/ontology-evolution/external-real-holdout-v1#"
   xmlns:owl="http://www.w3.org/2002/07/owl#"
   xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
   xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
   xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
>
  <rdf:Description rdf:about="https://w3id.org/ontology-evolution/external-real-holdout-v1/{event.event_id}/mutant">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#Ontology"/>
  </rdf:Description>
  <rdf:Description rdf:about="{BASE_IRI}HoldoutObject">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#Class"/>
  </rdf:Description>
  <rdf:Description rdf:about="{xml(predicate_iri(event))}">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#DatatypeProperty"/>
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#FunctionalProperty"/>
    <rdfs:domain rdf:resource="{BASE_IRI}HoldoutObject"/>
    <rdfs:range rdf:resource="http://www.w3.org/2001/XMLSchema#string"/>
  </rdf:Description>
  <rdf:Description rdf:about="{xml(subject_iri(event))}">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#NamedIndividual"/>
    <rdf:type rdf:resource="{BASE_IRI}HoldoutObject"/>
    <hold:{pred} rdf:datatype="http://www.w3.org/2001/XMLSchema#string">{xml(value)}</hold:{pred}>
  </rdf:Description>
</rdf:RDF>
"""


def operation_json(event: EventSpec, new_value: str) -> str:
    operation = {
        "operator": "REPLACE_PROPERTY_VALUE",
        "subject_iri": subject_iri(event),
        "predicate_iri": predicate_iri(event),
        "old_value": {"kind": "literal", "lexical": f"{event.dimension}=unmodeled", "datatype": "http://www.w3.org/2001/XMLSchema#string"},
        "new_value": {"kind": "literal", "lexical": new_value, "datatype": "http://www.w3.org/2001/XMLSchema#string"},
    }
    return json.dumps(operation, ensure_ascii=False, separators=(",", ":"))


def ensure_dirs() -> None:
    for path in (
        BENCHMARK / "public" / "events",
        BENCHMARK / "public" / "documents",
        BENCHMARK / "public" / "excerpts",
        BENCHMARK / "public" / "retrieval",
        BENCHMARK / "repair-stage" / "candidates",
        BENCHMARK / "repair-stage" / "mutants",
        BENCHMARK / "private" / "oracle",
        CACHE,
        OUTPUT,
    ):
        path.mkdir(parents=True, exist_ok=True)


def cache_sources(timeout: int, force: bool) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    by_source: dict[str, dict[str, str]] = {}
    for source in SOURCES:
        raw_path = CACHE / f"{source.source_id}.raw"
        text_path = CACHE / f"{source.source_id}.txt"
        status = "SUCCESS"
        error = ""
        content_type = "text/plain"
        start = time.perf_counter()
        try:
            if force or not text_path.is_file() or not raw_path.is_file():
                raw, content_type = fetch_url(source.url, timeout)
                raw_path.write_bytes(raw)
                text_path.write_text(clean_text(raw, content_type), encoding="utf-8")
            raw_sha = sha256_file(raw_path)
            text_sha = sha256_file(text_path)
        except Exception as exc:
            status = "FAILED"
            error = f"{type(exc).__name__}: {exc}"
            raw_sha = ""
            text_sha = ""
        row = {
            "source_id": source.source_id,
            "domain": source.domain,
            "source_family": source.family,
            "title": source.title,
            "source_url": source.url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "content_type": content_type,
            "raw_path": str(raw_path.relative_to(PROJECT_DIR)) if raw_path.exists() else "",
            "text_path": str(text_path.relative_to(PROJECT_DIR)) if text_path.exists() else "",
            "raw_sha256": raw_sha,
            "text_sha256": text_sha,
            "runtime_ms": int((time.perf_counter() - start) * 1000),
            "status": status,
            "error": error,
        }
        rows.append(row)
        by_source[source.source_id] = {key: str(value) for key, value in row.items()}
    return by_source, rows


def build(args: argparse.Namespace) -> int:
    ensure_dirs()
    source_by_id, cache_rows = cache_sources(args.timeout, args.force_fetch)
    source_specs = {source.source_id: source for source in SOURCES}
    source_family_rows = [
        {
            "source_id": source.source_id,
            "domain": source.domain,
            "source_family": source.family,
            "source_title": source.title,
            "source_url": source.url,
            "status": source_by_id[source.source_id]["status"],
        }
        for source in SOURCES
    ]

    event_rows: list[dict[str, Any]] = []
    doc_rows: list[dict[str, Any]] = []
    retrieval_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    oracle_rows: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    support_rows: list[dict[str, Any]] = []

    for event in EVENTS:
        source = source_specs[event.source_id]
        cache = source_by_id[event.source_id]
        text_path = PROJECT_DIR / cache["text_path"] if cache.get("text_path") else Path()
        text = text_path.read_text(encoding="utf-8", errors="replace") if text_path.is_file() else ""
        windows, score = extract_windows(text, event.query_terms, limit=5)
        support = bool(windows) and score >= args.min_score
        status = "READY" if cache["status"] == "SUCCESS" and support else "DRAFT"
        retrieved_at = datetime.now(timezone.utc).isoformat()
        event_doc_id = f"DOC_{event.event_id}_NEW"
        doc_file = f"{event_doc_id}_{event.source_id}.txt"
        excerpt_file = f"{event.event_id}-evidence.md"
        doc_path = BENCHMARK / "public" / "documents" / doc_file
        excerpt_path = BENCHMARK / "public" / "excerpts" / excerpt_file
        if text:
            doc_path.write_text(text, encoding="utf-8")
        excerpt_text = "\n\n".join(f"[SOURCE_WINDOW_{idx + 1}]\n{window}" for idx, window in enumerate(windows))
        excerpt_path.write_text(excerpt_text + ("\n" if excerpt_text else ""), encoding="utf-8")

        event_rows.append(
            {
                "event_id": event.event_id,
                "split": "holdout",
                "semantic_type": event.semantic_type,
                "domain": event.domain,
                "title": event.title,
                "case_context": event.case_context,
                "subject_label": event.subject_label,
                "predicate_label": event.predicate_label,
                "value_kind": "literal_string",
                "allowed_min": "",
                "allowed_max": "",
                "document_ids": event_doc_id,
                "source_owl": "",
                "source_url": source.url,
                "retrieved_at": retrieved_at,
                "status": status,
                "notes": "Fresh hold-out draft; do not use for method tuning after freeze.",
                "lexical_status": "WINDOW_RETRIEVED" if support else "WINDOW_LOW_SCORE",
                "support_status": "SEMANTIC_REVIEW_REQUIRED",
                "semantic_support": "PASS" if support else "WARN",
                "support_adjudication_method": "source_window_keyword_gate_then_manual_review_required",
                "support_checked_before_model_run": "true",
                "support_gate_version": SUPPORT_GATE_VERSION,
            }
        )
        doc_rows.append(
            {
                "document_id": event_doc_id,
                "event_id": event.event_id,
                "file_name": doc_file,
                "authority": 100,
                "effective_from": "",
                "effective_to": "",
                "issuer": source.family,
                "document_type": "public_normative_full_text",
                "source_type": "EXTERNAL_PUBLIC_FULL_TEXT",
                "source_url": source.url,
                "retrieved_at": cache.get("retrieved_at", retrieved_at),
                "raw_sha256": cache.get("raw_sha256", ""),
                "text_sha256": cache.get("text_sha256", ""),
                "extraction_mode": "full_text_fetch_plus_evidence_window",
                "cache_path": cache.get("text_path", ""),
                "window_sha256": sha256_text(excerpt_text),
                "sha256": sha256_file(doc_path) if doc_path.is_file() else "",
                "status": "READY" if status == "READY" else "FAILED",
            }
        )
        retrieval_rows.append(
            {
                "event_id": event.event_id,
                "domain": event.domain,
                "source_id": source.source_id,
                "query_terms": "|".join(event.query_terms),
                "retrieval_status": "RETRIEVAL_READY" if status == "READY" else "RETRIEVAL_REVIEW_REQUIRED",
                "current_score": score,
                "previous_score": "",
                "current_windows": len(windows),
                "previous_windows": 0,
                "window_sha256": sha256_text(excerpt_text),
                "fallback_used": "false",
                "candidate_used": "false",
                "oracle_used": "false",
                "note_used": "false",
                "errors": "" if status == "READY" else "low evidence-window score or fetch failure",
            }
        )
        support_rows.append(
            {
                "event_id": event.event_id,
                "status": status,
                "best_score": score,
                "query_terms": "|".join(event.query_terms),
                "evidence_file": str(excerpt_path.relative_to(PROJECT_DIR)),
                "manual_review_required": "true",
            }
        )
        values = {
            "CAND_001": f"{event.dimension}=unmodeled",
            "CAND_002": f"{event.dimension}={event.value}",
            "CAND_003": f"{event.dimension}={event.distractor}",
        }
        (BENCHMARK / "repair-stage" / "mutants" / f"{event.event_id}.owl").write_text(
            owl_text(event, values["CAND_001"]),
            encoding="utf-8",
        )
        for candidate_id, value in values.items():
            candidate_rows.append(
                {
                    "event_id": event.event_id,
                    "candidate_id": candidate_id,
                    "display_value": value,
                    "operation_json": operation_json(event, value),
                    "status": "READY",
                    "notes": "Hold-out candidate generated before final method run; CAND_002 is private oracle, not public.",
                }
            )
        oracle_rows.append(
            {
                "event_id": event.event_id,
                "oracle_candidate_id": "CAND_002",
                "oracle_value": values["CAND_002"],
                "evidence_document_ids": event_doc_id,
                "evidence_spans_json": json.dumps(
                    [{"document_id": event_doc_id, "evidence_type": "public_source_window", "quote": windows[0] if windows else ""}],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "annotator_1": "HOLDOUT_BUILDER_SOURCE_REVIEW_REQUIRED",
                "annotator_2": "",
                "adjudicator": "",
                "agreement_status": "PENDING_MANUAL_REVIEW",
                "status": "READY" if status == "READY" else "DRAFT",
                "notes": "Private oracle generated from event specification and fetched public evidence; requires independent blind review before final paper use.",
            }
        )
        if status == "READY":
            manifest_rows.append(
                {
                    "event_id": event.event_id,
                    "split": "holdout",
                    "semantic_type": event.semantic_type,
                    "domain": event.domain,
                    "source_id": source.source_id,
                    "source_url": source.url,
                    "evidence_sha256": sha256_text(excerpt_text),
                    "candidate_sha256": sha256_text(json.dumps(values, sort_keys=True)),
                    "created_at_utc": retrieved_at,
                    "frozen_for_method_tuning": "false",
                }
            )

    write_csv(BENCHMARK / "public" / "events" / "external-real-event-template.csv", event_rows)
    write_csv(BENCHMARK / "public" / "documents" / "external-real-document-template.csv", doc_rows)
    write_csv(BENCHMARK / "public" / "retrieval" / "external-real-v8-grounded-source-families.csv", source_family_rows)
    write_csv(BENCHMARK / "public" / "retrieval" / "external-real-v8-event-retrieval.csv", retrieval_rows)
    write_csv(BENCHMARK / "public" / "retrieval" / "source-cache-manifest.csv", cache_rows)
    write_csv(BENCHMARK / "public" / "retrieval" / "support-adjudication.csv", support_rows)
    write_csv(BENCHMARK / "repair-stage" / "candidates" / "external-real-candidate-template.csv", candidate_rows)
    write_csv(BENCHMARK / "private" / "oracle" / "external-real-oracle-template.csv", oracle_rows)
    write_csv(OUTPUT / "external-real-holdout-v1-freeze-manifest.csv", manifest_rows)

    summary = {
        "benchmark": BENCHMARK.name,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ready_events": len(manifest_rows),
        "total_events": len(EVENTS),
        "domains": sorted({event.domain for event in EVENTS}),
        "domain_count": len({event.domain for event in EVENTS}),
        "source_families": len(SOURCES),
        "source_failures": [row for row in cache_rows if row["status"] != "SUCCESS"],
        "manual_review_required_before_freeze": True,
    }
    (OUTPUT / "external-real-holdout-v1-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (BENCHMARK / "README.md").write_text(
        "\n".join(
            [
                "# external-real-holdout-v1-draft",
                "",
                "Fresh hold-out draft built from fetched public normative sources.",
                "",
                f"- READY events: {summary['ready_events']}/{summary['total_events']}",
                f"- Domains: {summary['domain_count']}",
                f"- Source families: {summary['source_families']}",
                "- Public construction files do not include candidates or oracle labels.",
                "- Private oracle rows are present for evaluation but require independent review before a final frozen paper run.",
                "- Do not tune methods on this dataset after creating a freeze commit/manifest.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(manifest_rows) >= args.min_ready else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=40)
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument("--min-score", type=float, default=2.0)
    parser.add_argument("--min-ready", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    return build(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
