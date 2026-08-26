from __future__ import annotations

r"""审计 Qwen 直接生成数值时的失败类型。

用途：把旧实验中的“无效/幻觉”拆成可解释、可复现的类别：

1. JSON 解析错误；
2. JSON 结构/类型错误；
3. 带单位但可归一化的正确值，例如“65周岁”“0天”“24个月”；
4. 白名单外数值；
5. 归一化后仍然选择了错误候选；
6. 严格接口下正确的输出。

严格门禁的判定不会被放宽。归一化结果只用于离线诊断，不能自动进入本体。

先确保同目录存在 compare_semantic_ranking_methods.py，并且已经构建 E9-E11：

    python .\src\build_semantic_ambiguity_benchmark.py --timeout 120

推荐运行：

    python .\src\audit_direct_generation_outputs.py `
      --runs 5 `
      --temperature 0.2 `
      --seed 20260820 `
      --timeout 180 `
      --num-predict 300

若旧版明细CSV中已经保存raw_content，可零调用重新审计：

    python .\src\audit_direct_generation_outputs.py --reanalyze-existing
"""

import argparse
import csv
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import compare_semantic_ranking_methods as base
except ImportError as exc:
    print(
        "缺少 compare_semantic_ranking_methods.py。"
        "请把本脚本放到 ontology-evolution\\src。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
ROOT = PROJECT_DIR / "benchmark" / "semantic-ambiguity"
EVENT_FILE = ROOT / "semantic-events.json"
ORACLE_FILE = ROOT / "semantic-oracle.csv"
OUTPUT_DIR = PROJECT_DIR / "output"
DETAIL_CSV = OUTPUT_DIR / "direct-generation-audit-details.csv"
SUMMARY_CSV = OUTPUT_DIR / "direct-generation-audit-summary.csv"
BY_EVENT_CSV = OUTPUT_DIR / "direct-generation-audit-by-event.csv"
OUTPUT_JSON = OUTPUT_DIR / "direct-generation-audit.json"
LOG_FILE = OUTPUT_DIR / "direct-generation-audit.log"


UNIT_PATTERN = re.compile(
    r"^\s*([+-]?\d+)\s*(周岁|岁|天|日|个月|月)\s*$",
    re.IGNORECASE,
)
INTEGER_PATTERN = re.compile(r"^\s*([+-]?\d+)\s*$")
ANY_INTEGER_PATTERN = re.compile(r"(?<!\d)([+-]?\d+)(?!\d)")


def bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "pass"}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"没有数据可写入：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def raw_qwen_call(
    event: dict[str, Any],
    args: argparse.Namespace,
    run_seed: int,
) -> dict[str, Any]:
    """调用与原实验相同的直接生成接口，但保留模型原始文本。"""
    schema = base.direct_schema()
    payload = {
        "model": args.model,
        "stream": False,
        "think": False,
        "keep_alive": args.keep_alive,
        "format": schema,
        "messages": [
            {"role": "system", "content": base.qwen_system_prompt(False)},
            {"role": "user", "content": base.qwen_prompt(event, False)},
        ],
        "options": {
            "temperature": args.temperature,
            "seed": run_seed,
            "num_predict": args.num_predict,
        },
    }
    attempts: list[tuple[str, dict[str, Any]]] = [("json_schema", payload)]
    if not args.no_schema_fallback:
        fallback = deepcopy(payload)
        fallback["format"] = "json"
        attempts.append(("json_fallback", fallback))

    started = time.perf_counter()
    response: dict[str, Any] | None = None
    response_mode = ""
    for index, (mode, attempt) in enumerate(attempts):
        try:
            response = base.http_json(
                "POST",
                f"{args.ollama_url.rstrip('/')}/api/chat",
                args.timeout,
                attempt,
            )
            response_mode = mode
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
        raise RuntimeError("Ollama没有返回文本")
    return {
        "raw_content": content,
        "runtime_ms": runtime_ms,
        "response_mode": response_mode,
        "prompt_eval_count": response.get("prompt_eval_count", 0),
        "eval_count": response.get("eval_count", 0),
        "done_reason": response.get("done_reason", ""),
    }


def loose_value_from_text(text: str) -> object | None:
    """尽量从损坏的 JSON 中只提取 value 字段，不能用于在线接受。"""
    quoted = re.search(
        r'["\']?value["\']?\s*:\s*["\']([^"\']*)',
        text,
        re.IGNORECASE,
    )
    if quoted:
        return quoted.group(1)
    numeric = re.search(
        r'["\']?value["\']?\s*:\s*([+-]?\d+(?:\.\d+)?)',
        text,
        re.IGNORECASE,
    )
    if numeric:
        value = numeric.group(1)
        return float(value) if "." in value else int(value)
    stripped = text.strip()
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", stripped):
        return float(stripped) if "." in stripped else int(stripped)
    return None


def normalize_numeric(value: object | None) -> dict[str, Any]:
    """离线诊断归一化；返回结果绝不能直接绕过安全门禁。"""
    result = {
        "normalized_value": "",
        "weak_extracted_value": "",
        "normalization_kind": "NO_VALUE",
        "normalization_strength": "NONE",
        "normalizable": False,
        "weak_extractable": False,
    }
    if value is None:
        return result
    if isinstance(value, bool):
        result["normalization_kind"] = "BOOLEAN_NOT_NUMERIC"
        return result
    if isinstance(value, int):
        result.update({
            "normalized_value": str(value),
            "normalization_kind": "NUMERIC_JSON_TYPE",
            "normalization_strength": "STRONG",
            "normalizable": True,
        })
        return result
    if isinstance(value, float):
        if value.is_integer():
            result.update({
                "normalized_value": str(int(value)),
                "normalization_kind": "NUMERIC_JSON_TYPE",
                "normalization_strength": "STRONG",
                "normalizable": True,
            })
        else:
            result["normalization_kind"] = "NON_INTEGER_NUMBER"
        return result
    if not isinstance(value, str):
        result["normalization_kind"] = "UNSUPPORTED_VALUE_TYPE"
        return result

    text = value.strip()
    if not text:
        result["normalization_kind"] = "EMPTY_VALUE"
        return result
    exact = INTEGER_PATTERN.fullmatch(text)
    if exact:
        result.update({
            "normalized_value": str(int(exact.group(1))),
            "normalization_kind": "EXACT_INTEGER_STRING",
            "normalization_strength": "STRONG",
            "normalizable": True,
        })
        return result
    unit = UNIT_PATTERN.fullmatch(text)
    if unit:
        result.update({
            "normalized_value": str(int(unit.group(1))),
            "normalization_kind": "UNIT_DECORATED_VALUE",
            "normalization_strength": "STRONG",
            "normalizable": True,
        })
        return result

    numbers = ANY_INTEGER_PATTERN.findall(text)
    if len(numbers) == 1:
        # d0、v2 等字母数字粘连标记不能当作保险业务数值。
        if re.search(r"[A-Za-z\u4e00-\u9fff]\d|\d[A-Za-z\u4e00-\u9fff]", text):
            result["normalization_kind"] = "ALPHANUMERIC_TOKEN"
        else:
            # 从整句或嵌套文本里抽取单个数字只能算弱证据，不能算安全归一化。
            result.update({
                "weak_extracted_value": str(int(numbers[0])),
                "normalization_kind": "FREE_TEXT_SINGLE_NUMBER",
                "normalization_strength": "WEAK",
                "weak_extractable": True,
            })
    elif len(numbers) > 1:
        result["normalization_kind"] = "AMBIGUOUS_MULTIPLE_NUMBERS"
    else:
        result["normalization_kind"] = "NON_NUMERIC_VALUE"
    return result


def best_effort_payload_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"abstain_raw": "", "confidence": "", "rationale": ""}
    return {
        "abstain_raw": payload.get("abstain", ""),
        "confidence": payload.get("confidence", ""),
        "rationale": payload.get("rationale", ""),
    }


def audit_output(
    event: dict[str, Any],
    call_result: dict[str, Any],
) -> dict[str, Any]:
    raw_content = call_result["raw_content"]
    parsed: dict[str, Any] | None = None
    parse_error = ""
    schema_error = ""
    json_parse_pass = False
    schema_pass = False
    strict_abstain = False
    strict_selected: dict[str, Any] | None = None
    raw_value: object | None = None

    try:
        parsed = base.extract_json(raw_content)
        json_parse_pass = True
        raw_value = parsed.get("value")
    except Exception as exc:
        parse_error = f"{type(exc).__name__}: {exc}"
        raw_value = loose_value_from_text(raw_content)

    if parsed is not None:
        try:
            abstain, value, _, _ = base.validate_common(parsed, "value")
            schema_pass = True
            strict_abstain = abstain
            if not abstain:
                strict_selected = base.candidate_by_value(event, value)
        except Exception as exc:
            schema_error = f"{type(exc).__name__}: {exc}"

    normalized = normalize_numeric(raw_value)
    normalized_selected = None
    if normalized["normalizable"]:
        normalized_selected = base.candidate_by_value(
            event, normalized["normalized_value"]
        )
    weak_selected = None
    if normalized["weak_extractable"]:
        weak_selected = base.candidate_by_value(
            event, normalized["weak_extracted_value"]
        )

    exact_candidate_pass = strict_selected is not None
    strict_accepted = exact_candidate_pass
    format_error = not json_parse_pass or not schema_pass
    out_of_candidate = bool(
        normalized["normalized_value"] and normalized_selected is None
    )
    normalizable_violation = bool(
        normalized_selected is not None and not strict_accepted
    )
    weak_text_candidate_match = weak_selected is not None

    if strict_accepted:
        category = "STRICT_VALID_SELECTED"
        strict_status = "SELECTED"
    elif strict_abstain and schema_pass:
        category = "STRICT_VALID_ABSTAIN"
        strict_status = "ABSTAIN"
    elif not json_parse_pass:
        category = (
            "JSON_ERROR_NORMALIZABLE"
            if normalized_selected is not None
            else (
                "JSON_ERROR_WEAKLY_EXTRACTABLE"
                if weak_selected is not None
                else "JSON_FORMAT_ERROR"
            )
        )
        strict_status = "REJECTED_JSON"
    elif not schema_pass:
        category = (
            "SCHEMA_ERROR_NORMALIZABLE"
            if normalized_selected is not None
            else (
                "SCHEMA_ERROR_WEAKLY_EXTRACTABLE"
                if weak_selected is not None
                else "SCHEMA_ERROR"
            )
        )
        strict_status = "REJECTED_SCHEMA"
    elif normalized_selected is not None:
        category = normalized["normalization_kind"] + "_NORMALIZABLE"
        strict_status = "REJECTED_NORMALIZABLE"
    elif weak_selected is not None:
        category = "WEAK_TEXT_EXTRACTION_CANDIDATE"
        strict_status = "REJECTED_WEAK_EXTRACTION"
    elif out_of_candidate:
        category = "OUT_OF_CANDIDATE_VALUE"
        strict_status = "REJECTED_OUT_OF_CANDIDATE"
    else:
        category = normalized["normalization_kind"]
        strict_status = "REJECTED_NON_NUMERIC"

    row = {
        "strict_status": strict_status,
        "audit_category": category,
        "json_parse_pass": json_parse_pass,
        "schema_pass": schema_pass,
        "candidate_whitelist_pass": exact_candidate_pass,
        "strict_accepted": strict_accepted,
        "strict_abstain": strict_abstain and schema_pass,
        "strict_selected_candidate_id": (
            strict_selected["candidate_id"] if strict_selected else ""
        ),
        "strict_selected_value": (
            strict_selected["proposed_value"] if strict_selected else ""
        ),
        "raw_value": "" if raw_value is None else raw_value,
        **normalized,
        "normalized_candidate_id": (
            normalized_selected["candidate_id"] if normalized_selected else ""
        ),
        "normalized_candidate_value": (
            normalized_selected["proposed_value"] if normalized_selected else ""
        ),
        "weak_extracted_candidate_id": (
            weak_selected["candidate_id"] if weak_selected else ""
        ),
        "weak_extracted_candidate_value": (
            weak_selected["proposed_value"] if weak_selected else ""
        ),
        "format_error": format_error,
        "normalizable_violation": normalizable_violation,
        "weak_text_candidate_match": weak_text_candidate_match,
        "out_of_candidate_value": out_of_candidate,
        "parse_error": parse_error,
        "schema_error": schema_error,
        **best_effort_payload_fields(parsed),
        **call_result,
    }
    return row


def failed_call_row(exc: Exception) -> dict[str, Any]:
    return {
        "strict_status": "CALL_ERROR",
        "audit_category": "TRANSPORT_OR_RUNTIME_ERROR",
        "json_parse_pass": False,
        "schema_pass": False,
        "candidate_whitelist_pass": False,
        "strict_accepted": False,
        "strict_abstain": False,
        "strict_selected_candidate_id": "",
        "strict_selected_value": "",
        "raw_value": "",
        "normalized_value": "",
        "weak_extracted_value": "",
        "normalization_kind": "NO_VALUE",
        "normalization_strength": "NONE",
        "normalizable": False,
        "weak_extractable": False,
        "normalized_candidate_id": "",
        "normalized_candidate_value": "",
        "weak_extracted_candidate_id": "",
        "weak_extracted_candidate_value": "",
        "format_error": False,
        "normalizable_violation": False,
        "weak_text_candidate_match": False,
        "out_of_candidate_value": False,
        "parse_error": "",
        "schema_error": "",
        "abstain_raw": "",
        "confidence": "",
        "rationale": "",
        "raw_content": "",
        "runtime_ms": 0,
        "response_mode": "",
        "prompt_eval_count": 0,
        "eval_count": 0,
        "done_reason": "",
        "call_error": f"{type(exc).__name__}: {exc}",
    }


def load_oracle() -> dict[str, dict[str, str]]:
    return {row["event_id"]: row for row in base.load_csv(ORACLE_FILE)}


def evaluate(rows: list[dict[str, Any]], oracle: dict[str, dict[str, str]]) -> None:
    for row in rows:
        expected = oracle[row["event_id"]]
        row["oracle_value"] = expected["oracle_value"]
        row["oracle_candidate_id"] = expected["oracle_candidate_id"]
        row["strict_oracle_success"] = bool(
            row["strict_accepted"]
            and str(row["strict_selected_value"]) == expected["oracle_value"]
        )
        row["normalized_oracle_success"] = bool(
            row["normalized_candidate_id"]
            and str(row["normalized_candidate_value"]) == expected["oracle_value"]
        )
        row["weak_extraction_oracle_match"] = bool(
            row["weak_extracted_candidate_id"]
            and str(row["weak_extracted_candidate_value"]) == expected["oracle_value"]
        )
        row["strict_semantic_wrong"] = bool(
            row["strict_accepted"] and not row["strict_oracle_success"]
        )
        row["normalized_semantic_wrong"] = bool(
            row["normalized_candidate_id"] and not row["normalized_oracle_success"]
        )
        row["weak_extraction_semantic_wrong"] = bool(
            row["weak_extracted_candidate_id"]
            and not row["weak_extraction_oracle_match"]
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
    strict_accepted = sum(bool(row["strict_accepted"]) for row in rows)
    strict_correct = sum(bool(row["strict_oracle_success"]) for row in rows)
    normalized_selected = sum(bool(row["normalized_candidate_id"]) for row in rows)
    normalized_correct = sum(bool(row["normalized_oracle_success"]) for row in rows)
    weak_selected = sum(bool(row["weak_extracted_candidate_id"]) for row in rows)
    weak_correct = sum(bool(row["weak_extraction_oracle_match"]) for row in rows)
    low, high = wilson_interval(normalized_correct, attempts)
    categories = Counter(str(row["audit_category"]) for row in rows)
    runtimes = [int(row["runtime_ms"]) for row in rows if int(row["runtime_ms"]) > 0]
    return {
        "method": "QWEN_DIRECT_VALUE_AUDITED",
        "independent_events": len({row["event_id"] for row in rows}),
        "runs": len({row["run_id"] for row in rows}),
        "attempts": attempts,
        "strict_accepted": strict_accepted,
        "strict_coverage": strict_accepted / attempts,
        "strict_oracle_successes": strict_correct,
        "strict_oracle_success_rate": strict_correct / attempts,
        "normalized_diagnostic_selected": normalized_selected,
        "normalized_diagnostic_coverage": normalized_selected / attempts,
        "normalized_oracle_successes": normalized_correct,
        "normalized_oracle_success_rate": normalized_correct / attempts,
        "normalized_oracle_95ci_low": low,
        "normalized_oracle_95ci_high": high,
        "weak_text_candidate_matches": weak_selected,
        "weak_text_candidate_match_rate": weak_selected / attempts,
        "weak_extraction_oracle_matches": weak_correct,
        "weak_extraction_oracle_match_rate": weak_correct / attempts,
        "json_error_rate": sum(
            not bool(row["json_parse_pass"]) for row in rows
        ) / attempts,
        "schema_error_rate": sum(
            bool(row["json_parse_pass"]) and not bool(row["schema_pass"])
            for row in rows
        ) / attempts,
        "normalizable_violation_rate": sum(
            bool(row["normalizable_violation"]) for row in rows
        ) / attempts,
        "out_of_candidate_value_rate": sum(
            bool(row["out_of_candidate_value"]) for row in rows
        ) / attempts,
        "strict_semantic_wrong_rate": sum(
            bool(row["strict_semantic_wrong"]) for row in rows
        ) / attempts,
        "normalized_semantic_wrong_rate": sum(
            bool(row["normalized_semantic_wrong"]) for row in rows
        ) / attempts,
        "weak_extraction_semantic_wrong_rate": sum(
            bool(row["weak_extraction_semantic_wrong"]) for row in rows
        ) / attempts,
        "abstain_rate": sum(bool(row["strict_abstain"]) for row in rows) / attempts,
        "call_error_rate": sum(row["strict_status"] == "CALL_ERROR" for row in rows)
        / attempts,
        "mean_runtime_ms": sum(runtimes) / len(runtimes) if runtimes else 0.0,
        "category_counts_json": json.dumps(categories, ensure_ascii=False, sort_keys=True),
    }


def summarize_by_event(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(row)
    result: list[dict[str, Any]] = []
    for event_id, items in sorted(grouped.items()):
        attempts = len(items)
        categories = Counter(str(item["audit_category"]) for item in items)
        result.append({
            "event_id": event_id,
            "semantic_type": items[0]["semantic_type"],
            "attempts": attempts,
            "strict_oracle_success_rate": sum(
                bool(item["strict_oracle_success"]) for item in items
            ) / attempts,
            "normalized_oracle_success_rate": sum(
                bool(item["normalized_oracle_success"]) for item in items
            ) / attempts,
            "weak_extraction_oracle_match_rate": sum(
                bool(item["weak_extraction_oracle_match"]) for item in items
            ) / attempts,
            "format_error_rate": sum(
                bool(item["format_error"]) for item in items
            ) / attempts,
            "normalizable_violation_rate": sum(
                bool(item["normalizable_violation"]) for item in items
            ) / attempts,
            "out_of_candidate_value_rate": sum(
                bool(item["out_of_candidate_value"]) for item in items
            ) / attempts,
            "normalized_semantic_wrong_rate": sum(
                bool(item["normalized_semantic_wrong"]) for item in items
            ) / attempts,
            "category_counts_json": json.dumps(
                categories, ensure_ascii=False, sort_keys=True
            ),
        })
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="审计Qwen直接生成的格式错误、可归一化输出和语义错误"
    )
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--only", default="", help="只运行指定事件，例如E9")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--num-predict", type=int, default=300)
    parser.add_argument("--keep-alive", default="30m")
    parser.add_argument(
        "--no-schema-fallback",
        action="store_true",
        help="Ollama不支持JSON Schema时不回退到普通JSON模式",
    )
    parser.add_argument(
        "--reanalyze-existing",
        action="store_true",
        help="读取现有direct-generation-audit-details.csv重新分类，不调用Ollama",
    )
    return parser.parse_args()


def reanalyze_existing_rows(
    events: list[dict[str, Any]], args: argparse.Namespace
) -> list[dict[str, Any]]:
    if not DETAIL_CSV.is_file():
        raise FileNotFoundError(f"没有可重分析的明细文件：{DETAIL_CSV}")
    event_map = {event["event_id"]: event for event in events}
    old_rows = base.load_csv(DETAIL_CSV)
    rows: list[dict[str, Any]] = []
    for old in old_rows:
        event_id = old.get("event_id", "")
        if event_id not in event_map:
            continue
        run_id = int(old.get("run_id", "0") or 0)
        run_seed = int(old.get("seed", "0") or 0)
        event = base.blinded_event(event_map[event_id], run_seed)
        raw_content = old.get("raw_content", "")
        if raw_content:
            call_result = {
                "raw_content": raw_content,
                "runtime_ms": int(old.get("runtime_ms", "0") or 0),
                "response_mode": old.get("response_mode", ""),
                "prompt_eval_count": int(old.get("prompt_eval_count", "0") or 0),
                "eval_count": int(old.get("eval_count", "0") or 0),
                "done_reason": old.get("done_reason", ""),
            }
            audited = audit_output(event, call_result)
        else:
            message = old.get("call_error", "旧明细没有raw_content")
            audited = failed_call_row(RuntimeError(message))
        rows.append({
            "run_id": run_id,
            "seed": run_seed,
            "event_id": event_id,
            "semantic_type": event["semantic_type"],
            **audited,
        })
    if not rows:
        raise RuntimeError("现有明细中没有匹配当前事件的记录")
    return rows


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs必须大于0")
    for path in (EVENT_FILE, ORACLE_FILE):
        if not path.is_file():
            raise FileNotFoundError(
                f"缺少{path}，请先运行build_semantic_ambiguity_benchmark.py。"
            )

    payload = base.load_json(EVENT_FILE)
    events = payload.get("events", [])
    if not isinstance(events, list) or not events:
        raise RuntimeError("semantic-events.json没有events")
    if args.only:
        wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
        events = [event for event in events if event["event_id"].upper() in wanted]
        if not events:
            raise RuntimeError(f"--only没有匹配事件：{sorted(wanted)}")

    print("Qwen直接生成：错误类型审计")
    if args.reanalyze_existing:
        print("模式=重分析现有raw_content，Ollama调用=0\n")
        rows = reanalyze_existing_rows(events, args)
        args.runs = len({row["run_id"] for row in rows})
        print(f"已重新分类：{len(rows)}条记录\n")
    else:
        print(f"[连接检查] Ollama={args.ollama_url}，模型={args.model}")
        base.check_ollama(args.ollama_url, args.model, args.timeout)
        print("[连接检查] Ollama与模型均可用。\n")
        print(
            f"事件={len(events)}，runs={args.runs}，"
            f"调用计划={len(events) * args.runs}\n"
        )
        rows = []
        index = 0
        total = len(events) * args.runs
        for run_id in range(1, args.runs + 1):
            run_seed = args.seed + run_id - 1
            for source_event in events:
                index += 1
                event = base.blinded_event(source_event, run_seed)
                print(
                    f"[{index}/{total}] {event['event_id']} run={run_id} "
                    f"seed={run_seed}：正在调用Qwen……"
                )
                try:
                    call_result = raw_qwen_call(event, args, run_seed)
                    audited = audit_output(event, call_result)
                except Exception as exc:
                    audited = failed_call_row(exc)
                row = {
                    "run_id": run_id,
                    "seed": run_seed,
                    "event_id": event["event_id"],
                    "semantic_type": event["semantic_type"],
                    **audited,
                }
                rows.append(row)
                print(
                    f"  严格结果={row['strict_status']} | "
                    f"类别={row['audit_category']} | "
                    f"raw={str(row['raw_value'])[:30] or '-'} | "
                    f"normalized={row['normalized_value'] or '-'} | "
                    f"weak={row['weak_extracted_value'] or '-'} | "
                    f"{row['runtime_ms']}ms"
                )

    # 预测和归一化全部完成后才读取Oracle。
    oracle = load_oracle()
    evaluate(rows, oracle)
    summary = summarize(rows)
    by_event = summarize_by_event(rows)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(DETAIL_CSV, rows)
    write_csv(SUMMARY_CSV, [summary])
    write_csv(BY_EVENT_CSV, by_event)
    output = {
        "schema_version": "2.1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "audit_direct_generation_outputs.py",
        "oracle_loaded_after_prediction": True,
        "normalization_is_diagnostic_only": True,
        "events": [event["event_id"] for event in events],
        "runs": args.runs,
        "seed": args.seed,
        "model": args.model,
        "summary": summary,
        "by_event": by_event,
        "details": rows,
    }
    OUTPUT_JSON.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    categories = json.loads(summary["category_counts_json"])
    lines = [
        "Qwen直接生成错误类型审计日志",
        f"独立语义事件：{summary['independent_events']}",
        f"重复运行：{summary['runs']}",
        f"总尝试：{summary['attempts']}",
        "归一化仅用于离线审计，不会绕过严格门禁。",
        "",
        f"严格接口Oracle成功率：{summary['strict_oracle_success_rate']:.2%}",
        f"归一化诊断Oracle成功率：{summary['normalized_oracle_success_rate']:.2%}",
        f"弱文本抽取Oracle匹配率：{summary['weak_extraction_oracle_match_rate']:.2%}",
        f"JSON错误率：{summary['json_error_rate']:.2%}",
        f"Schema错误率：{summary['schema_error_rate']:.2%}",
        f"可归一化但被严格拒绝：{summary['normalizable_violation_rate']:.2%}",
        f"白名单外数值率：{summary['out_of_candidate_value_rate']:.2%}",
        f"归一化后语义选错率：{summary['normalized_semantic_wrong_rate']:.2%}",
        f"类别计数：{json.dumps(categories, ensure_ascii=False, sort_keys=True)}",
        "",
        "论文中不要把全部接口失败统称为幻觉。",
        "应分别报告：格式错误、强归一化违规、弱文本抽取、白名单外数值、语义错误。",
        f"明细：{DETAIL_CSV}",
        f"汇总：{SUMMARY_CSV}",
        f"按事件：{BY_EVENT_CSV}",
        f"JSON：{OUTPUT_JSON}",
    ]
    LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n审计汇总")
    print(f"严格接口Oracle成功率：{summary['strict_oracle_success_rate']:.2%}")
    print(f"归一化诊断Oracle成功率：{summary['normalized_oracle_success_rate']:.2%}")
    print(f"弱文本抽取Oracle匹配率：{summary['weak_extraction_oracle_match_rate']:.2%}")
    print(f"JSON错误率：{summary['json_error_rate']:.2%}")
    print(f"Schema错误率：{summary['schema_error_rate']:.2%}")
    print(f"可归一化但被严格拒绝：{summary['normalizable_violation_rate']:.2%}")
    print(f"白名单外数值率：{summary['out_of_candidate_value_rate']:.2%}")
    print(f"归一化后语义选错率：{summary['normalized_semantic_wrong_rate']:.2%}")
    print(f"类别计数：{json.dumps(categories, ensure_ascii=False, sort_keys=True)}")
    print(f"\n明细：{DETAIL_CSV}")
    print(f"汇总：{SUMMARY_CSV}")
    print(f"按事件：{BY_EVENT_CSV}")
    print(f"JSON：{OUTPUT_JSON}")
    print(f"日志：{LOG_FILE}")
    print("[完成] 现在可以把接口格式失败与真正的语义错误分开报告。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[直接生成审计停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
