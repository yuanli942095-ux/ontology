from __future__ import annotations

"""Curation-stage semantic support adjudication for v8.

This is Gold-assisted dataset curation, not Gold-assisted model inference.
Lexical ratio >= 0.60 is only a prefilter. READY requires semantic_support=PASS.
"""

import re
from typing import Any

from external_real_v8_layout import SUPPORT_GATE_VERSION


RELATION_PARAPHRASES: dict[str, tuple[str, ...]] = {
    "TEMPORAL_VERSION": (
        r"supersede",
        r"replac(?:e|es|ed|ing)",
        r"no longer current",
        r"withdrawn",
        r"obsolete",
        r"succeeds",
        r"废止",
        r"替代",
        r"取代",
        r"不再适用",
        r"新条款",
        r"旧条款",
        r"新旧条款",
        r"生效",
    ),
    "CROSS_SENTENCE_SCOPE": (
        r"\bscope\b",
        r"\binclud(?:e|es|ed|ing)\b",
        r"\bappl(?:y|ies|icable)\b",
        r"范围",
        r"适用",
        r"包括",
        r"不包括",
        r"列明",
    ),
    "GENERAL_RULE_EXCEPTION": (
        r"\bexcept(?:ion|ions)?\b",
        r"\bunless\b",
        r"\bhowever\b",
        r"除外",
        r"例外",
        r"但",
        r"不适用于",
    ),
}


def lexical_bucket(lexical_status: str) -> str:
    status = str(lexical_status or "").upper()
    if status in {"PASS", "LEXICAL_PASS"}:
        return "LEXICAL_PASS"
    if status in {"WARN", "LEXICAL_AMBIGUOUS"}:
        return "LEXICAL_AMBIGUOUS"
    return "LEXICAL_FAIL"


def paraphrase_hits(semantic_type: str, evidence_text: str) -> list[str]:
    body = str(evidence_text or "")
    hits: list[str] = []
    for pattern in RELATION_PARAPHRASES.get(semantic_type, ()):
        if re.search(pattern, body, flags=re.I):
            hits.append(pattern)
    return hits


def years_in_text(text: str) -> set[str]:
    return set(re.findall(r"\b(?:19|20)\d{2}\b", text))


def adjudicate_semantic_support(
    event: dict[str, str],
    evidence_text: str,
    lexical_row: dict[str, Any],
) -> dict[str, Any]:
    """Map lexical prefilter + paraphrase evidence onto SEMANTIC_PASS or FAIL.

    Curation may consult Gold conclusion tokens already scored in lexical_row.
    Those tokens are not written back into public excerpts.
    """
    bucket = lexical_bucket(str(lexical_row.get("status", "")))
    hits = paraphrase_hits(str(event.get("semantic_type", "")), evidence_text)
    missing_numeric = str(lexical_row.get("missing_numeric_tokens", "")).strip()
    distinctive = int(lexical_row.get("distinctive_token_count", 0) or 0)
    event_years = years_in_text(
        " ".join(str(event.get(key, "")) for key in ("title", "case_context", "subject_label"))
    )
    evidence_years = years_in_text(evidence_text)
    year_overlap = bool(event_years & evidence_years)

    if bucket == "LEXICAL_PASS" and not missing_numeric:
        support_status = "LEXICAL_PASS"
        semantic = "PASS"
        method = "lexical_prefilter_confirmed"
        reason = "lexical PASS confirmed as semantic support"
    elif missing_numeric:
        support_status = "FAIL"
        semantic = "FAIL"
        method = "lexical_prefilter"
        reason = str(lexical_row.get("reason", "critical numeric/date/entity tokens missing"))
    elif hits and (distinctive or year_overlap or bucket == "LEXICAL_AMBIGUOUS"):
        support_status = "SEMANTIC_PASS"
        semantic = "PASS"
        method = "paraphrase_relation_adjudication"
        reason = "relation paraphrase supports the target despite weak lexical overlap"
    else:
        support_status = "FAIL"
        semantic = "FAIL"
        method = "lexical_prefilter"
        reason = str(lexical_row.get("reason", "no semantic paraphrase support"))

    return {
        "event_id": event.get("event_id", ""),
        "lexical_status": bucket,
        "support_status": support_status,
        "semantic_support": semantic,
        "support_adjudication_method": method,
        "support_checked_before_model_run": True,
        "support_gate_version": SUPPORT_GATE_VERSION,
        "paraphrase_hit_count": len(hits),
        "reason": reason,
        "curation_not_inference": True,
    }
