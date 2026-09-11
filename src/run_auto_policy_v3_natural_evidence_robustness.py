from __future__ import annotations

"""Strict natural-evidence robustness for the 213-event naturalized benchmark.

Raw variants retrieve only from public source documents using their declared
metadata condition. They never use structured evidence notes as retrieval
queries or fallbacks and never load candidate, Oracle, or formal-policy data
during generation.
"""

import argparse
import csv
import html
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_public_window_handoff import (
    INPUT_CONSTRUCTION_ERROR,
    PUBLIC_FROZEN_WINDOW,
    RETRIEVAL_FAILED,
    frozen_window_blocks,
    public_ready,
)
from external_real_v8_layout import (
    FULL_METADATA_FIELDS,
    LIGHT_METADATA_FIELDS,
    construction_paths,
)
from run_auto_policy_v3_robustness import build_prompt, save_csv


BENCHMARK_NAME = "external-real-v3-naturalized"
BENCHMARK_DIR = v3.ROOT / "benchmark" / BENCHMARK_NAME
OUTPUT_DIR = v3.ROOT / "output" / BENCHMARK_NAME / "natural-evidence-robustness"
DOCUMENT_CSV = BENCHMARK_DIR / "input" / "external-real-document-template.csv"
DOCUMENT_DIR = BENCHMARK_DIR / "documents"
RETRIEVAL_CSV: Path | None = None
_PUBLIC_RETRIEVAL_INDEX: dict[str, dict[str, str]] | None = None
RUNS = 1
SEED_BASE = 20260827

VARIANTS = {
    "STRUCTURED_NOTE": "candidate-blind structured evidence note",
    "RAW_SHORT_WINDOW": "top raw source windows retrieved from full event metadata",
    "RAW_PROVENANCE_WINDOW": "raw source windows ranked by target and document provenance",
    "RAW_WINDOW_WITH_DISTRACTOR": "raw target windows plus deterministic raw-source distractors",
    "RAW_WINDOW_METADATA_LIGHT": "frozen public retrieval windows with light prompt metadata",
    "RAW_WINDOW_NO_METADATA": "deterministic raw source windows without event metadata",
}


@dataclass(frozen=True)
class RetrievalResult:
    evidence: str
    retrieval_status: str
    source_label: str
    raw_source_used: bool
    fallback_used: bool
    retrieved_doc_count: int
    retrieved_window_count: int
    source_urls: tuple[str, ...] = ()
    forbidden_input_markers: tuple[str, ...] = ()
    candidate_used: bool = False
    oracle_used: bool = False
    note_used: bool = False
    retrieval_source: str = ""
    retrieval_replayed: bool = False
    manual_policy_used: bool = False


def assert_raw_isolation(result: RetrievalResult, variant: str) -> None:
    """Hard boundary for RAW conditions: failure is a result, never a fallback."""
    if not variant.startswith("RAW_"):
        return
    if result.fallback_used is not False:
        raise AssertionError(f"{variant}: fallback_used must be False")
    if result.candidate_used is not False:
        raise AssertionError(f"{variant}: candidate_used must be False")
    if result.oracle_used is not False:
        raise AssertionError(f"{variant}: oracle_used must be False")
    if result.note_used is not False:
        raise AssertionError(f"{variant}: note_used must be False")
    if result.manual_policy_used is not False:
        raise AssertionError(f"{variant}: manual_policy_used must be False")
    if result.retrieval_status not in {
        "RETRIEVED",
        "RETRIEVAL_FAILED",
        "FORBIDDEN_INPUT",
        INPUT_CONSTRUCTION_ERROR,
    }:
        raise AssertionError(f"{variant}: unexpected retrieval_status {result.retrieval_status}")
    if result.retrieval_status != "RETRIEVED" and result.evidence:
        raise AssertionError(f"{variant}: failed retrieval must not carry substitute evidence")
    if result.retrieval_status == INPUT_CONSTRUCTION_ERROR and result.retrieval_replayed:
        raise AssertionError(f"{variant}: input construction error must not replay retrieval")


def configure_benchmark(benchmark_name: str, output_dir: Path | None = None) -> None:
    global BENCHMARK_NAME, BENCHMARK_DIR, OUTPUT_DIR, DOCUMENT_CSV, DOCUMENT_DIR, RETRIEVAL_CSV
    global _PUBLIC_RETRIEVAL_INDEX

    BENCHMARK_NAME = benchmark_name
    BENCHMARK_DIR = v3.ROOT / "benchmark" / benchmark_name
    paths = construction_paths(BENCHMARK_DIR)
    if output_dir is None:
        OUTPUT_DIR = (
            v3.ROOT / "output" / benchmark_name / "natural-evidence-robustness"
        )
    else:
        OUTPUT_DIR = output_dir if output_dir.is_absolute() else v3.ROOT / output_dir
    DOCUMENT_CSV = paths["document_csv"]
    DOCUMENT_DIR = paths["document_dir"]
    retrieval_dir = paths["retrieval_dir"]
    staged_csv = retrieval_dir / "external-real-v8-event-retrieval.csv"
    RETRIEVAL_CSV = staged_csv if staged_csv.is_file() else None
    _PUBLIC_RETRIEVAL_INDEX = None
    v3.configure_benchmark(benchmark_name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Policy V3 natural-evidence robustness")
    parser.add_argument("--benchmark", default=BENCHMARK_NAME)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--seed", type=int, default=SEED_BASE)
    parser.add_argument("--prefix", default="auto-policy-v3-natural-evidence-robustness-r1-seed20260827")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--only", default="", help="comma-separated event ids for smoke runs")
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--skip-evaluation", action="store_true")
    parser.add_argument("--skip-repair", action="store_true")
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="build and audit blind inputs without calling the model",
    )
    parser.add_argument(
        "--overwrite-statuses",
        default="",
        help="comma-separated raw statuses to regenerate instead of resuming",
    )
    parser.add_argument("--qwen-timeout", type=int, default=0, help="override Qwen HTTP timeout seconds")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def event_ids(events: dict[str, dict[str, str]], only: str = "") -> list[str]:
    if only.strip():
        return [item.strip() for item in only.split(",") if item.strip()]
    return sorted(
        event_id
        for event_id, event in events.items()
        if str(event.get("status", "")).strip().upper() == "READY"
    )


def document_rows_by_event() -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(DOCUMENT_CSV):
        if row.get("status", "").strip().upper() == "READY":
            grouped[row["event_id"]].append(row)
    return grouped


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|li|h[1-6]|tr|section|article|div)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text)


def strip_tags(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|svg).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|li|h[1-6]|blockquote|dt|dd|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def read_pdf_text(path: Path) -> str:
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = __import__(module_name)
            reader = module.PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages[:80]]
            return "\n".join(pages)
        except Exception:
            continue
    return ""


def read_source_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return strip_html(path.read_text(encoding="utf-8", errors="replace"))
    if suffix == ".pdf":
        return read_pdf_text(path)
    return path.read_text(encoding="utf-8", errors="replace")


def extract_raw_public_excerpt(text: str) -> str:
    """Remove benchmark-only wrapper metadata from stored raw windows."""
    marker = re.search(r"(?im)^\s*raw source excerpt window\s*:\s*$", text)
    if marker is None:
        return text
    excerpt = text[marker.end() :]
    boundary = re.search(r"(?im)^\s*benchmark boundary\s*:\s*$", excerpt)
    if boundary is not None:
        excerpt = excerpt[: boundary.start()]
    return excerpt.strip()


def split_paragraphs(text: str) -> list[str]:
    raw = re.split(r"\n\s*\n|(?<=[。.!?])\s+(?=[A-Z0-9\u4e00-\u9fff])", text)
    paragraphs: list[str] = []
    for item in raw:
        item = re.sub(r"\s+", " ", item).strip()
        if len(item) >= 30:
            paragraphs.append(item)
    return paragraphs


def metadata_fields(mode: str) -> tuple[str, ...]:
    if mode == "full":
        return FULL_METADATA_FIELDS
    if mode == "light":
        return LIGHT_METADATA_FIELDS
    if mode == "none":
        return ()
    raise ValueError(f"unknown metadata mode: {mode}")


def metadata_context(event: dict[str, str], mode: str) -> dict[str, str]:
    allowed = set(metadata_fields(mode))
    result = {
        key: str(event.get(key, "")) if key in allowed else ""
        for key in FULL_METADATA_FIELDS
    }
    result["event_id"] = str(event.get("event_id", "")) if mode == "full" else ""
    return result


def query_terms(event: dict[str, str], metadata_mode: str = "full") -> list[str]:
    """Build retrieval terms only from the condition's frozen metadata fields."""
    text = " ".join(
        str(event.get(key, ""))
        for key in metadata_fields(metadata_mode)
    )
    terms = set(re.findall(r"\b\d+\.\d+\.\d+\b|\b\d{3,4}\b|[A-Za-z][A-Za-z0-9_-]{3,}|[\u4e00-\u9fff]{2,}", text))
    stop = {
        "public",
        "source",
        "event",
        "candidate",
        "oracle",
        "status",
        "evidence",
        "summary",
        "target",
        "当前",
        "事件",
        "公开",
        "证据",
        "候选",
        "本体",
    }
    return [term for term in sorted(terms, key=len, reverse=True) if term.lower() not in stop][:24]


def score_paragraph(paragraph: str, terms: list[str]) -> int:
    lower = paragraph.lower()
    score = 0
    for term in terms:
        score += 3 if re.fullmatch(r"\d+\.\d+\.\d+", term) and term in paragraph else 0
        score += 1 if term.lower() in lower else 0
    return score


def target_code(
    event: dict[str, str],
    metadata_mode: str = "full",
) -> str:
    text = " ".join(
        str(event.get(key, ""))
        for key in metadata_fields(metadata_mode)
    )
    match = re.search(r"\b\d+\.\d+\.\d+\b", text)
    return match.group(0) if match else ""


def current_doc_priority(doc: dict[str, str]) -> int:
    effective_to = str(doc.get("effective_to", "")).strip()
    document_id = str(doc.get("document_id", "")).upper()
    title = str(doc.get("source_title", "")).lower()
    filename = str(doc.get("file_name", "")).lower()
    if document_id.endswith("_NEW") or not effective_to:
        return 120
    if "current" in filename or "new" in filename or "new in" in title:
        return 100
    return 20


def html_line_windows(raw_html: str, code: str, terms: list[str], window: int = 10) -> list[str]:
    lines = raw_html.splitlines()
    matches: list[int] = []
    term_pattern = "|".join(re.escape(term) for term in terms if len(term) > 3 and not re.fullmatch(r"\d{3,4}", term))
    for index, line in enumerate(lines):
        if re.search(r"markdown-toc|tocxref|table-of-contents|<nav\b", line, flags=re.I):
            continue
        plain = strip_tags(line)
        if code and code in plain:
            if re.search(r"<h[1-6]\b|success criterion", line, flags=re.I):
                matches.append(index)
        elif not code and term_pattern and re.search(term_pattern, plain, flags=re.I):
            if re.search(r"<h[1-6]\b|success criterion", line, flags=re.I):
                matches.append(index)
    windows: list[str] = []
    seen: set[tuple[int, int]] = set()
    for index in matches:
        start = max(0, index - 2)
        end = min(len(lines), index + window)
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        plain = strip_tags("\n".join(lines[start:end]))
        if len(plain) >= 40:
            windows.append(plain)
    return windows


def raw_source_block(doc: dict[str, str], excerpt: str) -> str:
    return (
        f"Source title: {doc.get('source_title', '').strip()}\n"
        f"Source URL: {doc.get('source_url', '').strip()}\n"
        f"{excerpt.strip()}"
    )


def retrieval_result(
    blocks: list[tuple[str, str, str]],
    source_label: str,
) -> RetrievalResult:
    if not blocks:
        return RetrievalResult(
            evidence="",
            retrieval_status="RETRIEVAL_FAILED",
            source_label=source_label,
            raw_source_used=True,
            fallback_used=False,
            retrieved_doc_count=0,
            retrieved_window_count=0,
            candidate_used=False,
            oracle_used=False,
            note_used=False,
        )
    safe_blocks: list[tuple[str, str, str]] = []
    discarded_markers: set[str] = set()
    for block in blocks:
        markers = v3.find_forbidden_input_markers(block[2])
        if markers:
            discarded_markers.update(markers)
        else:
            safe_blocks.append(block)
    if not safe_blocks:
        return RetrievalResult(
            evidence="",
            retrieval_status="FORBIDDEN_INPUT",
            source_label=source_label,
            raw_source_used=True,
            fallback_used=False,
            retrieved_doc_count=len(
                {document_id for document_id, _, _ in blocks}
            ),
            retrieved_window_count=len(blocks),
            source_urls=tuple(
                sorted({source_url for _, source_url, _ in blocks if source_url})
            ),
            forbidden_input_markers=tuple(sorted(discarded_markers)),
            candidate_used=False,
            oracle_used=False,
            note_used=False,
        )
    evidence = "\n\n---\n\n".join(block for _, _, block in safe_blocks)
    return RetrievalResult(
        evidence=evidence,
        retrieval_status="RETRIEVED",
        source_label=source_label,
        raw_source_used=True,
        fallback_used=False,
        retrieved_doc_count=len(
            {document_id for document_id, _, _ in safe_blocks}
        ),
        retrieved_window_count=len(safe_blocks),
        source_urls=tuple(
            sorted(
                {source_url for _, source_url, _ in safe_blocks if source_url}
            )
        ),
        forbidden_input_markers=tuple(sorted(discarded_markers)),
        candidate_used=False,
        oracle_used=False,
        note_used=False,
        retrieval_source=source_label,
        retrieval_replayed=False,
        manual_policy_used=False,
    )


def public_retrieval_index() -> dict[str, dict[str, str]]:
    global _PUBLIC_RETRIEVAL_INDEX
    if _PUBLIC_RETRIEVAL_INDEX is not None:
        return _PUBLIC_RETRIEVAL_INDEX
    if RETRIEVAL_CSV is None or not RETRIEVAL_CSV.is_file():
        _PUBLIC_RETRIEVAL_INDEX = {}
        return _PUBLIC_RETRIEVAL_INDEX
    _PUBLIC_RETRIEVAL_INDEX = {row["event_id"]: row for row in read_csv(RETRIEVAL_CSV)}
    return _PUBLIC_RETRIEVAL_INDEX


def public_frozen_window_context(event_id: str, docs: list[dict[str, str]]) -> RetrievalResult:
    """Handoff frozen public READY windows. Does not re-query or re-rank."""
    row = public_retrieval_index().get(event_id)
    ready = public_ready(row)
    docs_by_id = {str(doc.get("document_id") or ""): doc for doc in docs}
    blocks: list[tuple[str, str, str]] = []
    for document_id, source_url, window in frozen_window_blocks(docs, DOCUMENT_DIR):
        doc = docs_by_id.get(document_id, {"source_title": "", "source_url": source_url})
        blocks.append((document_id, source_url, raw_source_block(doc, window)))
    if ready and not blocks:
        return RetrievalResult(
            evidence="",
            retrieval_status=INPUT_CONSTRUCTION_ERROR,
            source_label=PUBLIC_FROZEN_WINDOW,
            raw_source_used=True,
            fallback_used=False,
            retrieved_doc_count=0,
            retrieved_window_count=0,
            retrieval_source=PUBLIC_FROZEN_WINDOW,
            retrieval_replayed=False,
        )
    if not ready:
        return RetrievalResult(
            evidence="",
            retrieval_status=RETRIEVAL_FAILED,
            source_label=PUBLIC_FROZEN_WINDOW,
            raw_source_used=True,
            fallback_used=False,
            retrieved_doc_count=0,
            retrieved_window_count=0,
            retrieval_source=PUBLIC_FROZEN_WINDOW,
            retrieval_replayed=False,
        )
    result = retrieval_result(blocks, PUBLIC_FROZEN_WINDOW)
    return replace(result, retrieval_source=PUBLIC_FROZEN_WINDOW, retrieval_replayed=False)


def provenance_source_context(
    event: dict[str, str],
    docs: list[dict[str, str]],
    metadata_mode: str = "full",
) -> RetrievalResult:
    terms = query_terms(event, metadata_mode)
    code = target_code(event, metadata_mode)
    blocks: list[tuple[int, str, str]] = []
    for doc in docs:
        path = DOCUMENT_DIR / doc["file_name"]
        if not path.exists():
            continue
        role_score = current_doc_priority(doc)
        if path.suffix.lower() in {".html", ".htm"}:
            raw_html = path.read_text(encoding="utf-8", errors="replace")
            if metadata_mode == "none":
                for paragraph_index, paragraph in enumerate(
                    split_paragraphs(strip_html(raw_html))
                ):
                    blocks.append(
                        (
                            role_score - paragraph_index,
                            doc["document_id"],
                            raw_source_block(doc, paragraph),
                        )
                    )
                continue
            for window in html_line_windows(raw_html, code, terms):
                score = role_score + score_paragraph(window, [code, *terms]) * 5
                if code and code in window:
                    score += 80
                if re.search(r"\b(Level|conformance level)\s*(AAA|AA|A)\b|\((AAA|AA|A)\)", window, flags=re.I):
                    score += 40
                blocks.append((score, doc["document_id"], raw_source_block(doc, window)))
            continue
        text = extract_raw_public_excerpt(read_source_text(path))
        paragraphs = split_paragraphs(text)
        for paragraph_index, paragraph in enumerate(paragraphs):
            score = role_score + score_paragraph(paragraph, terms) * 5
            if metadata_mode == "none":
                score -= paragraph_index
            elif score < role_score + 10:
                continue
            blocks.append((score, doc["document_id"], raw_source_block(doc, paragraph)))

    docs_by_id = {str(doc["document_id"]): doc for doc in docs}
    blocks.sort(
        key=lambda item: (
            -item[0],
            (
                -current_doc_priority(docs_by_id[item[1]])
                if metadata_mode != "none"
                else 0
            ),
            item[1],
        )
    )
    selected: list[tuple[str, str, str]] = []
    seen_text: set[str] = set()
    for _, document_id, block in blocks:
        compact = re.sub(r"\W+", "", block.lower())[:400]
        if compact in seen_text:
            continue
        seen_text.add(compact)
        selected.append(
            (
                document_id,
                str(docs_by_id[document_id].get("source_url", "")),
                block,
            )
        )
        if len(selected) >= 5:
            break
    return retrieval_result(selected, f"raw_provenance_{metadata_mode}")


def source_context(
    event: dict[str, str],
    docs: list[dict[str, str]],
    limit: int,
    metadata_mode: str = "full",
) -> RetrievalResult:
    terms = query_terms(event, metadata_mode)
    scored: list[tuple[int, str, str, str]] = []
    for doc in docs:
        path = DOCUMENT_DIR / doc["file_name"]
        if not path.exists():
            continue
        text = extract_raw_public_excerpt(read_source_text(path))
        for paragraph_index, paragraph in enumerate(split_paragraphs(text)):
            score = score_paragraph(paragraph, terms)
            if metadata_mode == "none":
                score = -paragraph_index
            elif score <= 0:
                continue
            scored.append(
                (
                    score,
                    str(doc["document_id"]),
                    str(doc.get("source_url", "")),
                    raw_source_block(doc, paragraph),
                )
            )
    scored.sort(key=lambda item: (-item[0], item[1], item[3]))
    selected = [(doc_id, source_url, block) for _, doc_id, source_url, block in scored[:limit]]
    return retrieval_result(selected, f"raw_short_{metadata_mode}")


def raw_distractor_context(
    event_id: str,
    all_events: dict[str, dict[str, str]],
    docs_by_event: dict[str, list[dict[str, str]]],
    seed: int,
    limit: int = 2,
) -> RetrievalResult:
    event = all_events[event_id]
    primary_url = str(event.get("source_url", ""))
    candidates = [
        other_id
        for other_id, other in all_events.items()
        if other_id != event_id
        and other.get("domain") == event.get("domain")
        and docs_by_event.get(other_id)
    ]
    candidates.sort(
        key=lambda other_id: (
            str(all_events[other_id].get("source_url", "")) == primary_url,
            str(all_events[other_id].get("source_url", "")),
            other_id,
        )
    )
    if candidates:
        offset = seed % len(candidates)
        candidates = candidates[offset:] + candidates[:offset]

    selected: list[tuple[str, str, str]] = []
    for other_id in candidates:
        for doc in sorted(
            docs_by_event[other_id],
            key=lambda item: str(item.get("document_id", "")),
        ):
            path = DOCUMENT_DIR / doc["file_name"]
            if not path.exists():
                continue
            paragraphs = split_paragraphs(
                extract_raw_public_excerpt(read_source_text(path))
            )
            if not paragraphs:
                continue
            selected.append(
                (
                    str(doc["document_id"]),
                    str(doc.get("source_url", "")),
                    raw_source_block(doc, paragraphs[seed % len(paragraphs)]),
                )
            )
            break
        if len(selected) >= limit:
            break
    return retrieval_result(selected, "raw_same_domain_distractor")


def evidence_for_variant(
    variant: str,
    event_id: str,
    event: dict[str, str],
    docs: list[dict[str, str]],
    all_events: dict[str, dict[str, str]],
    docs_by_event: dict[str, list[dict[str, str]]],
    seed: int,
) -> RetrievalResult:
    evidence_path = v3.EXCERPT_DIR / f"{event_id}-evidence.md"
    if variant == "STRUCTURED_NOTE":
        clean_evidence = v3.remove_candidate_sections(
            evidence_path.read_text(encoding="utf-8")
        )
        markers = v3.find_forbidden_input_markers(clean_evidence)
        if markers:
            raise RuntimeError(f"{event_id}: forbidden note markers remained: {markers}")
        return RetrievalResult(
            evidence=clean_evidence,
            retrieval_status="STRUCTURED_NOTE",
            source_label=str(evidence_path.relative_to(v3.ROOT)),
            raw_source_used=False,
            fallback_used=False,
            retrieved_doc_count=0,
            retrieved_window_count=0,
            note_used=True,
        )
    if variant == "RAW_SHORT_WINDOW":
        return source_context(event, docs, limit=5, metadata_mode="full")
    if variant == "RAW_PROVENANCE_WINDOW":
        return provenance_source_context(event, docs, metadata_mode="full")
    if variant == "RAW_WINDOW_METADATA_LIGHT":
        if RETRIEVAL_CSV is not None:
            return public_frozen_window_context(event_id, docs)
        return provenance_source_context(event, docs, metadata_mode="light")
    if variant == "RAW_WINDOW_NO_METADATA":
        return provenance_source_context(event, docs, metadata_mode="none")
    if variant == "RAW_WINDOW_WITH_DISTRACTOR":
        primary = provenance_source_context(event, docs, metadata_mode="full")
        if primary.retrieval_status != "RETRIEVED":
            return primary
        distractor = raw_distractor_context(
            event_id,
            all_events,
            docs_by_event,
            seed,
        )
        if distractor.retrieval_status != "RETRIEVED":
            return RetrievalResult(
                evidence="",
                retrieval_status="RETRIEVAL_FAILED",
                source_label="raw_target_plus_distractor",
                raw_source_used=True,
                fallback_used=False,
                retrieved_doc_count=primary.retrieved_doc_count,
                retrieved_window_count=primary.retrieved_window_count,
                source_urls=primary.source_urls,
                candidate_used=False,
                oracle_used=False,
                note_used=False,
            )
        return RetrievalResult(
            evidence=primary.evidence + "\n\n---\n\n" + distractor.evidence,
            retrieval_status="RETRIEVED",
            source_label="raw_target_plus_distractor",
            raw_source_used=True,
            fallback_used=False,
            retrieved_doc_count=primary.retrieved_doc_count + distractor.retrieved_doc_count,
            retrieved_window_count=primary.retrieved_window_count + distractor.retrieved_window_count,
            source_urls=tuple(sorted(set(primary.source_urls + distractor.source_urls))),
            candidate_used=False,
            oracle_used=False,
            note_used=False,
        )
    raise ValueError(f"unknown variant: {variant}")


def prompt_metadata_mode(variant: str) -> str:
    if variant == "RAW_WINDOW_METADATA_LIGHT":
        return "light"
    if variant == "RAW_WINDOW_NO_METADATA":
        return "none"
    return "full"


def generate_variant(
    variant: str,
    events: dict[str, dict[str, str]],
    docs_by_event: dict[str, list[dict[str, str]]],
    runs: int,
    seed_base: int,
    selected_event_ids: list[str],
    retrieval_only: bool = False,
    overwrite_statuses: set[str] | None = None,
) -> None:
    variant_dir = OUTPUT_DIR / variant.lower()
    raw_dir = variant_dir / "raw"
    evidence_dir = variant_dir / "candidate-blind-evidence"
    raw_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    total = len(selected_event_ids) * runs
    index = 0
    for event_id in selected_event_ids:
        event = events[event_id]
        event_for_prompt = metadata_context(event, prompt_metadata_mode(variant))
        for run in range(1, runs + 1):
            seed = seed_base + run - 1
            index += 1
            evidence_file = evidence_dir / f"{event_id}-run{run}-seed{seed}-candidate-blind.md"
            raw_path = raw_dir / f"{event_id}-run{run}-seed{seed}.json"
            print(f"[{variant} {index}/{total}] {event_id} run={run} seed={seed}", flush=True)
            resume = (
                raw_path.is_file()
                and not retrieval_only
                and (
                    not overwrite_statuses
                    or str(json.loads(raw_path.read_text(encoding="utf-8-sig")).get("status") or "")
                    not in overwrite_statuses
                )
            )
            if resume:
                record = json.loads(raw_path.read_text(encoding="utf-8-sig"))
                print(f"  [resume] {raw_path.name} status={record.get('status', '')}", flush=True)
                rows.append(
                    {
                        "event_id": event_id,
                        "semantic_type": event["semantic_type"].strip(),
                        "run": run,
                        "seed": seed,
                        "variant": variant,
                        "status": record.get("status", ""),
                        "retrieval_status": record.get("retrieval_status", ""),
                        "raw_source_used": record.get("raw_source_used", ""),
                        "fallback_used": record.get("fallback_used", ""),
                        "retrieved_doc_count": record.get("retrieved_doc_count", ""),
                        "retrieved_window_count": record.get("retrieved_window_count", ""),
                        "source_urls": "|".join(record.get("source_urls") or []),
                        "forbidden_input_markers": "|".join(
                            record.get("forbidden_input_markers") or []
                        ),
                        "runtime_ms": record.get("runtime_ms", 0),
                        "prompt_eval_count": record.get("prompt_eval_count", 0),
                        "eval_count": record.get("eval_count", 0),
                        "canonical_status": record.get("canonical_status", ""),
                        "canonical_semantic_result": record.get(
                            "canonical_semantic_result", ""
                        ),
                        "raw_output_file": str(raw_path.relative_to(v3.ROOT)),
                    }
                )
                continue
            retrieval = evidence_for_variant(
                variant,
                event_id,
                event,
                docs_by_event[event_id],
                events,
                docs_by_event,
                seed,
            )
            if (
                variant == "RAW_WINDOW_METADATA_LIGHT"
                and public_ready(public_retrieval_index().get(event_id))
                and retrieval.retrieval_status not in {"FORBIDDEN_INPUT", INPUT_CONSTRUCTION_ERROR}
                and not str(retrieval.evidence or "").strip()
            ):
                retrieval = replace(
                    retrieval,
                    evidence="",
                    retrieval_status=INPUT_CONSTRUCTION_ERROR,
                    retrieval_source=PUBLIC_FROZEN_WINDOW,
                    retrieval_replayed=False,
                )
                print(f"  INPUT_CONSTRUCTION_ERROR {event_id}: public READY but empty evidence", flush=True)
            assert_raw_isolation(retrieval, variant)
            evidence_file.write_text(retrieval.evidence, encoding="utf-8")
            retrieval_ready = retrieval.retrieval_status in {
                "RETRIEVED",
                "STRUCTURED_NOTE",
            }
            status = (
                "RETRIEVAL_READY"
                if retrieval_ready
                else retrieval.retrieval_status
            )
            runtime_ms = prompt_eval_count = eval_count = 0
            forbidden_markers: list[str] = []
            schema_valid = False
            validation_reason = "not_run"
            canonical_status = "not_run"
            canonical_result: dict[str, Any] | None = None
            canonical_semantic_result = ""
            parsed: Any = None
            response_text = ""
            qwen_response: dict[str, Any] = {}
            if retrieval_ready and not retrieval_only:
                prompt = build_prompt(event_for_prompt, retrieval.evidence)
                try:
                    qwen_response, runtime_ms = v3.call_qwen(prompt, seed)
                    response_text = str(qwen_response.get("response", ""))
                    prompt_eval_count = int(qwen_response.get("prompt_eval_count", 0) or 0)
                    eval_count = int(qwen_response.get("eval_count", 0) or 0)
                    forbidden_markers = v3.forbidden_output_markers(response_text)
                    parsed = v3.extract_json(response_text)
                    event_for_normalize = {
                        **event_for_prompt,
                        "semantic_type": event["semantic_type"],
                    }
                    parsed, canonical_status, canonical_result, canonical_semantic_result = v3.normalize_generated_policy(
                        parsed,
                        event_for_normalize,
                        retrieval.evidence,
                    )
                    schema_valid, validation_reason = v3.validate_generated_policy(parsed, event["semantic_type"].strip())
                    if forbidden_markers:
                        status = "FORBIDDEN_OUTPUT"
                    elif parsed is None:
                        status = "INVALID_JSON"
                    elif isinstance(parsed, dict) and parsed.get("abstain", False):
                        status = "ABSTAIN"
                    elif not schema_valid:
                        status = "INVALID_SCHEMA"
                    else:
                        status = "GENERATED"
                except Exception as exc:
                    parsed = {"error": repr(exc)}
                    validation_reason = "exception"
                    canonical_status = "exception"
                    status = "ERROR"
            record = {
                "event_id": event_id,
                "semantic_type": event["semantic_type"].strip(),
                "run": run,
                "seed": seed,
                "variant": variant,
                "model": v3.MODEL,
                "prompt_version": f"AUTO_POLICY_V3_NATURAL_EVIDENCE_{variant}",
                "source_type": "CANDIDATE_BLIND_NATURAL_EVIDENCE",
                "source_file": retrieval.source_label,
                "candidate_blind_file": str(evidence_file.relative_to(v3.ROOT)),
                "oracle_used": retrieval.oracle_used,
                "candidate_used": retrieval.candidate_used,
                "note_used": retrieval.note_used,
                "manual_formal_policy_used": retrieval.manual_policy_used,
                "manual_policy_used": retrieval.manual_policy_used,
                "retrieval_source": retrieval.retrieval_source,
                "retrieval_replayed": retrieval.retrieval_replayed,
                "retrieval_status": retrieval.retrieval_status,
                "raw_source_used": retrieval.raw_source_used,
                "fallback_used": retrieval.fallback_used,
                "retrieved_doc_count": retrieval.retrieved_doc_count,
                "retrieved_window_count": retrieval.retrieved_window_count,
                "source_urls": list(retrieval.source_urls),
                "forbidden_input_markers": list(
                    retrieval.forbidden_input_markers
                ),
                "status": status,
                "runtime_ms": runtime_ms,
                "done_reason": qwen_response.get("done_reason", ""),
                "prompt_eval_count": prompt_eval_count,
                "eval_count": eval_count,
                "forbidden_markers": forbidden_markers,
                "schema_valid": schema_valid,
                "validation_reason": validation_reason,
                "canonical_status": canonical_status,
                "canonical_result": canonical_result,
                "canonical_semantic_result": canonical_semantic_result,
                "raw_response_text": response_text,
                "response": parsed,
            }
            raw_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append(
                {
                    "event_id": event_id,
                    "semantic_type": event["semantic_type"].strip(),
                    "run": run,
                    "seed": seed,
                    "variant": variant,
                    "status": status,
                    "retrieval_status": retrieval.retrieval_status,
                    "raw_source_used": retrieval.raw_source_used,
                    "fallback_used": retrieval.fallback_used,
                    "retrieved_doc_count": retrieval.retrieved_doc_count,
                    "retrieved_window_count": retrieval.retrieved_window_count,
                    "source_urls": "|".join(retrieval.source_urls),
                    "forbidden_input_markers": "|".join(
                        retrieval.forbidden_input_markers
                    ),
                    "runtime_ms": runtime_ms,
                    "prompt_eval_count": prompt_eval_count,
                    "eval_count": eval_count,
                    "canonical_status": canonical_status,
                    "canonical_semantic_result": canonical_semantic_result,
                    "raw_output_file": str(raw_path.relative_to(v3.ROOT)),
                }
            )
    save_csv(variant_dir / f"{variant.lower()}-generation-details.csv", rows)


def run_command(args: list[str]) -> None:
    print(" ".join(args))
    subprocess.run(args, cwd=v3.ROOT, check=True)


def evaluate_variants(
    variants: list[str],
    runs: int,
    seed: int,
    skip_repair: bool,
    only: str = "",
) -> None:
    python = sys.executable
    only_args = ["--only", only] if only.strip() else []
    for variant in variants:
        variant_dir = OUTPUT_DIR / variant.lower()
        raw_dir = variant_dir / "raw"
        semantic_prefix = f"auto-policy-v3-natural-evidence-{variant.lower()}-semantic-evaluation"
        repair_prefix = f"auto-policy-v3-natural-evidence-{variant.lower()}-candidate-repair-r{runs}-seed{seed}"
        run_command(
            [
                python,
                str(Path("src") / "evaluate_auto_formal_policy_v2_semantic.py"),
                "--benchmark-dir",
                str(BENCHMARK_DIR),
                "--raw-dir",
                str(raw_dir),
                "--output-dir",
                str(variant_dir),
                "--prefix",
                semantic_prefix,
                "--runs",
                str(runs),
                "--seed",
                str(seed),
                *only_args,
            ]
        )
        if not skip_repair:
            run_command(
                [
                    python,
                    str(Path("src") / "run_auto_policy_v2_candidate_repair.py"),
                    "--benchmark-dir",
                    str(BENCHMARK_DIR),
                    "--raw-dir",
                    str(raw_dir),
                    "--prefix",
                    repair_prefix,
                    "--runs",
                    str(runs),
                    "--seed",
                    str(seed),
                    *only_args,
                ]
            )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def summary_rows(prefix: str, variants: list[str], runs: int, seed: int, skip_repair: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant in variants:
        variant_dir = OUTPUT_DIR / variant.lower()
        semantic_path = variant_dir / f"auto-policy-v3-natural-evidence-{variant.lower()}-semantic-evaluation-summary.json"
        semantic = load_json(semantic_path)["summary"][0]
        row: dict[str, Any] = {
            "variant": variant,
            "description": VARIANTS[variant],
            "events": semantic["events"],
            "attempts": semantic["attempts"],
            "semantic_accuracy": semantic["semantic_accuracy"],
            "semantic_strict_event_successes": semantic["strict_event_successes"],
            "semantic_strict_event_accuracy": semantic["strict_event_accuracy"],
            "generation_successes": semantic["generation_successes"],
        }
        if not skip_repair:
            repair_prefix = f"auto-policy-v3-natural-evidence-{variant.lower()}-candidate-repair-r{runs}-seed{seed}"
            repair = read_csv(v3.ROOT / "output" / f"{repair_prefix}-summary.csv")[0]
            row.update(
                {
                    "oracle_accuracy": repair["oracle_accuracy"],
                    "full_closure_accuracy": repair["full_closure_accuracy"],
                    "repair_strict_event_successes": repair["strict_event_successes"],
                    "repair_strict_event_accuracy": repair["strict_event_accuracy"],
                    "abstains": repair["abstains"],
                }
            )
        rows.append(row)
    save_csv(v3.ROOT / "output" / f"{prefix}-summary.csv", rows)
    (v3.ROOT / "output" / f"{prefix}.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "runs": runs,
                "seed_base": seed,
                "rows": rows,
                "boundary": (
                    "Raw variants use public source text only, retrieve from "
                    "their declared event-metadata condition, never fall back "
                    "to structured notes, and never load candidate, Oracle, "
                    "or formal-policy data during generation."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return rows


def main() -> int:
    args = parse_args()
    configure_benchmark(args.benchmark, args.output_dir)
    if args.qwen_timeout > 0:
        v3.TIMEOUT = args.qwen_timeout
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    unknown = [item for item in variants if item not in VARIANTS]
    if unknown:
        raise ValueError("unknown variants: " + ", ".join(unknown))
    events = v3.load_events()
    selected_event_ids = event_ids(events, args.only)
    missing = [event_id for event_id in selected_event_ids if event_id not in events]
    if missing:
        raise ValueError("unknown event ids: " + ", ".join(missing))
    docs_by_event = document_rows_by_event()
    overwrite_statuses = {
        item.strip().upper() for item in str(args.overwrite_statuses or "").split(",") if item.strip()
    }
    if not args.skip_generation:
        for variant in variants:
            generate_variant(
                variant,
                events,
                docs_by_event,
                args.runs,
                args.seed,
                selected_event_ids,
                args.retrieval_only,
                overwrite_statuses,
            )
    if not args.skip_evaluation and not args.retrieval_only:
        evaluate_variants(variants, args.runs, args.seed, args.skip_repair, args.only)
    if args.skip_evaluation or args.retrieval_only:
        mode = "retrieval-only" if args.retrieval_only else "generation"
        print(f"{mode} finished; evaluation skipped", flush=True)
        return 0
    rows = summary_rows(args.prefix, variants, args.runs, args.seed, args.skip_repair)
    for row in rows:
        print(
            f"{row['variant']}: semantic={float(row['semantic_accuracy']):.2%} "
            f"strict={row['semantic_strict_event_successes']}/{row['events']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
