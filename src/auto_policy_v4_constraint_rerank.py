from __future__ import annotations

"""Type-specific constraint-aware reranking for AUTO_POLICY_V4.2 / V4.3.

IR is still built candidate-blind. This module only sees candidates at ranking
time. Token/family overlap is a residual feature; role, value, and direction
do the work. Equal top scores abstain instead of breaking ties by CAND_001.
V4.3: TEMPORAL unique Top-1 with no contradiction may SELECT below min_score.
"""

import re
from dataclasses import dataclass, field
from typing import Any

def _v4():
    from run_auto_policy_v4_ir_candidate_repair import (  # noqa: PLC0415
        SemanticIR,
        compact,
        extract_codes,
        extract_numbers,
        salient_numbers,
        tokens,
    )

    return SemanticIR, compact, extract_codes, extract_numbers, salient_numbers, tokens


FORWARD_RELATIONS = {
    "SUPERSEDES",
    "REPLACES",
    "AMENDS",
    "EFFECTIVE_FROM",
    "ADDED",
}

PREVIOUS_ROLE_MARKERS = (
    "previous_target",
    "previous_or_not_encoded",
    "old_revision",
    "old_or_absent",
    "not_present",
    "not_integrated",
    "total_only",
    "unmodeled",
    "near_miss",
    "wrong_scope",
)

CURRENT_ROLE_MARKERS = (
    "current_target",
    "current_text",
    "currently",
    "updated",
    "inserted",
    "integrated",
    "expanded",
    "published",
    "replacement_plan",
    "replaced_entries",
    "deferred_date",
    "final",
    "rev4",
    "wcag22",
    "extends",
    "noted",
)


@dataclass
class StructuredRepair:
    subject: str = ""
    predicate: str = ""
    old_value: str = ""
    new_value: str = ""
    old_status: str = "UNSPECIFIED"
    new_status: str = "UNSPECIFIED"
    effective_time: str = ""
    relation_direction: str = "UNKNOWN"
    operation_type: str = ""
    assignments: dict[str, str] = field(default_factory=dict)
    raw: str = ""
    numbers: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    source_family: str = ""


def parse_assignments(text: str) -> dict[str, str]:
    _, compact, *_rest = _v4()
    assignments: dict[str, str] = {}
    blob = str(text or "")
    for chunk in re.split(r"[;|]", blob):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key_n = compact(key)
        value_n = compact(value)
        if key_n and value_n:
            assignments[key_n] = value_n
    return assignments


def lexical_value(operation: Any, side: str) -> str:
    if not isinstance(operation, dict):
        return ""
    payload = operation.get(f"{side}_value") or operation.get(side)
    if isinstance(payload, dict):
        return str(payload.get("lexical") or "")
    return str(payload or "")


def role_from_text(text: str) -> str:
    _, compact, *_rest = _v4()
    blob = compact(text)
    if any(marker in blob for marker in PREVIOUS_ROLE_MARKERS):
        return "PREVIOUS"
    if any(marker in blob for marker in CURRENT_ROLE_MARKERS):
        return "CURRENT"
    return "UNSPECIFIED"


def flatten_field(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(flatten_field(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key} {flatten_field(item)}" for key, item in value.items())
    return str(value or "")


def parse_ir_structure(ir: Any) -> StructuredRepair:
    fields = ir.fields or {}
    result = flatten_field(fields.get("result"))
    new_value = flatten_field(fields.get("new_value")) or result
    old_value = flatten_field(fields.get("old_value"))
    relation = str(fields.get("relation") or "UNKNOWN")
    direction = "FORWARD" if relation in FORWARD_RELATIONS else "UNKNOWN"
    new_status = "CURRENT" if direction == "FORWARD" else role_from_text(new_value)
    if new_status == "UNSPECIFIED" and (ir.source_family or result):
        new_status = "CURRENT"
    return StructuredRepair(
        subject=flatten_field(fields.get("subject")),
        predicate=flatten_field(fields.get("statement") or fields.get("predicate")),
        old_value=old_value,
        new_value=new_value,
        old_status=role_from_text(old_value) if old_value else "PREVIOUS" if direction == "FORWARD" else "UNSPECIFIED",
        new_status=new_status,
        effective_time=flatten_field(fields.get("effective_time")),
        relation_direction=direction,
        operation_type=relation,
        assignments=parse_assignments(" ".join([result, new_value, ir.source_family])),
        raw=" ".join([result, new_value, ir.source_family, relation]),
        numbers=list(ir.numbers),
        codes=list(ir.codes),
        source_family=ir.source_family,
    )


def parse_candidate_structure(display_value: str, operation: Any = None) -> StructuredRepair:
    _, compact, *_rest = _v4()
    operation = operation or {}
    new_value = lexical_value(operation, "new") or str(display_value or "")
    old_value = lexical_value(operation, "old")
    predicate = ""
    if isinstance(operation, dict):
        predicate_iri = str(operation.get("predicate_iri") or "")
        predicate = predicate_iri.rsplit("#", 1)[-1] if predicate_iri else ""
        subject_iri = str(operation.get("subject_iri") or "")
        subject = subject_iri.rsplit("#", 1)[-1] if subject_iri else ""
        operation_type = str(operation.get("operator") or "")
    else:
        subject = ""
        operation_type = ""
    assignments = parse_assignments(new_value)
    return StructuredRepair(
        subject=subject,
        predicate=predicate,
        old_value=old_value,
        new_value=new_value,
        old_status=role_from_text(old_value),
        new_status=role_from_text(new_value),
        effective_time="",
        relation_direction="FORWARD" if old_value and new_value and compact(old_value) != compact(new_value) else "UNKNOWN",
        operation_type=operation_type,
        assignments=assignments,
        raw=str(display_value or ""),
    )


def _token_overlap(left: str, right: str) -> float:
    _semantic_ir, _compact, _codes, _numbers, _salient, tokens = _v4()
    left_tokens = tokens(left)
    right_tokens = tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def _assignment_match(ir: StructuredRepair, candidate: StructuredRepair) -> float:
    if not ir.assignments or not candidate.assignments:
        return 0.0
    hits = 0
    compared = 0
    for key, value in ir.assignments.items():
        if key in candidate.assignments:
            compared += 1
            if candidate.assignments[key] == value or value in candidate.assignments[key] or candidate.assignments[key] in value:
                hits += 1
        elif value in candidate.assignments.values():
            compared += 1
            hits += 1
    if compared:
        return hits / compared
    ir_values = set(ir.assignments.values())
    cand_values = set(candidate.assignments.values())
    if not ir_values or not cand_values:
        return 0.0
    return len(ir_values & cand_values) / max(1, min(len(ir_values), len(cand_values)))


def score_structured(
    ir: StructuredRepair,
    candidate: StructuredRepair,
    semantic_type: str,
    *,
    ir_numbers: list[str] | None = None,
    ir_codes: list[str] | None = None,
    ir_source_family: str = "",
) -> dict[str, float]:
    _semantic_ir, compact, extract_codes, extract_numbers, salient_numbers, _tokens = _v4()
    ir_numbers = ir_numbers if ir_numbers is not None else list(ir.numbers)
    ir_codes = ir_codes if ir_codes is not None else list(ir.codes)
    ir_source_family = ir_source_family or ir.source_family
    value_overlap = _token_overlap(ir.new_value, candidate.new_value)
    assignment_score = _assignment_match(ir, candidate)
    number_overlap = 0.0
    cand_numbers = salient_numbers(extract_numbers(candidate.new_value))
    ir_number_set = salient_numbers(ir_numbers or extract_numbers(ir.new_value))
    if cand_numbers and ir_number_set:
        number_overlap = len(cand_numbers & ir_number_set) / max(1, len(cand_numbers))
    code_overlap = 0.0
    cand_codes = set(extract_codes(candidate.new_value))
    ir_code_set = set(ir_codes or extract_codes(ir.new_value))
    if cand_codes and ir_code_set:
        code_overlap = len(cand_codes & ir_code_set) / max(1, len(cand_codes))
    structured_value_match = max(value_overlap, assignment_score, number_overlap, code_overlap)
    if ir_source_family == "insurance_amount_split" and len(candidate.assignments) >= 2 and number_overlap:
        structured_value_match = max(structured_value_match, 0.85)

    temporal_role_match = 0.0
    contradiction_penalty = 0.0
    ir_wants_current = ir.new_status == "CURRENT" or ir.relation_direction == "FORWARD"
    if ir_wants_current and candidate.new_status == "CURRENT":
        temporal_role_match = 1.0
    elif ir_wants_current and candidate.new_status == "PREVIOUS":
        contradiction_penalty = 0.55
    elif ir.new_status == "PREVIOUS" and candidate.new_status == "PREVIOUS":
        temporal_role_match = 1.0

    relation_direction_match = 0.0
    if ir.relation_direction == "FORWARD":
        if candidate.new_status != "PREVIOUS":
            relation_direction_match = 0.7
        if any(marker in compact(candidate.new_value) for marker in ("supersed", "replac", "amend", "updated", "extends")):
            relation_direction_match = 1.0

    status_consistency = 0.0
    if candidate.old_value and compact(candidate.old_value) != compact(candidate.new_value):
        status_consistency += 0.5
    if candidate.new_status == ir.new_status and candidate.new_status != "UNSPECIFIED":
        status_consistency += 0.5
    if ir_source_family and ir_source_family in compact(candidate.new_value):
        status_consistency = min(1.0, status_consistency + 0.4)

    ontology_constraint_score = 0.0
    family = next(iter(candidate.assignments), compact(candidate.new_value.split("=", 1)[0]) if "=" in candidate.new_value else "")
    if ir_source_family and family and (family == ir_source_family or ir_source_family in family or family in ir_source_family):
        ontology_constraint_score = 1.0
    elif semantic_type == "TEMPORAL_VERSION" and any(part in family for part in ("revision", "status", "version", "effective")):
        ontology_constraint_score = 0.25

    small_token_score = _token_overlap(ir.raw, candidate.raw)
    exact_candidate_value_match = 0.0
    ir_blob = compact(" ".join([ir.raw, ir.new_value, " ".join(ir.assignments.values())]))
    candidate_blob = compact(candidate.new_value)
    if candidate_blob and (candidate_blob == ir_blob or candidate_blob in ir_blob):
        exact_candidate_value_match = 1.0

    score = (
        0.42 * exact_candidate_value_match
        + 0.26 * structured_value_match
        + 0.22 * temporal_role_match
        + 0.10 * relation_direction_match
        + 0.08 * status_consistency
        + 0.06 * ontology_constraint_score
        + 0.04 * small_token_score
        - contradiction_penalty
    )
    score = max(0.0, min(1.0, score))
    return {
        "score": round(score, 4),
        "structured_value_match": round(structured_value_match, 4),
        "temporal_role_match": round(temporal_role_match, 4),
        "relation_direction_match": round(relation_direction_match, 4),
        "status_consistency": round(status_consistency, 4),
        "ontology_constraint_score": round(ontology_constraint_score, 4),
        "small_token_score": round(small_token_score, 4),
        "exact_candidate_value_match": round(exact_candidate_value_match, 4),
        "contradiction_penalty": round(contradiction_penalty, 4),
    }


def rank_candidates(
    ir: Any,
    candidates: list[dict[str, Any]],
    semantic_type: str | None = None,
) -> list[dict[str, Any]]:
    semantic_ir_cls, *_rest = _v4()
    structured_ir = ir if isinstance(ir, StructuredRepair) else parse_ir_structure(ir)
    type_name = semantic_type
    ir_numbers: list[str] = []
    ir_codes: list[str] = []
    source_family = ""
    if isinstance(ir, semantic_ir_cls):
        type_name = type_name or ir.semantic_type
        ir_numbers = list(ir.numbers)
        ir_codes = list(ir.codes)
        source_family = ir.source_family
    type_name = type_name or "TEMPORAL_VERSION"
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        structure = parse_candidate_structure(str(candidate.get("display_value") or ""), candidate.get("operation"))
        parts = score_structured(
            structured_ir,
            structure,
            type_name,
            ir_numbers=ir_numbers,
            ir_codes=ir_codes,
            ir_source_family=source_family,
        )
        ranked.append(
            {
                **parts,
                "candidate_id": candidate.get("candidate_id", ""),
                "display_value": candidate.get("display_value", ""),
                "structure": structure,
                "new_status": structure.new_status,
                "new_value": structure.new_value,
            }
        )
    ranked.sort(key=lambda item: -float(item["score"]))
    return ranked


def select_ranked(
    ranked: list[dict[str, Any]],
    *,
    min_score: float,
    min_margin: float,
    allow_unique_top1: bool = False,
) -> tuple[dict[str, Any] | None, str, str, str]:
    if not ranked:
        return None, "ABSTAIN", "no candidates", "NO_CANDIDATES"
    ordered = sorted(ranked, key=lambda item: -float(item["score"]))
    top = ordered[0]
    second_score = float(ordered[1]["score"]) if len(ordered) > 1 else 0.0
    margin = float(top["score"]) - second_score
    if len(ordered) > 1 and float(top["score"]) == second_score:
        return None, "ABSTAIN", f"tied top scores={top['score']}", "IR_RANK_TIE_ABSTAIN"
    if float(top["score"]) >= min_score and margin >= min_margin:
        return top, "SELECTED", f"constraint rank score={top['score']} margin={margin:.4f}", "IR_CONSTRAINT_RANK"
    contradiction = float(top.get("contradiction_penalty") or 0)
    if allow_unique_top1 and contradiction <= 0:
        return (
            top,
            "SELECTED",
            f"temporal unique top-1 score={top['score']} margin={margin:.4f} contradiction={contradiction}",
            "IR_TEMPORAL_UNIQUE_TOP1",
        )
    return None, "ABSTAIN", f"no confident candidate score={top['score']} margin={margin:.4f}", "IR_RANK_ABSTAIN"
