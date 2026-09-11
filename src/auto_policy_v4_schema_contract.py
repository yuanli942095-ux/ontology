from __future__ import annotations

"""Candidate-blind schema contract validation and deterministic repair (V4.5 ablation).

Repair only reorganizes semantics already present in the model JSON. It must not read
candidate, oracle, or manual formal policy artifacts.
"""

import copy
import re
from dataclasses import dataclass, field
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_v4_css_slot import CSS_SCOPE_RELATIONS, complete_css_scope_relation

FORBIDDEN_REPAIR_KEYS = frozenset(
    {
        "candidate_id",
        "oracle_candidate_id",
        "oracle_value",
        "gold_action",
        "allowed_values",
        "manual_formal_policy",
        "correct_candidate_id",
    }
)

RULE_FIELD_ALIASES = {
    "result": "semantic_result",
    "action": "semantic_result",
    "outcome": "semantic_result",
    "change_value": "semantic_result",
    "canonical_value": "semantic_result",
    "value": "semantic_result",
}

CANONICAL_VALUE_KEYS = ("semantic_result", "change_value", "canonical_value", "value", "result")

FAILURE_CLASSES = frozenset({"C1", "C2", "C3", "C4", "C5", "C6", "C7"})
REPAIRABLE_CLASSES = frozenset({"C1", "C2", "C3", "C4"})


@dataclass
class RepairAuditEntry:
    repair_rule_id: str
    source_field: str
    target_field: str
    old_value: Any
    new_value: Any
    repair_reason: str
    deterministic: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "repair_rule_id": self.repair_rule_id,
            "source_field": self.source_field,
            "target_field": self.target_field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "repair_reason": self.repair_reason,
            "deterministic": self.deterministic,
        }


@dataclass
class ContractRepairResult:
    response: dict[str, Any] | None
    repair_applied: bool
    failure_class: str
    failure_reason: str
    schema_valid: bool
    validation_reason: str
    audit: list[RepairAuditEntry] = field(default_factory=list)
    candidate_used: bool = False
    oracle_used: bool = False
    manual_policy_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "repair_applied": self.repair_applied,
            "failure_class": self.failure_class,
            "failure_reason": self.failure_reason,
            "schema_valid": self.schema_valid,
            "validation_reason": self.validation_reason,
            "audit": [entry.as_dict() for entry in self.audit],
            "candidate_used": self.candidate_used,
            "oracle_used": self.oracle_used,
            "manual_policy_used": self.manual_policy_used,
        }


def assert_candidate_blind_context(**flags: bool) -> None:
    assert not flags.get("candidate_used", False), "contract repair must not read candidate"
    assert not flags.get("oracle_used", False), "contract repair must not read oracle"
    assert not flags.get("manual_policy_used", False), "contract repair must not read manual formal policy"


def _walk_values(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)


def contains_forbidden_repair_keys(payload: Any) -> bool:
    for mapping in _walk_values(payload):
        for key in mapping:
            if str(key).strip().lower() in FORBIDDEN_REPAIR_KEYS:
                return True
    return False


def _nonempty_scalar(value: Any) -> bool:
    return value not in (None, "", [], {}) and not isinstance(value, (dict, list))


def canonical_semantic_value(canonical: Any) -> str:
    if not isinstance(canonical, dict):
        return ""
    for key in CANONICAL_VALUE_KEYS:
        value = canonical.get(key)
        if _nonempty_scalar(value):
            return str(value).strip()
    return ""


def rule_semantic_values(rules: Any) -> list[str]:
    if not isinstance(rules, list):
        return []
    values: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        text = str(rule.get("semantic_result", "")).strip()
        if text:
            values.append(text)
    return values


def top_level_semantic_candidates(response: dict[str, Any]) -> set[str]:
    candidates: set[str] = set()
    for key in ("result", "semantic_result", "change_value", "canonical_value"):
        value = response.get(key)
        if _nonempty_scalar(value):
            candidates.add(str(value).strip())
    canonical = response.get("canonical_result")
    text = canonical_semantic_value(canonical)
    if text:
        candidates.add(text)
    candidates.update(rule_semantic_values(response.get("rules")))
    return {item for item in candidates if item}


def has_usable_semantic_payload(response: dict[str, Any]) -> bool:
    if top_level_semantic_candidates(response):
        return True
    facts = response.get("facts")
    if isinstance(facts, dict) and any(_nonempty_scalar(value) for value in facts.values()):
        return True
    canonical = response.get("canonical_result")
    return isinstance(canonical, dict) and bool(canonical)


def detect_internal_conflict(response: dict[str, Any]) -> str:
    values = sorted(top_level_semantic_candidates(response))
    rules = response.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            for alias, target in RULE_FIELD_ALIASES.items():
                alias_value = rule.get(alias)
                target_value = rule.get(target)
                if _nonempty_scalar(alias_value) and _nonempty_scalar(target_value):
                    if str(alias_value).strip() != str(target_value).strip():
                        return f"conflicting rule fields {alias}!={target}"
    explicit_scope = str(response.get("scope_relation", "")).strip().upper()
    if explicit_scope in CSS_SCOPE_RELATIONS:
        alt_scope = ""
        for mapping in _walk_values(response):
            for key in ("scope_relation", "relation"):
                value = str(mapping.get(key, "")).strip().upper()
                if value in CSS_SCOPE_RELATIONS and value != explicit_scope:
                    return f"conflicting scope_relation {explicit_scope}!={value}"
    if len(values) >= 2 and not values[0].startswith(values[1][: min(8, len(values[1]))]):
        # Different semantic_result strings across canonical/rules/top-level.
        canonical = canonical_semantic_value(response.get("canonical_result"))
        rule_values = set(rule_semantic_values(response.get("rules")))
        if canonical and rule_values and canonical not in rule_values:
            return "canonical semantic value disagrees with rules"
    return ""


def classify_contract_failure(
    response: dict[str, Any] | None,
    semantic_type: str,
    *,
    schema_valid: bool,
    validation_reason: str,
) -> tuple[str, str]:
    if schema_valid:
        return "OK", "schema_valid"
    if response is None or not isinstance(response, dict):
        return "C5", "response_not_object"
    if contains_forbidden_repair_keys(response):
        return "C7", "forbidden_repair_context"
    conflict = detect_internal_conflict(response)
    if conflict:
        return "C7", conflict
    if not has_usable_semantic_payload(response):
        return "C5", "missing_critical_semantics"

    reason = str(validation_reason or "")
    if reason == "semantic_type_mismatch":
        actual = str(response.get("semantic_type", "")).strip()
        if not actual:
            return "C3", "missing semantic_type"
        return "C7", "semantic_type_mismatch"

    alias_hits = False
    rules = response.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict) and any(key in rule for key in RULE_FIELD_ALIASES):
                alias_hits = True
                break

    canonical = response.get("canonical_result")
    canonical_value = canonical_semantic_value(canonical)
    rule_values = rule_semantic_values(rules)
    if reason in {"semantic_result_missing", "rules_missing"}:
        if alias_hits:
            return "C1", reason
        if canonical_value and not rule_values:
            return "C2", reason
        if canonical_value and len(top_level_semantic_candidates(response)) == 1:
            return "C3", reason
        if not canonical_value and not rule_values:
            return "C5", reason
        return "C6", reason

    if reason == "facts_missing":
        if isinstance(response.get("facts"), dict):
            return "C4", reason
        if any(key in response for key in ("facts", "subject_label", "predicate_label", "domain")):
            return "C2", reason
        return "C5", reason

    if reason == "canonical_result_missing":
        if isinstance(canonical, dict) and canonical:
            return "C2", reason
        return "C5", reason

    if reason in {"not_json_object"}:
        return "C5", reason

    if alias_hits:
        return "C1", reason
    if canonical_value and not rule_values:
        return "C2", reason
    return "C6", reason


def _normalize_percent(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.endswith("%"):
        try:
            return float(text[:-1].strip()) / 100.0
        except ValueError:
            return value
    return value


def _normalize_yearish(value: Any) -> Any:
    if isinstance(value, str) and re.fullmatch(r"20\d{2}", value.strip()):
        return int(value.strip())
    return value


def apply_r4_type_format_normalization(response: dict[str, Any], audit: list[RepairAuditEntry]) -> None:
    for mapping in _walk_values(response):
        for key, value in list(mapping.items()):
            if key in {"revision_year", "source_revision_year", "assessment_year", "effective_year"}:
                new_value = _normalize_yearish(value)
                if new_value != value:
                    audit.append(
                        RepairAuditEntry(
                            "R4",
                            key,
                            key,
                            value,
                            new_value,
                            "normalize year token",
                        )
                    )
                    mapping[key] = new_value
            if key.endswith("_rate") or key.endswith("_percent"):
                new_value = _normalize_percent(value)
                if new_value != value:
                    audit.append(
                        RepairAuditEntry(
                            "R4",
                            key,
                            key,
                            value,
                            new_value,
                            "normalize percent token",
                        )
                    )
                    mapping[key] = new_value


def apply_r1_field_alias_mapping(response: dict[str, Any], audit: list[RepairAuditEntry]) -> None:
    rules = response.get("rules")
    if not isinstance(rules, list):
        return
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        for alias, target in RULE_FIELD_ALIASES.items():
            if alias == target:
                continue
            alias_value = rule.get(alias)
            target_value = rule.get(target)
            if _nonempty_scalar(alias_value) and not _nonempty_scalar(target_value):
                audit.append(
                    RepairAuditEntry(
                        "R1",
                        f"rules[{index}].{alias}",
                        f"rules[{index}].{target}",
                        alias_value,
                        alias_value,
                        "field alias mapping",
                    )
                )
                rule[target] = alias_value


def apply_r2_structural_relocation(response: dict[str, Any], audit: list[RepairAuditEntry]) -> None:
    canonical = response.get("canonical_result")
    canonical_value = canonical_semantic_value(canonical)
    rules = response.get("rules")
    if not isinstance(rules, list):
        rules = []
        response["rules"] = rules

    if canonical_value:
        if not isinstance(canonical, dict):
            canonical = {}
            response["canonical_result"] = canonical
        if not canonical_value:
            pass
        elif not rule_semantic_values(rules):
            rule = {"priority": 300, "conditions": [], "semantic_result": canonical_value, "canonical_result": canonical}
            rules.append(rule)
            audit.append(
                RepairAuditEntry(
                    "R2",
                    "canonical_result",
                    "rules[0].semantic_result",
                    canonical_value,
                    canonical_value,
                    "relocate canonical semantic value into rules",
                )
            )

    top_result = response.get("result") or response.get("semantic_result")
    if _nonempty_scalar(top_result) and not rule_semantic_values(rules):
        rule = {"priority": 300, "conditions": [], "semantic_result": str(top_result).strip()}
        rules.append(rule)
        audit.append(
            RepairAuditEntry(
                "R2",
                "result",
                "rules[0].semantic_result",
                top_result,
                str(top_result).strip(),
                "relocate top-level result into rules",
            )
        )

    if not isinstance(response.get("facts"), dict):
        facts_source = {}
        for key in ("domain", "subject_label", "predicate_label", "source_title"):
            if key in response:
                facts_source[key] = response[key]
        if facts_source:
            response["facts"] = facts_source
            audit.append(
                RepairAuditEntry(
                    "R2",
                    "top_level",
                    "facts",
                    facts_source,
                    facts_source,
                    "relocate top-level metadata into facts",
                )
            )
        else:
            response["facts"] = {}
            audit.append(
                RepairAuditEntry(
                    "R2",
                    "missing",
                    "facts",
                    None,
                    {},
                    "initialize empty facts object",
                )
            )

    if not isinstance(response.get("canonical_result"), dict):
        if isinstance(canonical, dict) and canonical:
            response["canonical_result"] = canonical
        else:
            semantic = rule_semantic_values(response.get("rules"))
            if semantic:
                payload = {"semantic_result": semantic[0]}
                response["canonical_result"] = payload
                audit.append(
                    RepairAuditEntry(
                        "R2",
                        "rules[0].semantic_result",
                        "canonical_result",
                        semantic[0],
                        payload,
                        "relocate rule semantic value into canonical_result",
                    )
                )


def apply_r3_deterministic_slot_completion(
    response: dict[str, Any],
    semantic_type: str,
    audit: list[RepairAuditEntry],
) -> bool:
    if semantic_type == "CROSS_SENTENCE_SCOPE":
        explicit = str(response.get("scope_relation", "")).strip().upper()
        if explicit in CSS_SCOPE_RELATIONS:
            return True
        chosen, klass, reason = complete_css_scope_relation(response, {})
        if klass == "S1" and chosen:
            response["scope_relation"] = chosen
            audit.append(
                RepairAuditEntry(
                    "R3",
                    "payload_votes",
                    "scope_relation",
                    None,
                    chosen,
                    reason,
                )
            )
            return True
        if klass == "S2":
            return False
    if not str(response.get("semantic_type", "")).strip():
        response["semantic_type"] = semantic_type
        audit.append(
            RepairAuditEntry(
                "R3",
                "missing",
                "semantic_type",
                None,
                semantic_type,
                "fill semantic_type from event metadata",
            )
        )
    return True


def apply_schema_contract_repair(
    response: dict[str, Any] | None,
    semantic_type: str,
    *,
    candidate_used: bool = False,
    oracle_used: bool = False,
    manual_policy_used: bool = False,
) -> ContractRepairResult:
    assert_candidate_blind_context(
        candidate_used=candidate_used,
        oracle_used=oracle_used,
        manual_policy_used=manual_policy_used,
    )
    if response is None or not isinstance(response, dict):
        return ContractRepairResult(
            response=None,
            repair_applied=False,
            failure_class="C5",
            failure_reason="response_not_object",
            schema_valid=False,
            validation_reason="not_json_object",
        )

    working = copy.deepcopy(response)
    schema_valid, validation_reason = v3.validate_generated_policy(working, semantic_type)
    failure_class, failure_reason = classify_contract_failure(
        working,
        semantic_type,
        schema_valid=schema_valid,
        validation_reason=validation_reason,
    )
    if schema_valid:
        return ContractRepairResult(
            response=working,
            repair_applied=False,
            failure_class="OK",
            failure_reason="schema_valid",
            schema_valid=True,
            validation_reason=validation_reason,
        )
    if failure_class not in REPAIRABLE_CLASSES:
        return ContractRepairResult(
            response=working,
            repair_applied=False,
            failure_class=failure_class,
            failure_reason=failure_reason,
            schema_valid=False,
            validation_reason=validation_reason,
        )

    audit: list[RepairAuditEntry] = []
    apply_r4_type_format_normalization(working, audit)
    apply_r1_field_alias_mapping(working, audit)
    apply_r2_structural_relocation(working, audit)
    if not apply_r3_deterministic_slot_completion(working, semantic_type, audit):
        schema_valid, validation_reason = v3.validate_generated_policy(working, semantic_type)
        return ContractRepairResult(
            response=working,
            repair_applied=bool(audit),
            failure_class="C6",
            failure_reason="ambiguous deterministic completion",
            schema_valid=schema_valid,
            validation_reason=validation_reason,
            audit=audit,
        )

    schema_valid, validation_reason = v3.validate_generated_policy(working, semantic_type)
    failure_class, failure_reason = classify_contract_failure(
        working,
        semantic_type,
        schema_valid=schema_valid,
        validation_reason=validation_reason,
    )
    return ContractRepairResult(
        response=working,
        repair_applied=bool(audit),
        failure_class=failure_class if not schema_valid else "OK",
        failure_reason=failure_reason if not schema_valid else "schema_valid",
        schema_valid=schema_valid,
        validation_reason=validation_reason,
        audit=audit,
    )


def prepare_policy_response(
    response: dict[str, Any] | None,
    event: dict[str, str],
    evidence: str,
    *,
    apply_contract_repair: bool,
    candidate_used: bool = False,
    oracle_used: bool = False,
    manual_policy_used: bool = False,
) -> tuple[dict[str, Any] | None, str, str, bool, str, ContractRepairResult | None]:
    """Return parsed policy, canonical_status, canonical_result_json, schema_valid, validation_reason, repair_meta."""
    semantic_type = event["semantic_type"].strip()
    repair_meta: ContractRepairResult | None = None
    parsed = copy.deepcopy(response) if isinstance(response, dict) else response

    if apply_contract_repair and isinstance(parsed, dict):
        repair_meta = apply_schema_contract_repair(
            parsed,
            semantic_type,
            candidate_used=candidate_used,
            oracle_used=oracle_used,
            manual_policy_used=manual_policy_used,
        )
        parsed = repair_meta.response

    event_for_normalize = {**event, "semantic_type": semantic_type}
    parsed, canonical_status, canonical_result, _canonical_semantic = v3.normalize_generated_policy(
        parsed,
        event_for_normalize,
        evidence,
    )
    schema_valid, validation_reason = v3.validate_generated_policy(parsed, semantic_type)
    return parsed, canonical_status, canonical_result, schema_valid, validation_reason, repair_meta
