from __future__ import annotations

"""LLM backends for M13 rule refinement (primary Ollama vs secondary DeepSeek API)."""

import json
import os
import socket
import time
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
import urllib.request
from dataclasses import dataclass
from typing import Literal

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_m12_deepseek_client import DeepSeekConfig, call_deepseek_chat

LLMBackend = Literal["ollama", "deepseek_api"]

M13_TEMPERATURE = 0
M13_NUM_PREDICT = 900
TRANSPORT_MAX_ATTEMPTS = 3
TRANSPORT_BACKOFF_SECONDS = (1.0, 2.0)
TRANSPORT_ERROR_MARKERS = (
    "incompleteread",
    "urlerror",
    "httperror",
    "connectionerror",
    "connectionreseterror",
    "timeouterror",
    "remote end closed connection",
    "temporarily unavailable",
)


@dataclass
class LlmCallResult:
    text: str
    runtime_ms: int
    completion_tokens: int
    done_reason: str
    backend: str
    model: str
    prompt_tokens: int = 0
    total_tokens: int = 0
    attempt_count: int = 1
    retry_errors: tuple[str, ...] = ()


def is_transient_transport_error(exc: BaseException) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(exc, (IncompleteRead, URLError, TimeoutError, ConnectionError, socket.timeout)):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return any(marker in name or marker in message for marker in TRANSPORT_ERROR_MARKERS)


def resume_artifact_is_complete(path: Path) -> bool:
    """Transport-failed JSON artifacts are not valid resume checkpoints."""
    if not path.is_file():
        return False
    try:
        payload = path.read_text(encoding="utf-8-sig").lower()
    except OSError:
        return False
    return not any(marker in payload for marker in TRANSPORT_ERROR_MARKERS)


def deepseek_config_for_m13(timeout: int) -> DeepSeekConfig:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    return DeepSeekConfig(
        api_key=api_key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/"),
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip(),
        temperature=M13_TEMPERATURE,
        max_tokens=M13_NUM_PREDICT,
        timeout=timeout,
    )


def call_ollama_text(prompt: str, seed: int, timeout: int, *, num_predict: int = M13_NUM_PREDICT) -> LlmCallResult:
    payload = {
        "model": v3.MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": M13_TEMPERATURE,
            "num_predict": num_predict,
            "num_ctx": 16384,
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
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
    return LlmCallResult(
        text=str(raw.get("response", "")),
        runtime_ms=int((time.perf_counter() - start) * 1000),
        completion_tokens=int(raw.get("eval_count", 0) or 0),
        done_reason=str(raw.get("done_reason", "")),
        backend="ollama",
        model=v3.MODEL,
        prompt_tokens=int(raw.get("prompt_eval_count", 0) or 0),
    )


def call_deepseek_text(prompt: str, seed: int, timeout: int, *, num_predict: int = M13_NUM_PREDICT) -> LlmCallResult:
    del seed  # DeepSeek chat API has no deterministic seed; logged in audit only.
    config = deepseek_config_for_m13(timeout)
    config = DeepSeekConfig(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        temperature=M13_TEMPERATURE,
        max_tokens=num_predict,
        timeout=timeout,
    )
    result = call_deepseek_chat(prompt, config=config, response_json=False)
    sleep_ms = os.environ.get("DEEPSEEK_INTER_CALL_SLEEP_MS", "300").strip()
    try:
        delay = max(0.0, int(sleep_ms) / 1000.0)
    except ValueError:
        delay = 0.3
    if delay:
        time.sleep(delay)
    return LlmCallResult(
        text=result.content,
        runtime_ms=result.runtime_ms,
        completion_tokens=result.completion_tokens,
        done_reason="stop",
        backend="deepseek_api",
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        total_tokens=result.total_tokens,
    )


def call_llm_text(
    backend: LLMBackend,
    prompt: str,
    seed: int,
    timeout: int,
    *,
    num_predict: int = M13_NUM_PREDICT,
) -> LlmCallResult:
    errors: list[str] = []
    for attempt in range(1, TRANSPORT_MAX_ATTEMPTS + 1):
        try:
            if backend == "deepseek_api":
                result = call_deepseek_text(prompt, seed, timeout, num_predict=num_predict)
            else:
                result = call_ollama_text(prompt, seed, timeout, num_predict=num_predict)
            result.attempt_count = attempt
            result.retry_errors = tuple(errors)
            return result
        except Exception as exc:
            if not is_transient_transport_error(exc) or attempt == TRANSPORT_MAX_ATTEMPTS:
                raise
            errors.append(f"{type(exc).__name__}: {exc}")
            time.sleep(TRANSPORT_BACKOFF_SECONDS[attempt - 1])
    raise AssertionError("unreachable transport retry state")


def call_llm_json(
    backend: LLMBackend,
    prompt: str,
    seed: int,
    timeout: int,
    *,
    num_predict: int = M13_NUM_PREDICT,
) -> LlmCallResult:
    """Return raw LLM text for JSON extraction (M14/M16 stages)."""
    return call_llm_text(backend, prompt, seed, timeout, num_predict=num_predict)
