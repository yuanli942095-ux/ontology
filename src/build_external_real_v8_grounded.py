from __future__ import annotations

"""Build external-real-v8-grounded from official cached full text.

Scaffold copies event/document/source-family/alignment CSVs only. It does not
copy candidates, private Oracle, rules, or mutants. Retrieval queries use
event-target metadata only and never fall back to notes or gold labels.
"""

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_external_real_v5_retrieved_evidence as v5
from external_real_v8_layout import (
    FULL_METADATA_FIELDS,
    LIGHT_METADATA_FIELDS,
    BenchmarkLayout,
)
from run_auto_formal_policy_batch_v3 import find_forbidden_input_markers


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "benchmark" / "external-real-v4-evidence-aligned"
DST = ROOT / "benchmark" / "external-real-v8-grounded"
V6_CACHE = ROOT / "data" / "external-real-v6-normative-cache"
V8_CACHE = ROOT / "data" / "external-real-v8-grounded-cache"
REGISTRY_CSV = V6_CACHE / "normative-source-registry.csv"
OUTPUT_DIR = ROOT / "output" / "external-real-v8-grounded"

OLD_NAMES = (
    "external-real-v4-evidence-aligned-naturalized",
    "external-real-v4-evidence-aligned",
    "external-real-v3-naturalized",
)
NEW_NAME = "external-real-v8-grounded"

LIGHT_QUERY_FIELDS = LIGHT_METADATA_FIELDS
FULL_QUERY_FIELDS = FULL_METADATA_FIELDS

DOMAIN_DEFAULT_SOURCE = {
    "ai_regulation": "NAT_EU_AI_ACT_EURLEX",
    "cybersecurity_controls": "NAT_NIST_CSF_20",
    "digital_identity": "NAT_NIST_800_63_4",
    "eu_regulation": "NAT_EU_CLP_2020_2174",
    "insurance": "NAT_INS_BEIJING_2026_TERMS",
    "medical_device_cybersecurity": "NAT_FDA_MED_CYBER_MAIN",
    "privacy_framework": "NAT_NIST_PRIVACY_11",
    "sustainability_reporting": "NAT_IFRS_S1",
    "us_regulation": "NAT_SEC_CYBER_FEDREG_2023",
    "web_accessibility": "NAT_WCAG_TR_22",
}

WINDOW_LIMIT = 20
_UNIT_CACHE: dict[str, list[tuple[str, set[str]]]] = {}


def split_units(text: str) -> list[str]:
    """Keep table-like source lines that v5.split_units would drop as too short."""
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" \t-*#")
        if not line or re.search(r"\.{5,}", line):
            continue
        lines.append(line)
    chunks: list[str] = []
    buf = ""
    for line in lines:
        if len(line) >= 90:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(line)
            continue
        buf = f"{buf} {line}".strip()
        if len(buf) >= 140:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    units: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        pieces = re.split(r"(?<=[.!?。！？])\s+", chunk) if len(chunk) > 1200 else [chunk]
        for piece in pieces:
            piece = piece.strip()
            min_len = 12 if re.search(r"[\u4e00-\u9fff]", piece) else 35
            if not min_len <= len(piece) <= 1200:
                continue
            key = piece.lower()
            if key in seen:
                continue
            seen.add(key)
            units.append(piece)
    return units


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="build external-real-v8-grounded")
    parser.add_argument("--only", default="", help="comma-separated event ids")
    parser.add_argument("--query-mode", choices=("full", "light"), default="full")
    parser.add_argument("--force", action="store_true", help="overwrite generated documents")
    parser.add_argument("--skip-scaffold", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_only(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def rewrite_text(text: str) -> str:
    for old in OLD_NAMES:
        text = text.replace(old, NEW_NAME)
    return text


def copy_rewritten_csv(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(rewrite_text(src.read_text(encoding="utf-8-sig")), encoding="utf-8")


def metadata_query_text(event: dict[str, str], mode: str = "full") -> str:
    if mode == "light":
        fields = LIGHT_QUERY_FIELDS
    elif mode == "full":
        fields = FULL_QUERY_FIELDS
    else:
        raise ValueError(f"unknown query mode: {mode}")
    return " ".join(str(event.get(key, "") or "") for key in fields)


def query_tokens(event: dict[str, str], mode: str = "full") -> set[str]:
    text = metadata_query_text(event, mode)
    base = v5.tokens(text)
    extra: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text.lower()):
        extra.add(run)
        extra.update(run[index : index + 2] for index in range(len(run) - 1))
    return base | extra


def is_previous_document(doc: dict[str, str]) -> bool:
    document_id = str(doc.get("document_id", "")).upper()
    file_name = str(doc.get("file_name", "")).upper()
    return (
        document_id.endswith("_OLD")
        or document_id.endswith("_2025")
        or "_OLD." in file_name
        or file_name.endswith("_OLD.TXT")
        or "2025_TERMS" in file_name
    )


def load_registry(path: Path = REGISTRY_CSV) -> dict[str, dict[str, str]]:
    return {row["source_id"]: row for row in read_csv(path)}


def load_cache(manifest: Path) -> dict[str, dict[str, str]]:
    rows = read_csv(manifest) if manifest.is_file() else []
    cache: dict[str, dict[str, str]] = {}
    for row in rows:
        path = Path(row.get("text_path", ""))
        if row.get("status") == "SUCCESS" and path.is_file():
            cache[row["url"]] = row
    return cache


def reuse_v6_cache() -> Path:
    V8_CACHE.mkdir(parents=True, exist_ok=True)
    src = V6_CACHE / "source-cache-manifest.csv"
    dst = V8_CACHE / "source-cache-manifest.csv"
    if src.is_file():
        shutil.copy2(src, dst)
        summary = {
            "reused_from": str(V6_CACHE),
            "copied_at_utc": datetime.now(timezone.utc).isoformat(),
            "rows": len(read_csv(dst)),
            "note": "v8 reuses v6 SUCCESS full-text files in place; failed URLs must be repaired or replaced.",
        }
        (V8_CACHE / "reuse-from-v6.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return dst


def locked_source_id(
    event: dict[str, str],
    alignment: dict[str, str],
    registry: dict[str, dict[str, str]],
) -> str:
    source_id = str(alignment.get("source_id", "")).strip()
    if source_id in registry:
        return source_id
    url = str(event.get("source_url", "")).strip()
    if url:
        for row in registry.values():
            if url in {row.get("source_url", ""), row.get("old_source_url", "")}:
                return row["source_id"]
    domain = str(event.get("domain", "")).strip()
    fallback = DOMAIN_DEFAULT_SOURCE.get(domain, "")
    if fallback in registry:
        return fallback
    raise KeyError(f"no locked registry source for {event.get('event_id')} domain={domain}")


def domain_source_ids(registry: dict[str, dict[str, str]], domain: str) -> list[str]:
    return [row["source_id"] for row in registry.values() if row.get("domain") == domain]


def cached_units(url: str, cache: dict[str, dict[str, str]]) -> tuple[list[tuple[str, set[str]]], dict[str, str], str]:
    record = cache.get(url)
    if not record:
        return [], {}, "cache unavailable"
    if url not in _UNIT_CACHE:
        text = Path(record["text_path"]).read_text(encoding="utf-8", errors="replace")
        _UNIT_CACHE[url] = [(unit, v5.tokens(unit)) for unit in split_units(text)]
    return _UNIT_CACHE[url], record, ""


def retrieve_windows(
    event: dict[str, str],
    units: list[tuple[str, set[str]]],
    mode: str = "full",
    limit: int = WINDOW_LIMIT,
) -> tuple[list[str], float, str]:
    query = query_tokens(event, mode)
    if not query:
        return [], 0.0, "empty query"
    if not units:
        return [], 0.0, "no cached units"
    subject_parts = [
        part.strip().lower()
        for part in re.split(r"::|/|\||：|:", event.get("subject_label", ""))
        if len(part.strip()) >= 4
    ]
    predicate = event.get("predicate_label", "").strip().lower()
    total_weight = sum(v5.token_weight(token) for token in query)
    ranked: list[tuple[float, str, set[str]]] = []
    for unit, unit_tokens in units:
        overlap = query & unit_tokens
        score = sum(v5.token_weight(token) for token in overlap) / max(total_weight, 1.0)
        lower = unit.lower()
        if predicate and predicate in lower:
            score += 0.20
        if any(part in lower for part in subject_parts):
            score += 0.20
        if overlap:
            score += 0.03 * math.log2(1 + len(overlap))
        if score > 0:
            ranked.append((score, unit, unit_tokens))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected: list[str] = []
    selected_token_sets: list[set[str]] = []
    for _score, unit, unit_tokens in ranked:
        if any(
            len(unit_tokens & existing) / max(1, len(unit_tokens | existing)) > 0.75
            for existing in selected_token_sets
        ):
            continue
        selected.append(unit)
        selected_token_sets.append(unit_tokens)
        if len(selected) == limit:
            break
    if not selected:
        return [], 0.0, "no relevant text unit"
    unit_order = [unit for unit, _tokens in units]
    index = {unit: position for position, unit in enumerate(unit_order)}
    with_neighbors: list[str] = []
    seen_selected: set[str] = set()
    for unit in selected:
        position = index.get(unit)
        if position is None:
            continue
        for neighbor in unit_order[max(0, position - 1) : min(len(unit_order), position + 2)]:
            if neighbor in seen_selected:
                continue
            seen_selected.add(neighbor)
            with_neighbors.append(neighbor)
    return with_neighbors, round(ranked[0][0], 4), ""


def sanitize_windows(windows: list[str]) -> list[str]:
    cleaned: list[str] = []
    for window in windows:
        if not find_forbidden_input_markers(window):
            cleaned.append(window)
            continue
        kept_lines = [
            line for line in window.splitlines() if not find_forbidden_input_markers(line)
        ]
        stripped = re.sub(r"\s+", " ", " ".join(kept_lines)).strip()
        if len(stripped) >= 35 and not find_forbidden_input_markers(stripped):
            cleaned.append(stripped)
    return cleaned


def render_document_excerpt(
    source_title: str,
    source_url: str,
    windows: list[str],
) -> str:
    lines = [
        f"Source title: {source_title}",
        f"Source URL: {source_url}",
        "",
        "Raw source excerpt window:",
        "",
        *[f"- {window}" for window in windows],
        "",
    ]
    return "\n".join(lines)


def render_evidence_excerpt(
    event_id: str,
    family: dict[str, str],
    current_windows: list[str],
    previous_windows: list[str],
) -> str:
    lines = [
        f"# {event_id} Evidence",
        "",
        f"- Source title: {family['source_title']}",
        f"- Source URL: {family['source_url']}",
        f"- Prior source title: {family.get('old_source_title', '')}",
        f"- Prior source URL: {family.get('old_source_url', '')}",
        "",
        "Raw source excerpt windows:",
        "",
        *[f"- {window}" for window in current_windows],
        "",
        "Prior source excerpt windows:",
        "",
        *[f"- {window}" for window in previous_windows],
        "",
    ]
    return "\n".join(lines)


def write_scaffold() -> None:
    layout = BenchmarkLayout(DST)
    layout.public_events.mkdir(parents=True, exist_ok=True)
    layout.public_documents.mkdir(parents=True, exist_ok=True)
    layout.public_excerpts.mkdir(parents=True, exist_ok=True)
    layout.public_retrieval.mkdir(parents=True, exist_ok=True)
    for path in layout.public_documents.iterdir():
        if path.is_file() and path.suffix.lower() in {".txt", ".html", ".pdf", ".md"}:
            path.unlink()
    for path in layout.public_excerpts.glob("*.md"):
        path.unlink()

    copy_rewritten_csv(
        SRC / "input" / "external-real-event-template.csv",
        layout.event_csv,
    )
    copy_rewritten_csv(
        SRC / "input" / "external-real-document-template.csv",
        layout.document_csv,
    )
    copy_rewritten_csv(
        SRC / "source-intake" / "external-real-v4-event-source-alignment.csv",
        layout.alignment_csv,
    )
    copy_rewritten_csv(
        SRC / "source-intake" / "external-real-v4-evidence-aligned-source-families.csv",
        layout.family_csv,
    )
    if REGISTRY_CSV.is_file():
        shutil.copy2(REGISTRY_CSV, layout.registry_csv)

    events = read_csv(layout.event_csv)
    for event in events:
        event["source_owl"] = ""
        event["notes"] = ""
        event["status"] = "DRAFT"
        event.setdefault("lexical_status", "")
        event.setdefault("support_status", "")
        event.setdefault("semantic_support", "")
        event.setdefault("support_adjudication_method", "")
        event.setdefault("support_checked_before_model_run", "")
        event.setdefault("support_gate_version", "")
    write_csv(layout.event_csv, events)

    docs = read_csv(layout.document_csv)
    for doc in docs:
        doc["notes"] = ""
        doc["status"] = "DRAFT"
        doc.setdefault("retrieved_at", "")
        doc.setdefault("raw_sha256", "")
        doc.setdefault("text_sha256", "")
        doc.setdefault("extraction_mode", "")
        doc.setdefault("cache_path", "")
        doc.setdefault("window_sha256", "")
        doc["source_type"] = "EXTERNAL_PUBLIC_RAW_EXCERPT_WINDOW"
        doc["document_type"] = "public_normative_retrieved_excerpt"
    write_csv(layout.document_csv, docs)

    layout.query_contracts.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "FULL_METADATA": list(FULL_METADATA_FIELDS),
                "LIGHT_METADATA": list(LIGHT_METADATA_FIELDS),
                "note": "Frozen retrieval query contracts. Do not rewrite after freeze to chase model errors.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (DST / "ACCESS.md").write_text(
        """# Stage access

Gold-assisted dataset curation is not Gold-assisted model inference.

| Stage | May read |
|---|---|
| Auto Policy Construction | `public/` only |
| Candidate Selection | `public/` + `repair-stage/` |
| Evaluation | `public/` + `repair-stage/` + `private/` |
| Hard Gate | `public/` + `repair-stage/` + `rules/` as upper bound only |

`repair-stage/candidates` must not live under `public/`.
""",
        encoding="utf-8",
    )
    (DST / "README.md").write_text(
        """# External Real V8 Grounded

Staged public sources for Auto Policy Construction. Candidate lists, mutants,
and operations live in `repair-stage/`. Oracle lives in `private/`. Formal
policies live in `rules/` and are blind-forbidden during construction.

- READY requires `semantic_support=PASS`, not lexical ratio alone.
- Support adjudication is recorded before any model run and frozen.
- RAW retrieval never falls back to notes, summaries, or candidates.
""",
        encoding="utf-8",
    )
    (DST / "protocol.md").write_text(
        """# External Real V8 Grounded Protocol

1. Cache official public full text outside the frozen revision.
2. Retrieve excerpt windows with frozen FULL_METADATA or LIGHT_METADATA only.
3. Store Source title, Source URL, and raw windows. Do not store Event, Target,
   Benchmark boundary, candidate, Oracle, formal-policy, gold, or Status labels.
4. Offline support scoring is dataset curation. It may read policy conclusion
   tokens. Those tokens are never written back into public excerpts.
5. KEEP/READY events must have semantic_support=PASS. Lexical ratio is a
   prefilter. WARN and FAIL are replaced or dropped before freeze.
6. After freeze, do not change sources, windows, or tokens because a model erred.
7. Auto Policy Construction may read `public/` only.
""",
        encoding="utf-8",
    )
    (layout.public_retrieval / "README.md").write_text(
        """# Public retrieval provenance

Source-family catalog, registry, alignment, retrieval CSV, query contracts,
support adjudication, and metadata leakage audit.

Construction may read these files. They must not contain candidate IDs, Oracle
labels, or allowed_values.
""",
        encoding="utf-8",
    )



def retrieve_family_windows(
    event: dict[str, str],
    family: dict[str, str],
    cache: dict[str, dict[str, str]],
    mode: str,
) -> dict[str, Any]:
    current_units, current_record, current_error = cached_units(family["source_url"], cache)
    previous_units, previous_record, previous_error = cached_units(family.get("old_source_url", ""), cache)
    current_windows, current_score, current_retrieval_error = retrieve_windows(event, current_units, mode)
    previous_windows, previous_score, previous_retrieval_error = retrieve_windows(event, previous_units, mode)
    current_windows = sanitize_windows(current_windows)
    previous_windows = sanitize_windows(previous_windows)
    errors = [
        error
        for error in (current_error, previous_error, current_retrieval_error, previous_retrieval_error)
        if error
    ]
    if current_windows:
        status = "RETRIEVAL_READY"
    else:
        status = "RETRIEVAL_FAILED"
        if not current_retrieval_error:
            errors.append("no sanitized current window")
    return {
        "current_windows": current_windows,
        "previous_windows": previous_windows,
        "current_score": current_score,
        "previous_score": previous_score,
        "current_record": current_record,
        "previous_record": previous_record,
        "status": status,
        "errors": " | ".join(errors),
        "fallback_used": False,
        "candidate_used": False,
        "oracle_used": False,
        "note_used": False,
    }


def write_event_files(
    event: dict[str, str],
    docs: list[dict[str, str]],
    family: dict[str, str],
    retrieved: dict[str, Any],
) -> None:
    document_dir = BenchmarkLayout(DST).public_documents
    excerpts = BenchmarkLayout(DST).public_excerpts
    document_dir.mkdir(parents=True, exist_ok=True)
    excerpts.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for doc in docs:
        previous = is_previous_document(doc)
        windows = retrieved["previous_windows"] if previous else retrieved["current_windows"]
        record = retrieved["previous_record"] if previous else retrieved["current_record"]
        source_url = family["old_source_url"] if previous else family["source_url"]
        source_title = family["old_source_title"] if previous else family["source_title"]
        content = render_document_excerpt(source_title, source_url, windows)
        path = document_dir / f"{doc['document_id']}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        rel_cache = ""
        if record:
            cache_path = Path(record.get("text_path", ""))
            try:
                rel_cache = str(cache_path.resolve().relative_to(ROOT))
            except ValueError:
                rel_cache = str(cache_path)
        doc["file_name"] = path.name
        doc["source_title"] = source_title
        doc["source_url"] = source_url
        doc["publisher"] = family.get("publisher", doc.get("publisher", ""))
        doc["source_type"] = "EXTERNAL_PUBLIC_RAW_EXCERPT_WINDOW"
        doc["document_type"] = "public_normative_retrieved_excerpt"
        doc["sha256"] = sha256_file(path)
        doc["retrieved_at"] = record.get("retrieved_at_utc", retrieved_at) if record else retrieved_at
        doc["raw_sha256"] = record.get("raw_sha256", "") if record else ""
        doc["text_sha256"] = record.get("text_sha256", "") if record else ""
        doc["extraction_mode"] = record.get("extraction_mode", "") if record else ""
        doc["cache_path"] = rel_cache
        doc["window_sha256"] = sha256_text("\n".join(windows))
        doc["status"] = "READY" if retrieved["status"] == "RETRIEVAL_READY" else "FAILED"
        doc["notes"] = ""
    evidence = render_evidence_excerpt(
        event["event_id"],
        family,
        retrieved["current_windows"],
        retrieved["previous_windows"],
    )
    (excerpts / f"{event['event_id']}-evidence.md").write_text(evidence, encoding="utf-8")
    event["source_url"] = family["source_url"]
    event["retrieved_at"] = retrieved_at
    event["notes"] = ""
    event["status"] = "DRAFT"


def retrieval_row(
    event: dict[str, str],
    source_id: str,
    family: dict[str, str],
    retrieved: dict[str, Any],
    query_mode: str,
) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "domain": event["domain"],
        "semantic_type": event["semantic_type"],
        "source_id": source_id,
        "current_url": family.get("source_url", ""),
        "previous_url": family.get("old_source_url", ""),
        "retrieval_status": retrieved["status"],
        "current_score": retrieved["current_score"],
        "previous_score": retrieved["previous_score"],
        "current_windows": len(retrieved["current_windows"]),
        "previous_windows": len(retrieved["previous_windows"]),
        "window_sha256": sha256_text("\n".join(retrieved["current_windows"])),
        "query_mode": query_mode,
        "query_fields": "|".join(FULL_QUERY_FIELDS if query_mode == "full" else LIGHT_QUERY_FIELDS),
        "errors": retrieved["errors"],
        "fallback_used": False,
        "candidate_used": False,
        "oracle_used": False,
        "note_used": False,
        "formal_policy_used": False,
    }


def build(only: set[str] | None = None, query_mode: str = "full") -> list[dict[str, Any]]:
    layout = BenchmarkLayout(DST)
    events_path = layout.event_csv
    docs_path = layout.document_csv
    events = read_csv(events_path)
    docs = read_csv(docs_path)
    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for doc in docs:
        docs_by_event[doc["event_id"]].append(doc)
    alignments = {
        row["event_id"]: row
        for row in read_csv(layout.alignment_csv)
    }
    registry = load_registry(layout.registry_csv)
    cache = load_cache(reuse_v6_cache())
    retrievals: list[dict[str, Any]] = []
    selected = [
        event
        for event in events
        if not only or event["event_id"] in only
    ]
    for event in selected:
        event_id = event["event_id"]
        source_id = locked_source_id(event, alignments.get(event_id, {}), registry)
        family = registry[source_id]
        retrieved = retrieve_family_windows(event, family, cache, query_mode)
        write_event_files(event, docs_by_event[event_id], family, retrieved)
        retrievals.append(retrieval_row(event, source_id, family, retrieved, query_mode))

    write_csv(events_path, events)
    write_csv(docs_path, docs)
    existing = []
    retrieval_path = layout.retrieval_csv
    if retrieval_path.is_file() and only:
        existing = [row for row in read_csv(retrieval_path) if row["event_id"] not in only]
    merged = existing + retrievals
    merged.sort(key=lambda row: row["event_id"])
    write_csv(retrieval_path, merged)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT_DIR / "event-retrieval.csv", merged)
    return retrievals


def main() -> int:
    args = parse_args()
    if not args.skip_scaffold:
        write_scaffold()
    only = parse_only(args.only)
    retrievals = build(only=only or None, query_mode=args.query_mode)
    ready = sum(row["retrieval_status"] == "RETRIEVAL_READY" for row in retrievals)
    print(json.dumps(
        {
            "benchmark": NEW_NAME,
            "events": len(retrievals),
            "retrieval_ready": ready,
            "retrieval_failed": len(retrievals) - ready,
            "query_mode": args.query_mode,
            "fallback_used": False,
            "candidate_used": False,
            "oracle_used": False,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
