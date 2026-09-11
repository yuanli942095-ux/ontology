from __future__ import annotations

"""Frozen V2.3 candidate-blind source grounding safeguards.

V2.3 adds only two domain-independent operations to V2.2: bibliography-like
reference entries fail closed, and ASCII table decoration is ignored when
matching a predicted verbatim span. It deliberately contains no claim-aware
neighbor-window reranking.
"""

import copy
import re
from typing import Any

from rfc213_direct_repair_ir import normalize_space, source_windows
from rfc213_direct_repair_ir_v2 import _benchmark_literal_for_ir, resolve_source_window


DEONTIC_RE = re.compile(r"\b(?:MUST(?:\s+NOT)?|SHALL(?:\s+NOT)?|SHOULD(?:\s+NOT)?|MAY|REQUIRED|RECOMMENDED)\b", re.I)
REFERENCE_PATTERNS = {
    "bracketed_reference_key": re.compile(r"^\s*\[(?:RFC\s*)?\d+[A-Za-z]?\]", re.I),
    "rfc_info_url": re.compile(r"https?://(?:www\.)?rfc-editor\.org/(?:info|rfc)/", re.I),
    "doi": re.compile(r"\b(?:doi\s*:\s*|https?://doi\.org/)10\.\d{4,9}/\S+", re.I),
    "rfc_bibliographic_date": re.compile(r"\bRFC\s*\d+\b.{0,180}\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b", re.I | re.S),
    "bibliographic_lead": re.compile(r"^\s*(?:\[[^\]]+\]\s*)?(?:[A-Z][A-Za-z.'-]+,\s*){1,4}[A-Z].{0,240}(?:RFC\s*\d+|doi\s*:)", re.I | re.S),
}


def reference_markers(text: str) -> list[str]:
    return [name for name, pattern in REFERENCE_PATTERNS.items() if pattern.search(text)]


def is_reference_nonassertion(text: str) -> bool:
    markers = reference_markers(text)
    strong = "bracketed_reference_key" in markers or "bibliographic_lead" in markers
    return not DEONTIC_RE.search(text) and strong and len(markers) >= 2


def normalize_ascii_table_layout(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and re.fullmatch(r"[+|:\-= ]{3,}", stripped):
            continue
        kept.append(re.sub(r"\s*\|\s*", " ", line))
    return normalize_space(" ".join(kept)).lower()


def _result(status: str, canonical_ir: dict[str, Any] | None, matches: list[tuple[str, str]], candidates: list[str], path: str) -> dict[str, Any]:
    return {
        "status": status,
        "canonical_ir": canonical_ir,
        "matching_window_ids": [window_id for window_id, _ in matches],
        "canonical_candidates": candidates,
        "resolution_path": path,
        "reference_markers": {window_id: reference_markers(text) for window_id, text in matches},
    }


def resolve_source_window_v23(ir: dict[str, Any], evidence: str) -> dict[str, Any]:
    v22 = resolve_source_window(ir, evidence)
    matches_by_id = dict(source_windows(evidence))
    matched = [(window_id, matches_by_id[window_id]) for window_id in v22["matching_window_ids"]]
    if v22["status"] == "RESOLVED":
        if matched and all(is_reference_nonassertion(text) for _, text in matched):
            return _result("REFERENCE_NON_ASSERTION", None, matched, v22["canonical_candidates"], "V22_EXACT")
        enriched = dict(v22)
        enriched.update({"resolution_path": "V22_EXACT", "reference_markers": {wid: reference_markers(text) for wid, text in matched}})
        return enriched
    if v22["status"] != "NO_WINDOW_MATCH" or ir.get("decision") != "REPAIR":
        enriched = dict(v22)
        enriched.update({"resolution_path": "V22", "reference_markers": {}})
        return enriched

    spans = ir.get("evidence_spans", [])
    if not spans or not isinstance(spans[0], str):
        return _result("NO_WINDOW_MATCH", None, [], [], "ASCII_TABLE")
    needle = normalize_ascii_table_layout(spans[0])
    matches = [(wid, text) for wid, text in source_windows(evidence) if needle and needle in normalize_ascii_table_layout(text)]
    if not matches:
        return _result("NO_WINDOW_MATCH", None, [], [], "ASCII_TABLE")
    if all(is_reference_nonassertion(text) for _, text in matches):
        return _result("REFERENCE_NON_ASSERTION", None, matches, [], "ASCII_TABLE")

    canonical = copy.deepcopy(ir)
    by_literal: dict[str, list[str]] = {}
    for window_id, text in matches:
        literal = _benchmark_literal_for_ir(canonical, text)
        by_literal.setdefault(literal, []).append(window_id)
    if len(by_literal) != 1:
        return _result("AMBIGUOUS_WINDOW", None, matches, sorted(by_literal), "ASCII_TABLE")
    literal = next(iter(by_literal))
    canonical["replacement"]["new_value"]["lexical"] = literal
    canonical["evidence_spans"] = [normalize_space(str(span)) for span in canonical.get("evidence_spans", [])]
    return _result("RESOLVED", canonical, matches, [literal], "ASCII_TABLE")
