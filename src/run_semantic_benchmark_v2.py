from __future__ import annotations

"""运行语义歧义基准V2的盲化对比实验。

排序阶段只读取公开semantic-events.json与文档；全部预测结束后才加载私有
Oracle进行离线评分。安全候选仍可能语义错误，因此Oracle正确率不能当作
部署时在线门禁。
"""

import argparse
import json
import random
import re
import statistics
import sys
import time
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from semantic_v2_common import (
    BUILD_DIR,
    DOCUMENT_DIR,
    ORACLE_CSV,
    OUTPUT_DIR,
    check_ollama,
    extract_json,
    http_json,
    load_csv,
    stable_seed,
    wilson_interval,
    write_csv,
)


EVENT_JSON = BUILD_DIR / "semantic-events.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行语义歧义基准V2")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--num-predict", type=int, default=300)
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--keep-alive", default="30m")
    parser.add_argument("--skip-qwen", action="store_true")
    parser.add_argument(
        "--methods",
        default="FIRST_POSITION,RANDOM,LATEST_AUTHORITY_UNIQUE,QWEN_DIRECT_VALUE,QWEN_CONSTRAINED_CANDIDATE",
        help="逗号分隔的方法名",
    )
    return parser.parse_args()


def load_public_events(split: str) -> list[dict[str, Any]]:
    if not EVENT_JSON.is_file():
        raise FileNotFoundError(f"缺少{EVENT_JSON}，请先运行build_semantic_benchmark_v2.py")
    payload = json.loads(EVENT_JSON.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise RuntimeError("semantic-events.json结构错误")
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    if "oracle_candidate_id" in serialized or "oracle_value" in serialized:
        raise RuntimeError("公开事件文件含Oracle字段，实验中止")
    events = payload["events"]
    if split != "all":
        events = [event for event in events if event.get("split") == split]
    if not events:
        raise RuntimeError(f"split={split}没有事件")
    return events


def read_documents(event: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for document in event["documents"]:
        path = DOCUMENT_DIR / document["file_name"]
        if not path.is_file():
            raise FileNotFoundError(path)
        result.append({**document, "text": path.read_text(encoding="utf-8-sig")})
    return result


def blinded_event(event: dict[str, Any], run_seed: int) -> dict[str, Any]:
    candidates = deepcopy(event["candidates"])
    random.Random(stable_seed(run_seed, event["event_id"], "order")).shuffle(candidates)
    for index, candidate in enumerate(candidates):
        candidate["original_candidate_id"] = candidate["candidate_id"]
        candidate["candidate_id"] = f"OPTION_{chr(ord('A') + index)}"
        candidate.pop("operation", None)
        effect = candidate.get("formal_effect", {})
        candidate["formal_effect"] = {
            "reasoner_result": effect.get("reasoner_result"),
            "minimal_edit": effect.get("minimal_edit"),
            "triples_removed": effect.get("triples_removed"),
            "triples_added": effect.get("triples_added"),
        }
    return {**event, "candidates": candidates, "document_contents": read_documents(event)}


def by_option(event: dict[str, Any], option_id: str) -> dict[str, Any] | None:
    matches = [item for item in event["candidates"] if item["candidate_id"] == option_id]
    return matches[0] if len(matches) == 1 else None


def by_value(event: dict[str, Any], value: object) -> dict[str, Any] | None:
    text = str(value).strip()
    matches = [
        item for item in event["candidates"]
        if str(item["display_value"]).strip() == text
    ]
    return matches[0] if len(matches) == 1 else None


def first_position(event: dict[str, Any], _: int) -> tuple[dict[str, Any] | None, str, str]:
    return event["candidates"][0], "SELECTED", "打乱后的首个候选"


def random_choice(event: dict[str, Any], seed: int) -> tuple[dict[str, Any] | None, str, str]:
    selected = random.Random(stable_seed(seed, event["event_id"], "random")).choice(event["candidates"])
    return selected, "SELECTED", "候选中均匀随机选择"


def parse_date(value: object) -> date:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text) if text else date.min
    except ValueError:
        return date.min


def occurrence(text: str, value: object) -> int:
    token = str(value).strip()
    if not token:
        return 0
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", token):
        return len(re.findall(rf"(?<![\d.]){re.escape(token)}(?![\d.])", text))
    return text.count(token)


def latest_authority_unique(
    event: dict[str, Any], _: int
) -> tuple[dict[str, Any] | None, str, str]:
    documents = event["document_contents"]
    keys = [
        (int(item.get("authority", 0)), parse_date(item.get("effective_from", "")))
        for item in documents
    ]
    best = max(keys)
    active = [item for item, key in zip(documents, keys) if key == best]
    text = "\n".join(item["text"] for item in active)
    matches = [
        candidate for candidate in event["candidates"]
        if occurrence(text, candidate["display_value"]) > 0
    ]
    if len(matches) != 1:
        return None, "ABSTAIN", f"最高权威且最新文档无法唯一决定：匹配{len(matches)}个"
    return matches[0], "SELECTED", "最高权威且最新文档中的唯一候选值"


def qwen_call(
    args: argparse.Namespace,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    seed: int,
) -> tuple[dict[str, Any], int, str, str, dict[str, Any]]:
    payload = {
        "model": args.model,
        "stream": False,
        "think": False,
        "keep_alive": args.keep_alive,
        "format": schema,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": args.temperature,
            "seed": seed,
            "num_predict": args.num_predict,
        },
    }
    attempts = [("json_schema", payload)]
    fallback = deepcopy(payload)
    fallback["format"] = "json"
    attempts.append(("json_fallback", fallback))
    started = time.perf_counter()
    response: dict[str, Any] | None = None
    mode = ""
    last_error: Exception | None = None
    for attempt_mode, attempt in attempts:
        try:
            response = http_json(
                "POST", f"{args.ollama_url.rstrip('/')}/api/chat", args.timeout, attempt
            )
            mode = attempt_mode
            break
        except Exception as exc:
            last_error = exc
            if "HTTP 400" not in str(exc) or attempt_mode == "json_fallback":
                raise
    if response is None:
        raise RuntimeError(f"Ollama没有返回响应：{last_error}")
    runtime_ms = round((time.perf_counter() - started) * 1000)
    message = response.get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        content = response.get("response", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Ollama响应没有文本内容")
    metadata = {
        "prompt_eval_count": response.get("prompt_eval_count", 0),
        "eval_count": response.get("eval_count", 0),
        "done_reason": response.get("done_reason", ""),
    }
    return extract_json(content), runtime_ms, mode, content, metadata


def base_system_prompt(constrained: bool) -> str:
    boundary = (
        "只能选择给定OPTION_ID或ABSTAIN；禁止生成新数值、IRI、算子或OWL。"
        if constrained
        else "直接给出文档支持的目标值；不要读取或猜测候选列表。"
    )
    return (
        "你是受控文档语义消歧器。只能依据案件上下文和给定文档，禁止外部常识。"
        "依次判断文件权威性、生效时间、一般规则与例外优先级、条件是否满足、"
        "跨句指代和适用范围。证据不足必须ABSTAIN。" + boundary +
        "只输出严格JSON，不要Markdown、思考过程或额外文字。"
    )


def qwen_direct(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    payload = {
        "event_id": event["event_id"],
        "semantic_type": event["semantic_type"],
        "case_context": event["case_context"],
        "target": event["target"],
        "documents": event["document_contents"],
        "instruction": "输出value；证据不足时abstain=true且value为空字符串。",
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["abstain", "value", "reason"],
        "properties": {
            "abstain": {"type": "boolean"},
            "value": {"type": "string"},
            "reason": {"type": "string", "maxLength": 180},
        },
    }
    parsed, runtime_ms, mode, raw, metadata = qwen_call(
        args, base_system_prompt(False),
        json.dumps(payload, ensure_ascii=False, indent=2), schema, seed,
    )
    extra = {"runtime_ms": runtime_ms, "response_mode": mode, "raw_content": raw, **metadata}
    abstain = parsed.get("abstain")
    value = parsed.get("value")
    if not isinstance(abstain, bool) or not isinstance(value, str):
        return None, "REJECTED_SCHEMA", "字段类型不符合Schema", extra
    if abstain:
        return None, "ABSTAIN", str(parsed.get("reason", "")), extra
    selected = by_value(event, value)
    if selected is None:
        return None, "REJECTED_OUT_OF_CANDIDATE", f"直接生成值不在候选白名单：{value!r}", extra
    return selected, "SELECTED", str(parsed.get("reason", "")), extra


def qwen_constrained(
    event: dict[str, Any], seed: int, args: argparse.Namespace
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    option_ids = [item["candidate_id"] for item in event["candidates"]]
    payload = {
        "event_id": event["event_id"],
        "semantic_type": event["semantic_type"],
        "case_context": event["case_context"],
        "target": event["target"],
        "documents": event["document_contents"],
        "formally_safe_candidates": [
            {
                "option_id": item["candidate_id"],
                "description": item["description"],
                "display_value": item["display_value"],
                "formal_effect": item["formal_effect"],
            }
            for item in event["candidates"]
        ],
        "instruction": "选择语义上适用于当前案件的option_id；证据不足则ABSTAIN。",
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["abstain", "option_id", "reason"],
        "properties": {
            "abstain": {"type": "boolean"},
            "option_id": {"type": "string", "enum": option_ids + ["ABSTAIN"]},
            "reason": {"type": "string", "maxLength": 180},
        },
    }
    parsed, runtime_ms, mode, raw, metadata = qwen_call(
        args, base_system_prompt(True),
        json.dumps(payload, ensure_ascii=False, indent=2), schema, seed,
    )
    extra = {"runtime_ms": runtime_ms, "response_mode": mode, "raw_content": raw, **metadata}
    abstain = parsed.get("abstain")
    option_id = parsed.get("option_id")
    if not isinstance(abstain, bool) or not isinstance(option_id, str):
        return None, "REJECTED_SCHEMA", "字段类型不符合Schema", extra
    if abstain or option_id == "ABSTAIN":
        return None, "ABSTAIN", str(parsed.get("reason", "")), extra
    selected = by_option(event, option_id)
    if selected is None:
        return None, "REJECTED_OUT_OF_CANDIDATE", f"非法option_id={option_id!r}", extra
    return selected, "SELECTED", str(parsed.get("reason", "")), extra


def load_oracle_after_predictions() -> dict[str, dict[str, str]]:
    # 调用位置必须位于预测循环之后；Oracle只用于离线评分。
    rows = [row for row in load_csv(ORACLE_CSV) if row.get("status", "").strip().upper() == "READY"]
    return {row["event_id"]: row for row in rows}


def summarize(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        groups[(str(row["method"]), str(row["semantic_type"]))].append(row)
        groups[(str(row["method"]), "ALL")].append(row)
    rows: list[dict[str, Any]] = []
    for (method, semantic_type), items in sorted(groups.items()):
        total = len(items)
        selected = sum(item["status"] == "SELECTED" for item in items)
        correct = sum(bool(item["oracle_correct"]) for item in items)
        invalid = sum(str(item["status"]).startswith("REJECTED") for item in items)
        abstain = sum(item["status"] == "ABSTAIN" for item in items)
        wrong = sum(item["status"] == "SELECTED" and not item["oracle_correct"] for item in items)
        low, high = wilson_interval(correct, total)
        runtimes = [int(item["runtime_ms"]) for item in items if int(item["runtime_ms"]) > 0]
        rows.append({
            "method": method,
            "semantic_type": semantic_type,
            "attempts": total,
            "coverage": selected / total,
            "oracle_accuracy": correct / total,
            "oracle_ci95_low": low,
            "oracle_ci95_high": high,
            "wrong_selection_rate": wrong / total,
            "invalid_output_rate": invalid / total,
            "abstain_rate": abstain / total,
            "mean_runtime_ms": round(statistics.mean(runtimes), 2) if runtimes else 0,
        })
    return rows


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs必须大于0")
    methods = [item.strip().upper() for item in args.methods.split(",") if item.strip()]
    allowed = {
        "FIRST_POSITION", "RANDOM", "LATEST_AUTHORITY_UNIQUE",
        "QWEN_DIRECT_VALUE", "QWEN_CONSTRAINED_CANDIDATE",
    }
    unknown = sorted(set(methods) - allowed)
    if unknown:
        raise ValueError(f"未知方法：{unknown}")
    if args.skip_qwen:
        methods = [item for item in methods if not item.startswith("QWEN_")]
    if not methods:
        raise ValueError("没有可运行的方法")
    events = load_public_events(args.split)
    qwen_methods = [item for item in methods if item.startswith("QWEN_")]
    if qwen_methods:
        print(f"[连接检查] Ollama={args.ollama_url}，模型={args.model}")
        check_ollama(args.ollama_url, args.model, args.timeout)
        print("[连接检查] Ollama与模型均可用。")

    total = len(events) * args.runs * len(methods)
    print("\n语义歧义基准V2盲化实验")
    print(f"split={args.split}，事件={len(events)}，runs={args.runs}，总尝试={total}")
    details: list[dict[str, Any]] = []
    counter = 0
    deterministic: dict[str, Callable[..., Any]] = {
        "FIRST_POSITION": first_position,
        "RANDOM": random_choice,
        "LATEST_AUTHORITY_UNIQUE": latest_authority_unique,
    }
    for run in range(1, args.runs + 1):
        run_seed = args.seed + run - 1
        for source_event in events:
            event = blinded_event(source_event, run_seed)
            for method in methods:
                counter += 1
                print(f"[{counter}/{total}] {event['event_id']} run={run} {method}", flush=True)
                selected: dict[str, Any] | None = None
                status = ""
                reason = ""
                extra: dict[str, Any] = {}
                try:
                    if method in deterministic:
                        selected, status, reason = deterministic[method](event, run_seed)
                    elif method == "QWEN_DIRECT_VALUE":
                        selected, status, reason, extra = qwen_direct(
                            event, stable_seed(run_seed, event["event_id"], method), args
                        )
                    else:
                        selected, status, reason, extra = qwen_constrained(
                            event, stable_seed(run_seed, event["event_id"], method), args
                        )
                except Exception as exc:
                    status = "REJECTED_ERROR"
                    reason = f"{type(exc).__name__}: {exc}"
                original_id = selected.get("original_candidate_id", "") if selected else ""
                displayed_id = selected.get("candidate_id", "") if selected else ""
                display_value = selected.get("display_value", "") if selected else ""
                details.append({
                    "split": source_event["split"],
                    "event_id": event["event_id"],
                    "semantic_type": event["semantic_type"],
                    "run": run,
                    "seed": run_seed,
                    "method": method,
                    "status": status,
                    "selected_option_id": displayed_id,
                    "selected_candidate_id": original_id,
                    "selected_value": display_value,
                    "reason": reason,
                    "candidate_order": "|".join(
                        f"{item['candidate_id']}={item['original_candidate_id']}"
                        for item in event["candidates"]
                    ),
                    "runtime_ms": extra.get("runtime_ms", 0),
                    "response_mode": extra.get("response_mode", ""),
                    "prompt_eval_count": extra.get("prompt_eval_count", 0),
                    "eval_count": extra.get("eval_count", 0),
                    "done_reason": extra.get("done_reason", ""),
                    "raw_content": extra.get("raw_content", ""),
                })
                print(
                    f"  结果={status} | candidate={original_id or '-'} | "
                    f"value={display_value or '-'} | {extra.get('runtime_ms', 0)}ms",
                    flush=True,
                )

    # 关键隔离点：只有全部预测结束后，才加载私有Oracle并评分。
    oracles = load_oracle_after_predictions()
    for row in details:
        oracle = oracles.get(str(row["event_id"]))
        if oracle is None:
            raise RuntimeError(f"{row['event_id']}缺少READY Oracle")
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        row["oracle_value"] = oracle["oracle_value"]
        row["oracle_correct"] = (
            row["status"] == "SELECTED"
            and row["selected_candidate_id"] == oracle["oracle_candidate_id"]
        )

    summary = summarize(details)
    output_prefix = f"semantic-v2-{args.split}"
    detail_csv = OUTPUT_DIR / f"{output_prefix}-details.csv"
    summary_csv = OUTPUT_DIR / f"{output_prefix}-summary.csv"
    by_event_csv = OUTPUT_DIR / f"{output_prefix}-by-event.csv"
    output_json = OUTPUT_DIR / f"{output_prefix}-results.json"
    log_file = OUTPUT_DIR / f"{output_prefix}.log"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(detail_csv, details)
    write_csv(summary_csv, summary)

    by_event: list[dict[str, Any]] = []
    event_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        event_groups[(str(row["method"]), str(row["event_id"]))].append(row)
    for (method, event_id), items in sorted(event_groups.items()):
        correct = sum(bool(item["oracle_correct"]) for item in items)
        by_event.append({
            "method": method,
            "event_id": event_id,
            "semantic_type": items[0]["semantic_type"],
            "attempts": len(items),
            "oracle_successes": correct,
            "oracle_accuracy": correct / len(items),
            "invalid_outputs": sum(str(item["status"]).startswith("REJECTED") for item in items),
            "abstains": sum(item["status"] == "ABSTAIN" for item in items),
        })
    write_csv(by_event_csv, by_event)
    payload = {
        "schema_version": "2.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "split": args.split,
        "event_count": len(events),
        "runs": args.runs,
        "oracle_loaded_after_predictions": True,
        "summary": summary,
        "by_event": by_event,
        "details": details,
    }
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "语义歧义基准V2实验日志",
        f"split={args.split}，事件={len(events)}，runs={args.runs}",
        "Oracle在全部预测结束后加载=True",
        "",
    ]
    print("\n方法汇总")
    for row in summary:
        if row["semantic_type"] != "ALL":
            continue
        line = (
            f"[{row['method']}] 覆盖={row['coverage']:.2%} | "
            f"Oracle={row['oracle_accuracy']:.2%} | "
            f"95%CI=[{row['oracle_ci95_low']:.2%}, {row['oracle_ci95_high']:.2%}] | "
            f"错误选择={row['wrong_selection_rate']:.2%} | "
            f"无效={row['invalid_output_rate']:.2%} | ABSTAIN={row['abstain_rate']:.2%}"
        )
        print(line)
        lines.append(line)
    lines.extend(["", f"明细：{detail_csv}", f"汇总：{summary_csv}", f"按事件：{by_event_csv}", f"JSON：{output_json}"])
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n明细：{detail_csv}")
    print(f"汇总：{summary_csv}")
    print(f"按事件：{by_event_csv}")
    print(f"JSON：{output_json}")
    print("[边界] Oracle正确率是离线评估结果，不是部署时可用的在线门禁。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[实验停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
