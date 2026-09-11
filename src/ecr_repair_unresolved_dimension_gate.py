from __future__ import annotations

import json
import re
from dataclasses import dataclass


def concepts(value: str) -> set[str]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value or "").casefold()
    return {x for x in re.findall(r"[a-z0-9]+", value) if len(x) > 1}


@dataclass(frozen=True)
class GateResult:
    decision: str
    reason: str
    candidates: tuple[dict[str, str], ...] = ()


def apply_unresolved_dimension_gate(candidates: list[tuple[float, dict[str, str]]], case_context: str) -> GateResult:
    """Keep alternatives unless a condition/profile/version dimension is resolved by context."""
    valued = [(score, row) for score, row in candidates if row.get("tuple_value_id", "").strip()]
    if not valued:
        return GateResult("ABSTAIN", "INSUFFICIENT_EVIDENCE")
    values = {row["tuple_value_id"] for _, row in valued}
    if len(values) <= 1:
        return GateResult("CANDIDATE", "UNIQUE_VALUE", (valued[0][1],))
    context = concepts(case_context)
    conditional = []
    for score, row in valued:
        try:
            conditions = json.loads(row.get("tuple_conditions_json", "[]") or "[]")
        except json.JSONDecodeError:
            conditions = []
        condition_tokens = concepts(" ".join(conditions))
        conditional.append((len(context & condition_tokens), score, row, bool(condition_tokens)))
    matching = [item for item in conditional if item[0] > 0]
    if matching:
        matching.sort(key=lambda item: (-item[0], -item[1], item[2].get("frame_id", "")))
        if len(matching) == 1 or matching[0][0] > matching[1][0]:
            return GateResult("CANDIDATE", "CONDITION_RESOLVED", (matching[0][2],))
    # If alternatives are unconditioned or context does not resolve them, do not date-filter or top-score them away.
    return GateResult("ABSTAIN", "UNRESOLVED_VERSION_PROFILE_OR_SCOPE", tuple(row for _, row in valued))
