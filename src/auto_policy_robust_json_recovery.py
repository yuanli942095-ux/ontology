from __future__ import annotations

"""Conservative offline JSON recovery for Auto Policy Qwen outputs.

Format-only repairs: strip fences/preamble, minor syntax fixes, bracket matching,
multi-JSON selection. Never invents semantic fields from Oracle/candidates.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

NUM_PREDICT = 1000

FAILURE_BUCKETS: dict[str, str] = {
    "FENCE_OR_PREAMBLE": "JSON mostly complete; code fence or surrounding prose",
    "MINOR_SYNTAX": "Single quotes, trailing commas, or escape issues",
    "MULTIPLE_JSON": "Multiple JSON objects in one response",
    "TRUNCATED": "JSON cut off before closing structure",
    "WRONG_BOUNDARY": "Complete JSON but greedy parser picked wrong span",
    "SEVERELY_DAMAGED": "Unrecoverable without semantic guessing",
    "RAW_TEXT_MISSING": "Raw Qwen text not saved; fine bucket unknown",
    "TRUNCATED_PROXY": "Metadata-only proxy: done_reason=length or eval_count at cap",
    "NON_TRUNCATED_PROXY": "Metadata-only proxy: done_reason=stop; sub-bucket unknown",
}


@dataclass(frozen=True)
class RecoveryResult:
    parsed: dict[str, Any] | None
    bucket: str
    method: str
    salvageable: bool
    detail: str = ""


def looks_truncated_metadata(record: dict[str, Any]) -> bool:
    done = str(record.get("done_reason") or "").strip().lower()
    if done in {"length", "max_tokens", "max_length"}:
        return True
    try:
        eval_count = int(record.get("eval_count") or 0)
    except (TypeError, ValueError):
        eval_count = 0
    return eval_count >= NUM_PREDICT


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json") :]
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```") :]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _try_load(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def find_balanced_objects(text: str) -> list[tuple[int, int, str]]:
    """Return non-greedy top-level {...} spans via bracket matching."""
    spans: list[tuple[int, int, str]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_string = False
        escape = False
        start = i
        for j in range(i, n):
            ch = text[j]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    snippet = text[start : j + 1]
                    spans.append((start, j + 1, snippet))
                    i = j + 1
                    break
        else:
            # Unclosed object — truncated tail span for diagnosis.
            spans.append((start, n, text[start:]))
            break
    return spans


def greedy_regex_span(text: str) -> str | None:
    match = re.search(r"\{.*\}", text, flags=re.S)
    return match.group(0) if match else None


def recover_json_object(text: str) -> RecoveryResult:
    """Attempt format-only recovery; returns first valid dict object."""
    raw = text.strip()
    if not raw:
        return RecoveryResult(None, "SEVERELY_DAMAGED", "empty", False, "empty response")

    fenced = strip_code_fences(raw)
    had_fence = fenced != raw

    def first_valid_dict(
        spans: list[tuple[int, int, str]],
    ) -> list[tuple[int, dict[str, Any], str, str]]:
        found: list[tuple[int, dict[str, Any], str, str]] = []
        for start, end, snippet in spans:
            parsed = _try_load(snippet)
            method = "balanced_object"
            if not isinstance(parsed, dict):
                fixed = _remove_trailing_commas(snippet)
                parsed = _try_load(fixed)
                method = "trailing_comma"
            if isinstance(parsed, dict):
                found.append((start, parsed, snippet, method))
        return found

    # 1) Direct parse (full body, optionally fence-stripped)
    for candidate_text, method in ((raw, "direct"), (fenced, "strip_fence")):
        parsed = _try_load(candidate_text)
        if isinstance(parsed, dict):
            bucket = "FENCE_OR_PREAMBLE" if method == "strip_fence" or had_fence else "FENCE_OR_PREAMBLE"
            return RecoveryResult(parsed, bucket, method, True)

    spans = find_balanced_objects(raw)
    valid = first_valid_dict(spans)

    # 2) Multiple complete JSON objects
    if len(valid) > 1:
        return RecoveryResult(
            valid[0][1],
            "MULTIPLE_JSON",
            "first_valid_object",
            True,
            f"{len(valid)} objects",
        )

    # 3) Single balanced object
    if len(valid) == 1:
        start, parsed, _snippet, method = valid[0]
        bucket = "MINOR_SYNTAX" if method == "trailing_comma" else "FENCE_OR_PREAMBLE"
        return RecoveryResult(parsed, bucket, method, True)

    # 4) Minor syntax on fenced body
    minor = _remove_trailing_commas(fenced)
    parsed = _try_load(minor)
    if isinstance(parsed, dict):
        return RecoveryResult(parsed, "MINOR_SYNTAX", "trailing_comma", True)

    # 5) Truncated tail object
    if spans:
        _start, end, snippet = spans[-1]
        if end == len(raw) and snippet.count("{") > snippet.count("}"):
            return RecoveryResult(None, "TRUNCATED", "unclosed", False, "missing closing brace")

    if raw.lstrip().startswith("{") and not raw.rstrip().endswith("}"):
        return RecoveryResult(None, "TRUNCATED", "unclosed", False, "missing closing brace")

    # 6) Greedy regex (legacy parser) vs balanced — boundary diagnosis
    greedy = greedy_regex_span(fenced)
    if greedy:
        parsed = _try_load(greedy)
        balanced = find_balanced_objects(fenced)
        if parsed is None and balanced:
            return RecoveryResult(None, "WRONG_BOUNDARY", "greedy_failed", False)
        if parsed is not None and balanced and greedy != balanced[0][2]:
            return RecoveryResult(parsed, "WRONG_BOUNDARY", "greedy_differs", True)

    return RecoveryResult(None, "SEVERELY_DAMAGED", "none", False)


def classify_failure(text: str | None, record: dict[str, Any]) -> RecoveryResult:
    """Classify INVALID_JSON without mutating record; tries recovery when text exists."""
    if not text or not str(text).strip():
        if looks_truncated_metadata(record):
            return RecoveryResult(
                None,
                "TRUNCATED_PROXY",
                "metadata_only",
                False,
                "raw_response_text missing; done_reason/eval_count suggest truncation",
            )
        return RecoveryResult(
            None,
            "NON_TRUNCATED_PROXY",
            "metadata_only",
            False,
            "raw_response_text missing; done_reason=stop; fine bucket unknown",
        )

    result = recover_json_object(str(text))
    if result.parsed is not None:
        return result

    if looks_truncated_metadata(record) and result.bucket not in {
        "FENCE_OR_PREAMBLE",
        "MINOR_SYNTAX",
        "MULTIPLE_JSON",
        "WRONG_BOUNDARY",
    }:
        return RecoveryResult(
            None,
            "TRUNCATED",
            result.method,
            False,
            result.detail or "metadata suggests token limit truncation",
        )
    return result


def classify_failure_metadata_only(record: dict[str, Any]) -> RecoveryResult:
    raw = record.get("raw_response_text") or record.get("response_text")
    if isinstance(raw, str) and raw.strip():
        return classify_failure(raw, record)
    return classify_failure(None, record)
