from __future__ import annotations

"""Task-formulated M12 extraction: router + mode-specific schema + partial slot audit."""

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_m12_decomposed_extraction import (
    DecompositionResult,
    ExtractionBackend,
    NUM_CTX,
    NUM_PREDICT,
)
from auto_policy_task_formulation import (
    METHOD_NAME,
    TaskFormulation,
    atomic_schema_for_mode,
    audit_atomic_slots,
    build_atomic_prompt,
    build_policy_from_task_derivation,
    route_task_formulation,
    symbolic_derive_for_mode,
)
from auto_policy_v45_generation import ReliabilityMode, call_qwen_reliable
from run_auto_policy_v4_ir_candidate_repair import parse_semantic_ir


@dataclass
class TaskFormulationResult(DecompositionResult):
    formulation: TaskFormulation | None = None
    required_slots: list[str] = field(default_factory=list)
    filled_slots: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)


def call_atomic_extraction_with_schema(
    prompt: str,
    seed: int,
    schema: dict[str, Any],
    *,
    backend: ExtractionBackend = ExtractionBackend.QWEN,
    timeout: int | None = None,
) -> tuple[dict[str, Any] | None, int, int, int, int, int, str]:
    if backend is not ExtractionBackend.QWEN:
        raise NotImplementedError("Task formulation pilot currently supports Qwen only")

    def request_once(text_prompt: str) -> tuple[dict[str, Any], int]:
        payload: dict[str, Any] = {
            "model": v3.MODEL,
            "prompt": text_prompt,
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
            raw_response = json.loads(response.read().decode("utf-8"))
        return raw_response, int((time.perf_counter() - start) * 1000)

    def looks_like_legacy_policy(value: Any) -> bool:
        return isinstance(value, dict) and (
            "canonical_result" in value
            or "rules" in value
            or str(value.get("semantic_type", "")).strip() in {"TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}
        )

    def coerce_atomic_shape(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if isinstance(value.get("facts"), dict) and looks_like_legacy_policy(value):
            value = value["facts"]
        out: dict[str, Any] = {}
        for key, item in value.items():
            if "." not in str(key):
                out[str(key)] = item
                continue
            head, tail = str(key).split(".", 1)
            nested = out.setdefault(head, {})
            if isinstance(nested, dict):
                nested[tail] = item
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        if "subject_entity" in properties and "subject_entity" not in out and out.get("subject_label"):
            out["subject_entity"] = out.get("subject_label")
        if "attribute_name" in properties and "attribute_name" not in out and out.get("predicate_label"):
            out["attribute_name"] = out.get("predicate_label")
        return out

    def atomic_only_retry_prompt(text_prompt: str) -> str:
        return (
            text_prompt
            + "\n\n"
            + "CRITICAL OUTPUT CONTRACT:\n"
            + "Return only the atomic fact object requested above.\n"
            + "Do not return semantic_type, facts, canonical_result, rules, policy, or candidate decisions.\n"
            + "If a requested value is not directly stated, use an empty string for that field and keep evidence_spans empty.\n"
        )

    import time

    start = time.perf_counter()
    try:
        raw, runtime_ms = request_once(prompt)
    except Exception as exc:
        runtime_ms = int((time.perf_counter() - start) * 1000)
        return None, runtime_ms, 0, 1, 0, 0, f"{v3.MODEL}:error:{type(exc).__name__}"
    text = str(raw.get("response", ""))
    facts = v3.extract_json(text)
    calls = 1
    if looks_like_legacy_policy(facts):
        try:
            retry_raw, retry_runtime_ms = request_once(atomic_only_retry_prompt(prompt))
            retry_facts = v3.extract_json(str(retry_raw.get("response", "")))
            runtime_ms += retry_runtime_ms
            calls = 2
            raw = retry_raw
            facts = retry_facts
        except Exception:
            pass
    facts = coerce_atomic_shape(facts)
    if not isinstance(facts, dict):
        call = call_qwen_reliable(prompt, seed, ReliabilityMode.STRUCTURED_OUTPUT, timeout=timeout)
        facts = coerce_atomic_shape(v3.extract_json(str(call.response.get("response", ""))))
        runtime_ms += call.runtime_ms
        calls = 2
    if not isinstance(facts, dict):
        return None, runtime_ms, int(raw.get("eval_count", 0) or 0), calls, 0, 0, v3.MODEL
    return facts, runtime_ms, int(raw.get("eval_count", 0) or 0), calls, 0, 0, v3.MODEL


def attach_partial_audit(record: dict[str, Any], result: TaskFormulationResult) -> dict[str, Any]:
    audit = {
        "required_slots": result.required_slots,
        "filled_slots": result.filled_slots,
        "missing_slots": result.missing_slots,
        "atomic_complete": result.atomic_complete,
    }
    if result.formulation:
        audit["task_formulation"] = result.formulation.as_dict()
    if result.atomic_facts:
        audit["partial_atomic_facts"] = result.atomic_facts
    if result.derivation:
        audit["derivation"] = result.derivation.as_dict()
    out = {
        **record,
        "m12_task_formulation_audit": audit,
    }
    if isinstance(result.atomic_facts, dict) and result.atomic_facts:
        out["m12_partial_atomic_facts"] = result.atomic_facts
    return out


def apply_task_formulated_extraction(
    record: dict[str, Any],
    event: dict[str, str],
    evidence: str,
    *,
    subtype: str,
    seed: int,
    qwen_timeout: int | None = None,
    backend: ExtractionBackend = ExtractionBackend.QWEN,
    dry_run: bool = False,
    relaxed_css_scope: bool = False,
    force_trigger: bool = False,
) -> tuple[dict[str, Any], TaskFormulationResult]:
    from auto_policy_m12_decomposed_extraction import DerivationAudit, assert_candidate_blind, should_trigger_decomposition

    assert_candidate_blind(
        candidate_used=bool(record.get("candidate_used")),
        oracle_used=bool(record.get("oracle_used")),
        manual_policy_used=bool(record.get("manual_formal_policy_used") or record.get("manual_policy_used")),
    )
    triggered, reason = should_trigger_decomposition(record, event, subtype=subtype)
    if force_trigger and not triggered:
        triggered = True
        reason = f"forced_task_formulation_after_{reason}"
    formulation = route_task_formulation(event, evidence)
    if not triggered:
        result = TaskFormulationResult(False, reason, {}, False, None, None, "skipped", formulation=formulation)
        return record, result

    if dry_run:
        required, _, _, _ = audit_atomic_slots(
            formulation.extraction_mode,
            {},
            relaxed_css_scope=relaxed_css_scope,
        )
        result = TaskFormulationResult(
            True,
            reason,
            {},
            False,
            None,
            None,
            "dry_run",
            formulation=formulation,
            required_slots=required,
        )
        return record, result

    prompt = build_atomic_prompt(event, evidence, formulation)
    schema = atomic_schema_for_mode(formulation.extraction_mode)
    facts, runtime_ms, eval_count, call_count, prompt_tokens, completion_tokens, model_id = call_atomic_extraction_with_schema(
        prompt,
        seed,
        schema,
        backend=backend,
        timeout=qwen_timeout,
    )

    complete, required, filled, missing = audit_atomic_slots(
        formulation.extraction_mode,
        facts if isinstance(facts, dict) else None,
        relaxed_css_scope=relaxed_css_scope,
    )

    def make_result(
        extraction_status: str,
        derivation: DerivationAudit | None,
        atomic_complete: bool,
        policy: dict[str, Any] | None = None,
    ) -> TaskFormulationResult:
        return TaskFormulationResult(
            triggered=True,
            trigger_reason=reason,
            atomic_facts=facts if isinstance(facts, dict) else {},
            atomic_complete=atomic_complete,
            derivation=derivation,
            policy_response=policy,
            extraction_status=extraction_status,
            runtime_ms=runtime_ms,
            eval_count=eval_count,
            generation_call_count=call_count,
            extraction_backend=backend.value,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            model_id=model_id,
            formulation=formulation,
            required_slots=required,
            filled_slots=filled,
            missing_slots=missing,
        )

    if not isinstance(facts, dict):
        derivation = DerivationAudit("X0", {}, "", "ABSTAIN", "atomic extraction failed", failure_layer="D1")
        result = make_result("extraction_failed", derivation, False)
        return attach_partial_audit(record, result), result

    if not complete:
        derivation = DerivationAudit("X0", facts, "", "ABSTAIN", "missing:" + "|".join(missing), failure_layer="D1")
        result = make_result("atomic_incomplete", derivation, False)
        return attach_partial_audit(record, result), result

    derivation = symbolic_derive_for_mode(formulation.extraction_mode, facts, event, formulation)
    policy = build_policy_from_task_derivation(event, facts, derivation, formulation)
    if policy is None:
        result = make_result("derivation_abstain", derivation, True)
        return attach_partial_audit(record, result), result

    normalized, canonical_status, canonical_result, _csr = v3.normalize_generated_policy(
        policy,
        {**event, "semantic_type": formulation.benchmark_semantic_type},
        evidence,
    )
    schema_valid, validation_reason = v3.validate_generated_policy(normalized, formulation.benchmark_semantic_type)
    status = "GENERATED" if schema_valid else "INVALID_SCHEMA"
    out = {
        **record,
        "status": status,
        "response": normalized,
        "schema_valid": schema_valid,
        "validation_reason": validation_reason,
        "canonical_status": canonical_status,
        "canonical_result": canonical_result,
        "m12_task_formulated_extraction": True,
        "m12_atomic_facts": facts,
        "m12_derivation": derivation.as_dict(),
        "m12_task_formulation": formulation.as_dict(),
        "method": METHOD_NAME,
        "m12_extraction_backend": backend.value,
        "m12_extraction_model": model_id,
        "candidate_used": False,
        "oracle_used": False,
        "manual_policy_used": False,
        "m12_task_formulation_audit": {
            "required_slots": required,
            "filled_slots": filled,
            "missing_slots": missing,
            "atomic_complete": True,
            "task_formulation": formulation.as_dict(),
            "partial_atomic_facts": facts,
        },
    }
    ir_after = parse_semantic_ir(out, event, robust_ir=True)
    extraction_status = "derived_ir_ok" if ir_after.ir_status == "OK" else "derived_ir_fail"
    if ir_after.ir_status != "OK" and derivation.failure_layer == "":
        derivation.failure_layer = "D4"
    result = make_result(extraction_status, derivation, True, normalized if isinstance(normalized, dict) else None)
    return out, result
