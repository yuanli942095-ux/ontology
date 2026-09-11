from __future__ import annotations

"""Task formulation layer: route benchmark semantic_type to evidence-aligned extraction modes."""

import copy
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from auto_policy_m12_decomposed_extraction import (
    DerivationAudit,
    derive_gre,
    normalize_token,
    nonempty,
)

METHOD_NAME = "M12_TASK_FORMULATION_EXTRACTION"

EXCEPTION_CUE_RE = re.compile(
    r"\b(unless|except(?:\s+when|\s+that)?|subject\s+to|notwithstanding|provided\s+that)\b",
    re.IGNORECASE,
)

NORMATIVE_PREDICATE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"tool\s+status",
        r"profile\s+scope",
        r"^scope$",
        r"change\s+basis",
        r"resource\s+status",
        r"versioned\s+requirement",
        r"revision\s+change",
        r"input\s+modality",
        r"mobile\s+application\s+guidance",
        r"living[\s-]?tool",
        r"stakeholder\s+response",
        r"mapping\s+spreadsheet",
    )
)

PREDICATE_VALUE_SCOPE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^scope$",
        r"disclosure\s+topic",
        r"topic",
        r"cash[\s-]?flow",
    )
)

# Fixed public predicate→dimension map (from benchmark metadata, not repair candidates).
PUBLIC_PREDICATE_DIMENSION_MAP: dict[str, str] = {
    "tool status": "tool",
    "profile scope": "scope",
    "scope": "scope",
    "change basis": "basis",
    "resource status": "resource",
    "disclosure topic": "topic",
}


class ExtractionMode(str, Enum):
    TEMPORAL_CHANGE = "TEMPORAL_CHANGE"
    RULE_EXCEPTION = "RULE_EXCEPTION"
    NORMATIVE_ATTRIBUTE = "NORMATIVE_ATTRIBUTE"
    SCOPE_ATTACHMENT = "SCOPE_ATTACHMENT"
    PREDICATE_VALUE_SCOPE = "PREDICATE_VALUE_SCOPE"


@dataclass(frozen=True)
class TaskFormulation:
    benchmark_semantic_type: str
    extraction_mode: ExtractionMode
    route_reason: str
    predicate_label: str
    subject_label: str
    repair_dimension: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "benchmark_semantic_type": self.benchmark_semantic_type,
            "extraction_mode": self.extraction_mode.value,
            "route_reason": self.route_reason,
            "predicate_label": self.predicate_label,
            "subject_label": self.subject_label,
            "repair_dimension": self.repair_dimension,
        }


def infer_repair_dimension(predicate_label: str) -> str:
    key = str(predicate_label or "").strip().lower()
    if key in PUBLIC_PREDICATE_DIMENSION_MAP:
        return PUBLIC_PREDICATE_DIMENSION_MAP[key]
    for pattern, dimension in PUBLIC_PREDICATE_DIMENSION_MAP.items():
        if pattern in key:
            return dimension
    return normalize_token(key.split()[0] if key else "value")


def predicate_matches(patterns: tuple[re.Pattern[str], ...], predicate_label: str) -> bool:
    text = str(predicate_label or "").strip()
    return any(pattern.search(text) for pattern in patterns)


def route_task_formulation(
    event: dict[str, str],
    evidence: str,
) -> TaskFormulation:
    semantic_type = str(event.get("semantic_type", "")).strip()
    predicate_label = str(event.get("predicate_label", "")).strip()
    subject_label = str(event.get("subject_label", "")).strip()
    has_exception_cue = bool(EXCEPTION_CUE_RE.search(evidence or ""))
    normative_predicate = predicate_matches(NORMATIVE_PREDICATE_PATTERNS, predicate_label)

    if semantic_type == "TEMPORAL_VERSION":
        return TaskFormulation(semantic_type, ExtractionMode.TEMPORAL_CHANGE, "temporal_benchmark_type", predicate_label, subject_label)

    if semantic_type == "GENERAL_RULE_EXCEPTION":
        if normative_predicate:
            return TaskFormulation(
                semantic_type,
                ExtractionMode.NORMATIVE_ATTRIBUTE,
                "predicate_normative_attribute",
                predicate_label,
                subject_label,
                infer_repair_dimension(predicate_label),
            )
        if has_exception_cue:
            return TaskFormulation(
                semantic_type,
                ExtractionMode.RULE_EXCEPTION,
                "evidence_exception_cue",
                predicate_label,
                subject_label,
            )
        return TaskFormulation(
            semantic_type,
            ExtractionMode.RULE_EXCEPTION,
            "default_true_exception",
            predicate_label,
            subject_label,
        )

    if predicate_matches(PREDICATE_VALUE_SCOPE_PATTERNS, predicate_label):
        dimension = infer_repair_dimension(predicate_label)
        return TaskFormulation(
            semantic_type,
            ExtractionMode.PREDICATE_VALUE_SCOPE,
            "predicate_value_focus",
            predicate_label,
            subject_label,
            dimension,
        )
    return TaskFormulation(
        semantic_type,
        ExtractionMode.SCOPE_ATTACHMENT,
        "cross_sentence_attachment",
        predicate_label,
        subject_label,
    )


def ontology_normalize_value(text: str) -> str:
    """Evidence-grounded token normalization only; no candidate/oracle vocabulary."""
    return normalize_token(text)


def atomic_schema_for_mode(mode: ExtractionMode) -> dict[str, Any]:
    if mode is ExtractionMode.TEMPORAL_CHANGE:
        return {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "previous_value": {"type": "string"},
                "current_value": {"type": "string"},
                "effective_time": {"type": "string"},
                "change_relation": {"type": "string"},
                "previous_still_valid": {"type": ["boolean", "null"]},
                "evidence_spans": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "subject",
                "previous_value",
                "current_value",
                "effective_time",
                "change_relation",
                "previous_still_valid",
                "evidence_spans",
            ],
            "additionalProperties": False,
        }
    if mode is ExtractionMode.RULE_EXCEPTION:
        return {
            "type": "object",
            "properties": {
                "general_rule": {"type": "string"},
                "exception_condition": {"type": "string"},
                "exception_rule": {"type": "string"},
                "condition_satisfied": {"type": ["boolean", "null"]},
                "priority_cue": {"type": "string"},
                "evidence_spans": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "general_rule",
                "exception_condition",
                "exception_rule",
                "condition_satisfied",
                "priority_cue",
                "evidence_spans",
            ],
            "additionalProperties": False,
        }
    if mode is ExtractionMode.NORMATIVE_ATTRIBUTE:
        return {
            "type": "object",
            "properties": {
                "subject_entity": {"type": "string"},
                "attribute_name": {"type": "string"},
                "stated_value": {"type": "string"},
                "evidence_spans": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["subject_entity", "attribute_name", "stated_value", "evidence_spans"],
            "additionalProperties": False,
        }
    if mode is ExtractionMode.PREDICATE_VALUE_SCOPE:
        return {
            "type": "object",
            "properties": {
                "scope_semantics": {
                    "type": "object",
                    "properties": {
                        "qualifier": {"type": "string"},
                        "scope_target": {"type": "string"},
                        "relation": {"type": "string"},
                        "ambiguity": {"type": ["boolean", "null"]},
                    },
                    "required": ["qualifier", "scope_target", "relation", "ambiguity"],
                    "additionalProperties": False,
                },
                "repair_semantics": {
                    "type": "object",
                    "properties": {
                        "dimension": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["dimension", "value"],
                    "additionalProperties": False,
                },
                "evidence_spans": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["scope_semantics", "repair_semantics", "evidence_spans"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": {
            "qualifier": {"type": "string"},
            "scope_target": {"type": "string"},
            "scope_relation_cue": {"type": "string"},
            "alternative_target": {"type": "string"},
            "ambiguous": {"type": ["boolean", "null"]},
            "evidence_spans": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "qualifier",
            "scope_target",
            "scope_relation_cue",
            "alternative_target",
            "ambiguous",
            "evidence_spans",
        ],
        "additionalProperties": False,
    }


def required_slots_for_mode(mode: ExtractionMode, *, relaxed_css_scope: bool = False) -> list[str]:
    schema = atomic_schema_for_mode(mode)
    if mode is ExtractionMode.PREDICATE_VALUE_SCOPE:
        if relaxed_css_scope:
            return [
                "repair_semantics.dimension",
                "repair_semantics.value",
                "evidence_spans",
            ]
        return [
            "scope_semantics.qualifier",
            "scope_semantics.scope_target",
            "scope_semantics.relation",
            "scope_semantics.ambiguity",
            "repair_semantics.dimension",
            "repair_semantics.value",
            "evidence_spans",
        ]
    return list(schema.get("required", []))


def slot_filled(facts: dict[str, Any], slot: str) -> bool:
    if "." not in slot:
        value = facts.get(slot)
        if slot == "evidence_spans":
            return isinstance(value, list) and len(value) > 0
        if slot == "ambiguous" or slot.endswith(".ambiguity"):
            return value is not None
        return nonempty(value)
    head, tail = slot.split(".", 1)
    nested = facts.get(head)
    if not isinstance(nested, dict):
        return False
    return slot_filled(nested, tail)


def audit_atomic_slots(
    mode: ExtractionMode,
    facts: dict[str, Any] | None,
    *,
    relaxed_css_scope: bool = False,
) -> tuple[bool, list[str], list[str], list[str]]:
    required = required_slots_for_mode(mode, relaxed_css_scope=relaxed_css_scope)
    if not isinstance(facts, dict):
        return False, required, [], required
    filled = [slot for slot in required if slot_filled(facts, slot)]
    missing = [slot for slot in required if slot not in filled]
    return not missing, required, filled, missing


def build_atomic_prompt(
    event: dict[str, str],
    evidence: str,
    formulation: TaskFormulation,
) -> str:
    common = f"""
You are extracting atomic facts only from public regulatory evidence.

CRITICAL:
- Do NOT determine or output semantic_result.
- Do NOT output rules, canonical_result, candidate ids, oracle values, or repair decisions.
- Only extract the requested atomic facts supported by the evidence.
- If a field is unsupported, use an empty string or null as appropriate.

event_id: {event.get("event_id", "")}
domain: {event.get("domain", "")}
benchmark_semantic_type: {formulation.benchmark_semantic_type}
extraction_mode: {formulation.extraction_mode.value}
subject_label: {formulation.subject_label}
predicate_label: {formulation.predicate_label}
case_context: {event.get("case_context", "")}

===== PUBLIC EVIDENCE BEGIN =====
{evidence}
===== PUBLIC EVIDENCE END =====
""".strip()

    if formulation.extraction_mode is ExtractionMode.NORMATIVE_ATTRIBUTE:
        questions = f"""
This is a normative attribute extraction task (not a rule-exception task).
Focus on predicate_label="{formulation.predicate_label}" for subject_label="{formulation.subject_label}".
The stated_value must be a concise evidence-grounded phrase:
- for "change basis", use the reason after cues such as "in response to", "because", or "desire for";
- for "resource status", use availability/repository/webpage/mapping-spreadsheet status stated by the evidence;
- for "scope" or "profile scope", use the phrase that states what the tool/profile/framework is intended to cover or help with.

Answer only these atomic questions in JSON:
1. subject_entity: the entity described in the evidence
2. attribute_name: the attribute being described (use predicate_label when supported)
3. stated_value: the value stated in evidence for that attribute
4. evidence_spans: short quoted spans supporting the above
""".strip()
    elif formulation.extraction_mode is ExtractionMode.RULE_EXCEPTION:
        questions = """
This is a true rule-exception task. Answer only these atomic questions in JSON:
1. general_rule: baseline/general requirement
2. exception_condition: unless/except/subject-to condition
3. exception_rule: what happens when the exception applies
4. condition_satisfied: does case_context satisfy the exception condition? (true/false/null)
5. priority_cue: unless/except/subject to signals in text
6. evidence_spans: short quoted spans
""".strip()
    elif formulation.extraction_mode is ExtractionMode.PREDICATE_VALUE_SCOPE:
        questions = f"""
This is a predicate-focused scope task. Extract both linguistic scope and repair-oriented semantics.
predicate_label="{formulation.predicate_label}" defines the repair dimension focus.
For disclosure topic, use the noun phrase after "disclose information about/on" as repair_semantics.value.
For scope, ignore GHG "Scope 1/2/3" numbering unless the subject itself is greenhouse-gas scope.
Use empty strings only when no supplied span directly states the value.

Answer only this JSON structure:
- scope_semantics.qualifier: limiting phrase if any, else empty string
- scope_semantics.scope_target: sentence/clause/requirement the qualifier applies to
- scope_semantics.relation: applies_to / exception / remains_valid / added / other
- scope_semantics.ambiguity: true if multiple targets are equally plausible, else false/null
- repair_semantics.dimension: public attribute dimension for predicate_label (e.g. scope, topic)
- repair_semantics.value: evidence-grounded normalized value phrase for that dimension
- evidence_spans: short quoted spans
""".strip()
    elif formulation.extraction_mode is ExtractionMode.SCOPE_ATTACHMENT:
        questions = """
Answer only these atomic questions in JSON:
1. qualifier: limiting phrase
2. scope_target: sentence/clause/requirement the qualifier applies to
3. scope_relation_cue: applies_to / exception / remains_valid / added / other
4. alternative_target: another plausible target if any, else empty string
5. ambiguous: true if multiple targets are equally plausible, else false/null
6. evidence_spans: short quoted spans
""".strip()
    else:
        questions = """
Answer only these atomic questions in JSON:
1. subject entity
2. previous version/state
3. current/effective version/state
4. effective time
5. change relation (replace / supersede / amend / effective / other)
6. previous_still_valid (true/false/null)
7. evidence_spans
""".strip()
    return f"{common}\n\n{questions}"


def derive_normative_attribute(facts: dict[str, Any], formulation: TaskFormulation) -> DerivationAudit:
    slots = copy.deepcopy(facts)
    dimension = infer_repair_dimension(str(facts.get("attribute_name") or formulation.predicate_label))
    value_raw = str(facts.get("stated_value", "")).strip()
    if not value_raw:
        return DerivationAudit("N1", slots, "", "ABSTAIN", "missing stated_value", failure_layer="D1")
    value = ontology_normalize_value(value_raw)
    if not value:
        return DerivationAudit("N1", slots, "", "ABSTAIN", "empty normalized value", failure_layer="D5")
    derived = f"{dimension}={value}"
    return DerivationAudit("N1", slots, derived, "DERIVED")


def derive_predicate_value_scope(facts: dict[str, Any], formulation: TaskFormulation) -> DerivationAudit:
    slots = copy.deepcopy(facts)
    repair = facts.get("repair_semantics") if isinstance(facts.get("repair_semantics"), dict) else {}
    scope = facts.get("scope_semantics") if isinstance(facts.get("scope_semantics"), dict) else {}
    dimension = str(repair.get("dimension") or formulation.repair_dimension or infer_repair_dimension(formulation.predicate_label)).strip()
    value_raw = str(repair.get("value") or scope.get("scope_target") or "").strip()
    if scope.get("ambiguity") is True:
        return DerivationAudit("P1", slots, "", "ABSTAIN", "ambiguous scope target", failure_layer="D3")
    if not value_raw:
        return DerivationAudit("P1", slots, "", "ABSTAIN", "missing repair value", failure_layer="D1")
    value = ontology_normalize_value(value_raw)
    if not value:
        return DerivationAudit("P1", slots, "", "ABSTAIN", "empty normalized value", failure_layer="D5")
    derived = f"{dimension}={value}"
    return DerivationAudit("P1", slots, derived, "DERIVED")


def symbolic_derive_for_mode(
    mode: ExtractionMode,
    facts: dict[str, Any],
    event: dict[str, str],
    formulation: TaskFormulation,
) -> DerivationAudit:
    from auto_policy_m12_decomposed_extraction import derive_css, derive_temporal

    if mode is ExtractionMode.TEMPORAL_CHANGE:
        return derive_temporal(facts)
    if mode is ExtractionMode.RULE_EXCEPTION:
        return derive_gre(facts, event)
    if mode is ExtractionMode.NORMATIVE_ATTRIBUTE:
        return derive_normative_attribute(facts, formulation)
    if mode is ExtractionMode.PREDICATE_VALUE_SCOPE:
        return derive_predicate_value_scope(facts, formulation)
    return derive_css(facts)


def build_policy_from_task_derivation(
    event: dict[str, str],
    facts: dict[str, Any],
    derivation: DerivationAudit,
    formulation: TaskFormulation,
) -> dict[str, Any] | None:
    from auto_policy_m12_decomposed_extraction import SCOPE_RELATION_MAP, TEMPORAL_RELATION_MAP, normalize_token

    if derivation.derivation_status != "DERIVED" or not derivation.derived_result:
        return None
    semantic_type = formulation.benchmark_semantic_type
    semantic_result = derivation.derived_result
    facts_payload: dict[str, Any] = {
        **facts,
        "task_formulation": formulation.as_dict(),
        "decomposed_atomic_facts": True,
    }
    canonical: dict[str, Any] = {
        "family": "task_formulation_derivation",
        "symbolic_rule_id": derivation.symbolic_rule_id,
        "derived_semantic_result": semantic_result,
        "extraction_mode": formulation.extraction_mode.value,
    }
    if formulation.extraction_mode is ExtractionMode.TEMPORAL_CHANGE:
        relation_raw = normalize_token(str(facts.get("change_relation", "")))
        for key, canonical_rel in TEMPORAL_RELATION_MAP.items():
            if key in relation_raw:
                facts_payload["relation"] = canonical_rel
                canonical["relation"] = canonical_rel
                break
    if formulation.extraction_mode in {ExtractionMode.SCOPE_ATTACHMENT, ExtractionMode.PREDICATE_VALUE_SCOPE}:
        relation_raw = ""
        if formulation.extraction_mode is ExtractionMode.PREDICATE_VALUE_SCOPE:
            scope = facts.get("scope_semantics") if isinstance(facts.get("scope_semantics"), dict) else {}
            relation_raw = normalize_token(str(scope.get("relation", "")))
        else:
            relation_raw = normalize_token(str(facts.get("scope_relation_cue", "")))
        for key, canonical_rel in SCOPE_RELATION_MAP.items():
            if key in relation_raw:
                facts_payload["scope_relation"] = canonical_rel
                canonical["scope_relation"] = canonical_rel
                break
        if formulation.extraction_mode is ExtractionMode.PREDICATE_VALUE_SCOPE:
            repair = facts.get("repair_semantics") if isinstance(facts.get("repair_semantics"), dict) else {}
            facts_payload["repair_dimension"] = repair.get("dimension", formulation.repair_dimension)
            facts_payload["repair_value"] = repair.get("value", "")
    if formulation.extraction_mode is ExtractionMode.NORMATIVE_ATTRIBUTE:
        facts_payload["attribute_dimension"] = infer_repair_dimension(
            str(facts.get("attribute_name") or formulation.predicate_label)
        )
    return {
        "semantic_type": semantic_type,
        "facts": facts_payload,
        "canonical_result": canonical,
        "rules": [
            {
                "priority": 300,
                "conditions": [],
                "canonical_result": canonical,
                "semantic_result": semantic_result,
            }
        ],
        "task_formulated": True,
    }
