from __future__ import annotations

"""Generation strategy ablation: sampling and adaptive vs always-structured calls."""

import json
import math
import random
import time
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_v45_generation import (
    BASE_NUM_CTX,
    BASE_NUM_PREDICT,
    EXTENDED_NUM_CTX,
    EXTENDED_NUM_PREDICT,
    ReliabilityMode,
    auto_policy_json_schema,
    budget_for_mode,
)

PILOT_OUTPUT_DIR = v3.ROOT / "output" / "generation-strategy-ablation"
PILOT_EVENTS_FILE = PILOT_OUTPUT_DIR / "pilot-60-events.json"
PILOT_SAMPLE_SEED = 20260831
PILOT_EVENTS_PER_TYPE = 20
PILOT_RUNS = 3
PILOT_SEED_BASE = 20260827
PILOT_SEMANTIC_TYPES = (
    "TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE",
)


class GenerationStrategy(str, Enum):
    ADAPTIVE = "v4.5_adaptive"
    ALWAYS_STRUCTURED = "always_structured"


@dataclass
class GenerationAttempt:
    attempt_index: int
    strategy_step: str
    format_constraint: str
    num_predict: int
    num_ctx: int
    response_text: str
    qwen_response: dict[str, Any]
    runtime_ms: int
    parsed: Any
    generation_status: str
    validation_reason: str
    schema_valid: bool
    done_reason: str = ""
    prompt_eval_count: int = 0
    eval_count: int = 0


@dataclass
class SlotGenerationResult:
    strategy: GenerationStrategy
    attempts: list[GenerationAttempt] = field(default_factory=list)
    final: GenerationAttempt | None = None

    @property
    def call_count(self) -> int:
        return len(self.attempts)


def stratified_event_sample(
    events: list[dict[str, str]],
    *,
    semantic_type: str,
    count: int,
    seed: int,
) -> list[dict[str, str]]:
    pool = [row for row in events if row.get("semantic_type", "").strip() == semantic_type]
    if len(pool) < count:
        raise ValueError(f"not enough {semantic_type} events: {len(pool)} < {count}")
    by_domain: dict[str, list[dict[str, str]]] = {}
    for row in pool:
        by_domain.setdefault(row.get("domain", "unknown").strip() or "unknown", []).append(row)
    rng = random.Random(seed)
    for rows in by_domain.values():
        rng.shuffle(rows)
    total = len(pool)
    allocations: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    assigned = 0
    for domain, rows in sorted(by_domain.items()):
        exact = count * len(rows) / total
        base = int(math.floor(exact))
        allocations[domain] = base
        assigned += base
        remainders.append((exact - base, domain))
    for _, domain in sorted(remainders, reverse=True):
        if assigned >= count:
            break
        allocations[domain] += 1
        assigned += 1
    selected: list[dict[str, str]] = []
    for domain, rows in sorted(by_domain.items()):
        take = min(allocations.get(domain, 0), len(rows))
        selected.extend(rows[:take])
    if len(selected) < count:
        chosen = {row["event_id"] for row in selected}
        for row in sorted(pool, key=lambda item: item["event_id"]):
            if len(selected) >= count:
                break
            if row["event_id"] not in chosen:
                selected.append(row)
    return sorted(selected[:count], key=lambda item: item["event_id"])


def build_pilot_manifest(events: list[dict[str, str]], *, seed: int = PILOT_SAMPLE_SEED) -> dict[str, Any]:
    ready = [row for row in events if str(row.get("status", "")).strip().upper() == "READY"]
    sampled: list[dict[str, str]] = []
    for index, semantic_type in enumerate(PILOT_SEMANTIC_TYPES):
        sampled.extend(
            stratified_event_sample(
                ready,
                semantic_type=semantic_type,
                count=PILOT_EVENTS_PER_TYPE,
                seed=seed + index,
            )
        )
    slots = []
    for row in sampled:
        for run in range(1, PILOT_RUNS + 1):
            slots.append(
                {
                    "event_id": row["event_id"],
                    "semantic_type": row["semantic_type"].strip(),
                    "domain": row.get("domain", "").strip(),
                    "run": run,
                    "seed": PILOT_SEED_BASE + run - 1,
                }
            )
    return {
        "benchmark": "external-real-v8-grounded",
        "sample_seed": seed,
        "events_per_type": PILOT_EVENTS_PER_TYPE,
        "runs": PILOT_RUNS,
        "seed_base": PILOT_SEED_BASE,
        "semantic_types": list(PILOT_SEMANTIC_TYPES),
        "events": sampled,
        "slots": slots,
        "attempt_count": len(slots),
    }


def call_qwen_generation(
    prompt: str,
    seed: int,
    *,
    num_predict: int,
    num_ctx: int,
    json_schema: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> tuple[dict[str, Any], int]:
    payload: dict[str, Any] = {
        "model": v3.MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": v3.TEMPERATURE,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
            "seed": seed,
        },
    }
    if json_schema is not None:
        payload["format"] = json_schema
    request = urllib.request.Request(
        v3.OLLAMA_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout or v3.TIMEOUT) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw), int((time.perf_counter() - start) * 1000)


def evaluate_generation(
    response_text: str,
    qwen_response: dict[str, Any],
    event: dict[str, str],
    event_for_prompt: dict[str, str],
    evidence: str,
) -> tuple[Any, str, str, bool, str, str, dict[str, Any] | None, str]:
    parsed = v3.extract_json(response_text)
    parsed, canonical_status, canonical_result, canonical_semantic_result = v3.normalize_generated_policy(
        parsed,
        {**event_for_prompt, "semantic_type": event["semantic_type"]},
        evidence,
    )
    schema_valid, validation_reason = v3.validate_generated_policy(parsed, event["semantic_type"].strip())
    forbidden_markers = v3.forbidden_output_markers(response_text)
    if forbidden_markers:
        status = "FORBIDDEN_OUTPUT"
    elif parsed is None:
        status = "INVALID_JSON"
    elif isinstance(parsed, dict) and parsed.get("abstain", False):
        status = "ABSTAIN"
    elif not schema_valid:
        status = "INVALID_SCHEMA"
    else:
        status = "GENERATED"
    return (
        parsed,
        status,
        validation_reason,
        schema_valid,
        canonical_status,
        canonical_semantic_result,
        canonical_result,
        "|".join(forbidden_markers),
    )


def attempt_from_call(
    *,
    attempt_index: int,
    strategy_step: str,
    format_constraint: str,
    num_predict: int,
    num_ctx: int,
    response_text: str,
    qwen_response: dict[str, Any],
    runtime_ms: int,
    event: dict[str, str],
    event_for_prompt: dict[str, str],
    evidence: str,
) -> GenerationAttempt:
    (
        parsed,
        status,
        validation_reason,
        schema_valid,
        _canonical_status,
        _canonical_semantic_result,
        _canonical_result,
        _forbidden,
    ) = evaluate_generation(response_text, qwen_response, event, event_for_prompt, evidence)
    return GenerationAttempt(
        attempt_index=attempt_index,
        strategy_step=strategy_step,
        format_constraint=format_constraint,
        num_predict=num_predict,
        num_ctx=num_ctx,
        response_text=response_text,
        qwen_response=qwen_response,
        runtime_ms=runtime_ms,
        parsed=parsed,
        generation_status=status,
        validation_reason=validation_reason,
        schema_valid=schema_valid,
        done_reason=str(qwen_response.get("done_reason") or ""),
        prompt_eval_count=int(qwen_response.get("prompt_eval_count", 0) or 0),
        eval_count=int(qwen_response.get("eval_count", 0) or 0),
    )


def needs_length_retry(attempt: GenerationAttempt) -> bool:
    return attempt.done_reason.strip().lower() in {"length", "max_tokens", "max_length"}


def needs_format_retry(attempt: GenerationAttempt) -> bool:
    return attempt.generation_status == "INVALID_JSON" and not needs_length_retry(attempt)


def run_free_generation(
    prompt: str,
    seed: int,
    *,
    attempt_index: int,
    strategy_step: str,
    event: dict[str, str],
    event_for_prompt: dict[str, str],
    evidence: str,
    timeout: int | None = None,
) -> GenerationAttempt:
    qwen_response, runtime_ms = call_qwen_generation(
        prompt,
        seed,
        num_predict=BASE_NUM_PREDICT,
        num_ctx=BASE_NUM_CTX,
        timeout=timeout,
    )
    return attempt_from_call(
        attempt_index=attempt_index,
        strategy_step=strategy_step,
        format_constraint="none",
        num_predict=BASE_NUM_PREDICT,
        num_ctx=BASE_NUM_CTX,
        response_text=str(qwen_response.get("response", "")),
        qwen_response=qwen_response,
        runtime_ms=runtime_ms,
        event=event,
        event_for_prompt=event_for_prompt,
        evidence=evidence,
    )


def run_structured_generation(
    prompt: str,
    seed: int,
    *,
    attempt_index: int,
    strategy_step: str,
    num_predict: int,
    num_ctx: int,
    event: dict[str, str],
    event_for_prompt: dict[str, str],
    evidence: str,
    timeout: int | None = None,
) -> GenerationAttempt:
    qwen_response, runtime_ms = call_qwen_generation(
        prompt,
        seed,
        num_predict=num_predict,
        num_ctx=num_ctx,
        json_schema=auto_policy_json_schema(),
        timeout=timeout,
    )
    return attempt_from_call(
        attempt_index=attempt_index,
        strategy_step=strategy_step,
        format_constraint="json_schema",
        num_predict=num_predict,
        num_ctx=num_ctx,
        response_text=str(qwen_response.get("response", "")),
        qwen_response=qwen_response,
        runtime_ms=runtime_ms,
        event=event,
        event_for_prompt=event_for_prompt,
        evidence=evidence,
    )


def run_extended_budget_retry(
    prompt: str,
    seed: int,
    *,
    attempt_index: int,
    strategy_step: str,
    structured: bool,
    event: dict[str, str],
    event_for_prompt: dict[str, str],
    evidence: str,
    timeout: int | None = None,
) -> GenerationAttempt:
    num_predict, num_ctx = budget_for_mode(ReliabilityMode.EXTENDED_BUDGET)
    if structured:
        return run_structured_generation(
            prompt,
            seed,
            attempt_index=attempt_index,
            strategy_step=strategy_step,
            num_predict=num_predict,
            num_ctx=num_ctx,
            event=event,
            event_for_prompt=event_for_prompt,
            evidence=evidence,
            timeout=timeout,
        )
    qwen_response, runtime_ms = call_qwen_generation(
        prompt,
        seed,
        num_predict=num_predict,
        num_ctx=num_ctx,
        timeout=timeout,
    )
    return attempt_from_call(
        attempt_index=attempt_index,
        strategy_step=strategy_step,
        format_constraint="none",
        num_predict=num_predict,
        num_ctx=num_ctx,
        response_text=str(qwen_response.get("response", "")),
        qwen_response=qwen_response,
        runtime_ms=runtime_ms,
        event=event,
        event_for_prompt=event_for_prompt,
        evidence=evidence,
    )


def attempt_from_baseline_record(
    record: dict[str, Any],
    *,
    attempt_index: int,
    event: dict[str, str],
    event_for_prompt: dict[str, str],
    evidence: str,
) -> GenerationAttempt:
    response_text = str(record.get("raw_response_text") or "")
    if not response_text and isinstance(record.get("response"), dict):
        response_text = json.dumps(record["response"], ensure_ascii=False)
    qwen_response = {
        "done_reason": record.get("done_reason", ""),
        "prompt_eval_count": record.get("prompt_eval_count", 0),
        "eval_count": record.get("eval_count", 0),
    }
    return attempt_from_call(
        attempt_index=attempt_index,
        strategy_step="baseline_free_reused",
        format_constraint="none",
        num_predict=BASE_NUM_PREDICT,
        num_ctx=BASE_NUM_CTX,
        response_text=response_text,
        qwen_response=qwen_response,
        runtime_ms=int(record.get("runtime_ms", 0) or 0),
        event=event,
        event_for_prompt=event_for_prompt,
        evidence=evidence,
    )


def run_adaptive_generation(
    prompt: str,
    seed: int,
    *,
    event: dict[str, str],
    event_for_prompt: dict[str, str],
    evidence: str,
    baseline_record: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> SlotGenerationResult:
    result = SlotGenerationResult(strategy=GenerationStrategy.ADAPTIVE)
    if baseline_record is not None:
        first = attempt_from_baseline_record(
            baseline_record,
            attempt_index=1,
            event=event,
            event_for_prompt=event_for_prompt,
            evidence=evidence,
        )
    else:
        first = run_free_generation(
            prompt,
            seed,
            attempt_index=1,
            strategy_step="baseline_free",
            event=event,
            event_for_prompt=event_for_prompt,
            evidence=evidence,
            timeout=timeout,
        )
    result.attempts.append(first)
    final = first
    if needs_length_retry(first):
        final = run_extended_budget_retry(
            prompt,
            seed,
            attempt_index=2,
            strategy_step="extended_budget_retry",
            structured=False,
            event=event,
            event_for_prompt=event_for_prompt,
            evidence=evidence,
            timeout=timeout,
        )
        result.attempts.append(final)
    elif needs_format_retry(first):
        predict, ctx = budget_for_mode(ReliabilityMode.STRUCTURED_OUTPUT)
        final = run_structured_generation(
            prompt,
            seed,
            attempt_index=2,
            strategy_step="structured_retry",
            num_predict=predict,
            num_ctx=ctx,
            event=event,
            event_for_prompt=event_for_prompt,
            evidence=evidence,
            timeout=timeout,
        )
        result.attempts.append(final)
    result.final = final
    return result


def run_always_structured_generation(
    prompt: str,
    seed: int,
    *,
    event: dict[str, str],
    event_for_prompt: dict[str, str],
    evidence: str,
    timeout: int | None = None,
) -> SlotGenerationResult:
    result = SlotGenerationResult(strategy=GenerationStrategy.ALWAYS_STRUCTURED)
    first = run_structured_generation(
        prompt,
        seed,
        attempt_index=1,
        strategy_step="structured_first",
        num_predict=BASE_NUM_PREDICT,
        num_ctx=BASE_NUM_CTX,
        event=event,
        event_for_prompt=event_for_prompt,
        evidence=evidence,
        timeout=timeout,
    )
    result.attempts.append(first)
    final = first
    if needs_length_retry(first):
        final = run_extended_budget_retry(
            prompt,
            seed,
            attempt_index=2,
            strategy_step="structured_extended_budget_retry",
            structured=True,
            event=event,
            event_for_prompt=event_for_prompt,
            evidence=evidence,
            timeout=timeout,
        )
        result.attempts.append(final)
    result.final = final
    return result


def generation_record_from_result(
    result: SlotGenerationResult,
    *,
    event: dict[str, str],
    run: int,
    seed: int,
    variant: str,
    retrieval_meta: dict[str, Any],
    evidence_file: Path,
) -> dict[str, Any]:
    final = result.final
    if final is None:
        raise ValueError("missing final generation attempt")
    first = result.attempts[0]
    return {
        "event_id": event["event_id"],
        "semantic_type": event["semantic_type"].strip(),
        "run": run,
        "seed": seed,
        "variant": variant,
        "model": v3.MODEL,
        "generation_strategy": result.strategy.value,
        "generation_call_count": result.call_count,
        "first_attempt_status": first.generation_status,
        "first_attempt_done_reason": first.done_reason,
        "first_attempt_format_constraint": first.format_constraint,
        "final_attempt_status": final.generation_status,
        "final_attempt_step": final.strategy_step,
        "format_constraint": final.format_constraint,
        "num_predict": final.num_predict,
        "num_ctx": final.num_ctx,
        "done_reason": final.done_reason,
        "prompt_eval_count": final.prompt_eval_count,
        "eval_count": final.eval_count,
        "runtime_ms": sum(item.runtime_ms for item in result.attempts),
        "status": final.generation_status,
        "validation_reason": final.validation_reason,
        "schema_valid": final.schema_valid,
        "raw_response_text": final.response_text,
        "response": final.parsed,
        "attempt_trace_json": json.dumps(
            [
                {
                    "attempt_index": item.attempt_index,
                    "strategy_step": item.strategy_step,
                    "format_constraint": item.format_constraint,
                    "generation_status": item.generation_status,
                    "done_reason": item.done_reason,
                    "runtime_ms": item.runtime_ms,
                    "eval_count": item.eval_count,
                }
                for item in result.attempts
            ],
            ensure_ascii=False,
        ),
        "candidate_blind_file": str(evidence_file),
        **retrieval_meta,
    }
