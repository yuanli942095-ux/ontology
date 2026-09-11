from __future__ import annotations

from datetime import datetime, timezone


def _parse(value: str, end: bool = False) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if len(text) == 4:
        text += "-12-31" if end else "-01-01"
    if len(text) == 7:
        text += "-28" if end else "-01"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def is_effective(document: dict[str, str], as_of: str) -> bool:
    point = _parse(as_of)
    if point is None or not document.get("effective_from"):
        return False
    start = _parse(document["effective_from"])
    end = _parse(document.get("effective_to", ""), end=True)
    return bool(start and start <= point and (end is None or point <= end))


def resolve_temporal_applicability(
    candidates: list[tuple[float, dict[str, str]]],
    as_of: str,
    documents: dict[str, dict[str, str]],
    case_context: str = "",
) -> tuple[tuple[float, dict[str, str]], ...] | None:
    """Return candidates uniquely active at as_of, or None when unresolved."""
    if not as_of:
        return None
    lowered = case_context.casefold() if isinstance(case_context, str) else ""
    unresolved_markers = (
        "version unrecorded", "version not recorded", "profile not specified",
        "does not identify", "omits whether", "scope not specified", "unknown",
    )
    if any(marker in lowered for marker in unresolved_markers):
        return None
    active = [
        item for item in candidates
        if is_effective(documents.get(item[1].get("document_id", ""), {}), as_of)
    ]
    values = {row.get("tuple_value_id", "") for _, row in active}
    # A date can resolve an alternative only when it actually excludes at
    # least one candidate. It must not collapse two values from one active
    # document into a decision.
    return tuple(active) if active and len(active) < len(candidates) and len(values) == 1 else None
