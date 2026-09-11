from __future__ import annotations

"""Shared normative tuple representation for gate, candidates, ranking, and literal compilation."""

import re
from datetime import datetime
from dataclasses import asdict, dataclass, field
from typing import Any


MODALITIES = {"MUST", "MUST_NOT", "SHOULD", "SHOULD_NOT", "MAY", "DESCRIPTIVE"}


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def tokens(value: Any) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", norm(value)) if len(token) > 1}


@dataclass(frozen=True)
class NormativeTuple:
    core: str
    modality: str = "DESCRIPTIVE"
    polarity: str = "POSITIVE"
    numeric_value: str = ""
    unit: str = ""
    conditions: tuple[str, ...] = field(default_factory=tuple)
    exceptions: tuple[str, ...] = field(default_factory=tuple)
    enumeration: tuple[str, ...] = field(default_factory=tuple)
    valid_from: str = ""
    valid_to: str = ""
    jurisdiction: str = "GLOBAL"

    def json(self) -> dict[str, Any]:
        return asdict(self)

    def applicability_key(self) -> tuple[Any, ...]:
        return (
            tuple(sorted(norm(x) for x in self.conditions)), tuple(sorted(norm(x) for x in self.exceptions)),
            norm(self.valid_from), norm(self.valid_to), norm(self.jurisdiction),
        )

    def value_key(self) -> tuple[Any, ...]:
        return (
            norm(self.core), self.modality, self.polarity, norm(self.numeric_value), norm(self.unit),
            tuple(sorted(norm(x) for x in self.enumeration)),
        )


def _modality(value: Any) -> str:
    candidate = str(value or "DESCRIPTIVE").upper().replace(" ", "_")
    return candidate if candidate in MODALITIES else "DESCRIPTIVE"


def tuple_from_frame(frame: dict[str, Any]) -> NormativeTuple:
    modality = _modality(frame.get("modality"))
    negative = modality in {"MUST_NOT", "SHOULD_NOT"} or bool(frame.get("negated"))
    return NormativeTuple(
        core=str(frame.get("value") or frame.get("core_value") or "").strip(), modality=modality,
        polarity="NEGATIVE" if negative else "POSITIVE", numeric_value=str(frame.get("numeric_value") or "").strip(),
        unit=str(frame.get("unit") or "").strip(), conditions=tuple(str(x).strip() for x in frame.get("conditions", []) if str(x).strip()),
        exceptions=tuple(str(x).strip() for x in frame.get("exceptions", []) if str(x).strip()),
        enumeration=tuple(str(x).strip() for x in frame.get("enumeration_members", []) if str(x).strip()),
        valid_from=str(frame.get("valid_from") or ""), valid_to=str(frame.get("valid_to") or ""),
        jurisdiction=str(frame.get("jurisdiction") or "GLOBAL"),
    )


def tuple_from_slot(frame: dict[str, Any]) -> NormativeTuple:
    enriched = dict(frame)
    enriched["value"] = frame.get("core_value") or frame.get("surface_value") or ""
    return tuple_from_frame(enriched)


def _date(value: str, end: bool = False) -> datetime | None:
    if not value: return None
    if re.fullmatch(r"\d{4}", value): value += "-12-31" if end else "-01-01"
    if re.fullmatch(r"\d{4}-\d{2}", value): value += "-28" if end else "-01"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value): value += "T23:59:59+00:00" if end else "T00:00:00+00:00"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def gate_tuples(items: list[NormativeTuple], as_of: str = "") -> dict[str, Any]:
    point = _date(as_of) if as_of else None
    valid = [item for item in items if norm(item.core) and (point is None or ((_date(item.valid_from) is None or _date(item.valid_from) <= point) and (_date(item.valid_to, True) is None or point <= _date(item.valid_to, True))))]
    if not valid:
        return {"decision": "ABSTAIN", "reason": "INSUFFICIENT_EVIDENCE"}
    scopes: dict[tuple[Any, ...], list[NormativeTuple]] = {}
    for item in valid: scopes.setdefault(item.applicability_key(), []).append(item)
    if any(len({item.value_key() for item in group}) > 1 for group in scopes.values()):
        return {"decision": "ABSTAIN", "reason": "CONFLICTING_EVIDENCE"}
    unique = {item.value_key(): item for item in valid}
    if len(scopes) > 1 or len(unique) != 1:
        return {"decision": "ABSTAIN", "reason": "UNRESOLVED_APPLICABILITY"}
    return {"decision": "CANDIDATE_EVALUATION_REQUIRED", "tuple": next(iter(unique.values()))}


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 1.0


def tuple_entailment_score(evidence: NormativeTuple, candidate: NormativeTuple) -> tuple[float, list[str]]:
    hard_errors = []
    if evidence.polarity != candidate.polarity: hard_errors.append("polarity")
    if evidence.numeric_value and norm(evidence.numeric_value) != norm(candidate.numeric_value): hard_errors.append("numeric_value")
    if evidence.unit and norm(evidence.unit) != norm(candidate.unit): hard_errors.append("unit")
    if evidence.enumeration and set(map(norm, evidence.enumeration)) != set(map(norm, candidate.enumeration)): hard_errors.append("enumeration")
    if hard_errors: return 0.0, hard_errors
    core = _jaccard(tokens(evidence.core), tokens(candidate.core))
    modality = 1.0 if evidence.modality == candidate.modality else (0.5 if "DESCRIPTIVE" in {evidence.modality, candidate.modality} else 0.0)
    conditions = _jaccard(tokens(" ".join(evidence.conditions)), tokens(" ".join(candidate.conditions)))
    exceptions = _jaccard(tokens(" ".join(evidence.exceptions)), tokens(" ".join(candidate.exceptions)))
    score = 0.55 * core + 0.15 * modality + 0.15 * conditions + 0.15 * exceptions
    return score, []


def rank_tuples(evidence: NormativeTuple, candidates: list[dict[str, Any]], threshold: float = 0.72, margin: float = 0.08) -> dict[str, Any]:
    scored = []
    for candidate in candidates:
        score, errors = tuple_entailment_score(evidence, candidate["tuple"])
        scored.append({"candidate_id": candidate["candidate_id"], "score": score, "hard_errors": errors, "candidate": candidate})
    scored.sort(key=lambda row: (-row["score"], row["candidate_id"]))
    if not scored or scored[0]["score"] < threshold:
        return {"decision": "ABSTAIN", "reason": "NO_ENTAILED_TUPLE", "ranking": scored}
    if len(scored) > 1 and scored[0]["score"] - scored[1]["score"] < margin:
        return {"decision": "ABSTAIN", "reason": "TUPLE_RANKING_AMBIGUOUS", "ranking": scored}
    return {"decision": "SELECT", "candidate": scored[0]["candidate"], "ranking": scored}


def compile_literal(candidate: dict[str, Any]) -> str:
    """Render only after tuple selection; fixed precedence prevents free-form ranking output."""
    source = candidate.get("source", {})
    for key in ("scope_complete_value", "core_value", "surface_value"):
        value = source.get(key)
        if isinstance(value, str) and value.strip(): return value.strip()
    item: NormativeTuple = candidate["tuple"]
    if item.enumeration: return ", ".join(item.enumeration[:-1]) + (", and " if len(item.enumeration) > 1 else "") + item.enumeration[-1]
    if item.numeric_value: return f"{item.numeric_value} {item.unit}".strip()
    prefix = item.modality.replace("_", " ") + " " if item.modality != "DESCRIPTIVE" else ""
    return (prefix + item.core).strip()
