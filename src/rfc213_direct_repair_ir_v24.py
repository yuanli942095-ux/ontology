from __future__ import annotations

"""Frozen V2.4 bibliography-aware candidate-blind source grounding."""

import re
from typing import Any

from rfc213_direct_repair_ir import source_windows
from rfc213_direct_repair_ir_v23 import reference_markers, resolve_source_window_v23


CITATION_KEY_RE = re.compile(r"^\s*\[[A-Za-z0-9_.-]+\]")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
LOCATOR_RE = re.compile(r"\b(?:DOI|ISBN|RFC)\s*[: ]?\s*[A-Za-z0-9./-]+|https?://", re.I)
QUOTED_TITLE_RE = re.compile(r'"[^"\n]{4,}"')
STRICT_DEONTIC_RE = re.compile(r"\b(?:MUST(?:\s+NOT)?|SHALL(?:\s+NOT)?|SHOULD(?:\s+NOT)?|MAY|REQUIRED|RECOMMENDED)\b")


def is_bibliographic_nonassertion_v24(text: str) -> bool:
    return bool(
        CITATION_KEY_RE.search(text)
        and QUOTED_TITLE_RE.search(text)
        and YEAR_RE.search(text)
        and LOCATOR_RE.search(text)
        and not STRICT_DEONTIC_RE.search(text)
    )


def resolve_source_window_v24(ir: dict[str, Any], evidence: str) -> dict[str, Any]:
    result = resolve_source_window_v23(ir, evidence)
    if result["status"] != "RESOLVED":
        enriched = dict(result)
        enriched["resolver_version"] = "v24"
        return enriched
    windows = dict(source_windows(evidence))
    matches = [(wid, windows[wid]) for wid in result["matching_window_ids"]]
    if matches and all(is_bibliographic_nonassertion_v24(text) for _, text in matches):
        return {
            "status": "REFERENCE_NON_ASSERTION",
            "canonical_ir": None,
            "matching_window_ids": [wid for wid, _ in matches],
            "canonical_candidates": result["canonical_candidates"],
            "resolution_path": "V24_BIBLIOGRAPHY_GATE",
            "reference_markers": {wid: reference_markers(text) + ["v24_bibliography_shape"] for wid, text in matches},
            "resolver_version": "v24",
        }
    enriched = dict(result)
    enriched["resolver_version"] = "v24"
    return enriched
