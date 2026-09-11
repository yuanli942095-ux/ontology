from __future__ import annotations

"""V4.5 Auto Policy generation reliability helpers.

Two infrastructure-only fixes (no prompt semantics, scorer, or gate changes):

1. extended_budget — raise num_predict / num_ctx for truncated JSON outputs
2. structured_output — Ollama JSON-schema constrained decoding for prose failures
"""

import json
import time
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any

import run_auto_formal_policy_batch_v3 as v3

METHOD_NAME = "AUTO_POLICY_V4.5_GENERATION_RELIABILITY"
PROMPT_VERSION_SUFFIX = "V4.5_GENERATION_RELIABILITY"

# V4.4 baseline budget
BASE_NUM_PREDICT = v3.NUM_PREDICT
BASE_NUM_CTX = 4096

# Truncated slots often hit either num_predict=1000 or total ctx≈4096.
EXTENDED_NUM_PREDICT = 3000
EXTENDED_NUM_CTX = 16384

STRUCTURED_NUM_PREDICT = 2000
STRUCTURED_NUM_CTX = 16384


class ReliabilityMode(str, Enum):
    EXTENDED_BUDGET = "extended_budget"
    STRUCTURED_OUTPUT = "structured_output"


@dataclass(frozen=True)
class GenerationCall:
    response: dict[str, Any]
    runtime_ms: int
    mode: ReliabilityMode
    num_predict: int
    num_ctx: int
    format_constraint: str


def auto_policy_json_schema() -> dict[str, Any]:
    """Permissive JSON Schema for Auto Policy V3 canonical output."""
    rule_item = {
        "type": "object",
        "properties": {
            "priority": {"type": "integer"},
            "conditions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string"},
                        "operator": {"type": "string"},
                        "value": {},
                    },
                    "additionalProperties": True,
                },
            },
            "canonical_result": {"type": "object", "additionalProperties": True},
            "semantic_result": {"type": "string"},
        },
        "additionalProperties": True,
    }
    return {
        "type": "object",
        "properties": {
            "semantic_type": {"type": "string"},
            "facts": {"type": "object", "additionalProperties": True},
            "canonical_result": {"type": "object", "additionalProperties": True},
            "rules": {"type": "array", "items": rule_item},
            "abstain": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["semantic_type", "facts", "rules", "canonical_result"],
        "additionalProperties": False,
    }


def budget_for_mode(mode: ReliabilityMode) -> tuple[int, int]:
    if mode is ReliabilityMode.EXTENDED_BUDGET:
        return EXTENDED_NUM_PREDICT, EXTENDED_NUM_CTX
    return STRUCTURED_NUM_PREDICT, STRUCTURED_NUM_CTX


def call_qwen_reliable(
    prompt: str,
    seed: int,
    mode: ReliabilityMode,
    *,
    timeout: int | None = None,
) -> GenerationCall:
    num_predict, num_ctx = budget_for_mode(mode)
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
    format_constraint = "none"
    if mode is ReliabilityMode.STRUCTURED_OUTPUT:
        payload["format"] = auto_policy_json_schema()
        format_constraint = "json_schema"
    request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        v3.OLLAMA_URL,
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout or v3.TIMEOUT) as response:
        raw_response = response.read().decode("utf-8")
    runtime_ms = int((time.perf_counter() - start) * 1000)
    return GenerationCall(
        response=json.loads(raw_response),
        runtime_ms=runtime_ms,
        mode=mode,
        num_predict=num_predict,
        num_ctx=num_ctx,
        format_constraint=format_constraint,
    )


def mode_for_failure_bucket(bucket: str) -> ReliabilityMode:
    if bucket == "TRUNCATED":
        return ReliabilityMode.EXTENDED_BUDGET
    return ReliabilityMode.STRUCTURED_OUTPUT


def generation_metadata(call: GenerationCall, qwen_response: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": METHOD_NAME,
        "generation_reliability_mode": call.mode.value,
        "format_constraint": call.format_constraint,
        "num_predict": call.num_predict,
        "num_ctx": call.num_ctx,
        "baseline_num_predict": BASE_NUM_PREDICT,
        "baseline_num_ctx": BASE_NUM_CTX,
        "done_reason": qwen_response.get("done_reason", ""),
        "prompt_eval_count": int(qwen_response.get("prompt_eval_count", 0) or 0),
        "eval_count": int(qwen_response.get("eval_count", 0) or 0),
        "total_duration": qwen_response.get("total_duration"),
        "load_duration": qwen_response.get("load_duration"),
        "prompt_eval_duration": qwen_response.get("prompt_eval_duration"),
        "eval_duration": qwen_response.get("eval_duration"),
    }
