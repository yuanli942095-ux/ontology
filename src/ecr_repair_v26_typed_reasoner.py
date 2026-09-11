from __future__ import annotations

"""Generic predicate-type constraints over canonical tuples."""

from dataclasses import dataclass
from typing import Any

from ecr_repair_v26_canonical_mapper import CanonicalTuple, _date, canonical_score, concepts


@dataclass(frozen=True)
class PredicateProfile:
    type_id: str
    predicate_id: str


def infer_profile(predicate_id: str, predicate_label: str, tuples: list[CanonicalTuple]) -> PredicateProfile:
    label = concepts(predicate_label)
    if any(item.enumeration_ids for item in tuples) or label & {"enum", "enumeration", "member", "membership", "available"}:
        type_id = "ENUMERATION"
    elif any(item.numeric_value for item in tuples) or label & {"age", "limit", "threshold", "duration", "period", "timeout", "rate", "size", "length"}:
        type_id = "NUMERIC_THRESHOLD"
    elif any(item.exception_concepts for item in tuples):
        type_id = "RULE_WITH_EXCEPTION"
    elif any(item.condition_concepts for item in tuples):
        type_id = "CONDITIONAL_RULE"
    elif label & {"scope", "origin", "jurisdiction", "population", "relationship", "access", "allowed"}:
        type_id = "SCOPE_RESTRICTION"
    elif any(item.valid_from for item in tuples):
        type_id = "TEMPORAL_VALUE"
    elif label & {"enabled", "boolean", "flag"}:
        type_id = "BOOLEAN"
    elif any(item.modality != "DESCRIPTIVE" for item in tuples) or label & {"require", "requirement", "obligation"}:
        type_id = "MODAL_REQUIREMENT"
    elif len(tuples) > 1:
        type_id = "COMPOSITE_PROPERTY"
    else:
        type_id = "COMPOSITE_PROPERTY"
    return PredicateProfile(type_id, predicate_id)


def valid_for_profile(item: CanonicalTuple, profile: PredicateProfile) -> bool:
    if not item.core_concepts: return False
    if profile.type_id == "ENUMERATION": return bool(item.enumeration_ids)
    if profile.type_id == "NUMERIC_THRESHOLD": return bool(item.numeric_value and item.unit_id)
    if profile.type_id == "CONDITIONAL_RULE": return bool(item.condition_concepts)
    if profile.type_id == "RULE_WITH_EXCEPTION": return bool(item.exception_concepts)
    if profile.type_id == "TEMPORAL_VALUE": return bool(item.valid_from)
    if profile.type_id == "MODAL_REQUIREMENT": return item.modality != "DESCRIPTIVE"
    return True


def typed_gate(items: list[CanonicalTuple], profile: PredicateProfile, case_context: str, as_of: str = "") -> dict[str, Any]:
    point = _date(as_of) if as_of else None
    valid = [item for item in items if valid_for_profile(item, profile) and (point is None or ((_date(item.valid_from) is None or _date(item.valid_from) <= point) and (_date(item.valid_to, True) is None or point <= _date(item.valid_to, True))))]
    if not valid: return {"decision": "ABSTAIN", "reason": "TYPE_REQUIRED_SLOTS_MISSING"}
    if profile.type_id in {"CONDITIONAL_RULE", "RULE_WITH_EXCEPTION"} and len(valid) > 1:
        context = concepts(case_context)
        scored = []
        for item in valid:
            scope = item.condition_concepts | item.exception_concepts
            scored.append((len(context & scope) / len(scope) if scope else 0.0, item))
        best = max(score for score, _ in scored)
        selected = [item for score, item in scored if score == best and score > 0]
        if len(selected) != 1: return {"decision": "ABSTAIN", "reason": "CONDITION_NOT_UNIQUELY_APPLICABLE"}
        valid = selected
    scopes: dict[tuple[Any, ...], list[CanonicalTuple]] = {}
    for item in valid: scopes.setdefault(item.applicability_key(), []).append(item)
    if any(len({item.value_key() for item in group}) > 1 for group in scopes.values()):
        return {"decision": "ABSTAIN", "reason": "CONFLICTING_EVIDENCE"}
    if len(valid) != 1:
        return {"decision": "ABSTAIN", "reason": "TYPE_COMPOSITION_UNRESOLVED"}
    return {"decision": "CANDIDATE_EVALUATION_REQUIRED", "tuple": valid[0]}


def typed_score(evidence: CanonicalTuple, candidate: CanonicalTuple, profile: PredicateProfile) -> tuple[float, list[str]]:
    base, errors = canonical_score(evidence, candidate)
    if errors: return base, errors
    if not valid_for_profile(candidate, profile): return 0.0, ["candidate_required_slots"]
    if profile.type_id == "ENUMERATION": return (1.0 if evidence.enumeration_ids == candidate.enumeration_ids else 0.0), []
    if profile.type_id == "NUMERIC_THRESHOLD": return (1.0 if (evidence.numeric_value, evidence.unit_id) == (candidate.numeric_value, candidate.unit_id) else 0.0), []
    return base, []


def rank_typed(evidence: CanonicalTuple, candidates: list[dict[str, Any]], profile: PredicateProfile, threshold: float = 0.68, margin: float = 0.08) -> dict[str, Any]:
    rows=[]
    for candidate in candidates:
        score,errors=typed_score(evidence,candidate["canonical_tuple"],profile)
        rows.append({"candidate_id":candidate["candidate_id"],"score":score,"hard_errors":errors,"candidate":candidate})
    rows.sort(key=lambda row:(-row["score"],row["candidate_id"]))
    if not rows or rows[0]["score"]<threshold:return {"decision":"ABSTAIN","reason":"NO_TYPED_ENTAILMENT","ranking":rows}
    if len(rows)>1 and rows[0]["score"]-rows[1]["score"]<margin:return {"decision":"ABSTAIN","reason":"TYPED_TIE","ranking":rows}
    return {"decision":"SELECT","candidate":rows[0]["candidate"],"ranking":rows}


def compile_typed_literal(candidate: dict[str, Any], profile: PredicateProfile) -> str:
    item: CanonicalTuple = candidate["canonical_tuple"]
    source = candidate.get("source", {})
    if profile.type_id == "ENUMERATION" and item.enumeration_ids:
        members = source.get("enumeration_members", [])
        if members: return ", ".join(members[:-1]) + (", and " if len(members)>1 else "") + members[-1]
    if profile.type_id == "NUMERIC_THRESHOLD" and item.numeric_value:return f"{item.numeric_value} {source.get('unit') or item.unit_id}".strip()
    if profile.type_id == "CONDITIONAL_RULE" and source.get("scope_complete_value"):return source["scope_complete_value"].strip()
    if profile.type_id == "RULE_WITH_EXCEPTION" and source.get("scope_complete_value"):return source["scope_complete_value"].strip()
    for key in ("scope_complete_value","core_value","surface_value"):
        if isinstance(source.get(key),str) and source[key].strip():return source[key].strip()
    return " ".join(sorted(item.core_concepts))
