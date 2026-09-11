from __future__ import annotations

"""Predicate-local canonical vocabulary and tuple mapping without Oracle access."""

import re
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Iterable

from ecr_repair_v26_tuple import NormativeTuple, norm


STOP = {"a", "an", "the", "of", "to", "for", "in", "on", "by", "with", "and", "or", "is", "are", "be", "as", "that", "this"}
ALIASES = {
    "required": "require", "requires": "require", "requirement": "require", "mandatory": "require",
    "allowed": "allow", "allows": "allow", "permitted": "allow", "permission": "allow",
    "prohibited": "deny", "forbidden": "deny", "disallowed": "deny", "reject": "deny", "rejected": "deny",
    "ignored": "ignore", "ignores": "ignore", "ignoring": "ignore",
    "supported": "support", "supports": "support", "available": "support",
    "maximum": "max", "highest": "max", "minimum": "min", "lowest": "min",
    "days": "day", "hours": "hour", "minutes": "minute", "years": "year",
    "true": "yes", "enabled": "yes", "enable": "yes", "false": "no", "disabled": "no", "disable": "no",
    "must": "must", "shall": "must", "should": "should", "may": "may",
}


def canonical_token(token: str) -> str:
    token = token.casefold().strip("-_")
    if token in ALIASES: return ALIASES[token]
    if len(token) > 5 and token.endswith("ing"): token = token[:-3]
    elif len(token) > 4 and token.endswith("ed"): token = token[:-2]
    elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"): token = token[:-1]
    return ALIASES.get(token, token)


def concepts(value: Any) -> frozenset[str]:
    return frozenset(canonical_token(token) for token in re.findall(r"[A-Za-z0-9]+", norm(value)) if token.casefold() not in STOP)


def enumeration_id(value: Any) -> str:
    parts = [canonical_token(token) for token in re.findall(r"[A-Za-z0-9]+", norm(value))]
    return "_".join(part for part in parts if part)


@dataclass(frozen=True)
class PredicateVocabulary:
    predicate_id: str
    anchor_concepts: frozenset[str]


@dataclass(frozen=True)
class CanonicalTuple:
    predicate_id: str
    core_concepts: frozenset[str]
    modality: str
    polarity: str
    numeric_value: str
    unit_id: str
    condition_concepts: frozenset[str]
    exception_concepts: frozenset[str]
    enumeration_ids: frozenset[str]
    valid_from: str
    valid_to: str
    jurisdiction_id: str

    def applicability_key(self) -> tuple[Any, ...]:
        return self.condition_concepts, self.exception_concepts, norm(self.valid_from), norm(self.valid_to), self.jurisdiction_id

    def value_key(self) -> tuple[Any, ...]:
        return self.core_concepts, self.modality, self.polarity, norm(self.numeric_value), self.unit_id, self.enumeration_ids


def build_predicate_vocabulary(predicate_iri: str, predicate_label: str) -> PredicateVocabulary:
    return PredicateVocabulary(predicate_id=predicate_iri, anchor_concepts=concepts(predicate_label))


def map_tuple(item: NormativeTuple, vocabulary: PredicateVocabulary) -> CanonicalTuple:
    return CanonicalTuple(
        predicate_id=vocabulary.predicate_id, core_concepts=concepts(item.core), modality=item.modality,
        polarity=item.polarity, numeric_value=norm(item.numeric_value), unit_id="_".join(sorted(concepts(item.unit))),
        condition_concepts=concepts(" ".join(item.conditions)), exception_concepts=concepts(" ".join(item.exceptions)),
        enumeration_ids=frozenset(enumeration_id(value) for value in item.enumeration if enumeration_id(value)),
        valid_from=item.valid_from, valid_to=item.valid_to, jurisdiction_id="_".join(sorted(concepts(item.jurisdiction))) or "global",
    )


def compatible(left: CanonicalTuple, right: CanonicalTuple) -> bool:
    return (
        left.predicate_id == right.predicate_id and left.polarity == right.polarity
        and (left.modality == right.modality or "DESCRIPTIVE" in {left.modality, right.modality})
        and (not left.numeric_value or not right.numeric_value or left.numeric_value == right.numeric_value)
        and (not left.unit_id or not right.unit_id or left.unit_id == right.unit_id)
        and left.applicability_key() == right.applicability_key()
    )


def compose_tuples(items: Iterable[CanonicalTuple]) -> list[CanonicalTuple]:
    """Merge complementary same-scope fragments; retain incompatible alternatives separately."""
    output: list[CanonicalTuple] = []
    for item in items:
        index = next((i for i, existing in enumerate(output) if compatible(existing, item)), None)
        if index is None:
            output.append(item); continue
        old = output[index]
        output[index] = CanonicalTuple(
            predicate_id=old.predicate_id, core_concepts=old.core_concepts | item.core_concepts,
            modality=item.modality if old.modality == "DESCRIPTIVE" else old.modality, polarity=old.polarity,
            numeric_value=old.numeric_value or item.numeric_value, unit_id=old.unit_id or item.unit_id,
            condition_concepts=old.condition_concepts, exception_concepts=old.exception_concepts,
            enumeration_ids=old.enumeration_ids | item.enumeration_ids, valid_from=old.valid_from,
            valid_to=old.valid_to, jurisdiction_id=old.jurisdiction_id,
        )
    return output


def canonical_score(evidence: CanonicalTuple, candidate: CanonicalTuple) -> tuple[float, list[str]]:
    errors = []
    if evidence.predicate_id != candidate.predicate_id: errors.append("predicate")
    if evidence.polarity != candidate.polarity: errors.append("polarity")
    if evidence.numeric_value and evidence.numeric_value != candidate.numeric_value: errors.append("numeric")
    if evidence.unit_id and evidence.unit_id != candidate.unit_id: errors.append("unit")
    if evidence.enumeration_ids and evidence.enumeration_ids != candidate.enumeration_ids: errors.append("enumeration")
    if errors: return 0.0, errors
    def jac(a: frozenset[str], b: frozenset[str]) -> float: return len(a & b) / len(a | b) if a or b else 1.0
    core = jac(evidence.core_concepts, candidate.core_concepts)
    modality = 1.0 if evidence.modality == candidate.modality else (0.65 if "DESCRIPTIVE" in {evidence.modality, candidate.modality} else 0.0)
    condition = jac(evidence.condition_concepts, candidate.condition_concepts)
    exception = jac(evidence.exception_concepts, candidate.exception_concepts)
    return 0.55 * core + 0.15 * modality + 0.15 * condition + 0.15 * exception, []


def _date(value: str, end: bool = False) -> datetime | None:
    if not value: return None
    if re.fullmatch(r"\d{4}", value): value += "-12-31" if end else "-01-01"
    if re.fullmatch(r"\d{4}-\d{2}", value): value += "-28" if end else "-01"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value): value += "T23:59:59+00:00" if end else "T00:00:00+00:00"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_gate(items: list[CanonicalTuple], as_of: str = "") -> dict[str, Any]:
    point = _date(as_of) if as_of else None
    applicable = [item for item in items if item.core_concepts and (point is None or ((_date(item.valid_from) is None or _date(item.valid_from) <= point) and (_date(item.valid_to, True) is None or point <= _date(item.valid_to, True))))]
    composed = compose_tuples(applicable)
    if not composed: return {"decision": "ABSTAIN", "reason": "INSUFFICIENT_EVIDENCE"}
    scopes: dict[tuple[Any, ...], list[CanonicalTuple]] = {}
    for item in composed: scopes.setdefault(item.applicability_key(), []).append(item)
    if any(len({item.value_key() for item in group}) > 1 for group in scopes.values()):
        return {"decision": "ABSTAIN", "reason": "CONFLICTING_EVIDENCE"}
    if len(scopes) != 1: return {"decision": "ABSTAIN", "reason": "UNRESOLVED_APPLICABILITY"}
    return {"decision": "CANDIDATE_EVALUATION_REQUIRED", "tuple": composed[0], "composed_count": len(composed)}


def rank_canonical(evidence: CanonicalTuple, candidates: list[dict[str, Any]], threshold: float = 0.64, margin: float = 0.06) -> dict[str, Any]:
    rows = []
    for candidate in candidates:
        score, errors = canonical_score(evidence, candidate["canonical_tuple"])
        rows.append({"candidate_id": candidate["candidate_id"], "score": score, "hard_errors": errors, "candidate": candidate})
    rows.sort(key=lambda row: (-row["score"], row["candidate_id"]))
    if not rows or rows[0]["score"] < threshold: return {"decision": "ABSTAIN", "reason": "NO_CANONICAL_ENTAILMENT", "ranking": rows}
    if len(rows) > 1 and rows[0]["score"] - rows[1]["score"] < margin: return {"decision": "ABSTAIN", "reason": "CANONICAL_TIE", "ranking": rows}
    return {"decision": "SELECT", "candidate": rows[0]["candidate"], "ranking": rows}
