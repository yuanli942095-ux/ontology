from __future__ import annotations

"""DeepSeek API client for M12 atomic fact extraction (OpenAI-compatible chat API)."""

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_m12_decomposed_extraction import NUM_PREDICT, atomic_schema  # noqa: E402

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
CHAT_PATH = "/chat/completions"


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int

    @classmethod
    def from_env(cls) -> "DeepSeekConfig":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
        model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL).strip()
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=v3.TEMPERATURE,
            max_tokens=NUM_PREDICT,
            timeout=v3.TIMEOUT,
        )


@dataclass
class DeepSeekCallResult:
    content: str
    runtime_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str


def chat_completions_url(base_url: str) -> str:
    if base_url.endswith("/v1"):
        return f"{base_url}{CHAT_PATH}"
    return f"{base_url}/v1{CHAT_PATH}"


def _deepseek_max_attempts() -> int:
    raw = os.environ.get("DEEPSEEK_MAX_ATTEMPTS", "4").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


def _deepseek_retry_sleep_seconds(attempt: int) -> float:
    raw = os.environ.get("DEEPSEEK_RETRY_SLEEP_SECONDS", "2").strip()
    try:
        base = float(raw)
    except ValueError:
        base = 2.0
    return min(30.0, base * (2 ** attempt))


def _retryable_deepseek_error(exc: BaseException) -> bool:
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, RuntimeError):
        message = str(exc)
        if "returned empty content" in message:
            return True
        if "DeepSeek API HTTP" in message:
            for code in ("429", "500", "502", "503", "504"):
                if f"HTTP {code}" in message:
                    return True
    return False


def _deepseek_thinking_payload() -> dict[str, Any] | None:
    mode = os.environ.get("DEEPSEEK_THINKING", "disabled").strip().lower()
    if mode in {"0", "false", "off", "disabled", "disable", "none"}:
        return {"type": "disabled"}
    if mode in {"1", "true", "on", "enabled", "enable"}:
        return {"type": "enabled"}
    return {"type": "disabled"}


def call_deepseek_chat_once(
    prompt: str,
    *,
    config: DeepSeekConfig,
    response_json: bool,
) -> DeepSeekCallResult:
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "stream": False,
    }
    thinking = _deepseek_thinking_payload()
    if thinking is not None:
        payload["thinking"] = thinking
    if response_json:
        payload["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        chat_completions_url(config.base_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {body}") from exc
    runtime_ms = int((time.perf_counter() - start) * 1000)
    choices = raw.get("choices") or []
    content = ""
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        content = str(message.get("content", ""))
    usage = raw.get("usage") or {}
    if not content.strip():
        raise RuntimeError("DeepSeek API returned empty content")
    return DeepSeekCallResult(
        content=content,
        runtime_ms=runtime_ms,
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        total_tokens=int(usage.get("total_tokens", 0) or 0),
        model=str(raw.get("model") or config.model),
    )


def call_deepseek_chat(
    prompt: str,
    *,
    config: DeepSeekConfig | None = None,
    response_json: bool = True,
    max_attempts: int | None = None,
) -> DeepSeekCallResult:
    config = config or DeepSeekConfig.from_env()
    attempts = max_attempts or _deepseek_max_attempts()
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            return call_deepseek_chat_once(prompt, config=config, response_json=response_json)
        except Exception as exc:
            last_error = exc
            if attempt >= attempts - 1 or not _retryable_deepseek_error(exc):
                raise
            time.sleep(_deepseek_retry_sleep_seconds(attempt))
    if last_error is not None:
        raise RuntimeError(f"DeepSeek API failed after {attempts} attempts: {last_error}") from last_error
    raise RuntimeError(f"DeepSeek API failed after {attempts} attempts")


def call_deepseek_atomic_extraction(
    prompt: str,
    semantic_type: str,
    *,
    config: DeepSeekConfig | None = None,
) -> tuple[dict[str, Any] | None, DeepSeekCallResult]:
    schema_hint = json.dumps(atomic_schema(semantic_type), ensure_ascii=False)
    enriched_prompt = (
        f"{prompt}\n\nReturn ONLY a single JSON object matching this JSON Schema:\n{schema_hint}"
    )
    result = call_deepseek_chat(enriched_prompt, config=config, response_json=True)
    facts = v3.extract_json(result.content)
    if not isinstance(facts, dict):
        return None, result
    return facts, result
