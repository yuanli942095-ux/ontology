from __future__ import annotations

"""Build the independent v6 Direct-IR hold-out without reading model outputs."""

import argparse
import csv
import hashlib
import html
import json
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from rfc213_direct_repair_ir import benchmark_literal_from_span, benchmark_value_slug


PROJECT_DIR = Path(__file__).resolve().parents[1]
BENCHMARK_NAME = "external-real-holdout-v6-direct-ir-blind"
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / BENCHMARK_NAME
OUTPUT_DIR = PROJECT_DIR / "output" / BENCHMARK_NAME
SEED_PATH = BENCHMARK_DIR / "private/construction/source-registry-seed.json"
PROTOCOL_PATH = BENCHMARK_DIR / "private/construction/sampling-protocol.json"
METHOD_MANIFEST = PROJECT_DIR / "method/ecr-ir-gamma/freeze-v2/method-freeze-manifest.json"
HISTORICAL_BENCHMARKS = (
    "external-real-holdout-v1-expanded",
    "external-real-holdout-v3-large",
    "external-real-holdout-v4-blind",
    "external-real-holdout-v5-blind-large",
    "rfc-213-confirmatory-core",
)
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
ONTOLOGY_NS = f"https://w3id.org/ontology-evolution/{BENCHMARK_NAME}#"
REPAIR_TYPES = (
    "TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE",
)
SAFETY_TYPES = (
    "NO_CHANGE",
    "INSUFFICIENT_EVIDENCE",
    "CONFLICTING_EVIDENCE",
)
PUBLIC_LEAKAGE_TERMS = (
    "gold",
    "oracle",
    "正确答案",
    "correct candidate",
    "gold_source_window",
    "proposed_baseline_candidate_id",
)
BOILERPLATE_RE = re.compile(
    r"(?i)("
    r"key words \"must\"|"
    r"bcp 14|"
    r"rfc 2119|"
    r"rfc 8174|"
    r"status of this memo|"
    r"copyright notice|"
    r"table of contents|"
    r"authors'? addresses|"
    r"ietf trust|"
    r"this document is subject to"
    r")"
)
NORMATIVE_RE = re.compile(r"\b(MUST(?: NOT)?|SHOULD(?: NOT)?|MAY|REQUIRED|RECOMMENDED)\b")
CONDITIONAL_RE = re.compile(r"\b(unless|except|only if|provided|when|if)\b", re.I)
TEMPORAL_RE = re.compile(
    r"\b(obsoletes?|updates?|replaces?|previous(?:ly)?|changed?|new version|deprecated)\b",
    re.I,
)
PUBLIC_EVENT_FIELDS = (
    "event_id",
    "semantic_type",
    "domain",
    "subject_label",
    "predicate_label",
    "case_context",
    "source_ids",
    "source_owl",
    "status",
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


def normalized_fingerprint(value: str) -> str:
    return sha256_text(normalize_text(value).casefold())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


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
    if not fields:
        raise ValueError(f"fieldnames required for empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(materialized)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def source_url(rfc: int) -> str:
    return f"https://www.rfc-editor.org/rfc/rfc{rfc}.txt"


def source_id(rfc: int) -> str:
    return f"V6_IETF_RFC{rfc}"


def source_family(rfc: int) -> str:
    return f"IETF_RFC_{rfc}"


def rfc_numbers(value: str) -> set[int]:
    return {int(item) for item in re.findall(r"(?i)\bRFC[\s/_-]?(\d{3,5})\b", value)}


def evidence_windows(value: str) -> list[str]:
    return [
        normalize_text(match.group(1))
        for match in re.finditer(
            r"(?s)\[SOURCE_WINDOW_\d+\]\s*(.*?)(?=\n\s*\[SOURCE_WINDOW_\d+\]|\Z)",
            value,
        )
        if normalize_text(match.group(1))
    ]


def collect_historical_contamination() -> dict[str, Any]:
    """Read benchmark construction artifacts only; never read output/model results."""
    urls: set[str] = set()
    source_ids: set[str] = set()
    rfcs: set[int] = set()
    window_hashes: set[str] = set()
    window_texts: set[str] = set()
    files_scanned: list[str] = []
    for name in HISTORICAL_BENCHMARKS:
        public_dir = PROJECT_DIR / "benchmark" / name / "public"
        if not public_dir.is_dir():
            continue
        for path in sorted(public_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".csv", ".json", ".jsonl", ".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            files_scanned.append(path.relative_to(PROJECT_DIR).as_posix())
            discovered_urls = set(re.findall(r"https://[^\s,\"')]+", text))
            urls.update(discovered_urls)
            # Citations inside evidence do not make the cited RFC a benchmark
            # source. RFC-level exclusion is therefore derived from registered
            # source URLs, not every "RFC n" mention in public prose.
            for url in discovered_urls:
                if "rfc-editor.org/rfc/rfc" in url.casefold():
                    rfcs.update(rfc_numbers(url))
            if path.suffix.lower() == ".csv":
                try:
                    for row in read_csv(path):
                        for key in ("source_id", "document_id"):
                            if row.get(key):
                                source_ids.add(row[key].strip())
                except (csv.Error, UnicodeError):
                    pass
            if "excerpts" in path.parts:
                for window in evidence_windows(text):
                    window_hashes.add(normalized_fingerprint(window))
                    window_texts.add(normalize_text(window).casefold())
    return {
        "schema_version": "v6-contamination-ledger-v1",
        "generated_at_utc": utc_now(),
        "construction_only": True,
        "historical_benchmarks": list(HISTORICAL_BENCHMARKS),
        "files_scanned": files_scanned,
        "urls": sorted(urls),
        "source_ids": sorted(source_ids),
        "rfc_numbers": sorted(rfcs),
        "normalized_evidence_sha256": sorted(window_hashes),
        "historical_window_count": len(window_texts),
        "_window_texts": sorted(window_texts),
    }


def historical_text_collision(window: str, historical_texts: set[str], historical_hashes: set[str]) -> bool:
    normalized = normalize_text(window).casefold()
    if not normalized:
        return True
    if normalized_fingerprint(window) in historical_hashes:
        return True
    if normalized in historical_texts:
        return True
    if len(normalized) < 80:
        return False
    return any(
        (normalized in old or old in normalized)
        for old in historical_texts
        if len(old) >= 80
    )


def download_source(seed: dict[str, Any], *, refresh: bool, offline: bool) -> dict[str, Any]:
    rfc = int(seed["rfc"])
    cache_path = BENCHMARK_DIR / "public/retrieval/source-cache" / f"{source_id(rfc)}.txt"
    url = source_url(rfc)
    if cache_path.is_file() and not refresh:
        raw = cache_path.read_bytes()
        accessed_at = datetime.fromtimestamp(cache_path.stat().st_mtime, timezone.utc).isoformat()
    else:
        if offline:
            raise FileNotFoundError(f"offline source cache missing: {cache_path}")
        request = urllib.request.Request(
            url,
            headers={"User-Agent": f"{BENCHMARK_NAME}/1.0 research benchmark builder"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
        if not raw.strip():
            raise RuntimeError(f"empty source response: {url}")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)
        accessed_at = utc_now()
    text = raw.decode("utf-8", errors="replace")
    if f"Request for Comments: {rfc}" not in text and f"RFC {rfc}" not in text:
        raise RuntimeError(f"download does not identify RFC {rfc}: {url}")
    return {
        **seed,
        "source_id": source_id(rfc),
        "source_family": source_family(rfc),
        "issuer": "IETF",
        "title": extract_title(text, rfc),
        "document_version": f"RFC {rfc}",
        "url": url,
        "accessed_at_utc": accessed_at,
        "sha256": sha256_bytes(raw),
        "cache_path": cache_path.relative_to(PROJECT_DIR).as_posix(),
        "text": text,
    }


def extract_title(text: str, rfc: int) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    skip = re.compile(
        r"^(Category|ISSN|Obsoletes|Updates|Stream|BCP|STD|FYI|Network Working Group|Internet Engineering Task Force):",
        re.I,
    )
    for index, line in enumerate(lines[:180]):
        if re.search(rf"Request for Comments:\s*{rfc}\b", line):
            for candidate in lines[index + 1 : index + 30]:
                value = candidate.strip()
                if not 12 <= len(value) <= 140:
                    continue
                if skip.match(value) or re.match(r"^[A-Z]\.\s+\S+", value):
                    continue
                if re.search(
                    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
                    value,
                ):
                    continue
                if re.match(r"^(IETF|USC/ISI|Verisign|ICANN|Google|Mozilla)\b", value):
                    continue
                return normalize_text(value)
    return f"IETF RFC {rfc}"


def paragraphs(text: str) -> list[str]:
    cleaned = text.replace("\f", "\n")
    blocks = re.split(r"\n\s*\n", cleaned)
    result: list[str] = []
    for block in blocks:
        lines = []
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(r"^\[(?:Page \d+|RFC \d+)\]$", stripped):
                continue
            if re.match(r"^[A-Za-z.-]+\s+(?:Standards Track|Informational|Experimental)\s+\[Page \d+\]$", stripped):
                continue
            lines.append(stripped)
        value = normalize_text(" ".join(lines))
        if (
            80 <= len(value) <= 1800
            and not value.lower().startswith("table of contents")
            and not BOILERPLATE_RE.search(value)
        ):
            result.append(value)
    if len(result) < 5:
        result.extend(sentence_units(text, seen=set(result)))
    if len(result) < 5:
        raise RuntimeError("source did not yield enough paragraphs")
    return result


def sentence_units(text: str, *, seen: set[str] | None = None) -> list[str]:
    seen = set(seen or ())
    extra: list[str] = []
    for block in re.split(r"(?<=[.!?])\s+", text.replace("\f", " ")):
        value = normalize_text(block)
        if not 60 <= len(value) <= 900:
            continue
        if value in seen:
            continue
        seen.add(value)
        extra.append(value)
    return extra


def paragraph_score(value: str, semantic_type: str, decision: str) -> tuple[int, int]:
    normative = bool(NORMATIVE_RE.search(value))
    sentences = len(re.findall(r"[.!?](?:\s|$)", value))
    score = 20 if normative else 0
    if semantic_type == "TEMPORAL_VERSION":
        score += 10 if TEMPORAL_RE.search(value) else 0
    elif semantic_type == "GENERAL_RULE_EXCEPTION":
        score += 10 if CONDITIONAL_RE.search(value) else 0
    elif semantic_type == "CROSS_SENTENCE_SCOPE":
        score += min(sentences, 4) * 3
    if decision == "ABSTAIN" and semantic_type == "SAFETY_CONTROL":
        score = (10 if not normative else 0) + min(sentences, 4)
    return score, -len(value)


def ranked_windows(source: dict[str, Any]) -> list[tuple[int, str]]:
    items = paragraphs(source["text"])
    return sorted(
        enumerate(items),
        key=lambda pair: paragraph_score(
            pair[1], source["semantic_type"], source["decision"]
        ),
        reverse=True,
    )


def choose_direct_window(source: dict[str, Any]) -> tuple[int, str]:
    return choose_ranked_window(source, rank=0, used=set())


def choose_ranked_window(
    source: dict[str, Any],
    *,
    rank: int,
    used: set[str],
    blocked_hashes: set[str] | None = None,
    blocked_texts: set[str] | None = None,
) -> tuple[int, str]:
    unused = []
    for pair in ranked_windows(source):
        if normalized_fingerprint(pair[1]) in used:
            continue
        if blocked_hashes and historical_text_collision(
            pair[1], blocked_texts or set(), blocked_hashes
        ):
            continue
        unused.append(pair)
    if rank >= len(unused):
        raise RuntimeError(
            f"{source['source_id']} lacks unused window rank {rank}; available={len(unused)}"
        )
    return unused[rank]


def neighbor(items: list[str], index: int, offset: int) -> str:
    candidate = max(0, min(len(items) - 1, index + offset))
    if candidate == index:
        candidate = (index + (2 if offset > 0 else -2)) % len(items)
    return items[candidate]


def ontology_terms(event_id: str, domain: str, title: str) -> dict[str, str]:
    dimension = re.sub(r"[^a-z0-9]+", "_", f"{domain}_{event_id}_claim".lower()).strip("_")
    return {
        "dimension": dimension,
        "subject_iri": f"{ONTOLOGY_NS}{event_id}",
        "predicate_iri": f"{ONTOLOGY_NS}{dimension}",
        "subject_label": f"{title} normative subject",
        "predicate_label": f"{domain.replace('_', ' ')} evidence-grounded value",
        "class_iri": f"{ONTOLOGY_NS}HoldoutObject",
        "sentinel_iri": f"{ONTOLOGY_NS}regressionSentinel",
    }


def literal_spec(lexical: str) -> dict[str, str]:
    return {"kind": "literal", "lexical": lexical, "datatype": XSD_STRING}


def rdf_xml(
    *,
    event_id: str,
    terms: dict[str, str],
    lexical: str,
    ontology_suffix: str,
) -> str:
    subject = html.escape(terms["subject_iri"], quote=True)
    predicate = html.escape(terms["predicate_iri"], quote=True)
    sentinel = html.escape(terms["sentinel_iri"], quote=True)
    class_iri = html.escape(terms["class_iri"], quote=True)
    ontology_iri = html.escape(f"{ONTOLOGY_NS.rstrip('#')}/{event_id}/{ontology_suffix}", quote=True)
    value = html.escape(lexical)
    dimension = terms["dimension"]
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF
   xmlns:v6="{html.escape(ONTOLOGY_NS, quote=True)}"
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
    <v6:{dimension} rdf:datatype="{XSD_STRING}">{value}</v6:{dimension}>
    <v6:regressionSentinel rdf:datatype="{XSD_STRING}">preserve</v6:regressionSentinel>
  </rdf:Description>
</rdf:RDF>
"""


def candidate_mapping(repair_index: int) -> dict[str, str]:
    ids = ("CAND_001", "CAND_002", "CAND_003")
    gold = ids[repair_index % 3]
    remaining = [item for item in ids if item != gold]
    return {"gold": gold, "old": remaining[0], "neighbor": remaining[1]}


def operation(
    terms: dict[str, str],
    old_lexical: str,
    new_lexical: str,
) -> dict[str, Any]:
    return {
        "operator": "REPLACE_PROPERTY_VALUE",
        "subject_iri": terms["subject_iri"],
        "predicate_iri": terms["predicate_iri"],
        "old_value": literal_spec(old_lexical),
        "new_value": literal_spec(new_lexical),
    }


def public_event(
    source: dict[str, Any],
    terms: dict[str, str],
    used_source_ids: list[str],
) -> dict[str, Any]:
    if source["decision"] == "NO_CHANGE":
        type_label = source.get("safety_type") or "NO_CHANGE"
    elif source.get("safety_type"):
        type_label = source["safety_type"]
    else:
        type_label = source["semantic_type"]
    return {
        "event_id": source["event_id"],
        "semantic_type": type_label,
        "domain": source["domain"],
        "subject_label": terms["subject_label"],
        "predicate_label": terms["predicate_label"],
        "case_context": (
            f"Using only the five frozen public source windows, determine whether "
            f"{terms['subject_label']} requires a repair for "
            f"{terms['predicate_label']}. Return REPAIR, NO_CHANGE, or ABSTAIN "
            f"under the frozen Direct-IR contract."
        ),
        "source_ids": used_source_ids,
        "source_owl": f"repair-stage/mutants/{source['event_id']}.owl",
        "status": "DRAFT_AWAITING_DUAL_ANNOTATION",
    }


def assert_seed_profile(seed_rows: list[dict[str, Any]], profile: dict[str, Any]) -> None:
    expected = int(profile["events"])
    if len(seed_rows) != expected:
        raise RuntimeError(f"seed/profile mismatch: rows={len(seed_rows)} expected={expected}")
    event_ids = [row["event_id"] for row in seed_rows]
    if len(set(event_ids)) != len(event_ids):
        raise RuntimeError("duplicate event_id in source registry seed")
    domains: dict[str, set[str]] = defaultdict(set)
    family_counts: Counter[str] = Counter()
    repair_counts = Counter()
    safety_counts = Counter()
    for row in seed_rows:
        family = source_family(int(row["rfc"]))
        domains[row["domain"]].add(family)
        family_counts[family] += 1
        if row["decision"] == "REPAIR":
            repair_counts[row["semantic_type"]] += 1
        else:
            safety_counts[row.get("safety_type") or row["decision"]] += 1
    if dict(repair_counts) != profile["repair"]:
        raise RuntimeError(f"repair quota mismatch: {dict(repair_counts)}")
    if dict(safety_counts) != profile["safety"]:
        raise RuntimeError(f"safety quota mismatch: {dict(safety_counts)}")
    required_domains = profile.get("domains", profile.get("domains_minimum"))
    if len(domains) < int(required_domains):
        raise RuntimeError(f"domain quota mismatch: {len(domains)}")
    required_families = profile.get(
        "source_families_per_domain", profile.get("source_families_per_domain_minimum")
    )
    bad = {domain: len(families) for domain, families in domains.items() if len(families) < int(required_families)}
    if bad:
        raise RuntimeError(f"source-family/domain quota mismatch: {bad}")
    max_family = profile.get("max_events_per_source_family")
    if max_family is None and profile.get("max_source_family_fraction") is not None:
        max_family = int(profile["events"]) * float(profile["max_source_family_fraction"])
    if max_family is not None:
        over = {family: count for family, count in family_counts.items() if count > max_family}
        if over:
            raise RuntimeError(f"source-family cap exceeded: {over}")


def expand_full_seed(pilot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pre-register 270 additional events from the same 30 families."""
    extra: list[dict[str, Any]] = []
    event_no = 31
    for seed in sorted(pilot_rows, key=lambda row: source_id(int(row["rfc"]))):
        for extra_index in range(9):
            row = dict(seed)
            row["event_id"] = f"H6_E{event_no:03d}"
            row["window_rank"] = extra_index + 1
            if seed["decision"] == "REPAIR":
                row["semantic_type"] = REPAIR_TYPES[extra_index % 3]
                row["decision"] = "REPAIR"
                row.pop("safety_type", None)
            else:
                safety = SAFETY_TYPES[extra_index % 3]
                row["semantic_type"] = "SAFETY_CONTROL"
                row["safety_type"] = safety
                row["decision"] = "NO_CHANGE" if safety == "NO_CHANGE" else "ABSTAIN"
            extra.append(row)
            event_no += 1
    pilots = [{**row, "window_rank": int(row.get("window_rank", 0))} for row in pilot_rows]
    return pilots + extra


def build(*, profile_name: str, refresh: bool, offline: bool) -> dict[str, Any]:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    profile = protocol["profiles"][profile_name]
    seed_rows = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    if profile_name == "full":
        seed_rows = expand_full_seed(seed_rows)
        write_json(BENCHMARK_DIR / "private/construction/source-registry-seed-full.json", seed_rows)
    assert_seed_profile(seed_rows, profile)

    method_manifest = json.loads(METHOD_MANIFEST.read_text(encoding="utf-8"))
    if method_manifest.get("manifest_sha256") != protocol["method_freeze_manifest_sha256"]:
        raise RuntimeError("frozen V2 method manifest hash binding failed")
    if method_manifest.get("status") != "FROZEN_FOR_NEW_INDEPENDENT_HOLDOUT_ONLY":
        raise RuntimeError("V2 method is not frozen for a new independent hold-out")

    ledger = collect_historical_contamination()
    historical_urls = set(ledger["urls"])
    historical_rfcs = set(ledger["rfc_numbers"])
    historical_source_ids = set(ledger["source_ids"])
    historical_window_hashes = set(ledger["normalized_evidence_sha256"])
    collisions = []
    for seed in seed_rows:
        rfc = int(seed["rfc"])
        checks = {
            "rfc": rfc in historical_rfcs,
            "url": source_url(rfc) in historical_urls,
            "source_id": source_id(rfc) in historical_source_ids,
        }
        if any(checks.values()):
            collisions.append({"event_id": seed["event_id"], **checks})
    ledger["new_source_collision_checks"] = collisions
    historical_window_texts = set(ledger.pop("_window_texts", []))
    write_json(BENCHMARK_DIR / "private/construction/contamination-ledger.json", ledger)
    if collisions:
        raise RuntimeError(f"historical source collision: {collisions}")

    downloaded: dict[int, dict[str, Any]] = {}
    for rfc in sorted({int(row["rfc"]) for row in seed_rows}):
        prototype = next(row for row in seed_rows if int(row["rfc"]) == rfc)
        downloaded[rfc] = download_source(prototype, refresh=refresh, offline=offline)
    sources = [{**downloaded[int(row["rfc"])], **row} for row in seed_rows]
    unique_sources = {source["source_id"]: source for source in sources}
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in unique_sources.values():
        by_domain[source["domain"]].append(source)

    source_paragraphs: dict[str, list[str]] = {
        source_id_value: paragraphs(source["text"]) for source_id_value, source in unique_sources.items()
    }
    used_direct: dict[str, set[str]] = defaultdict(set)
    event_windows: dict[str, tuple[int, str]] = {}
    family_windows: dict[str, tuple[int, str]] = {}
    for source in sorted(sources, key=lambda item: (item["source_id"], int(item.get("window_rank", 0)), item["event_id"])):
        direct_index, window_1 = choose_ranked_window(
            source,
            rank=0,
            used=used_direct[source["source_id"]],
            blocked_hashes=historical_window_hashes,
            blocked_texts=historical_window_texts,
        )
        used_direct[source["source_id"]].add(normalized_fingerprint(window_1))
        event_windows[source["event_id"]] = (direct_index, window_1)
        family_windows.setdefault(source["source_id"], (direct_index, window_1))

    events: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    source_manifest: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    proposed_gold: list[dict[str, Any]] = []
    public_cq: list[dict[str, Any]] = []
    private_cq: list[dict[str, Any]] = []
    target_contract: list[dict[str, Any]] = []
    repair_index = 0

    for source in sorted(sources, key=lambda item: item["event_id"]):
        event_id = source["event_id"]
        domain_sources = sorted(by_domain[source["domain"]], key=lambda item: item["source_id"])
        distractors = [item for item in domain_sources if item["source_id"] != source["source_id"]]
        if len(distractors) < 2:
            raise RuntimeError(f"domain lacks two distractor sources: {source['domain']}")
        direct_index, window_1 = event_windows[event_id]
        own_items = source_paragraphs[source["source_id"]]

        def local_neighbor(offset: int, occupied: set[str]) -> str:
            candidate = neighbor(own_items, direct_index, offset)
            if (
                not historical_text_collision(candidate, historical_window_texts, historical_window_hashes)
                and not BOILERPLATE_RE.search(candidate)
                and normalize_text(candidate) not in occupied
            ):
                return candidate
            for _, text in ranked_windows(source):
                if (
                    normalize_text(text) not in occupied
                    and not historical_text_collision(text, historical_window_texts, historical_window_hashes)
                    and not BOILERPLATE_RE.search(text)
                ):
                    return text
            raise RuntimeError(f"no clean neighbor window for {event_id}")

        window_2 = local_neighbor(-1, {window_1})
        window_3 = local_neighbor(1, {window_1, window_2})
        window_4 = next(
            text
            for _, text in ranked_windows(distractors[0])
            if not historical_text_collision(text, historical_window_texts, historical_window_hashes)
            and normalize_text(text) not in {window_1, window_2, window_3}
        )
        window_5 = next(
            text
            for _, text in ranked_windows(distractors[1])
            if not historical_text_collision(text, historical_window_texts, historical_window_hashes)
            and normalize_text(text) not in {window_1, window_2, window_3, window_4}
        )
        windows = [window_1, window_2, window_3, window_4, window_5]
        if any(
            historical_text_collision(item, historical_window_texts, historical_window_hashes)
            for item in windows
        ):
            raise RuntimeError(f"historical evidence text collision for {event_id}")
        if any(BOILERPLATE_RE.search(item) for item in windows):
            raise RuntimeError(f"boilerplate window selected for {event_id}")
        evidence = "\n\n".join(
            f"[SOURCE_WINDOW_{index}]\n{value}" for index, value in enumerate(windows, 1)
        ) + "\n"
        evidence_path = BENCHMARK_DIR / "public/excerpts" / f"{event_id}-evidence.md"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(evidence, encoding="utf-8")

        terms = ontology_terms(event_id, source["domain"], source["title"])
        dimension = terms["dimension"]
        generated_new = benchmark_literal_from_span(f"{dimension}=unmodeled", window_1)
        if source["decision"] == "NO_CHANGE":
            old_lexical = generated_new
        else:
            old_lexical = f"{dimension}=unmodeled"
        mutant_path = BENCHMARK_DIR / "repair-stage/mutants" / f"{event_id}.owl"
        mutant_path.parent.mkdir(parents=True, exist_ok=True)
        mutant_path.write_text(
            rdf_xml(
                event_id=event_id,
                terms=terms,
                lexical=old_lexical,
                ontology_suffix="mutant",
            ),
            encoding="utf-8",
        )

        used_source_ids = [source["source_id"], distractors[0]["source_id"], distractors[1]["source_id"]]
        events.append(public_event(source, terms, used_source_ids))
        documents.append(
            {
                "event_id": event_id,
                "document_id": f"DOC_{source['source_id']}",
                "source_id": source["source_id"],
                "source_family": source["source_family"],
                "issuer": source["issuer"],
                "title": source["title"],
                "document_version": source["document_version"],
                "source_url": source["url"],
                "accessed_at_utc": source["accessed_at_utc"],
                "source_sha256": source["sha256"],
                "evidence_sha256": sha256_file(evidence_path),
                "locator": f"deterministic paragraph index {direct_index}",
                "status": "SOURCE_REGISTERED_MACHINE_EXTRACTED",
            }
        )
        target_contract.append(
            {
                "event_id": event_id,
                "subject_iri": terms["subject_iri"],
                "predicate_iri": terms["predicate_iri"],
                "old_value": literal_spec(old_lexical),
                "mutant_sha256": sha256_file(mutant_path),
                "expected_old_occurrences": 1,
                "expected_non_target_triples_min": 1,
            }
        )
        target_cq = f"CQ_{event_id}_TARGET"
        non_target_cq = f"CQ_{event_id}_UNAFFECTED_01"
        public_cq.extend(
            [
                {
                    "cq_id": target_cq,
                    "event_id": event_id,
                    "cq_type": "DATA_PROPERTY_EXACT_VALUE",
                    "subject_iri": terms["subject_iri"],
                    "predicate_iri": terms["predicate_iri"],
                    "status": "DRAFT_AWAITING_DUAL_ANNOTATION",
                },
                {
                    "cq_id": non_target_cq,
                    "event_id": event_id,
                    "cq_type": "NON_TARGET_PRESERVATION",
                    "subject_iri": terms["subject_iri"],
                    "predicate_iri": terms["sentinel_iri"],
                    "status": "DRAFT_AWAITING_DUAL_ANNOTATION",
                },
            ]
        )
        expected_decision = source["decision"]
        private_cq.extend(
            [
                {
                    "cq_id": target_cq,
                    "event_id": event_id,
                    "expected_decision": expected_decision,
                    "expected_lexical": generated_new if expected_decision in {"REPAIR", "NO_CHANGE"} else "",
                    "datatype": XSD_STRING,
                    "status": "PROPOSED_AWAITING_ADJUDICATION",
                },
                {
                    "cq_id": non_target_cq,
                    "event_id": event_id,
                    "expected_decision": "PRESERVE",
                    "expected_lexical": "preserve",
                    "datatype": XSD_STRING,
                    "status": "PROPOSED_AWAITING_ADJUDICATION",
                },
            ]
        )

        gold_row: dict[str, Any] = {
            "event_id": event_id,
            "decision": expected_decision,
            "semantic_type": source.get("safety_type") or source["semantic_type"],
            "gold_source_window": "SOURCE_WINDOW_1" if expected_decision in {"REPAIR", "NO_CHANGE"} else "",
            "target": {
                "subject_iri": terms["subject_iri"],
                "predicate_iri": terms["predicate_iri"],
                "old_value": literal_spec(old_lexical),
            },
            "target_cq": target_cq,
            "non_target_cq": non_target_cq,
            "status": "MACHINE_PROPOSAL_NOT_HUMAN_GOLD",
        }
        if expected_decision == "REPAIR":
            gold_row.update(
                {
                    "operation": "UPDATE_LITERAL",
                    "replacement": {"new_value": literal_spec(generated_new)},
                }
            )
            after_path = BENCHMARK_DIR / "private/ontology-after" / f"{event_id}-proposed-gold.owl"
            after_path.parent.mkdir(parents=True, exist_ok=True)
            after_path.write_text(
                rdf_xml(
                    event_id=event_id,
                    terms=terms,
                    lexical=generated_new,
                    ontology_suffix="proposed-gold",
                ),
                encoding="utf-8",
            )
            mapping = candidate_mapping(repair_index)
            variants = {
                "gold": operation(terms, old_lexical, generated_new),
                "old": operation(terms, old_lexical, old_lexical),
                "neighbor": operation(
                    terms,
                    old_lexical,
                    benchmark_literal_from_span(old_lexical, window_4),
                ),
            }
            for role in ("gold", "old", "neighbor"):
                candidate_id = mapping[role]
                candidate_operation = variants[role]
                candidates.append(
                    {
                        "event_id": event_id,
                        "candidate_id": candidate_id,
                        "display_value": candidate_operation["new_value"]["lexical"],
                        "operation_json": json.dumps(
                            candidate_operation, ensure_ascii=False, separators=(",", ":")
                        ),
                        "status": "DRAFT_BASELINE_ONLY",
                        "notes": "Uniform baseline candidate; not available to Direct-IR inference.",
                    }
                )
                operation_path = (
                    BENCHMARK_DIR
                    / "repair-stage/operations"
                    / f"{event_id}-{candidate_id}.json"
                )
                write_json(operation_path, candidate_operation)
            gold_row["proposed_baseline_candidate_id"] = mapping["gold"]
            repair_index += 1
        else:
            gold_row["reason"] = (
                "Evidence supports the existing value."
                if expected_decision == "NO_CHANGE"
                else f"Safety control requires {source.get('safety_type', 'ABSTAIN')}."
            )
        proposed_gold.append(gold_row)

    for source in sorted(unique_sources.values(), key=lambda item: item["source_id"]):
        source_manifest.append(
            {
                "source_id": source["source_id"],
                "domain": source["domain"],
                "source_family": source["source_family"],
                "issuer": source["issuer"],
                "title": source["title"],
                "document_version": source["document_version"],
                "source_url": source["url"],
                "accessed_at_utc": source["accessed_at_utc"],
                "sha256": source["sha256"],
                "cache_path": source["cache_path"],
                "status": "SUCCESS",
            }
        )

    events_path = BENCHMARK_DIR / "public/events/events.jsonl"
    write_jsonl(events_path, events)
    write_csv(
        BENCHMARK_DIR / "public/events/external-real-event-template.csv",
        [
            {**event, "source_ids": json.dumps(event["source_ids"], ensure_ascii=False)}
            for event in events
        ],
        PUBLIC_EVENT_FIELDS,
    )
    write_csv(BENCHMARK_DIR / "public/documents/external-real-document-template.csv", documents)
    write_csv(BENCHMARK_DIR / "public/retrieval/source-cache-manifest.csv", source_manifest)
    write_csv(
        BENCHMARK_DIR / "repair-stage/candidates/external-real-candidate-template.csv",
        candidates,
    )
    write_jsonl(
        BENCHMARK_DIR / "private/construction/proposed-gold-repair-ir.jsonl",
        proposed_gold,
    )
    write_jsonl(
        BENCHMARK_DIR / "private/construction/target-contract.jsonl",
        target_contract,
    )
    write_csv(BENCHMARK_DIR / "public/cq/cq-template.csv", public_cq)
    write_csv(BENCHMARK_DIR / "private/cq/cq-proposed-answers.csv", private_cq)
    write_csv(
        BENCHMARK_DIR / "private/construct-audit/v6-gold-repair-construct-audit.csv",
        [
            {
                "event_id": row["event_id"],
                "gold_candidate_id": row.get("proposed_baseline_candidate_id", ""),
                "construct_type": "UPDATE_LITERAL",
                "supported_by_gamma_mvp": "TRUE" if row["decision"] == "REPAIR" else "FALSE",
                "decision": row["decision"],
                "status": row["status"],
            }
            for row in proposed_gold
        ],
    )
    write_json(
        BENCHMARK_DIR / "private/construction/build-summary.json",
        {
            "schema_version": "external-real-holdout-v6-build-v1",
            "benchmark": BENCHMARK_NAME,
            "profile": profile_name,
            "built_at_utc": utc_now(),
            "status": "DRAFT_AWAITING_DUAL_ANNOTATION",
            "events": len(events),
            "repair_events": sum(row["decision"] == "REPAIR" for row in proposed_gold),
            "safety_events": sum(row["decision"] != "REPAIR" for row in proposed_gold),
            "source_families": len(source_manifest),
            "domains": len({row["domain"] for row in events}),
            "model_results_read": False,
            "method_executed": False,
        },
    )
    return {
        "events": len(events),
        "repair": repair_index,
        "safety": len(events) - repair_index,
        "benchmark_dir": str(BENCHMARK_DIR),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build external-real hold-out v6")
    parser.add_argument("--profile", choices=("pilot", "full"), default="pilot")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build(profile_name=args.profile, refresh=args.refresh, offline=args.offline)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
