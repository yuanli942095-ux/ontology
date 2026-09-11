from __future__ import annotations

"""Shared constants and helpers for ECR-Repair Post-Freeze Blind Benchmark V1."""

import csv
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


PROJECT_DIR = Path(__file__).resolve().parents[1]
BENCHMARK_NAME = "ecr-repair-post-freeze-blind-v1"
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / BENCHMARK_NAME
METHOD_ID = "ECR-REPAIR-V2.4-FINAL"
METHOD_MANIFEST = PROJECT_DIR / "method/final-v24/final-method-manifest.json"
AS_OF = "2026-09-01T00:00:00Z"
ONTOLOGY_NS = f"https://w3id.org/ontology-evolution/{BENCHMARK_NAME}#"
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
USER_AGENT = (
    "Mozilla/5.0 (compatible; ecr-repair-post-freeze-blind-v1/1.0; "
    "+https://w3id.org/ontology-evolution; research source archival)"
)

BLOCKED_RFC_NUMBERS = {
    9420,
    9497,
    9530,
    9540,
    9550,
    9562,
    9570,
    9580,
    9590,
    9605,
    9610,
    9620,
    9630,
    9645,
    9700,
    9728,
}

HISTORICAL_BENCHMARKS = (
    "external-real-holdout-v6-2-direct-ir-blind",
    "rfc-213-confirmatory-core",
    "v23-natural-grounding-challenge-v1",
    "v23-natural-grounding-challenge-v2",
    "v23-natural-grounding-challenge-v3",
    "v24-natural-grounding-confirmatory",
)
EXTRA_EXCLUSION_BENCHMARKS = (
    "external-real-holdout-v6-direct-ir-blind",
    "external-real-holdout-v6-1-direct-ir-blind",
)

DOMAINS = (
    "webauthn_fido",
    "w3c_webappsec",
    "nist_cybersecurity",
    "oauth_oidc",
    "who_clinical",
    "eu_regulation",
    "cloud_provider",
    "ietf_unseen",
)

SOURCE_FAMILIES = (
    "FIDO_ALLIANCE",
    "W3C_WEB_AUTHENTICATION",
    "W3C_WEB_APPLICATION_SECURITY",
    "NIST_CSF",
    "NIST_SP_800",
    "OPENID_FOUNDATION",
    "WHO_GUIDELINES",
    "EU_OFFICIAL_JOURNAL",
    "AWS_DOCUMENTATION",
    "AZURE_DOCUMENTATION",
    "GCP_DOCUMENTATION",
    "IETF_ACME",
    "IETF_JOSE",
    "IETF_YANG",
)

FULL_PARTITION_COUNTS = {
    "REPAIR": 56,
    "NO_CHANGE": 8,
    "INSUFFICIENT_EVIDENCE": 8,
    "CONFLICTING_EVIDENCE": 8,
}
FULL_REPAIR_SEMANTIC_COUNTS = {
    "TEMPORAL_VERSION": 19,
    "GENERAL_RULE_EXCEPTION": 19,
    "CROSS_SENTENCE_SCOPE": 18,
}
FULL_DOMAIN_COUNTS = {domain: 10 for domain in DOMAINS}
TRANCHE1_PARTITION_COUNTS = {
    "REPAIR": 10,
    "NO_CHANGE": 2,
    "INSUFFICIENT_EVIDENCE": 2,
    "CONFLICTING_EVIDENCE": 2,
}

MAX_EVENTS_PER_DOCUMENT = 4
MAX_EVENTS_PER_FAMILY = 12
MAX_IETF_EVENTS = 12
MAX_EVENTS_PER_ORGANIZATION = 20
MAX_FAMILY_FRACTION = 0.15
MIN_WINDOWS = 3
MAX_WINDOWS = 7
MIN_WINDOW_WORDS = 50
MAX_WINDOW_WORDS = 350

PUBLIC_LEAKAGE_TERMS = (
    "gold",
    "oracle",
    "correct candidate",
    "gold_candidate_id",
    "expected answer",
    "adjudication result",
    "adjudicated",
    "private/oracle",
    "private/gold",
    "gold-repaired",
    "proposed_gold",
    "正确答案",
    "gold_source_window",
    "supporting_window_ids",
)

PUBLIC_EVENT_FIELDS = (
    "event_id",
    "split",
    "status",
    "domain",
    "source_family",
    "title",
    "case_context",
    "target",
    "source_owl",
    "evidence_file",
    "document_ids",
    "as_of",
)

RFC_URL_RE = re.compile(
    r"(?:https?://(?:www\.)?rfc-editor\.org/(?:rfc/rfc|info/rfc)|\bRFC\s*)(\d+)(?:\.txt)?\b",
    re.I,
)
SHA256_RE = re.compile(r"\b[a-f0-9]{64}\b", re.I)
HTML_BLOCK_RE = re.compile(
    r"(?is)<(script|style|noscript|nav|footer|header|iframe)[^>]*>.*?</\1>"
)
HTML_TAG_RE = re.compile(r"(?s)<[^>]+>")
HTML_BREAK_RE = re.compile(
    r"(?i)</?(p|div|br|li|tr|h[1-6]|blockquote|pre|section|article|table|thead|tbody)[^>]*>"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", value))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_csv(
    path: Path,
    rows: Iterable[dict[str, Any]],
    fieldnames: Iterable[str] | None = None,
) -> None:
    materialized = list(rows)
    fields = list(fieldnames or ())
    if not fields:
        for row in materialized:
            for key in row:
                if key not in fields:
                    fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def html_to_structured_text(raw: str) -> str:
    """Strip navigation chrome while keeping headings, lists, and table cells."""

    text = HTML_BLOCK_RE.sub("\n", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = HTML_BREAK_RE.sub("\n", text)
    text = re.sub(r"(?i)</(td|th)>", "\t", text)
    text = HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in text.splitlines()]
    collapsed: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                collapsed.append("")
            blank = True
            continue
        blank = False
        collapsed.append(line.strip())
    return "\n".join(collapsed).strip() + "\n"


def pdf_to_text(data: bytes) -> str:
    if not data.startswith(b"%PDF"):
        return ""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError:
            return ""
    import io

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            extracted = page.extract_text() or ""
            pages.append(extracted)
        return "\n".join(pages).strip() + ("\n" if pages else "")
    except Exception:
        return ""


def decode_source(raw: bytes, content_type: str, suffix: str) -> tuple[str, str]:
    lowered = f"{content_type} {suffix}".casefold()
    if raw.startswith(b"%PDF") or ("pdf" in lowered and raw[:16].lstrip().startswith(b"%PDF")):
        text = pdf_to_text(raw)
        return text, "pdf"
    text = raw.decode("utf-8", errors="replace")
    if "html" in lowered or "<html" in text[:2000].casefold() or text.lstrip().startswith("<!DOCTYPE"):
        return html_to_structured_text(text), "html"
    return text.replace("\r\n", "\n"), "text"


def evidence_windows(value: str) -> list[tuple[str, str]]:
    matches = list(
        re.finditer(
            r"(?s)\[(SOURCE_WINDOW_\d+)\]\s*(.*?)(?=\n\s*\[SOURCE_WINDOW_\d+\]|\Z)",
            value,
        )
    )
    return [(match.group(1), match.group(2).strip()) for match in matches if match.group(2).strip()]


def find_anchor_span(text: str, anchor: str) -> tuple[int, int]:
    index = text.find(anchor)
    if index < 0:
        folded = text.casefold()
        needle = anchor.casefold()
        index = folded.find(needle)
    if index < 0:
        # Generated anchors are selected from whitespace-normalized source
        # sentences; match the same words across HTML/PDF line boundaries.
        pattern = r"\s+".join(re.escape(part) for part in anchor.split())
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            index = match.start()
        if index < 0:
            raise ValueError(f"anchor not found: {anchor[:80]}")
    start = text.rfind("\n", 0, index)
    start = 0 if start < 0 else start + 1
    end = text.find("\n", index + len(anchor))
    end = len(text) if end < 0 else end
    return start, end


def expand_window(text: str, anchor: str, *, min_words: int = MIN_WINDOW_WORDS, max_words: int = MAX_WINDOW_WORDS) -> dict[str, Any]:
    start, end = find_anchor_span(text, anchor)
    lines = text.splitlines()
    prefixes = []
    offset = 0
    for line in lines:
        prefixes.append(offset)
        offset += len(line) + 1
    start_line = max(i for i, pos in enumerate(prefixes) if pos <= start)
    end_line = start_line
    for i, pos in enumerate(prefixes):
        if pos <= end:
            end_line = i

    def snippet(lo: int, hi: int) -> str:
        return "\n".join(lines[lo : hi + 1]).strip()

    lo, hi = start_line, end_line
    current = snippet(lo, hi)
    while word_count(current) < min_words and (lo > 0 or hi + 1 < len(lines)):
        if lo > 0:
            lo -= 1
        if word_count(snippet(lo, hi)) < min_words and hi + 1 < len(lines):
            hi += 1
        current = snippet(lo, hi)
        if word_count(current) >= max_words:
            break
    while word_count(current) > max_words and hi > lo:
        # Prefer keeping the anchor line.
        if hi > start_line:
            hi -= 1
        elif lo < start_line:
            lo += 1
        else:
            break
        current = snippet(lo, hi)
    if not current:
        raise ValueError(f"empty window for anchor: {anchor[:80]}")
    return {
        "text": current,
        "line_start": lo + 1,
        "line_end": hi + 1,
        "word_count": word_count(current),
        "char_start": prefixes[lo],
        "char_end": prefixes[hi] + len(lines[hi]),
    }


def ontology_terms(event_id: str, predicate_local: str) -> dict[str, str]:
    return {
        "subject_iri": f"{ONTOLOGY_NS}{event_id}",
        "predicate_iri": f"{ONTOLOGY_NS}{predicate_local}",
        "sentinel_iri": f"{ONTOLOGY_NS}regressionSentinel",
        "class_iri": f"{ONTOLOGY_NS}BenchmarkObject",
        "predicate_local": predicate_local,
    }


def rdf_xml(
    *,
    event_id: str,
    predicate_local: str,
    lexical: str,
    ontology_suffix: str,
) -> str:
    terms = ontology_terms(event_id, predicate_local)
    subject = html.escape(terms["subject_iri"], quote=True)
    predicate = html.escape(terms["predicate_iri"], quote=True)
    sentinel = html.escape(terms["sentinel_iri"], quote=True)
    class_iri = html.escape(terms["class_iri"], quote=True)
    ontology_iri = html.escape(f"{ONTOLOGY_NS.rstrip('#')}/{event_id}/{ontology_suffix}", quote=True)
    value = html.escape(lexical)
    ns = html.escape(ONTOLOGY_NS, quote=True)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF
   xmlns:blind="{ns}"
   xmlns:owl="http://www.w3.org/2002/07/owl#"
   xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
   xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
   xmlns:xsd="http://www.w3.org/2001/XMLSchema#">
  <rdf:Description rdf:about="{ontology_iri}">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#Ontology"/>
  </rdf:Description>
  <rdf:Description rdf:about="{class_iri}">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#Class"/>
  </rdf:Description>
  <rdf:Description rdf:about="{predicate}">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#DatatypeProperty"/>
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#FunctionalProperty"/>
    <rdfs:domain rdf:resource="{class_iri}"/>
    <rdfs:range rdf:resource="{XSD_STRING}"/>
  </rdf:Description>
  <rdf:Description rdf:about="{sentinel}">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#DatatypeProperty"/>
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#FunctionalProperty"/>
    <rdfs:domain rdf:resource="{class_iri}"/>
    <rdfs:range rdf:resource="{XSD_STRING}"/>
  </rdf:Description>
  <rdf:Description rdf:about="{subject}">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#NamedIndividual"/>
    <rdf:type rdf:resource="{class_iri}"/>
    <blind:{html.escape(predicate_local)} rdf:datatype="{XSD_STRING}">{value}</blind:{html.escape(predicate_local)}>
    <blind:regressionSentinel rdf:datatype="{XSD_STRING}">preserve</blind:regressionSentinel>
  </rdf:Description>
</rdf:RDF>
"""


def required_directories() -> list[Path]:
    root = BENCHMARK_DIR
    return [
        root / "public" / "events",
        root / "public" / "documents" / "raw",
        root / "public" / "documents" / "text",
        root / "public" / "excerpts",
        root / "public" / "ontology",
        root / "public" / "cq",
        root / "repair-stage" / "mutants",
        root / "repair-stage" / "candidates",
        root / "private" / "oracle",
        root / "private" / "gold-repaired-owl",
        root / "private" / "annotation",
        root / "private" / "adjudication",
        root / "private" / "construction" / "proposed-gold-repaired-owl",
        root / "extensions",
    ]


def canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def rfc_number_from_url(url: str) -> int | None:
    match = RFC_URL_RE.search(url)
    return int(match.group(1)) if match else None


def is_ietf_family(source_family: str) -> bool:
    return source_family.startswith("IETF_")
