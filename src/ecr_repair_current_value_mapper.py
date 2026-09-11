from __future__ import annotations

import re
from dataclasses import dataclass


WEAK = {"may", "can", "optional", "optionally", "might"}
STRONG = {"must", "shall", "required", "always"}


def concepts(value: str) -> set[str]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value or "").casefold()
    return {x for x in re.findall(r"[a-z0-9]+", value) if len(x) > 1}


@dataclass(frozen=True)
class CurrentValueMapping:
    value_id: str
    score: float
    equivalent: bool
    reason: str


def map_current_value(current_literal: str, candidate: dict[str, str]) -> CurrentValueMapping:
    """Map a current ontology literal without treating a shared noun as equivalence."""
    current = concepts(current_literal)
    value_id = candidate.get("tuple_value_id", "")
    value = concepts(value_id.replace("_", " "))
    quote = concepts(candidate.get("verbatim_quote", ""))
    if not current or not value:
        return CurrentValueMapping(value_id, 0.0, False, "EMPTY_VALUE")
    if value_id == "ENABLED_CANNOT_DISABLE" and {"enabled", "disable"}.issubset(current):
        return CurrentValueMapping(value_id, 1.0, True, "COMPOSITE_ALIAS")
    if (current & WEAK) and (candidate.get("tuple_modality", "") in {"MUST", "SHOULD"} or "always" in quote):
        return CurrentValueMapping(value_id, 0.0, False, "MODALITY_MISMATCH")
    overlap = len(current & value) / len(value)
    quote_overlap = len(current & quote) / len(current | quote) if quote else 0.0
    return CurrentValueMapping(value_id, max(overlap, quote_overlap), overlap >= 0.8, "VALUE_ID_ENTAILMENT" if overlap >= 0.8 else "INSUFFICIENT_VALUE_ENTAILMENT")
