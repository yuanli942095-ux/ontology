from __future__ import annotations

"""Claim-level, value-blind public target contract for v6.2 / v7 Direct IR.

Public fields pin which claim is under assessment. They must not contain the
replacement value, Gold window index, or candidate identity.
"""

import re
from typing import Iterable

RFC2119_RE = re.compile(
    r"\b(MUST(?:\s+NOT)?|SHALL(?:\s+NOT)?|SHOULD(?:\s+NOT)?|MAY|REQUIRED|RECOMMENDED|OPTIONAL)\b",
    re.I,
)
AUTHOR_RE = re.compile(
    r"(?i)\b(corp\.?|inc\.?|ltd\.?|gmbh|consulting|universit(?:y|aet)|technologies|microsoft|"
    r"akamai|nat\.consulting|ca technologies)\b"
)
GENERIC_TOKENS = {
    "the", "and", "for", "that", "this", "with", "from", "are", "not", "must",
    "should", "shall", "may", "can", "when", "where", "which", "into", "than",
    "then", "been", "have", "has", "its", "their", "section", "rfc", "normative",
    "subject", "value", "evidence", "grounded", "using", "only", "public",
    "windows", "return", "under", "contract", "determine", "whether",
}
CURRENT_SURFACE_UNMODELED = "No current assertion recorded."
CURRENT_SEMANTICS_UNMODELED = (
    "The current ontology does not bind a value for this claim. "
    "If REPAIR is warranted, copy the current ontology literal exactly as old_value. "
    "The replacement value must be taken from the uniquely identified evidence span "
    "and must not be assumed from this target description."
)
LEAKAGE_FIELDS = (
    "target_entity_label",
    "target_property_label",
    "value_neutral_question",
    "target_cq",
    "current_value_surface",
    "current_value_semantics",
    "subject_label",
    "predicate_label",
    "case_context",
)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def content_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{1,}", text)
        if token.lower() not in GENERIC_TOKENS
    ]


def token_set(text: str) -> set[str]:
    return {token.lower() for token in content_tokens(text)}


def strip_rfc2119(text: str) -> str:
    return normalize_space(RFC2119_RE.sub(" ", text))


def is_author_subject(label: str) -> bool:
    return bool(AUTHOR_RE.search(label or ""))


def rfc_title_from_source_text(text: str) -> str:
    stop = re.search(r"(?im)^(Abstract|Status of This Memo)\s*$", text)
    if not stop:
        return ""
    lines = [line.strip() for line in text[:stop.start()].splitlines()]
    title_lines: list[str] = []
    for line in reversed(lines):
        if not line:
            if title_lines:
                break
            continue
        if re.match(
            r"^(Internet Engineering|Network Working Group|Request for Comments|Category|ISSN|"
            r"Updates|Obsoletes|BCP|STD|RFC)\b",
            line,
            re.I,
        ):
            break
        if re.match(r"^[A-Z][a-z]+ \d{4}$", line):
            break
        if re.match(r"^[A-Z]\.\s", line) and len(line) < 80:
            break
        title_lines.append(line)
    return normalize_space(" ".join(reversed(title_lines)))[:160]


def slug_token_set(lexical: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{1,}", lexical or "")}


def value_like_tokens(new_lexical: str) -> set[str]:
    """Tokens that usually *are* the replacement filler rather than the claim name."""
    blocked = set()
    for token in slug_token_set(new_lexical):
        if "-" in token or token.isdigit() or re.fullmatch(r"[0-9a-f]{6,}", token):
            blocked.add(token)
    return blocked


def distinctive_phrases(
    gold_text: str,
    other_texts: Iterable[str],
    max_phrases: int = 4,
    exclude_tokens: Iterable[str] | None = None,
) -> list[str]:
    gold_words = [
        word
        for word in content_tokens(strip_rfc2119(gold_text))
        if word.lower() not in set(exclude_tokens or ())
    ]
    other = set()
    for text in other_texts:
        other |= token_set(strip_rfc2119(text))
    phrases: list[str] = []
    seen: set[str] = set()
    for size in (4, 3, 2, 1):
        if len(phrases) >= max_phrases:
            break
        for index in range(0, max(0, len(gold_words) - size + 1)):
            chunk = gold_words[index:index + size]
            if any(RFC2119_RE.search(word) for word in chunk):
                continue
            if any(word.lower() in other for word in chunk) and size < 3:
                continue
            if size >= 3 and sum(word.lower() not in other for word in chunk) < 2:
                continue
            phrase = " ".join(chunk)
            key = phrase.lower()
            if key in seen or len(phrase) < 8:
                continue
            if not any(len(word) >= 4 for word in chunk):
                continue
            if gold_text and len(phrase) > max(24, int(0.45 * len(normalize_space(gold_text)))):
                continue
            seen.add(key)
            phrases.append(phrase)
            if len(phrases) >= max_phrases:
                break
    if not phrases:
        fallback = " ".join(gold_words[:6])
        if fallback:
            phrases.append(fallback)
    return phrases[:max_phrases]


def discriminator_tokens(*parts: str) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        tokens |= token_set(part)
    return tokens - GENERIC_TOKENS


def score_windows(discriminators: set[str], windows: list[tuple[str, str]]) -> list[tuple[str, int]]:
    return [(window_id, len(discriminators & token_set(text))) for window_id, text in windows]


def unique_top_window(discriminators: set[str], windows: list[tuple[str, str]]) -> str | None:
    scores = score_windows(discriminators, windows)
    if not scores:
        return None
    best = max(score for _, score in scores)
    if best <= 0:
        return None
    tops = [window_id for window_id, score in scores if score == best]
    return tops[0] if len(tops) == 1 else None


def _property_phrases(property_label: str) -> list[str]:
    label = normalize_space(re.sub(r"\s+requirement$", "", property_label or "", flags=re.I))
    words = label.split()
    phrases = [label] if len(words) >= 3 else []
    for size in (5, 4, 3):
        if len(words) < size:
            continue
        for index in range(0, len(words) - size + 1):
            phrase = " ".join(words[index:index + size])
            if len(phrase) >= 12:
                phrases.append(phrase)
    seen: set[str] = set()
    ordered: list[str] = []
    for phrase in phrases:
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(phrase)
    return ordered


def property_window_score(property_label: str, window_text: str) -> int:
    """Token overlap plus contiguous phrase hits so claim pins survive bag-of-words ties."""
    score = len(discriminator_tokens(property_label) & token_set(window_text))
    blob = window_text.lower()
    for phrase in _property_phrases(property_label):
        if phrase.lower() in blob:
            score += max(3, len(phrase.split()))
    return score


def unique_property_window(property_label: str, windows: list[tuple[str, str]]) -> str | None:
    scores = [(window_id, property_window_score(property_label, text)) for window_id, text in windows]
    if not scores:
        return None
    best = max(score for _, score in scores)
    if best <= 0:
        return None
    tops = [window_id for window_id, score in scores if score == best]
    return tops[0] if len(tops) == 1 else None


ABSTAIN_SEMANTICS = (
    "The named claim is under assessment. Return REPAIR only if exactly one public "
    "window states a unique, sufficient current value for this claim. If the public "
    "windows are silent, incomplete, or mutually incompatible for this same claim, "
    "return ABSTAIN. Do not invent a value and do not substitute a different claim."
)


def bind_public_claim_fields(
    event: dict[str, str],
    *,
    entity: str,
    property_label: str,
    claim_id: str | None = None,
    semantics: str | None = None,
) -> dict[str, str]:
    """Write value-blind public claim fields. Does not copy Gold decisions or new values."""
    entity = normalize_space(entity)
    property_label = normalize_space(property_label)
    question = (
        f"What is the current {property_label} for {entity}? "
        "Use only the public evidence windows. Return REPAIR, NO_CHANGE, or ABSTAIN. "
        "Do not assume a replacement value from this question."
    )
    cq = f"What current ontology value is bound for the {property_label} of {entity}?"
    event["target_claim_id"] = claim_id or f"{event['event_id']}-claim-01"
    event["target_entity_label"] = entity
    event["target_property_label"] = property_label
    event["subject_label"] = entity
    event["predicate_label"] = property_label
    event["value_neutral_question"] = question
    event["target_cq"] = cq
    event["case_context"] = question
    if semantics:
        event["current_value_semantics"] = semantics
    return event



def leakage_hits(public_text: str, new_lexical: str, gold_window_text: str, pin_text: str | None = None) -> list[str]:
    hits: list[str] = []
    blob = public_text.lower()
    if new_lexical and new_lexical.lower() in blob:
        hits.append("new_value_lexical")
    if re.search(r"\bsource_window_\d\b", blob):
        hits.append("gold_window_index")
    if re.search(r"\bcand_00\d\b", blob):
        hits.append("candidate_id")
    span = normalize_space(gold_window_text)
    if len(span) >= 80 and span.lower() in blob:
        hits.append("full_gold_window")
    pin = pin_text if pin_text is not None else public_text
    for match in RFC2119_RE.finditer(gold_window_text or ""):
        keyword = match.group(0)
        if re.search(rf"\b{re.escape(keyword)}\b", pin, re.I):
            hits.append("rfc2119_in_target")
            break
    return hits


def humanize_slug(lexical: str) -> str:
    if not lexical or "unmodeled" in lexical:
        return CURRENT_SURFACE_UNMODELED
    _, _, rest = lexical.partition("=")
    body = rest or lexical
    return normalize_space(body.replace("_", " ")).capitalize() + "."


def build_claim_fields(
    *,
    event_id: str,
    domain: str,
    subject_label: str,
    predicate_iri: str,
    rfc_title: str,
    gold_window_text: str,
    other_window_texts: list[str],
    current_lexical: str,
    decision: str,
    new_lexical: str = "",
) -> dict[str, str]:
    entity = rfc_title or re.sub(r"\s+normative subject$", "", subject_label, flags=re.I)
    if is_author_subject(entity) and rfc_title:
        entity = rfc_title
    entity = normalize_space(entity) or f"{domain} protocol feature"
    exclude = value_like_tokens(new_lexical)
    phrases = distinctive_phrases(
        gold_window_text or " ".join(other_window_texts),
        other_window_texts,
        exclude_tokens=exclude,
    )
    property_label = normalize_space(phrases[0] + " requirement") if phrases else f"{domain} claim requirement"
    if decision == "ABSTAIN" and not gold_window_text:
        property_label = f"{entity} requirement under assessment"
    question = (
        f"What is the current {property_label} for {entity}? "
        "Use only the public evidence windows. Return REPAIR, NO_CHANGE, or ABSTAIN. "
        "Do not assume a replacement value from this question."
    )
    cq = f"What current ontology value is bound for the {property_label} of {entity}?"
    if "unmodeled" in (current_lexical or ""):
        surface = CURRENT_SURFACE_UNMODELED
        semantics = CURRENT_SEMANTICS_UNMODELED
    else:
        surface = humanize_slug(current_lexical)
        semantics = (
            "The current ontology already binds this claim. Treat the surface as the old assertion. "
            "If public evidence states an equivalent requirement, return NO_CHANGE. "
            "If it states a different requirement for this same claim, return REPAIR."
        )
    return {
        "target_claim_id": f"{event_id}-claim-01",
        "target_entity_label": entity,
        "target_property_label": property_label,
        "target_property_iri": predicate_iri,
        "current_value_surface": surface,
        "current_value_semantics": semantics,
        "value_neutral_question": question,
        "target_cq": cq,
    }


def strengthen_until_unique(
    fields: dict[str, str],
    windows: list[tuple[str, str]],
    gold_window: str,
    gold_text: str,
    other_texts: list[str],
    new_lexical: str = "",
) -> dict[str, str]:
    """Append distinctive phrases until Gold is the unique top window, if possible."""
    updated = dict(fields)
    exclude = value_like_tokens(new_lexical)
    phrases = distinctive_phrases(gold_text, other_texts, max_phrases=8, exclude_tokens=exclude)
    for phrase in phrases:
        if unique_property_window(updated["target_property_label"], windows) == gold_window:
            return updated
        extra = f"{updated['target_property_label']}; {phrase}"
        if len(extra) > 160:
            break
        updated["target_property_label"] = extra
        updated["value_neutral_question"] = (
            f"What is the current {updated['target_property_label']} for {updated['target_entity_label']}? "
            "Use only the public evidence windows. Return REPAIR, NO_CHANGE, or ABSTAIN. "
            "Do not assume a replacement value from this question."
        )
        updated["target_cq"] = (
            f"What current ontology value is bound for the {updated['target_property_label']} "
            f"of {updated['target_entity_label']}?"
        )
    return updated
