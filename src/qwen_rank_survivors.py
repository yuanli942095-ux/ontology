from __future__ import annotations

r"""
在通过反事实硬门禁的候选中执行分流选择：
0个候选 -> 自动ABSTAIN；1个候选 -> 确定性选择；多个候选 -> Qwen排序。

输出清单与 validate_ranked_repairs.py 兼容。

默认运行：
    python .\src\qwen_rank_survivors.py --runs 5 --temperature 0.2
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
    print("缺少 repair_operators.py。请把本文件放入 ontology-evolution\\src。")
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
DEFAULT_CATALOG = REPAIR_DIR / "surviving-candidates.json"
DEFAULT_MANIFEST = REPAIR_DIR / "qwen-ranked-candidates.csv"
RANKED_ROOT = REPAIR_DIR / "qwen-ranked-survivors"
OUTPUT_DIR = PROJECT_DIR / "output"
GENERATION_CSV = OUTPUT_DIR / "survivor-ranking-generation.csv"
GENERATION_JSON = OUTPUT_DIR / "survivor-ranking-generation.json"
GENERATION_LOG = OUTPUT_DIR / "survivor-ranking-generation.log"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/insurance-benchmark/survivor-ranked"
PROMPT_MODE = "counterfactual_survivors"


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


def resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_DIR / path


def bool_text(value: bool) -> str:
    return str(bool(value)).lower()


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


def call_ollama(
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    seed: int,
    timeout: int,
    num_predict: int,
    keep_alive: str,
) -> tuple[str, dict[str, Any], int]:
    # 普通JSON模式在本地Qwen3.5上明显快于复杂JSON Schema；完整的字段、
    # 互斥与候选白名单约束仍由validate_selection()严格执行。
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
    started = time.perf_counter()
    response = http_json("POST", f"{base_url.rstrip('/')}/api/chat", timeout, payload)
    runtime_ms = round((time.perf_counter() - started) * 1000)
    message = response.get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        content = response.get("response")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Ollama没有返回可解析文本：{str(response)[:500]}")
    metadata = {
        "prompt_eval_count": response.get("prompt_eval_count", 0),
        "eval_count": response.get("eval_count", 0),
        "done_reason": response.get("done_reason", ""),
    }
    return content, metadata, runtime_ms


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
            raise ValueError("abstain=true时candidate_id必须为空")
    elif candidate_id not in candidate_ids:
        raise ValueError(f"candidate_id不在存活候选白名单：{candidate_id}")
    return abstain, candidate_id, confidence, rationale.strip()


def effect_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    effect = candidate.get("effect", {})
    return {
        "candidate_id": candidate["candidate_id"],
        "description": candidate["description"],
        "operator": candidate["operator"],
        "post_state": effect.get("post_state", []),
        "reasoner_result": effect.get("reasoner_result", ""),
        "evidence_result": effect.get("evidence_result", ""),
        "all_cq_result": effect.get("all_cq_result", ""),
        "triples_removed": effect.get("triples_removed", 0),
        "triples_added": effect.get("triples_added", 0),
        "minimal_edit_gate": effect.get("minimal_edit_gate", False),
    }


def build_prompt(
    error: dict[str, Any], candidates: list[dict[str, Any]]
) -> tuple[str, str]:
    system_prompt = (
        "你是受形式化约束的OWL修复候选排序器。所有候选都已经通过Reasoner、"
        "当前证据、全部CQ和最小修改硬门禁。你只能在给定candidate_id中选择一个，"
        "或在仍无法可靠区分时严格ABSTAIN。禁止生成IRI、字面量、算子、OWL或新候选。"
        "只输出JSON对象，不要Markdown和前后说明。"
    )
    payload = {
        "error_id": error["error_id"],
        "error_type": error["error_type"],
        "failed_competency_questions": error.get("failed_cqs", []),
        "current_effective_evidence": error.get("evidence_context", []),
        "hard_gate_survivors": [effect_summary(candidate) for candidate in candidates],
    }
    user_prompt = (
        "从存活候选中选择语义最合适且修改最小的一个。严格输出字段：\n"
        '{"abstain": false, "candidate_id": "CAND_...", '
        '"confidence": 0.0, "rationale": "..."}\n'
        "若候选在现有证据下等价且无法可靠区分，可以ABSTAIN："
        "abstain=true、candidate_id为空字符串。不得输出额外字段。\n\n"
        "输入上下文：\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return system_prompt, user_prompt


def set_ontology_identity(
    graph: Graph, error_id: str, run_id: int, seed: int, source: str
) -> str:
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(
        f"{ONTOLOGY_BASE}/{source}/{error_id}/run-{run_id:03d}/seed-{seed}"
    )
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    return str(ontology_iri)


def blank_row() -> dict[str, Any]:
    # 前30列与qwen_rank_candidates.py保持一致，后3列是本脚本的分流审计字段。
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
        "selection_source": "",
        "survivor_count": 0,
        "llm_called": "false",
    }


def apply_selected(
    row: dict[str, Any],
    candidate: dict[str, Any],
    source_graph: Graph,
    owl_path: Path,
    error_id: str,
    run_id: int,
    seed: int,
    selection_source: str,
) -> None:
    operation = deepcopy(candidate["operation"])
    validate_operation_schema(operation)
    repaired = apply_operations(source_graph, [operation])
    ontology_iri = set_ontology_identity(
        repaired, error_id, run_id, seed, selection_source.lower()
    )
    repaired.serialize(destination=owl_path, format="xml", encoding="utf-8")
    row.update({
        "selected_candidate_id": candidate["candidate_id"],
        "selected_description": candidate["description"],
        "candidate_owl": str(owl_path.resolve()),
        "operations_json": json.dumps([operation], ensure_ascii=False),
        "operation_count": 1,
        "operators": operation["operator"],
        "accepted": "true",
        "candidate_membership_pass": "true",
        "apply_pass": "true",
        "ontology_iri": ontology_iri,
    })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="确定性分流并用Qwen排序硬门禁存活候选")
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
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--num-predict", type=int, default=300)
    parser.add_argument("--keep-alive", default="30m")
    parser.add_argument("--only", default="")
    parser.add_argument("--skip-model-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs必须大于等于1")
    if not 0 <= args.temperature <= 2:
        raise ValueError("--temperature必须位于[0,2]")
    if args.timeout < 1 or args.num_predict < 1:
        raise ValueError("--timeout与--num-predict必须大于0")
    if not args.catalog.is_file():
        raise FileNotFoundError(args.catalog)
    catalog = load_json(args.catalog)
    errors = catalog.get("errors")
    if not isinstance(errors, list) or not errors:
        raise RuntimeError("存活候选目录没有errors数组")
    selected = parse_only(args.only)
    if selected:
        known = {str(error["error_id"]).upper() for error in errors}
        unknown = selected - known
        if unknown:
            raise ValueError(f"未知错误编号：{sorted(unknown)}")
        errors = [error for error in errors if str(error["error_id"]).upper() in selected]

    needs_llm = any(len(error.get("candidates", [])) > 1 for error in errors)
    if needs_llm and not args.skip_model_check:
        print(f"[连接检查] Ollama={args.ollama_url}，模型={args.model}", flush=True)
        check_ollama(args.ollama_url, args.model, args.timeout)
        print("[连接检查] Ollama与模型均可用。\n", flush=True)
    elif not needs_llm:
        print("[分流检查] 所有错误均为0或1个存活候选，本次不调用Ollama。\n", flush=True)

    RANKED_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    logs = [
        "反事实存活候选分流与Qwen排序日志",
        f"模型：{args.model}",
        f"运行次数：{args.runs}",
        "策略：0个=ABSTAIN；1个=确定性选择；多个=Qwen排序。",
        "Ollama格式：普通JSON + think=false；程序执行严格字段与白名单校验。",
        "",
    ]
    total = len(errors) * args.runs
    current = 0

    for error in errors:
        error_id = str(error["error_id"])
        candidates = error.get("candidates", [])
        if not isinstance(candidates, list):
            raise RuntimeError(f"{error_id}的candidates不是数组")
        candidate_map = {str(candidate["candidate_id"]): candidate for candidate in candidates}
        if len(candidate_map) != len(candidates):
            raise RuntimeError(f"{error_id}存活候选ID重复")
        source_path = resolve_project_path(str(error["source_mutant"]))
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        source_graph = load_graph(source_path)

        for run_id in range(1, args.runs + 1):
            current += 1
            seed = args.seed + run_id - 1
            attempt_id = f"survivor-{error_id}-run-{run_id:03d}"
            run_dir = RANKED_ROOT / error_id / f"run-{run_id:03d}"
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
                "prompt_mode": PROMPT_MODE,
                "temperature": args.temperature,
                "source_catalog": str(args.catalog.resolve()),
                "source_mutant": str(source_path.resolve()),
                "selection_json": str(selection_path.resolve()),
                "raw_response": str(raw_path.resolve()),
                "survivor_count": len(candidates),
            })
            print(
                f"[{current}/{total}] {error_id} run={run_id} seed={seed} | "
                f"存活候选={len(candidates)}",
                flush=True,
            )
            raw_text = ""
            try:
                if len(candidates) == 0:
                    payload = {
                        "abstain": True,
                        "candidate_id": "",
                        "confidence": 1.0,
                        "rationale": "没有候选通过形式化硬门禁，必须交由人工复核。",
                    }
                    selection_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    row.update({
                        "selection_source": "HARD_GATE_ABSTAIN",
                        "abstained": "true",
                        "json_parse_pass": "true",
                        "strict_selection_pass": "true",
                        "candidate_membership_pass": "true",
                        "confidence": 1.0,
                        "rationale": payload["rationale"],
                        "ollama_format_mode": "not_called_no_survivor",
                    })
                    message = "  结果=ABSTAIN | 原因=无硬门禁存活候选 | Ollama未调用"
                elif len(candidates) == 1:
                    candidate = candidates[0]
                    payload = {
                        "abstain": False,
                        "candidate_id": candidate["candidate_id"],
                        "confidence": 1.0,
                        "rationale": "唯一候选已通过全部反事实硬门禁，执行确定性选择。",
                    }
                    selection_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    row.update({
                        "selection_source": "DETERMINISTIC_SELECT",
                        "json_parse_pass": "true",
                        "strict_selection_pass": "true",
                        "confidence": 1.0,
                        "rationale": payload["rationale"],
                        "ollama_format_mode": "not_called_single_survivor",
                    })
                    apply_selected(
                        row,
                        candidate,
                        source_graph,
                        owl_path,
                        error_id,
                        run_id,
                        seed,
                        "deterministic",
                    )
                    message = (
                        f"  结果=DETERMINISTIC_SELECT | {candidate['candidate_id']} | "
                        f"{candidate['operator']} | Ollama未调用"
                    )
                else:
                    row["selection_source"] = "QWEN_RANK"
                    row["llm_called"] = "true"
                    ordered = list(candidates)
                    random.Random(seed).shuffle(ordered)
                    system_prompt, user_prompt = build_prompt(error, ordered)
                    raw_text, metadata, runtime_ms = call_ollama(
                        args.ollama_url,
                        args.model,
                        system_prompt,
                        user_prompt,
                        args.temperature,
                        seed,
                        args.timeout,
                        args.num_predict,
                        args.keep_alive,
                    )
                    raw_path.write_text(raw_text, encoding="utf-8")
                    row.update({
                        "llm_runtime_ms": runtime_ms,
                        "prompt_eval_count": metadata.get("prompt_eval_count", 0),
                        "eval_count": metadata.get("eval_count", 0),
                        "ollama_format_mode": "plain_json_nothink",
                    })
                    payload = extract_json(raw_text)
                    row["json_parse_pass"] = "true"
                    selection_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    abstained, selected_id, confidence, rationale = validate_selection(
                        payload, set(candidate_map)
                    )
                    row.update({
                        "strict_selection_pass": "true",
                        "confidence": confidence,
                        "rationale": rationale,
                        "abstained": bool_text(abstained),
                    })
                    if abstained:
                        row["candidate_membership_pass"] = "true"
                        message = f"  结果=QWEN_ABSTAIN | confidence={confidence} | {runtime_ms}ms"
                    else:
                        candidate = candidate_map[selected_id]
                        apply_selected(
                            row,
                            candidate,
                            source_graph,
                            owl_path,
                            error_id,
                            run_id,
                            seed,
                            "qwen",
                        )
                        message = (
                            f"  结果=QWEN_SELECT | {selected_id} | {candidate['operator']} | "
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
    for path in (args.manifest, GENERATION_CSV):
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    GENERATION_JSON.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    selected_count = sum(row["accepted"] == "true" for row in rows)
    abstain_count = sum(row["abstained"] == "true" for row in rows)
    rejected_count = len(rows) - selected_count - abstain_count
    llm_calls = sum(row["llm_called"] == "true" for row in rows)
    deterministic = sum(row["selection_source"] == "DETERMINISTIC_SELECT" for row in rows)
    saved_calls = len(rows) - llm_calls
    summary = (
        f"分流汇总：总尝试={len(rows)}，选择={selected_count}，ABSTAIN={abstain_count}，"
        f"拒绝={rejected_count}，确定性选择={deterministic}，Qwen调用={llm_calls}，"
        f"避免Qwen调用={saved_calls}"
    )
    logs.extend(["", summary, f"候选清单：{args.manifest}"])
    GENERATION_LOG.write_text("\n".join(logs) + "\n", encoding="utf-8")
    print(f"\n{summary}")
    print(f"候选清单：{args.manifest}")
    print(f"生成记录：{GENERATION_CSV}")
    print(f"日志：{GENERATION_LOG}")
    print("[完成] 请继续运行 python .\\src\\validate_ranked_repairs.py。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已中断] 用户终止存活候选排序。")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[存活候选排序停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
