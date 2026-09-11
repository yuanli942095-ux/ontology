from __future__ import annotations

"""R2b-v2 candidate-blind source-window resolution.

The frozen v1 implementation remains untouched. V2 uses a predicted verbatim
span only to identify its containing public evidence window. If the matching
windows imply more than one canonical literal, resolution fails closed.
"""

import copy
from typing import Any

from rfc213_direct_repair_ir import (
    benchmark_literal_from_span,
    iri_valid,
    normalize_space,
    source_windows,
)


def _predicate_dimension(predicate_iri: str) -> str:
    if "#" in predicate_iri:
        return predicate_iri.rsplit("#", 1)[1]
    return predicate_iri.rstrip("/").rsplit("/", 1)[-1]


def _benchmark_literal_for_ir(ir: dict[str, Any], evidence_span: str) -> str:
    old_lexical = ir.get("target", {}).get("old_value", {}).get("lexical", "")
    if isinstance(old_lexical, str) and "=" in old_lexical:
        return benchmark_literal_from_span(old_lexical, evidence_span)
    predicate_iri = ir.get("target", {}).get("predicate_iri", "")
    if isinstance(predicate_iri, str) and iri_valid(predicate_iri):
        return f"{_predicate_dimension(predicate_iri)}={benchmark_literal_from_span('x=', evidence_span).split('=', 1)[1]}"
    return benchmark_literal_from_span(str(old_lexical), evidence_span)


def _normalize_surface_without_value_binding(ir: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(ir)
    if result.get("decision") != "REPAIR":
        if isinstance(result.get("reason"), str):
            result["reason"] = normalize_space(result["reason"])
        return result
    for branch, field in (("target", "old_value"), ("replacement", "new_value")):
        spec = result.get(branch, {}).get(field, {})
        if isinstance(spec, dict) and isinstance(spec.get("lexical"), str):
            spec["lexical"] = normalize_space(spec["lexical"])
    result["evidence_spans"] = [normalize_space(str(span)) for span in result.get("evidence_spans", [])]
    return result


def resolve_source_window(ir: dict[str, Any], evidence: str) -> dict[str, Any]:
    canonical = _normalize_surface_without_value_binding(ir)
    if canonical.get("decision") != "REPAIR":
        return {
            "status": "NOT_APPLICABLE",
            "canonical_ir": canonical,
            "matching_window_ids": [],
            "canonical_candidates": [],
        }

    spans = canonical.get("evidence_spans", [])
    if not spans or not isinstance(spans[0], str):
        return {
            "status": "NO_WINDOW_MATCH",
            "canonical_ir": None,
            "matching_window_ids": [],
            "canonical_candidates": [],
        }
    predicted_span = normalize_space(spans[0]).lower()
    matches = [
        (window_id, text)
        for window_id, text in source_windows(evidence)
        if predicted_span in normalize_space(text).lower()
    ]
    if not matches:
        return {
            "status": "NO_WINDOW_MATCH",
            "canonical_ir": None,
            "matching_window_ids": [],
            "canonical_candidates": [],
        }

    by_literal: dict[str, list[str]] = {}
    for window_id, text in matches:
        literal = _benchmark_literal_for_ir(canonical, text)
        by_literal.setdefault(literal, []).append(window_id)
    if len(by_literal) != 1:
        return {
            "status": "AMBIGUOUS_WINDOW",
            "canonical_ir": None,
            "matching_window_ids": [window_id for window_id, _ in matches],
            "canonical_candidates": sorted(by_literal),
        }

    resolved = copy.deepcopy(canonical)
    literal = next(iter(by_literal))
    resolved["replacement"]["new_value"]["lexical"] = literal
    return {
        "status": "RESOLVED",
        "canonical_ir": resolved,
        "matching_window_ids": [window_id for window_id, _ in matches],
        "canonical_candidates": [literal],
    }
