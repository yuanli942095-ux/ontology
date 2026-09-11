from __future__ import annotations

"""M1/M2 decomposed semantic extraction: atomic facts + candidate-blind symbolic derivation."""

from enum import Enum
import copy
import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any
import run_auto_formal_policy_batch_v3 as v3
from auto_policy_v45_generation import ReliabilityMode, call_qwen_reliable
from run_auto_policy_v4_ir_candidate_repair import parse_semantic_ir

METHOD_NAME = "M12_DECOMPOSED_SEMANTIC_EXTRACTION"
NUM_PREDICT = 1200
NUM_CTX = 16384


class ExtractionBackend(str, Enum):
    QWEN = "qwen3.5:9b"
    DEEPSEEK = "deepseek_api"

TEMPORAL_RELATION_MAP = {
    "replace": "REPLACES",
    "replaces": "REPLACES",
    "replacement": "REPLACES",
    "supersede": "SUPERSEDES",
    "supersedes": "SUPERSEDES",
    "amend": "AMENDS",
    "amends": "AMENDS",
    "effective": "EFFECTIVE_FROM",
    "effective_from": "EFFECTIVE_FROM",
    "revised": "EFFECTIVE_FROM",
    "update": "EFFECTIVE_FROM",
}

SCOPE_RELATION_MAP = {
    "applies_to": "APPLIES_TO",
    "apply": "APPLIES_TO",
    "scope": "APPLIES_TO",
    "exception": "EXCEPTION_OVERRIDES",
    "except": "EXCEPTION_OVERRIDES",
    "unless": "EXCEPTION_OVERRIDES",
    "remains_valid": "REMAINS_VALID",
    "still_valid": "REMAINS_VALID",
    "added": "ADDED",
}


@dataclass
class DerivationAudit:
    symbolic_rule_id: str
    input_slots: dict[str, Any]
    derived_result: str
    derivation_status: str
    abstain_reason: str = ""
    candidate_used: bool = False
    oracle_used: bool = False
    manual_policy_used: bool = False
    failure_layer: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbolic_rule_id": self.symbolic_rule_id,
            "input_slots": self.input_slots,
            "derived_result": self.derived_result,
            "derivation_status": self.derivation_status,
            "abstain_reason": self.abstain_reason,
            "candidate_used": self.candidate_used,
            "oracle_used": self.oracle_used,
            "manual_policy_used": self.manual_policy_used,
            "failure_layer": self.failure_layer,
        }


@dataclass
class DecompositionResult:
    triggered: bool
    trigger_reason: str
    atomic_facts: dict[str, Any]
    atomic_complete: bool
    derivation: DerivationAudit | None
    policy_response: dict[str, Any] | None
    extraction_status: str
    runtime_ms: int = 0
    eval_count: int = 0
    generation_call_count: int = 0
    extraction_backend: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_id: str = ""
    required_slots: list[str] = field(default_factory=list)
    filled_slots: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)


def assert_candidate_blind(**flags: bool) -> None:
    assert not flags.get("candidate_used", False)
    assert not flags.get("oracle_used", False)
    assert not flags.get("manual_policy_used", False)


def nonempty(value: Any) -> bool:
    return value not in (None, "", [], {}) and not (isinstance(value, str) and not str(value).strip())


def normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


def atomic_schema(semantic_type: str) -> dict[str, Any]:
    if semantic_type == "TEMPORAL_VERSION":
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
    if semantic_type == "GENERAL_RULE_EXCEPTION":
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


def build_atomic_prompt(event: dict[str, str], evidence: str, semantic_type: str) -> str:
    common = f"""
You are extracting atomic facts only from public regulatory evidence.

CRITICAL:
- Do NOT determine or output semantic_result.
- Do NOT output rules, canonical_result, candidate ids, oracle values, or repair decisions.
- Only extract the requested atomic facts supported by the evidence.
- If a field is unsupported, use an empty string or null as appropriate.

event_id: {event.get("event_id", "")}
domain: {event.get("domain", "")}
semantic_type: {semantic_type}
subject_label: {event.get("subject_label", "")}
predicate_label: {event.get("predicate_label", "")}
case_context: {event.get("case_context", "")}

===== PUBLIC EVIDENCE BEGIN =====
{evidence}
===== PUBLIC EVIDENCE END =====
""".strip()

    if semantic_type == "TEMPORAL_VERSION":
        questions = """
Answer only these atomic questions in JSON:
1. What is the subject entity?
2. What previous version/state is described?
3. What current/effective version/state is described?
4. When does the change become effective?
5. What relation is expressed (replace / supersede / amend / effective / other)?
6. Is the previous version still valid? (true/false/null)
7. evidence_spans: short quoted spans supporting the above.
""".strip()
    elif semantic_type == "GENERAL_RULE_EXCEPTION":
        questions = """
Answer only these atomic questions in JSON:
1. general_rule: baseline/general requirement
2. exception_condition: unless/except/subject-to condition
3. exception_rule: what happens when the exception applies
4. condition_satisfied: does case_context satisfy the exception condition? (true/false/null)
5. priority_cue: unless/except/subject to signals in text
6. evidence_spans: short quoted spans
""".strip()
    else:
        questions = """
Answer only these atomic questions in JSON:
1. qualifier: limiting phrase
2. scope_target: sentence/clause/requirement the qualifier applies to
3. scope_relation_cue: applies_to / exception / remains_valid / added / other
4. alternative_target: another plausible target if any, else empty string
5. ambiguous: true if multiple targets are equally plausible, else false/null
6. evidence_spans: short quoted spans
""".strip()
    return f"{common}\n\n{questions}"


def call_atomic_extraction_qwen(
    prompt: str,
    seed: int,
    semantic_type: str,
    *,
    timeout: int | None = None,
) -> tuple[dict[str, Any] | None, int, int, int, int, int]:
    schema = atomic_schema(semantic_type)
    payload: dict[str, Any] = {
        "model": v3.MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": schema,
        "options": {
            "temperature": v3.TEMPERATURE,
            "num_predict": NUM_PREDICT,
            "num_ctx": NUM_CTX,
            "seed": seed,
        },
    }
    request = urllib.request.Request(
        v3.OLLAMA_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout or v3.TIMEOUT) as response:
        raw = json.loads(response.read().decode("utf-8"))
    runtime_ms = int((time.perf_counter() - start) * 1000)
    text = str(raw.get("response", ""))
    facts = v3.extract_json(text)
    calls = 1
    if not isinstance(facts, dict):
        call = call_qwen_reliable(prompt, seed, ReliabilityMode.STRUCTURED_OUTPUT, timeout=timeout)
        facts = v3.extract_json(str(call.response.get("response", "")))
        runtime_ms += call.runtime_ms
        calls = 2
    if not isinstance(facts, dict):
        return None, runtime_ms, int(raw.get("eval_count", 0) or 0), calls, 0, 0
    return facts, runtime_ms, int(raw.get("eval_count", 0) or 0), calls, 0, 0


def call_atomic_extraction(
    prompt: str,
    seed: int,
    semantic_type: str,
    *,
    backend: ExtractionBackend = ExtractionBackend.QWEN,
    timeout: int | None = None,
) -> tuple[dict[str, Any] | None, int, int, int, int, int, str]:
    if backend is ExtractionBackend.QWEN:
        facts, runtime_ms, eval_count, calls, prompt_tokens, completion_tokens = call_atomic_extraction_qwen(
            prompt,
            seed,
            semantic_type,
            timeout=timeout,
        )
        return facts, runtime_ms, eval_count, calls, prompt_tokens, completion_tokens, v3.MODEL
    from auto_policy_m12_deepseek_client import call_deepseek_atomic_extraction

    facts, api_result = call_deepseek_atomic_extraction(prompt, semantic_type)
    return (
        facts,
        api_result.runtime_ms,
        api_result.completion_tokens,
        1,
        api_result.prompt_tokens,
        api_result.completion_tokens,
        api_result.model,
    )


def attach_partial_extraction_audit(
    record: dict[str, Any],
    facts: dict[str, Any],
    *,
    required_slots: list[str],
    filled_slots: list[str],
    missing_slots: list[str],
    atomic_complete: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "required_slots": required_slots,
        "filled_slots": filled_slots,
        "missing_slots": missing_slots,
        "atomic_complete": atomic_complete,
    }
    if extra:
        audit.update(extra)
    if facts:
        audit["partial_atomic_facts"] = facts
    out = {**record, "m12_extraction_audit": audit}
    if facts:
        out["m12_partial_atomic_facts"] = facts
    return out


def atomic_complete(semantic_type: str, facts: dict[str, Any]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if semantic_type == "TEMPORAL_VERSION":
        for key in ("current_value", "change_relation"):
            if not nonempty(facts.get(key)):
                missing.append(key)
    elif semantic_type == "GENERAL_RULE_EXCEPTION":
        for key in ("general_rule", "exception_condition", "exception_rule"):
            if not nonempty(facts.get(key)):
                missing.append(key)
        if facts.get("condition_satisfied") is None:
            missing.append("condition_satisfied")
    else:
        for key in ("qualifier", "scope_target", "scope_relation_cue"):
            if not nonempty(facts.get(key)):
                missing.append(key)
        if facts.get("ambiguous") is None:
            missing.append("ambiguous")
    return not missing, missing


def slot_audit_for_semantic_type(semantic_type: str, facts: dict[str, Any] | None) -> tuple[bool, list[str], list[str], list[str]]:
    if not isinstance(facts, dict):
        if semantic_type == "TEMPORAL_VERSION":
            required = ["current_value", "change_relation"]
        elif semantic_type == "GENERAL_RULE_EXCEPTION":
            required = ["general_rule", "exception_condition", "exception_rule", "condition_satisfied"]
        else:
            required = ["qualifier", "scope_target", "scope_relation_cue", "ambiguous"]
        return False, required, [], required
    complete, missing = atomic_complete(semantic_type, facts)
    if semantic_type == "TEMPORAL_VERSION":
        required = ["current_value", "change_relation"]
    elif semantic_type == "GENERAL_RULE_EXCEPTION":
        required = ["general_rule", "exception_condition", "exception_rule", "condition_satisfied"]
    else:
        required = ["qualifier", "scope_target", "scope_relation_cue", "ambiguous"]
    filled = [slot for slot in required if slot not in missing]
    return complete, required, filled, missing


def derive_temporal(facts: dict[str, Any]) -> DerivationAudit:
    slots = copy.deepcopy(facts)
    relation_raw = normalize_token(str(facts.get("change_relation", "")))
    relation = "UNKNOWN"
    for key, canonical in TEMPORAL_RELATION_MAP.items():
        if key in relation_raw:
            relation = canonical
            break
    current = str(facts.get("current_value", "")).strip()
    previous = str(facts.get("previous_value", "")).strip()
    still_valid = facts.get("previous_still_valid")
    if not current:
        return DerivationAudit("T1", slots, "", "ABSTAIN", "missing current_value", failure_layer="D1")
    if relation == "UNKNOWN":
        return DerivationAudit("T1", slots, "", "ABSTAIN", "ambiguous change_relation", failure_layer="D3")
    if still_valid is True and relation in {"REPLACES", "SUPERSEDES"}:
        return DerivationAudit("T1", slots, "", "ABSTAIN", "conflicting validity cues", failure_layer="D2")
    parts = [normalize_token(current)]
    if previous:
        parts.insert(0, normalize_token(previous))
    derived = f"{relation.lower()}:{':'.join(part for part in parts if part)}"
    return DerivationAudit("T1", slots, derived, "DERIVED", failure_layer="")


def derive_gre(facts: dict[str, Any], event: dict[str, str]) -> DerivationAudit:
    slots = copy.deepcopy(facts)
    satisfied = facts.get("condition_satisfied")
    general = str(facts.get("general_rule", "")).strip()
    exception_rule = str(facts.get("exception_rule", "")).strip()
    if satisfied is True:
        if not exception_rule:
            return DerivationAudit("G1", slots, "", "ABSTAIN", "missing exception_rule", failure_layer="D1")
        derived = f"EXCEPTION_OVERRIDES:{normalize_token(exception_rule)}"
        return DerivationAudit("G1", slots, derived, "DERIVED")
    if satisfied is False:
        if not general:
            return DerivationAudit("G2", slots, "", "ABSTAIN", "missing general_rule", failure_layer="D1")
        derived = f"GENERAL_RULE:{normalize_token(general)}"
        return DerivationAudit("G2", slots, derived, "DERIVED")
    return DerivationAudit("G3", slots, "", "ABSTAIN", "condition_satisfied unknown", failure_layer="D3")


def derive_css(facts: dict[str, Any]) -> DerivationAudit:
    slots = copy.deepcopy(facts)
    if facts.get("ambiguous") is True:
        return DerivationAudit("C1", slots, "", "ABSTAIN", "ambiguous scope target", failure_layer="D3")
    if nonempty(facts.get("alternative_target")) and facts.get("ambiguous") is not False:
        return DerivationAudit("C1", slots, "", "ABSTAIN", "alternative target present", failure_layer="D3")
    target = str(facts.get("scope_target", "")).strip()
    cue = normalize_token(str(facts.get("scope_relation_cue", "")))
    relation = "UNKNOWN"
    for key, canonical in SCOPE_RELATION_MAP.items():
        if key in cue:
            relation = canonical
            break
    if not target:
        return DerivationAudit("C2", slots, "", "ABSTAIN", "missing scope_target", failure_layer="D1")
    if relation == "UNKNOWN":
        return DerivationAudit("C2", slots, "", "ABSTAIN", "ambiguous scope_relation_cue", failure_layer="D3")
    qualifier = normalize_token(str(facts.get("qualifier", "")))
    derived = f"{relation}:{normalize_token(target)}" + (f":{qualifier}" if qualifier else "")
    return DerivationAudit("C2", slots, derived, "DERIVED")


def symbolic_derive(
    semantic_type: str,
    facts: dict[str, Any],
    event: dict[str, str],
) -> DerivationAudit:
    if semantic_type == "TEMPORAL_VERSION":
        return derive_temporal(facts)
    if semantic_type == "GENERAL_RULE_EXCEPTION":
        return derive_gre(facts, event)
    return derive_css(facts)


def build_policy_from_derivation(
    event: dict[str, str],
    facts: dict[str, Any],
    derivation: DerivationAudit,
) -> dict[str, Any] | None:
    if derivation.derivation_status != "DERIVED" or not derivation.derived_result:
        return None
    semantic_type = event["semantic_type"].strip()
    semantic_result = derivation.derived_result
    facts_payload = {**facts, "decomposed_atomic_facts": True}
    canonical = {
        "family": "m12_symbolic_derivation",
        "symbolic_rule_id": derivation.symbolic_rule_id,
        "derived_semantic_result": semantic_result,
    }
    if semantic_type == "TEMPORAL_VERSION":
        relation_raw = normalize_token(str(facts.get("change_relation", "")))
        for key, canonical_rel in TEMPORAL_RELATION_MAP.items():
            if key in relation_raw:
                facts_payload["relation"] = canonical_rel
                canonical["relation"] = canonical_rel
                break
    if semantic_type == "CROSS_SENTENCE_SCOPE":
        cue = normalize_token(str(facts.get("scope_relation_cue", "")))
        for key, canonical_rel in SCOPE_RELATION_MAP.items():
            if key in cue:
                facts_payload["scope_relation"] = canonical_rel
                canonical["scope_relation"] = canonical_rel
                break
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
        "m12_decomposed": True,
    }


def should_trigger_decomposition(
    record: dict[str, Any],
    event: dict[str, str],
    *,
    subtype: str,
) -> tuple[bool, str]:
    status = str(record.get("status") or "")
    if status in {"INVALID_JSON", "RETRIEVAL_FAILED", "INPUT_CONSTRUCTION_ERROR"}:
        return False, "generation_or_input_failure"
    if subtype not in {"M1_ABSTAIN_OR_EMPTY", "M2_MISSING_RESULT"}:
        return False, "not_m1_m2"
    ir = parse_semantic_ir(record, event, robust_ir=True)
    if ir.ir_status == "OK":
        return False, "ir_already_ok"
    return True, "m1_m2_incomplete_ir"


def apply_decomposed_extraction(
    record: dict[str, Any],
    event: dict[str, str],
    evidence: str,
    *,
    subtype: str,
    seed: int,
    qwen_timeout: int | None = None,
    backend: ExtractionBackend = ExtractionBackend.QWEN,
    dry_run: bool = False,
) -> tuple[dict[str, Any], DecompositionResult]:
    assert_candidate_blind(
        candidate_used=bool(record.get("candidate_used")),
        oracle_used=bool(record.get("oracle_used")),
        manual_policy_used=bool(record.get("manual_formal_policy_used") or record.get("manual_policy_used")),
    )
    triggered, reason = should_trigger_decomposition(record, event, subtype=subtype)
    if not triggered:
        return record, DecompositionResult(False, reason, {}, False, None, None, "skipped")

    semantic_type = event["semantic_type"].strip()
    prompt = build_atomic_prompt(event, evidence, semantic_type)
    if dry_run:
        return record, DecompositionResult(True, reason, {}, False, None, None, "dry_run")

    facts, runtime_ms, eval_count, call_count, prompt_tokens, completion_tokens, model_id = call_atomic_extraction(
        prompt,
        seed,
        semantic_type,
        backend=backend,
        timeout=qwen_timeout,
    )
    if not isinstance(facts, dict):
        audit = DerivationAudit("X0", {}, "", "ABSTAIN", "atomic extraction failed", failure_layer="D1")
        complete, required, filled, missing = slot_audit_for_semantic_type(semantic_type, None)
        result = DecompositionResult(
            True,
            reason,
            {},
            False,
            audit,
            None,
            "extraction_failed",
            runtime_ms,
            eval_count,
            call_count,
            backend.value,
            prompt_tokens,
            completion_tokens,
            prompt_tokens + completion_tokens,
            model_id,
            required,
            filled,
            missing,
        )
        return attach_partial_extraction_audit(record, {}, required_slots=required, filled_slots=filled, missing_slots=missing, atomic_complete=False), result

    complete, required, filled, missing = slot_audit_for_semantic_type(semantic_type, facts)
    if not complete:
        audit = DerivationAudit("X0", facts, "", "ABSTAIN", "missing:" + "|".join(missing), failure_layer="D1")
        result = DecompositionResult(
            True,
            reason,
            facts,
            False,
            audit,
            None,
            "atomic_incomplete",
            runtime_ms,
            eval_count,
            call_count,
            backend.value,
            prompt_tokens,
            completion_tokens,
            prompt_tokens + completion_tokens,
            model_id,
            required,
            filled,
            missing,
        )
        return attach_partial_extraction_audit(
            record,
            facts,
            required_slots=required,
            filled_slots=filled,
            missing_slots=missing,
            atomic_complete=False,
        ), result

    derivation = symbolic_derive(semantic_type, facts, event)
    policy = build_policy_from_derivation(event, facts, derivation)
    if policy is None:
        result = DecompositionResult(
            True,
            reason,
            facts,
            True,
            derivation,
            None,
            "derivation_abstain",
            runtime_ms,
            eval_count,
            call_count,
            backend.value,
            prompt_tokens,
            completion_tokens,
            prompt_tokens + completion_tokens,
            model_id,
            required,
            filled,
            missing,
        )
        return attach_partial_extraction_audit(
            record,
            facts,
            required_slots=required,
            filled_slots=filled,
            missing_slots=missing,
            atomic_complete=True,
        ), result

    normalized, canonical_status, canonical_result, _csr = v3.normalize_generated_policy(
        policy,
        {**event, "semantic_type": semantic_type},
        evidence,
    )
    schema_valid, validation_reason = v3.validate_generated_policy(normalized, semantic_type)
    status = "GENERATED" if schema_valid else "INVALID_SCHEMA"
    out = {
        **record,
        "status": status,
        "response": normalized,
        "schema_valid": schema_valid,
        "validation_reason": validation_reason,
        "canonical_status": canonical_status,
        "canonical_result": canonical_result,
        "m12_decomposed_extraction": True,
        "m12_atomic_facts": facts,
        "m12_derivation": derivation.as_dict(),
        "method": METHOD_NAME,
        "m12_extraction_backend": backend.value,
        "m12_extraction_model": model_id,
        "candidate_used": False,
        "oracle_used": False,
        "manual_policy_used": False,
    }
    ir_after = parse_semantic_ir(out, event, robust_ir=True)
    extraction_status = "derived_ir_ok" if ir_after.ir_status == "OK" else "derived_ir_fail"
    if ir_after.ir_status != "OK" and derivation.failure_layer == "":
        derivation.failure_layer = "D4"
    result = DecompositionResult(
        True,
        reason,
        facts,
        True,
        derivation,
        normalized if isinstance(normalized, dict) else None,
        extraction_status,
        runtime_ms,
        eval_count,
        call_count,
        backend.value,
        prompt_tokens,
        completion_tokens,
        prompt_tokens + completion_tokens,
        model_id,
        required,
        filled,
        missing,
    )
    return out, result
