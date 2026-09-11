from __future__ import annotations

"""Build tranche-1 proposed events. Does not write official Oracle or freeze Gold."""

import argparse
import json
import random
import re
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from collect_ecr_repair_post_freeze_blind_v1_exclusions import collect_exclusions
from ecr_repair_post_freeze_blind_v1_common import (
    AS_OF,
    BENCHMARK_DIR,
    BENCHMARK_NAME,
    METHOD_ID,
    METHOD_MANIFEST,
    FULL_PARTITION_COUNTS,
    PROJECT_DIR,
    TRANCHE1_PARTITION_COUNTS,
    USER_AGENT,
    XSD_STRING,
    canonical_url,
    decode_source,
    expand_window,
    is_ietf_family,
    ontology_terms,
    rdf_xml,
    read_json,
    required_directories,
    rfc_number_from_url,
    sha256_bytes,
    sha256_file,
    utc_now,
    word_count,
    write_csv,
    write_json,
    write_jsonl,
)


ORACLE_DIR = BENCHMARK_DIR / "private" / "oracle"
PROPOSED_GOLD = BENCHMARK_DIR / "private/construction/proposed-gold.jsonl"
SOURCE_POOL = BENCHMARK_DIR / "private/construction/source-pool.json"
CATALOG = BENCHMARK_DIR / "private/construction/tranche1-event-catalog.json"
CANDIDATE_SEED = 20260909


class BuildError(RuntimeError):
    """Raised when construction cannot continue honestly."""


def ensure_directories() -> None:
    for path in required_directories():
        path.mkdir(parents=True, exist_ok=True)
    for empty in (ORACLE_DIR, BENCHMARK_DIR / "private/gold-repaired-owl"):
        for child in empty.glob("*"):
            if child.name.lower() in {"readme.md", ".gitkeep"}:
                continue
            if child.is_file() and child.suffix.lower() in {".jsonl", ".owl", ".json", ".csv"}:
                raise BuildError(f"official Gold file is not allowed during construction: {child}")


def load_method_sha() -> str:
    if not METHOD_MANIFEST.is_file():
        return "MISSING"
    return sha256_file(METHOD_MANIFEST)


def document_is_excluded(doc: dict[str, Any], exclusions: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    url = canonical_url(doc["official_url"])
    if url in set(exclusions["urls"]):
        reasons.append("url_overlap")
    rfc = rfc_number_from_url(doc["official_url"])
    if rfc is not None and rfc in set(exclusions["rfc_numbers"]):
        reasons.append(f"rfc_overlap:{rfc}")
    return reasons


def download_document(doc: dict[str, Any], *, refresh: bool) -> dict[str, Any]:
    raw_path = BENCHMARK_DIR / "public/documents/raw" / doc["document_id"]
    text_path = BENCHMARK_DIR / "public/documents/text" / f"{doc['document_id']}.txt"
    meta_path = BENCHMARK_DIR / "public/documents" / f"{doc['document_id']}.json"
    suffix = doc.get("file_suffix") or Path(doc["official_url"].split("?")[0]).suffix or ".html"
    if not suffix.startswith("."):
        suffix = ".html"
    if "rfc-editor.org" in doc["official_url"]:
        suffix = ".txt"
    if doc["official_url"].casefold().endswith(".pdf"):
        suffix = ".pdf"
    raw_path = raw_path.with_suffix(suffix)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.8",
    }
    retrieved_at = utc_now()
    etag = ""
    last_modified = ""
    content_type = ""
    page_title = doc["official_title"]
    if raw_path.is_file() and not refresh:
        raw = raw_path.read_bytes()
        retrieved_at = datetime_from_mtime(raw_path)
        if meta_path.is_file():
            cached_meta = read_json(meta_path)
            etag = cached_meta.get("etag", "") or ""
            last_modified = cached_meta.get("last_modified", "") or ""
            content_type = cached_meta.get("content_type", "") or ""
            retrieved_at = cached_meta.get("retrieved_at") or retrieved_at
    else:
        request = Request(doc["official_url"], headers=headers)
        try:
            with urlopen(request, timeout=90) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
                etag = response.headers.get("ETag", "") or ""
                last_modified = response.headers.get("Last-Modified", "") or ""
                retrieved_at = utc_now()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise BuildError(f"download failed for {doc['document_id']}: {exc}") from exc
        if not raw:
            raise BuildError(f"empty download: {doc['document_id']}")
        head = raw[:4000].lower()
        if b"please enable javascript" in head or (len(raw) < 4000 and b"interstitial" in head):
            raise BuildError(f"{doc['document_id']} received a blocked interstitial")
        if suffix == ".pdf" and not raw.startswith(b"%PDF"):
            raise BuildError(
                f"{doc['document_id']} expected PDF but received {content_type or raw[:40]!r}"
            )
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)

    if suffix == ".pdf" and raw_path.is_file() and not raw.startswith(b"%PDF"):
        raise BuildError(f"{doc['document_id']} cached file is not a PDF")
    text, kind = decode_source(raw, content_type, suffix)
    if not text.strip():
        raise BuildError(f"no extractable text for {doc['document_id']} ({kind})")
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(text, encoding="utf-8", newline="\n")
    title_match = re.search(r"(?is)<title>(.*?)</title>", raw.decode("utf-8", errors="ignore"))
    if title_match:
        page_title = re.sub(r"\s+", " ", title_match.group(1)).strip() or page_title
    decoded = raw.decode("utf-8", errors="ignore")
    ms_date = re.search(r'(?is)<meta name="ms.date" content="([^"]+)"', decoded)
    if ms_date and not doc.get("publication_date"):
        doc = {**doc, "publication_date": ms_date.group(1)[:10]}
    record = {
        **doc,
        "retrieved_at": retrieved_at,
        "official_url": doc["official_url"],
        "canonical_url": canonical_url(doc["official_url"]),
        "source_file": raw_path.relative_to(PROJECT_DIR).as_posix(),
        "text_file": text_path.relative_to(PROJECT_DIR).as_posix(),
        "source_sha256": sha256_bytes(raw),
        "text_sha256": sha256_file(text_path),
        "etag": etag,
        "last_modified": last_modified,
        "content_type": content_type,
        "page_title": page_title,
        "bytes": len(raw),
        "extracted_kind": kind,
        "effective_from": doc.get("effective_from"),
        "effective_to": doc.get("effective_to"),
        "license_or_access_note": doc.get("license_or_access_note", ""),
    }
    write_json(meta_path, {k: v for k, v in record.items() if k != "text"})
    record["text"] = text
    return record


def datetime_from_mtime(path: Path) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def cloud_date_ok(record: dict[str, Any]) -> bool:
    if record["source_family"] not in {"AWS_DOCUMENTATION", "AZURE_DOCUMENTATION", "GCP_DOCUMENTATION"}:
        return True
    if record.get("publication_date"):
        return True
    if record.get("last_modified"):
        try:
            parsedate_to_datetime(record["last_modified"])
            return True
        except (TypeError, ValueError, IndexError):
            pass
    text = record.get("text", "")
    return bool(re.search(r"(20\d{2}-\d{2}-\d{2}|updated|last modified|January|February|20\d{2})", text, re.I))


def as_of_ok(doc: dict[str, Any]) -> bool:
    published = str(doc.get("publication_date") or "")
    if len(published) >= 10:
        return published[:10] <= AS_OF[:10]
    if re.match(r"^\d{4}-\d{2}$", published):
        return published <= AS_OF[:7]
    if re.match(r"^\d{4}$", published):
        return published <= AS_OF[:4]
    return True


def permute_candidates(event_index: int, correct: str, distractors: list[str]) -> list[tuple[str, str, bool]]:
    values = [correct, *[item for item in distractors if item and item != correct]]
    seen: list[str] = []
    for item in values:
        if item not in seen:
            seen.append(item)
    while len(seen) < 4:
        seen.append(f"{correct} (adjacent unused value {len(seen)})")
    seen = seen[:4]
    rng = random.Random(CANDIDATE_SEED + event_index)
    rng.shuffle(seen)
    rows = []
    for index, value in enumerate(seen, start=1):
        cand_id = f"CAND_{index:03d}"
        rows.append((cand_id, value, value == correct))
    return rows


def render_evidence(windows: list[dict[str, Any]]) -> str:
    parts = []
    for index, window in enumerate(windows, start=1):
        parts.append(f"[SOURCE_WINDOW_{index}]\n{window['text'].rstrip()}\n")
    return "\n".join(parts).rstrip() + "\n"


def public_event(event: dict[str, Any], document_ids: list[str]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "split": "post_freeze_blind_test",
        "status": "PROPOSED_NOT_FROZEN",
        "domain": event["domain"],
        "source_family": event["source_family"],
        "title": event["title"],
        "case_context": event["case_context"],
        "target": {
            "subject_label": event["subject_label"],
            "predicate_label": event["predicate_label"],
            "value_kind": "literal_string",
        },
        "source_owl": f"repair-stage/mutants/{event['event_id']}.owl",
        "evidence_file": f"public/excerpts/{event['event_id']}-evidence.md",
        "document_ids": document_ids,
        "as_of": AS_OF,
    }


def materialize(events: list[dict[str, Any]], documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    public_events = []
    proposed_gold = []
    cq_rows = []
    candidate_rows = []
    window_rows = []
    failures: list[str] = []
    for event_index, event in enumerate(events, start=1):
        extracted = []
        used_docs: list[str] = []
        for window_spec in event["windows"]:
            doc_id = window_spec["document_id"]
            record = documents.get(doc_id)
            if record is None:
                failures.append(f"{event['event_id']}: missing {doc_id}")
                continue
            try:
                payload = expand_window(record["text"], window_spec["anchor"])
            except ValueError as exc:
                failures.append(f"{event['event_id']}:{doc_id}:{exc}")
                continue
            payload.update(window_spec)
            payload["document_id"] = doc_id
            extracted.append(payload)
            if doc_id not in used_docs:
                used_docs.append(doc_id)
        if len(extracted) < 3:
            failures.append(f"{event['event_id']}: only {len(extracted)} windows")
            continue
        evidence = render_evidence(extracted)
        excerpt_path = BENCHMARK_DIR / "public/excerpts" / f"{event['event_id']}-evidence.md"
        excerpt_path.write_text(evidence, encoding="utf-8", newline="\n")
        terms = ontology_terms(event["event_id"], event["predicate_local"])
        mutant = rdf_xml(
            event_id=event["event_id"],
            predicate_local=event["predicate_local"],
            lexical=event["old_value"],
            ontology_suffix="mutant",
        )
        mutant_path = BENCHMARK_DIR / "repair-stage/mutants" / f"{event['event_id']}.owl"
        mutant_path.write_text(mutant, encoding="utf-8", newline="\n")
        public_owl = BENCHMARK_DIR / "public/ontology" / f"{event['event_id']}.owl"
        public_owl.write_text(mutant, encoding="utf-8", newline="\n")
        if event["partition"] == "REPAIR" and event.get("new_value"):
            repaired = rdf_xml(
                event_id=event["event_id"],
                predicate_local=event["predicate_local"],
                lexical=event["new_value"],
                ontology_suffix="proposed-repaired",
            )
            repaired_path = (
                BENCHMARK_DIR
                / "private/construction/proposed-gold-repaired-owl"
                / f"{event['event_id']}.owl"
            )
            repaired_path.write_text(repaired, encoding="utf-8", newline="\n")
        public_events.append(public_event(event, used_docs))
        decision = "REPAIR" if event["partition"] == "REPAIR" else (
            "NO_CHANGE" if event["partition"] == "NO_CHANGE" else "ABSTAIN"
        )
        gold = {
            "event_id": event["event_id"],
            "status": "PROPOSED_NOT_FROZEN",
            "decision": decision,
            "partition": event["partition"],
            "semantic_type": event["semantic_type"],
            "safety_feature": event["safety_feature"],
            "target": {
                "subject_iri": terms["subject_iri"],
                "predicate_iri": terms["predicate_iri"],
                "old_value": {
                    "kind": "literal",
                    "lexical": event["old_value"],
                    "datatype": XSD_STRING,
                },
            },
            "replacement": None,
            "supporting_window_ids": event.get("supporting_window_ids", []),
            "adjudication_status": "PROPOSED_AWAITING_DUAL_ANNOTATION",
            "operation": "UPDATE_LITERAL",
            "constructor_notes": "Constructor-proposed only. Not an official Oracle.",
        }
        if event["partition"] == "REPAIR" and event.get("new_value"):
            gold["replacement"] = {
                "new_value": {
                    "kind": "literal",
                    "lexical": event["new_value"],
                    "datatype": XSD_STRING,
                }
            }
        proposed_gold.append(gold)
        cq_rows.extend(
            [
                {
                    "cq_id": f"CQ_{event['event_id']}_TARGET",
                    "event_id": event["event_id"],
                    "cq_type": "DATA_PROPERTY_EXACT_VALUE",
                    "prompt": (
                        f"Does the subject still carry exactly one literal for "
                        f"{event['predicate_label']} after any authorized UPDATE_LITERAL?"
                    ),
                    "subject_iri": terms["subject_iri"],
                    "predicate_iri": terms["predicate_iri"],
                    "status": "PROPOSED_NOT_FROZEN",
                },
                {
                    "cq_id": f"CQ_{event['event_id']}_NON_TARGET_01",
                    "event_id": event["event_id"],
                    "cq_type": "NON_TARGET_PRESERVATION",
                    "prompt": "Is the regression sentinel literal still the value preserve?",
                    "subject_iri": terms["subject_iri"],
                    "predicate_iri": terms["sentinel_iri"],
                    "status": "PROPOSED_NOT_FROZEN",
                },
                {
                    "cq_id": f"CQ_{event['event_id']}_NON_TARGET_02",
                    "event_id": event["event_id"],
                    "cq_type": "TYPE_PRESERVATION",
                    "prompt": "Is the subject still typed as BenchmarkObject?",
                    "subject_iri": terms["subject_iri"],
                    "predicate_iri": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
                    "status": "PROPOSED_NOT_FROZEN",
                },
            ]
        )
        if event["partition"] == "REPAIR" and event.get("new_value"):
            for cand_id, value, is_gold in permute_candidates(
                event_index, event["new_value"], event.get("distractors", [])
            ):
                candidate_rows.append(
                    {
                        "event_id": event["event_id"],
                        "candidate_id": cand_id,
                        "display_value": value,
                        "operation_json": json.dumps(
                            {
                                "operator": "REPLACE_PROPERTY_VALUE",
                                "subject_iri": terms["subject_iri"],
                                "predicate_iri": terms["predicate_iri"],
                                "old_value": {
                                    "kind": "literal",
                                    "lexical": event["old_value"],
                                    "datatype": XSD_STRING,
                                },
                                "new_value": {
                                    "kind": "literal",
                                    "lexical": value,
                                    "datatype": XSD_STRING,
                                },
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "status": "DRAFT_BASELINE_ONLY",
                        "notes": "Baseline candidate; hidden from V2.4 generation.",
                    }
                )
                if is_gold:
                    gold["proposed_baseline_candidate_id"] = cand_id
        for index, window in enumerate(extracted, start=1):
            window_rows.append(
                {
                    "event_id": event["event_id"],
                    "window_id": f"SOURCE_WINDOW_{index}",
                    "document_id": window["document_id"],
                    "role": window.get("role", ""),
                    "line_start": window["line_start"],
                    "line_end": window["line_end"],
                    "word_count": window["word_count"],
                    "anchor": window.get("anchor", ""),
                }
            )
    if failures:
        raise BuildError("window extraction failed:\n" + "\n".join(failures))
    write_jsonl(BENCHMARK_DIR / "public/events/events.jsonl", public_events)
    write_jsonl(PROPOSED_GOLD, proposed_gold)
    write_csv(BENCHMARK_DIR / "public/cq/cq-template.csv", cq_rows)
    write_csv(BENCHMARK_DIR / "repair-stage/candidates/candidate-template.csv", candidate_rows)
    write_csv(BENCHMARK_DIR / "private/construction/window-locators.csv", window_rows)
    partitions = Counter(event["partition"] for event in events)
    expected_partitions = (
        FULL_PARTITION_COUNTS if len(events) == 80 else TRANCHE1_PARTITION_COUNTS
    )
    if dict(partitions) != expected_partitions:
        raise BuildError(
            f"partition mismatch for {len(events)} events: "
            f"actual={dict(partitions)} expected={expected_partitions}"
        )
    ietf_events = sum(1 for event in events if is_ietf_family(event["source_family"]))
    return {
        "events": len(public_events),
        "ietf_events": ietf_events,
        "partitions": dict(partitions),
        "proposed_gold": PROPOSED_GOLD.relative_to(PROJECT_DIR).as_posix(),
    }


def write_source_manifest(documents: list[dict[str, Any]]) -> None:
    rows = []
    for doc in documents:
        rows.append(
            {
                "document_id": doc["document_id"],
                "official_title": doc["official_title"],
                "issuer": doc["issuer"],
                "document_type": doc["document_type"],
                "version": doc.get("version") or "",
                "publication_date": doc.get("publication_date") or "",
                "effective_from": doc.get("effective_from") or "",
                "effective_to": doc.get("effective_to") or "",
                "retrieved_at": doc["retrieved_at"],
                "official_url": doc["official_url"],
                "source_file": doc["source_file"],
                "source_sha256": doc["source_sha256"],
                "license_or_access_note": doc.get("license_or_access_note") or "",
                "etag": doc.get("etag") or "",
                "last_modified": doc.get("last_modified") or "",
                "page_title": doc.get("page_title") or "",
                "source_family": doc["source_family"],
                "domain": doc["domain"],
            }
        )
    write_csv(BENCHMARK_DIR / "source-manifest.csv", rows)


def write_construction_manifest(summary: dict[str, Any], documents: list[dict[str, Any]]) -> None:
    files = []
    for path in sorted(BENCHMARK_DIR.rglob("*")):
        if not path.is_file():
            continue
        if "documents/raw" in path.as_posix() and path.suffix.lower() in {".pdf"}:
            files.append(
                {
                    "path": path.relative_to(PROJECT_DIR).as_posix(),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
            continue
        if path.stat().st_size > 8_000_000:
            continue
        files.append(
            {
                "path": path.relative_to(PROJECT_DIR).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    write_json(
        BENCHMARK_DIR / "freeze-manifest.json",
        {
            "benchmark_id": BENCHMARK_NAME,
            "status": "NOT_FROZEN",
            "reason": "Events are PROPOSED_NOT_FROZEN; dual annotation and official Oracle are incomplete.",
            "frozen_at": None,
            "method_id": METHOD_ID,
            "method_manifest_sha256": load_method_sha(),
            "as_of": AS_OF,
            "event_count": summary.get("events", 0),
            "partition_counts": summary.get("partitions", {}),
            "file_count": len(files),
            "files": files,
            "cached_documents": len(documents),
        },
    )


def phase_download(*, refresh: bool) -> list[dict[str, Any]]:
    ensure_directories()
    exclusions = collect_exclusions()
    write_json(BENCHMARK_DIR / "private/construction/used-source-exclusions.json", exclusions)
    pool = read_json(SOURCE_POOL)
    catalog = read_json(CATALOG)
    needed = {window["document_id"] for event in catalog["events"] for window in event["windows"]}
    documents = []
    for doc in pool["documents"]:
        if doc["document_id"] not in needed:
            continue
        reasons = document_is_excluded(doc, exclusions)
        if reasons:
            raise BuildError(f"{doc['document_id']} excluded: {reasons}")
        if not as_of_ok(doc):
            raise BuildError(f"{doc['document_id']} published after as_of")
        record = download_document(doc, refresh=refresh)
        if record["source_sha256"] in set(exclusions["sha256"]):
            raise BuildError(f"{doc['document_id']} sha256 overlap")
        if not cloud_date_ok(record):
            raise BuildError(f"{doc['document_id']} lacks a reliable version or update date")
        documents.append(record)
        write_json(
            BENCHMARK_DIR / "public/documents" / f"{doc['document_id']}.json",
            {k: v for k, v in record.items() if k != "text"},
        )
    write_source_manifest(documents)
    return documents


def phase_materialize(documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ensure_directories()
    catalog = read_json(CATALOG)
    if documents is None:
        documents = []
        for path in sorted((BENCHMARK_DIR / "public/documents").glob("DOC_*.json")):
            record = read_json(path)
            text_path = PROJECT_DIR / record["text_file"]
            record["text"] = text_path.read_text(encoding="utf-8")
            documents.append(record)
    by_id = {doc["document_id"]: doc for doc in documents}
    summary = materialize(catalog["events"], by_id)
    write_construction_manifest(summary, documents)
    write_json(BENCHMARK_DIR / "private/construction/build-summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("dirs", "download", "materialize", "all"), default="all")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.phase == "dirs":
        ensure_directories()
        print(json.dumps({"status": "directories_ready"}, indent=2))
        return 0
    documents = None
    if args.phase in {"download", "all"}:
        documents = phase_download(refresh=args.refresh)
        print(json.dumps({"downloaded": [doc["document_id"] for doc in documents]}, indent=2))
    if args.phase in {"materialize", "all"}:
        summary = phase_materialize(documents)
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
