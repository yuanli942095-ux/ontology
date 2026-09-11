from __future__ import annotations

"""V4.5 Adaptive Semantic Completion — narrow retry for missing IR semantic slots."""

import copy
import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_v45_generation import auto_policy_json_schema
from run_auto_policy_v4_ir_candidate_repair import SemanticIR, parse_semantic_ir

METHOD_NAME = "AUTO_POLICY_V4.5_ADAPTIVE_SEMANTIC_COMPLETION"
PROMPT_VERSION_SUFFIX = "V4.5_ADAPTIVE_SEMANTIC_COMPLETION"
COMPLETION_NUM_PREDICT = 1200
COMPLETION_NUM_CTX = 16384

SLOT_HINTS = {
    "TEMPORAL_VERSION": {
        "relation": "Temporal relation such as EFFECTIVE_FROM, REPLACES, SUPERSEDES, AMENDS.",
        "result": "Machine-readable semantic_result summarizing the version change.",
        "old_value": "Previous version or predecessor value if evidenced.",
        "new_value": "Current version or successor value if evidenced.",
        "effective_time": "Effective date/year if evidenced.",
    },
    "GENERAL_RULE_EXCEPTION": {
        "result": "Machine-readable semantic_result for the exception/general-rule outcome.",
        "semantic_result": "Short canonical string for the rule outcome.",
        "exception_condition": "The unless/except/provided-that condition if evidenced.",
        "general_rule": "Baseline rule before exception if evidenced.",
    },
    "CROSS_SENTENCE_SCOPE": {
        "result": "Machine-readable semantic_result for scoped applicability.",
        "semantic_result": "Short canonical string for scoped outcome.",
        "scope_relation": "One of APPLIES_TO, REMAINS_VALID, ADDED, EXCEPTION_OVERRIDES when supported.",
        "scope_target": "What the scope qualifier applies to.",
        "statement": "Main requirement/statement being scoped.",
    },
}


@dataclass
class CompletionAttempt:
    triggered: bool
    missing_fields: list[str]
    completion_status: str
    completion_reason: str
    merged: bool
    ir_status_before: str
    ir_status_after: str
    audit: dict[str, Any] = field(default_factory=dict)
    runtime_ms: int = 0
    eval_count: int = 0


def assert_candidate_blind_context(**flags: bool) -> None:
    assert not flags.get("candidate_used", False), "semantic completion must not read candidate"
    assert not flags.get("oracle_used", False), "semantic completion must not read oracle"
    assert not flags.get("manual_policy_used", False), "semantic completion must not read manual formal policy"


def parse_missing_fields(ir_reason: str) -> list[str]:
    text = str(ir_reason or "")
    if not text.startswith("missing:"):
        return []
    return [part.strip() for part in text.split("missing:", 1)[1].split("|") if part.strip()]


def should_attempt_completion(
    record: dict[str, Any],
    ir: SemanticIR,
    *,
    subtype: str = "",
) -> tuple[bool, list[str], str]:
    status = str(record.get("status") or "")
    if status in {"INVALID_JSON", "RETRIEVAL_FAILED", "INPUT_CONSTRUCTION_ERROR"}:
        return False, [], "generation_or_input_failure"
    if ir.ir_status == "OK":
        return False, [], "ir_already_ok"
    missing = parse_missing_fields(ir.ir_reason)
    if not missing and ir.ir_status == "INCOMPLETE_IR":
        if ir.ir_reason == "model_abstained":
            missing = ["result"]
        elif subtype.startswith("M6"):
            missing = ["semantic_result", "result"]
        elif subtype.startswith("M2"):
            missing = ["result"]
        elif subtype.startswith("M1"):
            missing = ["result"]
    if not missing:
        return False, [], "no_target_fields"
    return True, missing, "incomplete_ir"


def build_completion_prompt(
    event: dict[str, str],
    evidence: str,
    policy: dict[str, Any],
    missing_fields: list[str],
) -> str:
    semantic_type = event["semantic_type"].strip()
    hints = SLOT_HINTS.get(semantic_type, {})
    field_lines = []
    for name in missing_fields:
        field_lines.append(f"- {name}: {hints.get(name, 'fill only if supported by public evidence')}")
    policy_json = json.dumps(policy, ensure_ascii=False, indent=2)
    return f"""
你是一名本体工程与规范文档语义抽取助手。

这不是重新回答整个 Auto Policy，而是 **Semantic Completion Retry（语义补全重试）**。

你只能依据：
1. 当前事件公开元数据
2. 同一份 candidate-blind public evidence
3. 已经生成的 Auto Policy JSON（可能 facts 丰富但 rules / semantic_result 不完整）

你不能使用 private Oracle、Gold Answer、候选编号、候选值、人工 formal-policy.json，也不能根据候选集合反推答案。

任务：仅补齐缺失的关键语义槽位，并输出合法 JSON。

事件元数据：
event_id: {event.get("event_id", "").strip()}
domain: {event.get("domain", "").strip()}
semantic_type: {semantic_type}
subject_label: {event.get("subject_label", "").strip()}
predicate_label: {event.get("predicate_label", "").strip()}

需要补齐的槽位：
{chr(10).join(field_lines)}

要求：
- 只补缺失槽位；不要重复整个 policy；
- semantic_result 必须是 canonical_result 的短字符串表达；
- rules 至少包含 1 条带 semantic_result 的规则；
- 如果公开证据仍不足以补齐，输出 {{"abstain": true, "reason": "..."}}；
- 只输出 JSON，不要 Markdown，不要解释。

已有 Auto Policy JSON：
{policy_json}

===== CANDIDATE-BLIND PUBLIC EVIDENCE BEGIN =====
{evidence}
===== CANDIDATE-BLIND PUBLIC EVIDENCE END =====
""".strip()


def call_qwen_completion(prompt: str, seed: int, *, timeout: int | None = None) -> tuple[dict[str, Any], int, int]:
    payload: dict[str, Any] = {
        "model": v3.MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": auto_policy_json_schema(),
        "options": {
            "temperature": v3.TEMPERATURE,
            "num_predict": COMPLETION_NUM_PREDICT,
            "num_ctx": COMPLETION_NUM_CTX,
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
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    runtime_ms = int((time.perf_counter() - start) * 1000)
    return parsed, runtime_ms, int(parsed.get("eval_count", 0) or 0)


def _ensure_rules(policy: dict[str, Any], semantic_result: str, canonical: dict[str, Any] | None) -> None:
    rules = policy.setdefault("rules", [])
    if not isinstance(rules, list):
        rules = []
        policy["rules"] = rules
    if not rules:
        rules.append({"priority": 300, "conditions": []})
    top = rules[0]
    if not isinstance(top, dict):
        top = {"priority": 300, "conditions": []}
        rules[0] = top
    if semantic_result:
        top["semantic_result"] = semantic_result
    if isinstance(canonical, dict) and canonical:
        top["canonical_result"] = canonical


def merge_semantic_completion(base: dict[str, Any], completion: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    if completion.get("abstain") is True:
        return merged

    canonical = completion.get("canonical_result")
    if isinstance(canonical, dict) and canonical:
        existing = merged.get("canonical_result")
        if isinstance(existing, dict):
            existing.update(canonical)
            merged["canonical_result"] = existing
        else:
            merged["canonical_result"] = canonical

    semantic_result = str(completion.get("semantic_result", "")).strip()
    if not semantic_result:
        rules = completion.get("rules")
        if isinstance(rules, list) and rules and isinstance(rules[0], dict):
            semantic_result = str(rules[0].get("semantic_result", "")).strip()

    if isinstance(completion.get("rules"), list) and completion["rules"]:
        merged["rules"] = copy.deepcopy(completion["rules"])
    elif semantic_result:
        _ensure_rules(merged, semantic_result, merged.get("canonical_result") if isinstance(merged.get("canonical_result"), dict) else None)

    # Promote scalar completion slots into facts for IR alias extraction.
    facts = merged.setdefault("facts", {})
    if not isinstance(facts, dict):
        facts = {}
        merged["facts"] = facts
    for key in ("relation", "scope_relation", "scope_target", "exception_condition", "general_rule", "statement"):
        value = completion.get(key)
        if value not in (None, "", [], {}):
            facts[key] = value

    if isinstance(merged.get("canonical_result"), dict):
        canon = merged["canonical_result"]
        if canon.get("reason") in ("abstain", "abstained"):
            canon.pop("reason", None)
        if canon.get("family") in (None, "null", ""):
            if semantic_result:
                canon.setdefault("family", "semantic_completion")

    merged.pop("abstain", None)
    return merged


def apply_semantic_completion(
    record: dict[str, Any],
    event: dict[str, str],
    evidence: str,
    *,
    subtype: str = "",
    candidate_used: bool = False,
    oracle_used: bool = False,
    manual_policy_used: bool = False,
    seed: int,
    qwen_timeout: int | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], CompletionAttempt]:
    assert_candidate_blind_context(
        candidate_used=candidate_used,
        oracle_used=oracle_used,
        manual_policy_used=manual_policy_used,
    )
    ir_before = parse_semantic_ir(record, event, robust_ir=True)
    triggered, missing, trigger_reason = should_attempt_completion(record, ir_before, subtype=subtype)
    if not triggered:
        return record, CompletionAttempt(
            triggered=False,
            missing_fields=missing,
            completion_status="skipped",
            completion_reason=trigger_reason,
            merged=False,
            ir_status_before=ir_before.ir_status,
            ir_status_after=ir_before.ir_status,
        )

    response = record.get("response")
    if not isinstance(response, dict):
        return record, CompletionAttempt(
            triggered=True,
            missing_fields=missing,
            completion_status="fail_closed",
            completion_reason="response_not_object",
            merged=False,
            ir_status_before=ir_before.ir_status,
            ir_status_after=ir_before.ir_status,
        )

    prompt = build_completion_prompt(event, evidence, response, missing)
    if dry_run:
        return record, CompletionAttempt(
            triggered=True,
            missing_fields=missing,
            completion_status="dry_run",
            completion_reason="dry_run",
            merged=False,
            ir_status_before=ir_before.ir_status,
            ir_status_after=ir_before.ir_status,
            audit={"prompt_chars": len(prompt)},
        )

    qwen_response, runtime_ms, eval_count = call_qwen_completion(prompt, seed, timeout=qwen_timeout)
    response_text = str(qwen_response.get("response", ""))
    completion = v3.extract_json(response_text)
    if not isinstance(completion, dict):
        return record, CompletionAttempt(
            triggered=True,
            missing_fields=missing,
            completion_status="fail_closed",
            completion_reason="invalid_completion_json",
            merged=False,
            ir_status_before=ir_before.ir_status,
            ir_status_after=ir_before.ir_status,
            runtime_ms=runtime_ms,
            eval_count=eval_count,
        )

    if completion.get("abstain") is True:
        return record, CompletionAttempt(
            triggered=True,
            missing_fields=missing,
            completion_status="abstain",
            completion_reason=str(completion.get("reason", "completion_abstain")),
            merged=False,
            ir_status_before=ir_before.ir_status,
            ir_status_after=ir_before.ir_status,
            runtime_ms=runtime_ms,
            eval_count=eval_count,
        )

    merged_response = merge_semantic_completion(response, completion)
    event_for_normalize = {**event, "semantic_type": event["semantic_type"].strip()}
    normalized, canonical_status, canonical_result, _csr = v3.normalize_generated_policy(
        merged_response,
        event_for_normalize,
        evidence,
    )
    schema_valid, validation_reason = v3.validate_generated_policy(normalized, event["semantic_type"].strip())
    if isinstance(normalized, dict) and normalized.get("abstain") is True:
        status = "ABSTAIN"
    elif normalized is None:
        status = "INVALID_JSON"
    elif schema_valid:
        status = "GENERATED"
    else:
        status = "INVALID_SCHEMA"

    out_record = {
        **record,
        "status": status,
        "response": normalized,
        "schema_valid": schema_valid,
        "validation_reason": validation_reason,
        "canonical_status": canonical_status,
        "canonical_result": canonical_result,
        "semantic_completion_applied": True,
        "semantic_completion_missing_fields": missing,
        "semantic_completion_status": "merged",
        "method": METHOD_NAME,
        "candidate_used": False,
        "oracle_used": False,
        "manual_policy_used": False,
    }
    ir_after = parse_semantic_ir(out_record, event, robust_ir=True)
    return out_record, CompletionAttempt(
        triggered=True,
        missing_fields=missing,
        completion_status="merged" if ir_after.ir_status == "OK" else "fail_closed",
        completion_reason=validation_reason if ir_after.ir_status != "OK" else "ok",
        merged=True,
        ir_status_before=ir_before.ir_status,
        ir_status_after=ir_after.ir_status,
        runtime_ms=runtime_ms,
        eval_count=eval_count,
        audit={"completion_keys": sorted(completion.keys())},
    )
