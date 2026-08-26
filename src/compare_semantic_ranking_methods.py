from __future__ import annotations

r"""比较语义歧义修复候选的无LLM基线与本地Qwen。

先运行：
    python .\src\build_semantic_ambiguity_benchmark.py --timeout 120

完整实验：
    python .\src\compare_semantic_ranking_methods.py --runs 5 --timeout 180

只验证非LLM基线：
    python .\src\compare_semantic_ranking_methods.py --runs 1000 --skip-qwen

重要：排序阶段只读取semantic-events.json和文档，不读取Oracle。所有预测生成
完毕后，评估阶段才加载semantic-oracle.csv。
"""

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
import time
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


PROJECT_DIR = Path(__file__).resolve().parents[1]
ROOT = PROJECT_DIR / "benchmark" / "semantic-ambiguity"
EVENT_FILE = ROOT / "semantic-events.json"
ORACLE_FILE = ROOT / "semantic-oracle.csv"
DOCUMENT_DIR = ROOT / "documents"
OUTPUT_DIR = PROJECT_DIR / "output"
SUMMARY_CSV = OUTPUT_DIR / "semantic-ranking-comparison.csv"
DETAIL_CSV = OUTPUT_DIR / "semantic-ranking-details.csv"
BY_EVENT_CSV = OUTPUT_DIR / "semantic-ranking-by-event.csv"
OUTPUT_JSON = OUTPUT_DIR / "semantic-ranking-comparison.json"
LOG_FILE = OUTPUT_DIR / "semantic-ranking-comparison.log"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON顶层必须是对象：{path}")
    return value


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise RuntimeError(f"CSV为空：{path}")
    return rows


def stable_seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def local_opener(base_url: str):
    host = (urlparse(base_url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return build_opener(ProxyHandler({}))
    return build_opener()


def http_json(
    method: str,
    url: str,
    timeout: int,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = (
        None
        if payload is None
        else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with local_opener(url).open(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("Ollama响应顶层不是JSON对象")
        return value
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接Ollama：{exc.reason}") from exc


def check_ollama(base_url: str, model: str, timeout: int) -> None:
    response = http_json("GET", f"{base_url.rstrip('/')}/api/tags", min(timeout, 20))
    names = {str(item.get("name", "")) for item in response.get("models", [])}
    if model not in names:
        raise RuntimeError(f"Ollama中没有模型{model}，当前模型={sorted(names)}")


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.I | re.S)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        if start < 0:
            raise ValueError("模型输出中没有JSON对象")
        value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    if not isinstance(value, dict):
        raise ValueError("模型输出顶层必须是JSON对象")
    return value


def qwen_call(
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    temperature: float,
    seed: int,
    timeout: int,
    num_predict: int,
    keep_alive: str,
    allow_schema_fallback: bool,
) -> tuple[dict[str, Any], int, str, dict[str, Any]]:
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": keep_alive,
        "format": schema,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": temperature,
            "seed": seed,
            "num_predict": num_predict,
        },
    }
    attempts: list[tuple[str, dict[str, Any]]] = [("json_schema", payload)]
    if allow_schema_fallback:
        fallback = deepcopy(payload)
        fallback["format"] = "json"
        attempts.append(("json_fallback", fallback))

    started = time.perf_counter()
    response: dict[str, Any] | None = None
    used_mode = ""
    for index, (mode, attempt) in enumerate(attempts):
        try:
            response = http_json(
                "POST",
                f"{base_url.rstrip('/')}/api/chat",
                timeout,
                attempt,
            )
            used_mode = mode
            break
        except RuntimeError as exc:
            if "HTTP 400" not in str(exc) or index == len(attempts) - 1:
                raise
    if response is None:
        raise RuntimeError("Ollama没有返回响应")
    runtime_ms = round((time.perf_counter() - started) * 1000)
    message = response.get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        content = response.get("response")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Ollama没有返回可解析文本")
    metadata = {
        "prompt_eval_count": response.get("prompt_eval_count", 0),
        "eval_count": response.get("eval_count", 0),
        "done_reason": response.get("done_reason", ""),
    }
    return extract_json(content), runtime_ms, used_mode, metadata


def read_documents(event: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in event["documents"]:
        path = DOCUMENT_DIR / item["file_name"]
        if not path.is_file():
            raise FileNotFoundError(path)
        result.append({**item, "text": path.read_text(encoding="utf-8-sig")})
    return result


def blinded_event(event: dict[str, Any], run_seed: int) -> dict[str, Any]:
    candidates = [deepcopy(item) for item in event["candidates"]]
    random.Random(stable_seed(run_seed, event["event_id"], "candidate-order")).shuffle(
        candidates
    )
    for index, candidate in enumerate(candidates):
        candidate["original_candidate_id"] = candidate["candidate_id"]
        candidate["candidate_id"] = f"OPTION_{chr(ord('A') + index)}"
    return {**event, "candidates": candidates, "document_contents": read_documents(event)}


def candidate_by_value(event: dict[str, Any], value: str) -> dict[str, Any] | None:
    matches = [
        candidate
        for candidate in event["candidates"]
        if str(candidate["proposed_value"]).strip() == str(value).strip()
    ]
    return matches[0] if len(matches) == 1 else None


def candidate_by_id(event: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    matches = [
        candidate
        for candidate in event["candidates"]
        if candidate["candidate_id"] == candidate_id
    ]
    return matches[0] if len(matches) == 1 else None


def select_first(event: dict[str, Any], _: int) -> tuple[dict[str, Any] | None, str]:
    return event["candidates"][0], "打乱后的首个候选"


def select_random(event: dict[str, Any], seed: int) -> tuple[dict[str, Any] | None, str]:
    selected = random.Random(stable_seed(seed, event["event_id"], "random")).choice(
        event["candidates"]
    )
    return selected, "均匀随机选择"


def numeric_occurrences(text: str, value: str) -> int:
    return len(re.findall(rf"(?<!\d){re.escape(value)}(?!\d)", text))


def select_value_frequency(
    event: dict[str, Any], _: int
) -> tuple[dict[str, Any] | None, str]:
    text = event["case_context"] + "\n" + "\n".join(
        item["text"] for item in event["document_contents"]
    )
    scores = {
        candidate["candidate_id"]: numeric_occurrences(
            text, str(candidate["proposed_value"])
        )
        for candidate in event["candidates"]
    }
    maximum = max(scores.values())
    winners = [
        candidate
        for candidate in event["candidates"]
        if scores[candidate["candidate_id"]] == maximum
    ]
    if len(winners) != 1:
        return None, f"词面频次并列，主动放弃：{scores}"
    return winners[0], f"选择文档中数值出现次数最多的候选：{scores}"


def parse_date(value: str) -> date:
    text = value.strip()
    return date.min if not text else date.fromisoformat(text)


def select_latest_authority(
    event: dict[str, Any], _: int
) -> tuple[dict[str, Any] | None, str]:
    documents = event["document_contents"]
    best_key = max(
        (int(item.get("authority", 0)), parse_date(item.get("effective_from", "")))
        for item in documents
    )
    active = [
        item
        for item in documents
        if (
            int(item.get("authority", 0)),
            parse_date(item.get("effective_from", "")),
        )
        == best_key
    ]
    text = "\n".join(item["text"] for item in active)
    present = [
        candidate
        for candidate in event["candidates"]
        if numeric_occurrences(text, str(candidate["proposed_value"])) > 0
    ]
    if len(present) != 1:
        return None, "最高权威且最新的文档仍包含多个候选值，主动放弃"
    return present[0], "选择最高权威、最新生效文档中的唯一候选值"


def qwen_system_prompt(constrained: bool) -> str:
    boundary = (
        "你只能选择给定OPTION_ID或ABSTAIN，禁止生成新数值、IRI、算子或OWL。"
        if constrained
        else "你需要直接给出文档支持的整数值；若证据不足则ABSTAIN。"
    )
    return (
        "你是保险合同语义消歧器。只能依据提供的案件上下文和文档判断，"
        "禁止使用外部常识。依次考虑：文件权威性、生效时间、一般规则与明确"
        "例外的优先关系、条件是否被当前案件满足、跨句指代和适用范围。"
        + boundary
        + "只输出严格JSON，不要Markdown和额外说明。"
    )


def qwen_prompt(event: dict[str, Any], constrained: bool) -> str:
    payload: dict[str, Any] = {
        "event_id": event["event_id"],
        "semantic_type": event["semantic_type"],
        "case_context": event["case_context"],
        "target": {
            "subject": event["subject_label"],
            "predicate": event["predicate_label"],
        },
        "documents": event["document_contents"],
    }
    if constrained:
        payload["formally_safe_candidates"] = [
            {
                "candidate_id": candidate["candidate_id"],
                "proposed_value": candidate["proposed_value"],
                "formal_effect": candidate["formal_effect"],
            }
            for candidate in event["candidates"]
        ]
        instruction = (
            "请选择语义上适用于当前案件的candidate_id。形式门禁通过只代表"
            "逻辑安全，不代表文档语义正确。证据不足时abstain=true。"
        )
    else:
        instruction = (
            "不要读取候选列表，直接根据文档给出target的整数值。"
            "证据不足时abstain=true且value为空。"
        )
    return instruction + "\n\n输入：\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def direct_schema() -> dict[str, Any]:
    # value故意不使用候选enum，便于测量直接生成的越界/幻觉率。
    return {
        "type": "object",
        "properties": {
            "abstain": {"type": "boolean"},
            "value": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string", "minLength": 1},
        },
        "required": ["abstain", "value", "confidence", "rationale"],
        "additionalProperties": False,
    }


def constrained_schema(candidate_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "abstain": {"type": "boolean"},
            "candidate_id": {"type": "string", "enum": [""] + candidate_ids},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string", "minLength": 1},
        },
        "required": ["abstain", "candidate_id", "confidence", "rationale"],
        "additionalProperties": False,
    }


def validate_common(payload: dict[str, Any], value_key: str) -> tuple[bool, str, float, str]:
    required = {"abstain", value_key, "confidence", "rationale"}
    if set(payload) != required:
        raise ValueError(f"JSON字段必须恰好为{sorted(required)}")
    if not isinstance(payload["abstain"], bool):
        raise ValueError("abstain必须是JSON布尔值")
    value = payload[value_key]
    if not isinstance(value, str):
        raise ValueError(f"{value_key}必须是字符串")
    confidence = payload["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence必须是数值")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("confidence必须位于[0,1]")
    rationale = payload["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("rationale必须是非空字符串")
    if payload["abstain"] and value != "":
        raise ValueError(f"abstain=true时{value_key}必须为空")
    if not payload["abstain"] and value == "":
        raise ValueError(f"abstain=false时{value_key}不能为空")
    return payload["abstain"], value, float(confidence), rationale.strip()


def run_qwen_method(
    event: dict[str, Any],
    method: str,
    args: argparse.Namespace,
    run_seed: int,
) -> dict[str, Any]:
    constrained = method == "QWEN_CONSTRAINED_CANDIDATE"
    candidate_ids = [candidate["candidate_id"] for candidate in event["candidates"]]
    schema = constrained_schema(candidate_ids) if constrained else direct_schema()
    payload, runtime_ms, response_mode, metadata = qwen_call(
        args.ollama_url,
        args.model,
        qwen_system_prompt(constrained),
        qwen_prompt(event, constrained),
        schema,
        args.temperature,
        run_seed,
        args.timeout,
        args.num_predict,
        args.keep_alive,
        allow_schema_fallback=True,
    )
    key = "candidate_id" if constrained else "value"
    abstain, selected_text, confidence, rationale = validate_common(payload, key)
    if abstain:
        return {
            "selected": None,
            "status": "ABSTAIN",
            "invalid_or_hallucinated": False,
            "confidence": confidence,
            "rationale": rationale,
            "runtime_ms": runtime_ms,
            "response_mode": response_mode,
            "metadata": metadata,
        }
    selected = (
        candidate_by_id(event, selected_text)
        if constrained
        else candidate_by_value(event, selected_text)
    )
    invalid = selected is None
    return {
        "selected": selected,
        "status": "REJECTED_INVALID" if invalid else "SELECTED",
        "invalid_or_hallucinated": invalid,
        "raw_selection": selected_text,
        "confidence": confidence,
        "rationale": rationale,
        "runtime_ms": runtime_ms,
        "response_mode": response_mode,
        "metadata": metadata,
    }


def prediction_row(
    method: str,
    run_id: int,
    run_seed: int,
    event: dict[str, Any],
    selected: dict[str, Any] | None,
    status: str,
    rationale: str,
    invalid: bool = False,
    confidence: float | str = "",
    runtime_ms: int = 0,
    response_mode: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    return {
        "method": method,
        "run_id": run_id,
        "seed": run_seed,
        "event_id": event["event_id"],
        "semantic_type": event["semantic_type"],
        "status": status,
        "selected": selected is not None,
        "abstained": status == "ABSTAIN",
        "invalid_or_hallucinated": invalid,
        "selected_candidate_id": selected["candidate_id"] if selected else "",
        "selected_original_candidate_id": (
            selected["original_candidate_id"] if selected else ""
        ),
        "selected_value": selected["proposed_value"] if selected else "",
        "confidence": confidence,
        "rationale": rationale,
        "runtime_ms": runtime_ms,
        "response_mode": response_mode,
        "error_message": error_message,
    }


def load_oracle_after_predictions(path: Path) -> dict[str, dict[str, str]]:
    """有意在所有预测生成完毕后调用，避免Oracle进入排序输入。"""
    return {row["event_id"]: row for row in load_csv(path)}


def evaluate_predictions(
    predictions: list[dict[str, Any]], oracle: dict[str, dict[str, str]]
) -> None:
    for row in predictions:
        expected = oracle[row["event_id"]]
        row["oracle_value"] = expected["oracle_value"]
        row["oracle_candidate_id"] = expected["oracle_candidate_id"]
        row["oracle_success"] = (
            row["selected"]
            and str(row["selected_value"]) == expected["oracle_value"]
        )


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        p * (1 - p) / total + z * z / (4 * total * total)
    ) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = len(rows)
    selected = sum(bool(row["selected"]) for row in rows)
    correct = sum(bool(row["oracle_success"]) for row in rows)
    invalid = sum(bool(row["invalid_or_hallucinated"]) for row in rows)
    abstained = sum(bool(row["abstained"]) for row in rows)
    incorrect_selected = selected - correct
    low, high = wilson_interval(correct, attempts)
    runtimes = [int(row["runtime_ms"]) for row in rows if int(row["runtime_ms"]) > 0]
    return {
        "method": rows[0]["method"],
        "independent_events": len({row["event_id"] for row in rows}),
        "runs": len({row["run_id"] for row in rows}),
        "attempts": attempts,
        "selected": selected,
        "coverage": selected / attempts,
        "abstained": abstained,
        "abstain_rate": abstained / attempts,
        "invalid_or_hallucinated": invalid,
        "invalid_or_hallucinated_rate": invalid / attempts,
        "oracle_successes": correct,
        "oracle_success_rate": correct / attempts,
        "oracle_95ci_low": low,
        "oracle_95ci_high": high,
        "semantic_wrong_selections": incorrect_selected,
        "semantic_wrong_selection_rate": incorrect_selected / attempts,
        "accepted_precision": correct / selected if selected else 0.0,
        "mean_runtime_ms": sum(runtimes) / len(runtimes) if runtimes else 0.0,
    }


def summarize_by_event(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["method"], row["event_id"])].append(row)
    result: list[dict[str, Any]] = []
    for (method, event_id), items in sorted(grouped.items()):
        attempts = len(items)
        selected = sum(bool(item["selected"]) for item in items)
        correct = sum(bool(item["oracle_success"]) for item in items)
        result.append({
            "method": method,
            "event_id": event_id,
            "semantic_type": items[0]["semantic_type"],
            "attempts": attempts,
            "coverage": selected / attempts,
            "oracle_success_rate": correct / attempts,
            "invalid_rate": sum(
                bool(item["invalid_or_hallucinated"]) for item in items
            )
            / attempts,
            "abstain_rate": sum(bool(item["abstained"]) for item in items) / attempts,
        })
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"没有数据可写入：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比较语义歧义候选排序方法")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--skip-qwen", action="store_true")
    parser.add_argument("--only", default="", help="只运行指定事件，例如E9")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--num-predict", type=int, default=300)
    parser.add_argument("--keep-alive", default="30m")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs必须大于0")
    for path in (EVENT_FILE, ORACLE_FILE):
        if not path.is_file():
            raise FileNotFoundError(
                f"缺少{path}，请先运行build_semantic_ambiguity_benchmark.py。"
            )

    payload = load_json(EVENT_FILE)
    events = payload.get("events", [])
    if not isinstance(events, list) or not events:
        raise RuntimeError("semantic-events.json没有events")
    if args.only:
        wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
        events = [event for event in events if event["event_id"].upper() in wanted]
        if not events:
            raise RuntimeError(f"--only没有匹配事件：{sorted(wanted)}")

    if not args.skip_qwen:
        print(f"[连接检查] Ollama={args.ollama_url}，模型={args.model}")
        check_ollama(args.ollama_url, args.model, args.timeout)
        print("[连接检查] Ollama与模型均可用。\n")

    deterministic = {
        "FIRST_POSITION": select_first,
        "RANDOM": select_random,
        "VALUE_FREQUENCY": select_value_frequency,
        "LATEST_AUTHORITY_UNIQUE": select_latest_authority,
    }
    qwen_methods = (
        []
        if args.skip_qwen
        else ["QWEN_DIRECT_VALUE", "QWEN_CONSTRAINED_CANDIDATE"]
    )
    predictions: list[dict[str, Any]] = []
    total_qwen = len(events) * args.runs * len(qwen_methods)
    qwen_index = 0
    print("语义歧义修复候选排序对比")
    print(f"事件={len(events)}，runs={args.runs}，Qwen调用计划={total_qwen}\n")

    for run_id in range(1, args.runs + 1):
        run_seed = args.seed + run_id - 1
        for source_event in events:
            event = blinded_event(source_event, run_seed)
            for method, chooser in deterministic.items():
                selected, rationale = chooser(
                    event, stable_seed(run_seed, method, event["event_id"])
                )
                predictions.append(
                    prediction_row(
                        method,
                        run_id,
                        run_seed,
                        event,
                        selected,
                        "SELECTED" if selected else "ABSTAIN",
                        rationale,
                    )
                )

            for method in qwen_methods:
                qwen_index += 1
                print(
                    f"[{qwen_index}/{total_qwen}] {event['event_id']} run={run_id} "
                    f"{method}：正在调用Qwen……"
                )
                try:
                    result = run_qwen_method(event, method, args, run_seed)
                    predictions.append(
                        prediction_row(
                            method,
                            run_id,
                            run_seed,
                            event,
                            result["selected"],
                            result["status"],
                            result["rationale"],
                            result["invalid_or_hallucinated"],
                            result["confidence"],
                            result["runtime_ms"],
                            result["response_mode"],
                        )
                    )
                    selected_text = (
                        result["selected"]["proposed_value"]
                        if result["selected"]
                        else "-"
                    )
                    print(
                        f"  结果={result['status']} | value={selected_text} | "
                        f"{result['runtime_ms']}ms"
                    )
                except Exception as exc:
                    predictions.append(
                        prediction_row(
                            method,
                            run_id,
                            run_seed,
                            event,
                            None,
                            "REJECTED_ERROR",
                            "",
                            invalid=True,
                            error_message=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    print(f"  结果=REJECTED_ERROR | {type(exc).__name__}: {exc}")

    # 排序过程到此结束；现在才加载单独的Oracle进行离线评分。
    oracle = load_oracle_after_predictions(ORACLE_FILE)
    evaluate_predictions(predictions, oracle)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        grouped[row["method"]].append(row)
    summaries = [summarize(rows) for _, rows in sorted(grouped.items())]
    by_event = summarize_by_event(predictions)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(SUMMARY_CSV, summaries)
    write_csv(DETAIL_CSV, predictions)
    write_csv(BY_EVENT_CSV, by_event)
    output = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "compare_semantic_ranking_methods.py",
        "oracle_loaded_after_prediction": True,
        "events": [event["event_id"] for event in events],
        "runs": args.runs,
        "seed": args.seed,
        "model": None if args.skip_qwen else args.model,
        "summary": summaries,
        "by_event": by_event,
        "details": predictions,
    }
    OUTPUT_JSON.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "语义歧义修复候选排序对比日志",
        f"事件：{', '.join(event['event_id'] for event in events)}",
        f"runs：{args.runs}",
        "Oracle在所有预测结束后加载。",
        "",
    ]
    print("\n方法汇总")
    for row in summaries:
        line = (
            f"[{row['method']}] 覆盖={row['coverage']:.2%} | "
            f"Oracle={row['oracle_success_rate']:.2%} | "
            f"错误选择={row['semantic_wrong_selection_rate']:.2%} | "
            f"无效/幻觉={row['invalid_or_hallucinated_rate']:.2%} | "
            f"ABSTAIN={row['abstain_rate']:.2%}"
        )
        print(line)
        lines.append(line)
    lines.extend([
        "",
        "重复runs衡量输出稳定性，不增加独立语义事件数量。",
        f"汇总：{SUMMARY_CSV}",
        f"明细：{DETAIL_CSV}",
        f"按事件：{BY_EVENT_CSV}",
        f"JSON：{OUTPUT_JSON}",
    ])
    LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n汇总：{SUMMARY_CSV}")
    print(f"明细：{DETAIL_CSV}")
    print(f"按事件：{BY_EVENT_CSV}")
    print(f"JSON：{OUTPUT_JSON}")
    print(f"日志：{LOG_FILE}")
    print("[完成] 请比较Qwen直接生成与受限候选选择的Oracle、幻觉和错误选择率。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[语义排序实验停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
