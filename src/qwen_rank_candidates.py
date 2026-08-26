from __future__ import annotations

r"""
让本地Qwen只在符号程序已经枚举并验证可执行性的候选ID中选择。

安全边界：
1. Qwen不能生成IRI、字面量、算子或OWL文本；
2. 输出只能是候选ID或严格ABSTAIN；
3. abstain=true与候选选择互斥，禁止兼容归一化；
4. 选中的操作再次由 repair_operators.apply_operations 执行；
5. 本脚本只生成待验证OWL，不负责最终接受。

默认运行：
    python .\src\qwen_rank_candidates.py --runs 5 --temperature 0.2
"""

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

try:
    from rdflib import Graph, RDF, URIRef
    from rdflib.namespace import OWL
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)

try:
    from repair_operators import apply_operations, validate_operation_schema
except ImportError as exc:
    print(
        "缺少 repair_operators.py。请把本文件放入 ontology-evolution\\src。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = PROJECT_DIR / "benchmark" / "repairs" / "feasible-candidates.json"
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
RANKED_ROOT = REPAIR_DIR / "qwen-ranked"
DEFAULT_MANIFEST = REPAIR_DIR / "qwen-ranked-candidates.csv"
OUTPUT_DIR = PROJECT_DIR / "output"
GENERATION_CSV = OUTPUT_DIR / "qwen-ranking-generation.csv"
GENERATION_JSON = OUTPUT_DIR / "qwen-ranking-generation.json"
GENERATION_LOG = OUTPUT_DIR / "qwen-ranking-generation.log"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/insurance-benchmark/qwen-ranked"
PROMPT_MODES = {"evidence_only", "cq_evidence", "cq_only", "diagnostics_only"}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON顶层必须是对象：{path}")
    return value


def load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="xml")
    return graph


def parse_only(text: str) -> set[str]:
    return {item.strip().upper() for item in text.split(",") if item.strip()}


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
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
        raise RuntimeError(
            f"Ollama中没有模型 {model}。当前模型={sorted(name for name in names if name)}"
        )


def selection_schema(candidate_ids: list[str]) -> dict[str, Any]:
    if not candidate_ids:
        raise ValueError("候选ID集合不能为空")
    # 避免Ollama受限解码器在oneOf分支上长时间停滞。JSON Schema只约束
    # 字段类型和候选白名单；abstain/candidate_id的严格互斥由
    # validate_selection()再次执行，矛盾输出仍会被直接拒绝。
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


def call_ollama(
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    output_schema: dict[str, Any],
    temperature: float,
    seed: int,
    timeout: int,
    num_predict: int,
    keep_alive: str,
) -> tuple[str, dict[str, Any], int, str]:
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "format": "json",
        "think": False,
        "keep_alive": keep_alive,
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
    attempts: list[tuple[str, dict[str, Any]]] = [("simple_json_schema_nothink", payload)]
    # 只放宽服务器侧格式约束，不允许回退到Thinking模式。即使使用普通JSON
    # 输出，extract_json()和validate_selection()仍执行完整的程序门禁。
    json_fallback = deepcopy(payload)
    json_fallback["format"] = "json"
    attempts.append(("json_nothink_fallback", json_fallback))

    started = time.perf_counter()
    response: dict[str, Any] | None = None
    used_mode = ""
    last_error: RuntimeError | None = None
    for index, (mode, attempt) in enumerate(attempts):
        try:
            response = http_json("POST", f"{base_url.rstrip('/')}/api/chat", timeout, attempt)
            used_mode = mode
            break
        except RuntimeError as exc:
            last_error = exc
            if "HTTP 400" not in str(exc) or index == len(attempts) - 1:
                raise
    if response is None:
        raise last_error or RuntimeError("Ollama没有返回结果")
    runtime_ms = round((time.perf_counter() - started) * 1000)
    message = response.get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        content = response.get("response")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Ollama没有返回可解析文本：{str(response)[:500]}")
    metadata = {
        "done_reason": response.get("done_reason", ""),
        "prompt_eval_count": response.get("prompt_eval_count", 0),
        "eval_count": response.get("eval_count", 0),
        "total_duration": response.get("total_duration", 0),
    }
    return content, metadata, runtime_ms, used_mode


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.I | re.S)
    if fence:
        cleaned = fence.group(1).strip()
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


def validate_selection(
    payload: dict[str, Any], candidate_ids: set[str]
) -> tuple[bool, str, float, str]:
    expected_keys = {"abstain", "candidate_id", "confidence", "rationale"}
    if set(payload) != expected_keys:
        raise ValueError(
            f"选择JSON字段必须恰好为{sorted(expected_keys)}，实际={sorted(payload)}"
        )
    abstain = payload["abstain"]
    if not isinstance(abstain, bool):
        raise ValueError("abstain必须是JSON布尔值，禁止字符串归一化")
    candidate_id = payload["candidate_id"]
    if not isinstance(candidate_id, str):
        raise ValueError("candidate_id必须是字符串")
    confidence_raw = payload["confidence"]
    if isinstance(confidence_raw, bool) or not isinstance(confidence_raw, (int, float)):
        raise ValueError("confidence必须是0到1之间的数")
    confidence = float(confidence_raw)
    if not 0 <= confidence <= 1:
        raise ValueError("confidence必须位于[0,1]")
    rationale = payload["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("rationale必须是非空字符串")
    if abstain:
        if candidate_id != "":
            raise ValueError("abstain=true时candidate_id必须为空，禁止选择候选")
    elif candidate_id not in candidate_ids:
        raise ValueError(f"candidate_id不在符号候选白名单：{candidate_id}")
    return abstain, candidate_id, confidence, rationale.strip()


def prompt_payload(error: dict[str, Any], mode: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    failed_cqs: list[dict[str, Any]] = []
    for cq in error["failed_cqs"]:
        item = {
            "cq_id": cq["cq_id"],
            "question": cq["question"],
            "query_type": cq["query_type"],
            "actual_values": cq["actual_values"],
            "rationale": cq.get("rationale", ""),
        }
        if mode in {"cq_evidence", "cq_only"}:
            item["expected_answer"] = cq.get("expected_answer", "")
            item["expected_count"] = cq.get("expected_count", "")
        failed_cqs.append(item)
    candidate_items = [
        {
            "candidate_id": candidate["candidate_id"],
            "description": candidate["description"],
            "operator": candidate["operator"],
            "source_cq_ids": candidate["source_cq_ids"],
            "source_evidence_ids": candidate["source_evidence_ids"],
        }
        for candidate in candidates
    ]
    evidence: object
    if mode in {"evidence_only", "cq_evidence"}:
        evidence = error.get("evidence_context", [])
    else:
        evidence = "HIDDEN_FOR_ABLATION"
    return {
        "error_id": error["error_id"],
        "error_type": error["error_type"],
        "failed_competency_questions": failed_cqs,
        "current_effective_evidence": evidence,
        "symbolically_feasible_candidates": candidate_items,
    }


def build_prompt(error: dict[str, Any], mode: str, candidates: list[dict[str, Any]]) -> tuple[str, str]:
    system_prompt = (
        "你是受形式化约束的OWL修复候选排序器。"
        "候选已经由符号程序生成并验证可执行性。"
        "你只能选择一个给定candidate_id或严格ABSTAIN；"
        "禁止生成IRI、字面量、算子、OWL、SPARQL或新候选。"
        "逻辑可执行不代表语义正确，应优先满足当前有效证据和失败CQ。"
        "只输出JSON对象，不要Markdown和前后说明。"
    )
    user_prompt = (
        "请从输入候选中选择最可能恢复当前有效业务语义的一个。严格规则：\n"
        "1. 若能够可靠选择，输出abstain=false及白名单candidate_id。\n"
        "2. 若证据不足或候选均不合适，输出abstain=true且candidate_id为空字符串。\n"
        "3. abstain与候选选择严格互斥，禁止同时表达。\n"
        "4. 不要因为候选可执行就假定其语义正确。\n"
        "5. confidence为0到1之间的数，rationale简述选择依据。\n\n"
        "输入上下文：\n"
        + json.dumps(prompt_payload(error, mode, candidates), ensure_ascii=False, indent=2)
    )
    return system_prompt, user_prompt


def set_ontology_identity(graph: Graph, error_id: str, run_id: int, seed: int) -> str:
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(f"{ONTOLOGY_BASE}/{error_id}/run-{run_id:03d}/seed-{seed}")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    return str(ontology_iri)


def bool_text(value: bool) -> str:
    return str(bool(value)).lower()


def blank_row() -> dict[str, Any]:
    return {
        "attempt_id": "",
        "error_id": "",
        "error_type": "",
        "run_id": "",
        "seed": "",
        "model": "",
        "prompt_mode": "",
        "temperature": "",
        "source_catalog": "",
        "source_mutant": "",
        "selected_candidate_id": "",
        "selected_description": "",
        "selection_json": "",
        "raw_response": "",
        "candidate_owl": "",
        "operations_json": "[]",
        "operation_count": 0,
        "operators": "",
        "abstained": "false",
        "accepted": "false",
        "json_parse_pass": "false",
        "strict_selection_pass": "false",
        "candidate_membership_pass": "false",
        "apply_pass": "false",
        "confidence": "",
        "rationale": "",
        "ollama_format_mode": "",
        "llm_runtime_ms": 0,
        "prompt_eval_count": 0,
        "eval_count": 0,
        "ontology_iri": "",
        "error_message": "",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用本地Qwen在符号可执行候选中选择")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
    )
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--num-predict", type=int, default=500)
    parser.add_argument("--keep-alive", default="30m")
    parser.add_argument("--only", default="")
    parser.add_argument(
        "--prompt-mode",
        choices=sorted(PROMPT_MODES),
        default="evidence_only",
    )
    parser.add_argument("--skip-model-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs必须大于等于1")
    if not 0 <= args.temperature <= 2:
        raise ValueError("--temperature必须位于[0,2]")
    if not args.catalog.is_file():
        print(f"缺少候选目录：{args.catalog}")
        print("请先运行 python .\\src\\enumerate_feasible_candidates.py")
        return 2
    catalog = load_json(args.catalog)
    errors = catalog.get("errors")
    if not isinstance(errors, list) or not errors:
        raise RuntimeError("候选目录没有errors数组")
    selected = parse_only(args.only)
    if selected:
        known = {str(error["error_id"]).upper() for error in errors}
        unknown = selected - known
        if unknown:
            raise ValueError(f"未知错误编号：{sorted(unknown)}")
        errors = [error for error in errors if str(error["error_id"]).upper() in selected]
    if not args.skip_model_check:
        print(f"[连接检查] Ollama={args.ollama_url}，模型={args.model}", flush=True)
        check_ollama(args.ollama_url, args.model, args.timeout)
        print("[连接检查] Ollama与模型均可用。\n", flush=True)

    mode_root = RANKED_ROOT / args.prompt_mode
    mode_root.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    logs = [
        "Qwen符号候选排序日志",
        f"模型：{args.model}",
        f"提示模式：{args.prompt_mode}",
        f"运行次数：{args.runs}",
        f"temperature：{args.temperature}",
        f"首个seed：{args.seed}",
        "互斥策略：abstain=true时禁止选择候选，不进行兼容归一化。",
        "",
    ]
    total = len(errors) * args.runs
    current = 0
    for error in errors:
        error_id = str(error["error_id"])
        candidates = error.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise RuntimeError(f"{error_id}没有符号候选")
        candidate_map = {str(item["candidate_id"]): item for item in candidates}
        if len(candidate_map) != len(candidates):
            raise RuntimeError(f"{error_id}候选ID重复")
        source_path = Path(str(error["source_mutant"]))
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        source_graph = load_graph(source_path)

        for run_id in range(1, args.runs + 1):
            current += 1
            seed = args.seed + run_id - 1
            ordered = list(candidates)
            random.Random(seed).shuffle(ordered)
            system_prompt, user_prompt = build_prompt(error, args.prompt_mode, ordered)
            candidate_ids = [str(item["candidate_id"]) for item in ordered]
            attempt_id = f"rank-{error_id}-run-{run_id:03d}"
            run_dir = mode_root / error_id / f"run-{run_id:03d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            raw_path = run_dir / "raw-response.txt"
            selection_path = run_dir / "selection.json"
            owl_path = run_dir / "candidate.owl"
            row = blank_row()
            row.update({
                "attempt_id": attempt_id,
                "error_id": error_id,
                "error_type": error["error_type"],
                "run_id": run_id,
                "seed": seed,
                "model": args.model,
                "prompt_mode": args.prompt_mode,
                "temperature": args.temperature,
                "source_catalog": str(args.catalog.resolve()),
                "source_mutant": str(source_path.resolve()),
                "selection_json": str(selection_path.resolve()),
                "raw_response": str(raw_path.resolve()),
            })
            print(
                f"[{current}/{total}] {error_id} run={run_id} seed={seed}："
                f"Qwen正在选择{len(candidate_ids)}个候选之一……",
                flush=True,
            )
            raw_text = ""
            try:
                raw_text, metadata, runtime_ms, format_mode = call_ollama(
                    args.ollama_url,
                    args.model,
                    system_prompt,
                    user_prompt,
                    selection_schema(candidate_ids),
                    args.temperature,
                    seed,
                    args.timeout,
                    args.num_predict,
                    args.keep_alive,
                )
                raw_path.write_text(raw_text, encoding="utf-8")
                row["llm_runtime_ms"] = runtime_ms
                row["prompt_eval_count"] = metadata.get("prompt_eval_count", 0)
                row["eval_count"] = metadata.get("eval_count", 0)
                row["ollama_format_mode"] = format_mode
                payload = extract_json(raw_text)
                row["json_parse_pass"] = "true"
                selection_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                abstained, selected_id, confidence, rationale = validate_selection(
                    payload, set(candidate_map)
                )
                row["strict_selection_pass"] = "true"
                row["confidence"] = confidence
                row["rationale"] = rationale
                row["abstained"] = bool_text(abstained)
                if abstained:
                    row["candidate_membership_pass"] = "true"
                    message = f"  结果=ABSTAIN | confidence={confidence}"
                else:
                    selected_candidate = candidate_map[selected_id]
                    operation = deepcopy(selected_candidate["operation"])
                    validate_operation_schema(operation)
                    row["candidate_membership_pass"] = "true"
                    repaired = apply_operations(source_graph, [operation])
                    row["apply_pass"] = "true"
                    ontology_iri = set_ontology_identity(repaired, error_id, run_id, seed)
                    repaired.serialize(destination=owl_path, format="xml", encoding="utf-8")
                    row.update({
                        "selected_candidate_id": selected_id,
                        "selected_description": selected_candidate["description"],
                        "candidate_owl": str(owl_path.resolve()),
                        "operations_json": json.dumps([operation], ensure_ascii=False),
                        "operation_count": 1,
                        "operators": operation["operator"],
                        "accepted": "true",
                        "ontology_iri": ontology_iri,
                    })
                    message = (
                        f"  结果=SELECTED | {selected_id} | {operation['operator']} | "
                        f"confidence={confidence} | {runtime_ms}ms"
                    )
            except Exception as exc:
                if raw_text and not raw_path.exists():
                    raw_path.write_text(raw_text, encoding="utf-8")
                row["error_message"] = f"{type(exc).__name__}: {exc}"
                message = f"  结果=REJECTED | {row['error_message']}"
            rows.append(row)
            print(message, flush=True)
            logs.extend([f"[{attempt_id}]", message])

    fields = list(blank_row())
    with args.manifest.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with GENERATION_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    GENERATION_JSON.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    selected_count = sum(row["accepted"] == "true" for row in rows)
    abstain_count = sum(row["abstained"] == "true" for row in rows)
    rejected = len(rows) - selected_count - abstain_count
    summary = (
        f"排序汇总：总尝试={len(rows)}，选择候选={selected_count}，"
        f"主动放弃={abstain_count}，严格拒绝={rejected}"
    )
    logs.extend(["", summary, f"候选清单：{args.manifest}"])
    GENERATION_LOG.write_text("\n".join(logs) + "\n", encoding="utf-8")
    print(f"\n{summary}")
    print(f"候选清单：{args.manifest}")
    print(f"生成记录：{GENERATION_CSV}")
    print(f"日志：{GENERATION_LOG}")
    print("[完成] 选择结果尚未最终接受，请运行 validate_ranked_repairs.py。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已中断] 用户终止Qwen候选排序。")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[候选排序停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
