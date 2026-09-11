from __future__ import annotations

"""Candidate-blind CSS scope_relation slot completion.

Uses only Auto Policy fields already produced for the attempt. Does not read
Oracle, candidates, or manual formal policy. Completes only when exactly one
CSS scope relation is supported by payload values and structured keys.

Does not treat event.semantic_type, IR bundle text, or fields.relation inferred
from CROSS_SENTENCE_SCOPE as evidence.
"""

import re
from typing import Any

CSS_SCOPE_RELATIONS = (
    "APPLIES_TO",
    "REMAINS_VALID",
    "ADDED",
    "EXCEPTION_OVERRIDES",
)

PHRASE_VOTES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "except where",
            "except when",
            "except if",
            "unless",
            "overrides",
            "override",
            "not applicable if",
            "does not apply if",
            "例外",
            "除非",
            "除外",
        ),
        "EXCEPTION_OVERRIDES",
    ),
    (
        ("remains valid", "still valid", "continues to apply", "continues to be", "保持有效"),
        "REMAINS_VALID",
    ),
    (
        ("newly added", "new criterion added", "addition of", "新增"),
        "ADDED",
    ),
    (
        ("applies to", "apply to", "applicable to", "application of", "applies only", "适用"),
        "APPLIES_TO",
    ),
)

EXCEPTION_KEYS = {
    "exception_condition",
    "exception_rule",
    "exclusion_clause",
    "exclusion_reason",
    "exception",
    "applicability_limitation",
}

APPLIES_KEYS = {
    "applicable_standard",
    "applies_to",
    "scope_target",
    "qualifier",
    "scope_condition",
    "scope_clause",
    "scope_definition",
    "scope_description",
    "scope_context",
    "scope_subject",
}

SKIP_KEYS = {
    "domain",
    "subject_label",
    "predicate_label",
    "source_title",
    "guidance_title",
    "regulation_title",
    "standard_name",
}


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", str(text or "").lower()) + " "


def _value_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_value_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_value_text(item) for item in value)
    return str(value or "").strip()


def _walk_maps(response: dict[str, Any]) -> list[dict[str, Any]]:
    maps: list[dict[str, Any]] = []
    facts = response.get("facts")
    if isinstance(facts, dict):
        maps.append(facts)
    canonical = response.get("canonical_result")
    if isinstance(canonical, dict):
        maps.append(canonical)
    return maps


def payload_value_text(response: dict[str, Any], fields: dict[str, Any] | None = None) -> str:
    parts: list[str] = []
    for mapping in _walk_maps(response):
        for key, value in mapping.items():
            if str(key).strip().lower() in SKIP_KEYS:
                continue
            parts.append(_value_text(value))
    rules = response.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict):
                parts.append(str(rule.get("semantic_result") or ""))
    if isinstance(fields, dict):
        for key in ("result", "qualifier", "scope_target"):
            parts.append(_value_text(fields.get(key)))
    return " ".join(part for part in parts if part)


def phrase_votes(text: str) -> set[str]:
    lower = str(text or "").lower().replace("_", " ")
    spaced = _norm(lower)
    hits: set[str] = set()
    for phrases, label in PHRASE_VOTES:
        for phrase in phrases:
            needle = phrase.lower()
            if " " in needle or any("\u4e00" <= char <= "\u9fff" for char in needle):
                if needle in lower:
                    hits.add(label)
                    break
            elif f" {needle} " in spaced:
                hits.add(label)
                break
    return hits


def structured_key_votes(response: dict[str, Any], fields: dict[str, Any] | None = None) -> set[str]:
    hits: set[str] = set()
    for mapping in _walk_maps(response):
        for key, value in mapping.items():
            if value in (None, "", [], {}):
                continue
            leaf = str(key).split(".")[-1].lower()
            if leaf in SKIP_KEYS:
                continue
            if leaf in EXCEPTION_KEYS or leaf.startswith("exception_"):
                hits.add("EXCEPTION_OVERRIDES")
            elif leaf in APPLIES_KEYS or leaf.startswith("applicable") or leaf.startswith("applies_"):
                hits.add("APPLIES_TO")
    if isinstance(fields, dict):
        if fields.get("scope_target") not in (None, "", [], {}):
            hits.add("APPLIES_TO")
        if fields.get("qualifier") not in (None, "", [], {}):
            hits.add("APPLIES_TO")
    canonical = response.get("canonical_result")
    if isinstance(canonical, dict) and str(canonical.get("family") or "").strip().lower() == "wcag21_cross_scope":
        hits.add("APPLIES_TO")
    explicit = str((response.get("scope_relation") or (fields or {}).get("scope_relation") or "")).strip().upper()
    if explicit in CSS_SCOPE_RELATIONS:
        hits.add(explicit)
    return hits


def usable_result(fields: dict[str, Any], response: dict[str, Any]) -> bool:
    result = fields.get("result")
    if isinstance(result, list):
        result = " ".join(str(item) for item in result if item not in (None, "", [], {}))
    if str(result or "").strip():
        return True
    canonical = response.get("canonical_result")
    if isinstance(canonical, dict) and any(
        canonical.get(key) not in (None, "", [], {}) for key in ("change_value", "semantic_result", "family")
    ):
        return True
    return False


def css_scope_votes(response: dict[str, Any], fields: dict[str, Any] | None = None) -> set[str]:
    fields = fields or {}
    hits = phrase_votes(payload_value_text(response, fields))
    hits |= structured_key_votes(response, fields)
    return {item for item in hits if item in CSS_SCOPE_RELATIONS}


def complete_css_scope_relation(
    response: dict[str, Any],
    fields: dict[str, Any],
) -> tuple[str | None, str, str]:
    """Return (relation_or_none, class, reason).

    S1 unique complete, S2 ambiguous, S3 missing other critical semantics.
    """
    if not usable_result(fields, response):
        return None, "S3", "missing usable result"
    votes = css_scope_votes(response, fields)
    if len(votes) == 1:
        chosen = next(iter(votes))
        return chosen, "S1", f"unique scope_relation={chosen}"
    if len(votes) > 1:
        return None, "S2", "ambiguous:" + "|".join(sorted(votes))
    return None, "S2", "no unique scope_relation marker"
