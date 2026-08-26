from __future__ import annotations

r"""
使用本地 Ollama/Qwen 为 E1-E5 生成受限的 OWL 修复候选。

安全边界：
1. Qwen 只能返回 JSON，不能直接返回或修改 OWL 文本；
2. 只允许 repair_operators.py 中定义的有限修复算子；
3. 主体、属性、类别和对象 IRI 必须真实存在于当前本体；
4. 新字面量必须来自“当前本体 + 当前有效证据”的有限候选集；
5. 旧断言必须真实存在；所有候选先由程序校验，再写成候选 OWL；
6. 本脚本只生成候选，不把候选自动接受为正式修复。

默认运行方式：
    python .\src\qwen_repair_candidates.py

可复现实验（每类错误 5 次）：
    python .\src\qwen_repair_candidates.py --runs 5 --temperature 0
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import ProxyHandler, Request, build_opener

try:
    from rdflib import Graph, Literal, RDF, URIRef
    from rdflib.namespace import OWL, RDFS, XSD
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)

try:
    import run_cq_tests as cq_runner
    import validate_benchmark as benchmark_validator
    from repair_operators import (
        ALLOWED_OPERATORS,
        apply_operations,
        operation_key,
        term_to_spec,
        validate_operation_schema,
    )
except ImportError as exc:
    print(
        "缺少现有项目模块。请把本文件放到 ontology-evolution\\src，"
        "并确认 run_cq_tests.py、validate_benchmark.py、repair_operators.py 均在同一目录。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
CQ_FILE = PROJECT_DIR / "benchmark" / "cq" / "cq-tests.csv"
MANIFEST = PROJECT_DIR / "benchmark" / "ground-truth" / "error-manifest.csv"
MUTANTS_DIR = PROJECT_DIR / "benchmark" / "mutants"
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
QWEN_CANDIDATE_ROOT = REPAIR_DIR / "qwen-candidates"
DEFAULT_MANIFEST = REPAIR_DIR / "qwen-repair-candidates.csv"
OUTPUT_DIR = PROJECT_DIR / "output"
GENERATION_CSV = OUTPUT_DIR / "qwen-candidate-generation.csv"
GENERATION_JSON = OUTPUT_DIR / "qwen-candidate-generation.json"
GENERATION_LOG = OUTPUT_DIR / "qwen-candidate-generation.log"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/insurance-benchmark/qwen-repair"

PROMPT_MODES = {"evidence_only", "cq_evidence", "cq_only", "diagnostics_only"}
XSD_PREFIX = "http://www.w3.org/2001/XMLSchema#"


def response_schema(max_operations: int, failed_cq_ids: set[str]) -> dict[str, Any]:
    """按有限修复算子区分必填字段的Ollama结构化输出Schema。"""
    allowed_cq_ids = sorted(failed_cq_ids)
    if not allowed_cq_ids:
        raise ValueError("生成Schema时失败CQ集合不能为空")
    iri_value = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["iri"]},
            "iri": {"type": "string"},
        },
        "required": ["kind", "iri"],
        "additionalProperties": False,
    }
    literal_value = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["literal"]},
            "lexical": {"type": "string"},
            "datatype": {"type": "string"},
            "language": {"type": "string"},
        },
        "required": ["kind", "lexical"],
        "additionalProperties": False,
    }
    value_schema = {"oneOf": [iri_value, literal_value]}
    source_ids = {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string", "enum": allowed_cq_ids},
    }

    def operation_schema(
        operator: str,
        extra_properties: dict[str, Any],
        extra_required: list[str],
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "operator": {"type": "string", "enum": [operator]},
            "subject_iri": {"type": "string"},
            "source_cq_ids": source_ids,
            "rationale": {"type": "string", "minLength": 1},
        }
        properties.update(extra_properties)
        return {
            "type": "object",
            "properties": properties,
            "required": [
                "operator",
                "subject_iri",
                *extra_required,
                "source_cq_ids",
                "rationale",
            ],
            "additionalProperties": False,
        }

    property_fields = {"predicate_iri": {"type": "string"}}
    class_fields = {"class_iri": {"type": "string"}}
    variants = [
        operation_schema(
            "REPLACE_PROPERTY_VALUE",
            {**property_fields, "old_value": value_schema, "new_value": value_schema},
            ["predicate_iri", "old_value", "new_value"],
        ),
        operation_schema(
            "ADD_PROPERTY_VALUE",
            {**property_fields, "new_value": value_schema},
            ["predicate_iri", "new_value"],
        ),
        operation_schema(
            "REMOVE_PROPERTY_VALUE",
            {**property_fields, "old_value": value_schema},
            ["predicate_iri", "old_value"],
        ),
        operation_schema(
            "ADD_CLASS_ASSERTION",
            class_fields,
            ["class_iri"],
        ),
        operation_schema(
            "REMOVE_CLASS_ASSERTION",
            class_fields,
            ["class_iri"],
        ),
    ]
    return {
        "type": "object",
        "properties": {
            "abstain": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "operations": {
                "type": "array",
                "maxItems": max_operations,
                "items": {"oneOf": variants},
            },
        },
        "required": ["abstain", "confidence", "operations"],
        "additionalProperties": False,
    }


def local_name(value: object) -> str:
    text = unquote(str(value))
    if "#" in text:
        return text.rsplit("#", 1)[1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise RuntimeError(f"CSV为空：{path}")
    return rows


def load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="xml")
    return graph


def all_uris(graph: Graph) -> set[URIRef]:
    result: set[URIRef] = set()
    for subject, predicate, obj in graph:
        for value in (subject, predicate, obj):
            if isinstance(value, URIRef):
                result.add(value)
    return result


def find_unique_iri(graph: Graph, label: str) -> URIRef:
    matches = {value for value in all_uris(graph) if local_name(value) == label}
    if len(matches) != 1:
        raise RuntimeError(f"实体局部名必须唯一：{label}，实际={sorted(map(str, matches))}")
    return next(iter(matches))


def canonical_spec(spec: dict[str, str]) -> str:
    return json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def expand_datatype(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("xsd:"):
        return XSD_PREFIX + value.split(":", 1)[1]
    return value


def literal_from_evidence(row: dict[str, str]) -> Literal:
    datatype_text = expand_datatype(row.get("value_datatype", ""))
    datatype = URIRef(datatype_text) if datatype_text else None
    return Literal(row["object_or_value"], datatype=datatype)


def current_evidence_rows() -> tuple[Path, list[dict[str, str]]]:
    evidence_path = benchmark_validator.choose_evidence()
    return evidence_path, benchmark_validator.current_evidence_rows(evidence_path)


def failed_cqs(graph: Graph, cqs: list[dict[str, str]]) -> list[dict[str, str]]:
    return [cq for cq in cqs if not cq_runner.evaluate_cq(graph, cq)[0]]


def actual_for_cq(graph: Graph, cq: dict[str, str]) -> list[dict[str, str]]:
    subject = find_unique_iri(graph, cq["subject_label"])
    if cq["query_type"] in {"CLASS_PRESENT", "CLASS_ABSENT"}:
        values = [
            value
            for value in graph.objects(subject, RDF.type)
            if isinstance(value, URIRef)
            and not str(value).startswith((str(OWL), str(RDF), str(RDFS)))
        ]
    else:
        predicate = find_unique_iri(graph, cq["predicate_label"])
        values = [
            value
            for value in graph.objects(subject, predicate)
            if isinstance(value, (URIRef, Literal))
        ]
    result: list[dict[str, str]] = []
    for value in sorted(values, key=str):
        item = term_to_spec(value)
        item["label"] = local_name(value) if isinstance(value, URIRef) else str(value)
        result.append(item)
    return result


def evidence_is_relevant(row: dict[str, str], cq: dict[str, str]) -> bool:
    if row.get("subject_label", "").strip() != cq["subject_label"].strip():
        return False
    if cq["query_type"] in {"CLASS_PRESENT", "CLASS_ABSENT"}:
        return row.get("axiom_kind", "").strip() == "ClassAssertion"
    return row.get("predicate_label", "").strip() == cq["predicate_label"].strip()


def evidence_to_prompt(row: dict[str, str], graph: Graph) -> dict[str, str]:
    result = {
        "record_id": row.get("record_id", ""),
        "axiom_kind": row.get("axiom_kind", ""),
        "subject_label": row.get("subject_label", ""),
        "predicate_label": row.get("predicate_label", ""),
        "object_or_value": row.get("object_or_value", ""),
        "evidence_text": row.get("evidence_text", ""),
        "source_document_id": row.get("source_document_id", ""),
        "document_version": row.get("document_version", ""),
        "clause_no": row.get("clause_no", ""),
        "effective_from": row.get("effective_from", ""),
        "effective_to": row.get("effective_to", ""),
        "document_status": row.get("document_status", ""),
        "authority_level": row.get("authority_level", ""),
        "review_status": row.get("review_status", ""),
    }
    try:
        result["subject_iri"] = str(find_unique_iri(graph, row["subject_label"]))
    except RuntimeError:
        pass
    if row.get("axiom_kind", "").strip() != "ClassAssertion":
        try:
            result["predicate_iri"] = str(find_unique_iri(graph, row["predicate_label"]))
        except RuntimeError:
            pass
    if row.get("object_iri", "").strip():
        # 证据CSV可能使用 file:/G:/...，而OWL解析后为 file:///G:/...。
        # 始终优先写入当前本体中真实存在的规范IRI，防止路径形式差异造成误拒绝。
        try:
            result["object_iri"] = str(find_unique_iri(graph, row["object_or_value"]))
        except RuntimeError:
            result["object_iri"] = row["object_iri"].strip()
    if row.get("value_datatype", "").strip():
        result["value_datatype"] = expand_datatype(row["value_datatype"])
    return result


def literal_whitelist(
    graph: Graph, evidence_rows: list[dict[str, str]]
) -> tuple[set[str], list[dict[str, str]]]:
    specs: dict[str, dict[str, str]] = {}
    for _, _, obj in graph:
        if isinstance(obj, Literal):
            spec = term_to_spec(obj)
            specs[canonical_spec(spec)] = spec
    for row in evidence_rows:
        if row.get("axiom_kind", "").strip() == "DataPropertyAssertion":
            spec = term_to_spec(literal_from_evidence(row))
            specs[canonical_spec(spec)] = spec
    ordered = sorted(specs.values(), key=canonical_spec)
    return set(specs), ordered


def iri_whitelist(graph: Graph) -> tuple[set[str], list[dict[str, str]]]:
    values = sorted({str(item) for item in all_uris(graph)})
    return set(values), [{"label": local_name(value), "iri": value} for value in values]


def relevant_axioms(graph: Graph, violated: list[dict[str, str]]) -> list[dict[str, Any]]:
    seeds: set[URIRef] = set()
    for cq in violated:
        seeds.add(find_unique_iri(graph, cq["subject_label"]))
        if cq["query_type"] in {"CLASS_PRESENT", "CLASS_ABSENT"}:
            seeds.add(find_unique_iri(graph, cq["expected_answer"]))
        else:
            seeds.add(find_unique_iri(graph, cq["predicate_label"]))

    selected: set[tuple[object, object, object]] = set()
    structural_predicates = {
        RDF.type,
        RDFS.subClassOf,
        RDFS.domain,
        RDFS.range,
        RDFS.subPropertyOf,
        OWL.disjointWith,
        OWL.equivalentClass,
    }
    for triple in graph:
        subject, predicate, obj = triple
        if subject in seeds or predicate in seeds or obj in seeds:
            selected.add(triple)
            if predicate == RDF.type and isinstance(obj, URIRef):
                seeds.add(obj)
    for triple in graph:
        subject, predicate, obj = triple
        if predicate in structural_predicates and (subject in seeds or obj in seeds):
            selected.add(triple)

    def render(value: object) -> dict[str, str]:
        if isinstance(value, URIRef):
            return {"kind": "iri", "label": local_name(value), "iri": str(value)}
        if isinstance(value, Literal):
            result = term_to_spec(value)
            result["label"] = str(value)
            return result
        return {"kind": type(value).__name__, "value": str(value)}

    return [
        {"subject": render(s), "predicate": render(p), "object": render(o)}
        for s, p, o in sorted(selected, key=lambda triple: tuple(map(str, triple)))
    ]


def prompt_cq(cq: dict[str, str], graph: Graph, mode: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "cq_id": cq["cq_id"],
        "question": cq["question"],
        "query_type": cq["query_type"],
        "subject_label": cq["subject_label"],
        "predicate_label": cq["predicate_label"],
        "actual_values": actual_for_cq(graph, cq),
        "rationale": cq.get("rationale", ""),
    }
    if mode in {"cq_evidence", "cq_only"}:
        item["expected_answer"] = cq.get("expected_answer", "")
        item["expected_count"] = cq.get("expected_count", "")
    return item


def build_prompt(
    graph: Graph,
    error_id: str,
    violated: list[dict[str, str]],
    evidence_rows: list[dict[str, str]],
    mode: str,
    max_operations: int,
) -> tuple[str, str, set[str], set[str]]:
    iri_set, iri_items = iri_whitelist(graph)
    literal_set, literal_items = literal_whitelist(graph, evidence_rows)
    relevant_evidence = {
        row.get("record_id", ""): row
        for row in evidence_rows
        if any(evidence_is_relevant(row, cq) for cq in violated)
    }

    context: dict[str, Any] = {
        "experiment": {
            "error_id": error_id,
            "prompt_mode": mode,
        },
        "failed_competency_questions": [prompt_cq(cq, graph, mode) for cq in violated],
        "relevant_ontology_axioms": relevant_axioms(graph, violated),
        "allowed_operators": sorted(ALLOWED_OPERATORS),
        "max_operations": max_operations,
        "iri_whitelist": iri_items,
        "literal_whitelist": literal_items,
    }
    if mode in {"evidence_only", "cq_evidence"}:
        context["current_effective_evidence"] = [
            evidence_to_prompt(row, graph) for row in relevant_evidence.values()
        ]
    else:
        context["current_effective_evidence"] = "HIDDEN_FOR_ABLATION"

    system_prompt = (
        "你是受形式化约束的OWL本体修复候选选择器。"
        "你没有权限直接编辑OWL，也不能创造IRI或自由生成公理。"
        "你只能从给定有限算子、IRI白名单和字面量白名单中选择。"
        "证据不足时必须abstain。只输出一个JSON对象，不要Markdown，不要解释性前后缀。"
    )
    output_contract = {
        "abstain": False,
        "confidence": 0.0,
        "operations": [
            {
                "operator": "REPLACE_PROPERTY_VALUE",
                "subject_iri": "从IRI白名单原样复制",
                "predicate_iri": "从IRI白名单原样复制；类别断言算子不使用此字段",
                "class_iri": "仅类别断言算子使用；从IRI白名单原样复制",
                "old_value": {
                    "kind": "iri或literal",
                    "iri": "kind=iri时使用",
                    "lexical": "kind=literal时使用",
                    "datatype": "若原值有datatype则必须完整复制",
                },
                "new_value": {
                    "kind": "iri或literal",
                    "iri": "kind=iri时使用",
                    "lexical": "kind=literal时使用",
                    "datatype": "若候选值有datatype则必须完整复制",
                },
                "source_cq_ids": ["至少一个失败CQ编号"],
                "rationale": "简短说明该操作与证据或失败CQ的对应关系",
            }
        ],
    }
    user_prompt = (
        "请为以下错误本体选择最小修复候选。严格规则：\n"
        f"1. operations数量为1到{max_operations}；无法可靠决定时abstain=true且operations=[]。\n"
        "2. 不得输出SPARQL、Manchester Syntax、RDF/XML或任意OWL文本。\n"
        "3. IRI必须逐字符复制自iri_whitelist。\n"
        "4. 新字面量必须逐字段复制自literal_whitelist。\n"
        "5. REPLACE/REMOVE的old_value必须是actual_values中真实存在的值。\n"
        "6. 类别断言算子只使用subject_iri与class_iri；属性断言算子使用subject_iri、predicate_iri和值。\n"
        "7. 优先最少操作；不要为了提高置信度添加无关修改。\n"
        "8. confidence必须是0到1之间的数。\n"
        "9. 各算子的必填字段：\n"
        "   REPLACE_PROPERTY_VALUE = subject_iri + predicate_iri + old_value + new_value；\n"
        "   ADD_PROPERTY_VALUE = subject_iri + predicate_iri + new_value；\n"
        "   REMOVE_PROPERTY_VALUE = subject_iri + predicate_iri + old_value；\n"
        "   ADD_CLASS_ASSERTION/REMOVE_CLASS_ASSERTION = subject_iri + class_iri。\n"
        "10. source_cq_ids只能复制failed_competency_questions中的cq_id（CQ开头）；"
        "禁止把EVID开头的证据记录编号填入该字段。\n\n"
        "输出结构示例（字段不适用时应删除，而不是保留占位文字）：\n"
        + json.dumps(output_contract, ensure_ascii=False, indent=2)
        + "\n\n输入上下文：\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )
    return system_prompt, user_prompt, iri_set, literal_set


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
    opener = local_opener(url)
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
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
            f"Ollama中没有模型 {model}。当前模型={sorted(name for name in names if name)}；"
            f"请先执行 ollama pull {model}"
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
    output_schema: dict[str, Any],
) -> tuple[str, dict[str, Any], int]:
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "format": output_schema,
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
    attempts = [payload]
    without_think = deepcopy(payload)
    without_think.pop("think", None)
    attempts.append(without_think)
    json_only = deepcopy(without_think)
    json_only["format"] = "json"
    attempts.append(json_only)
    response: dict[str, Any] | None = None
    last_error: RuntimeError | None = None
    for index, attempt in enumerate(attempts):
        try:
            response = http_json("POST", f"{base_url.rstrip('/')}/api/chat", timeout, attempt)
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
        "total_duration": response.get("total_duration", 0),
        "load_duration": response.get("load_duration", 0),
        "prompt_eval_count": response.get("prompt_eval_count", 0),
        "eval_count": response.get("eval_count", 0),
        "eval_duration": response.get("eval_duration", 0),
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
        try:
            value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型输出不是有效JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("模型输出顶层必须是JSON对象")
    return value


class CandidateValidationError(ValueError):
    def __init__(self, message: str, guards: dict[str, bool]):
        super().__init__(message)
        self.guards = dict(guards)


def require_boolean(value: object, field: str) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "false"}:
            return normalized == "true"

    if not isinstance(value, bool):
        raise ValueError(f"{field}必须是JSON布尔值")

    return value


def validate_model_payload(
    payload: dict[str, Any],
    graph: Graph,
    failed_cq_ids: set[str],
    iri_set: set[str],
    literal_set: set[str],
    max_operations: int,
) -> tuple[list[dict[str, Any]], dict[str, bool], float, bool]:
    # 非空 operations 优先于 abstain，兼容本地模型偶发的字段冲突；
    # 后续仍必须通过结构、IRI、字面量和可应用性四层门禁。
    guards = {
        "schema_pass": False,
        "iri_guard_pass": False,
        "literal_guard_pass": False,
        "apply_pass": False,
    }
    try:
        abstain = require_boolean(payload.get("abstain"), "abstain")
        confidence_raw = payload.get("confidence")
        if isinstance(confidence_raw, bool) or not isinstance(confidence_raw, (int, float)):
            raise ValueError("confidence必须是0到1之间的数")
        confidence = float(confidence_raw)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence必须位于[0,1]")
        operations_raw = payload.get("operations")
        if not isinstance(operations_raw, list):
            raise ValueError("operations必须是数组")
        if abstain and operations_raw:
            print(
                "  [格式归一化] abstain=true但operations非空，按候选操作继续验证。",
                flush=True,
            )
            abstain = False

        if abstain:
            guards.update({
                "schema_pass": True,
                "iri_guard_pass": True,
                "literal_guard_pass": True,
            })
            return [], guards, confidence, True
        if not operations_raw:
            raise ValueError("abstain=false时至少需要一个修复操作")
        if len(operations_raw) > max_operations:
            raise ValueError(f"操作数{len(operations_raw)}超过上限{max_operations}")

        operations: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(operations_raw, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"第{index}个操作必须是JSON对象")
            operation = deepcopy(raw)
            validate_operation_schema(operation)
            source_ids = operation.get("source_cq_ids")
            if not isinstance(source_ids, list) or not source_ids or not all(
                isinstance(item, str) for item in source_ids
            ):
                raise ValueError(f"第{index}个操作的source_cq_ids必须是非空字符串数组")
            unknown_cqs = set(source_ids) - failed_cq_ids
            if unknown_cqs:
                raise ValueError(f"第{index}个操作引用了非失败CQ：{sorted(unknown_cqs)}")
            if not isinstance(operation.get("rationale"), str) or not operation["rationale"].strip():
                raise ValueError(f"第{index}个操作缺少rationale")
            key = operation_key(operation)
            if key in seen:
                raise ValueError("operations中存在重复操作")
            seen.add(key)
            operations.append(operation)
        guards["schema_pass"] = True
    except Exception as exc:
        raise CandidateValidationError(str(exc), guards) from exc

    try:
        for operation in operations:
            for field in ("subject_iri", "predicate_iri", "class_iri"):
                value = operation.get(field)
                if value is not None and str(value) not in iri_set:
                    raise ValueError(f"拒绝模型生成的非白名单IRI：{field}={value}")
            for field in ("old_value", "new_value"):
                spec = operation.get(field)
                if not isinstance(spec, dict):
                    continue
                if spec.get("kind") == "iri" and spec.get("iri") not in iri_set:
                    raise ValueError(f"拒绝模型生成的非白名单对象IRI：{spec.get('iri')}")
        guards["iri_guard_pass"] = True
    except Exception as exc:
        raise CandidateValidationError(str(exc), guards) from exc

    try:
        for operation in operations:
            new_value = operation.get("new_value")
            if isinstance(new_value, dict) and new_value.get("kind") == "literal":
                normalized = {
                    key: str(value)
                    for key, value in new_value.items()
                    if key in {"kind", "lexical", "datatype", "language"}
                    and value is not None
                    and value != ""
                }
                if canonical_spec(normalized) not in literal_set:
                    raise ValueError(f"拒绝模型生成的非白名单字面量：{normalized}")
        guards["literal_guard_pass"] = True
    except Exception as exc:
        raise CandidateValidationError(str(exc), guards) from exc

    try:
        # apply_operations还会验证主体/属性/类别存在、旧断言存在以及IRI对象存在。
        apply_operations(graph, operations)
        guards["apply_pass"] = True
    except Exception as exc:
        raise CandidateValidationError(str(exc), guards) from exc
    return operations, guards, confidence, False


def set_ontology_identity(graph: Graph, error_id: str, run_id: int, seed: int) -> str:
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(f"{ONTOLOGY_BASE}/{error_id}/run-{run_id:03d}/seed-{seed}")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    return str(ontology_iri)


def parse_only(text: str) -> set[str]:
    return {item.strip().upper() for item in text.split(",") if item.strip()}


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def blank_row() -> dict[str, Any]:
    return {
        "candidate_id": "",
        "error_id": "",
        "error_type": "",
        "run_id": "",
        "seed": "",
        "model": "",
        "prompt_mode": "",
        "temperature": "",
        "source_mutant": "",
        "raw_response": "",
        "candidate_json": "",
        "candidate_owl": "",
        "operations_json": "[]",
        "operation_count": 0,
        "operators": "",
        "source_cq_ids": "",
        "failed_cq_ids": "",
        "json_parse_pass": "false",
        "schema_pass": "false",
        "iri_guard_pass": "false",
        "literal_guard_pass": "false",
        "apply_pass": "false",
        "accepted": "false",
        "abstained": "false",
        "confidence": "",
        "llm_runtime_ms": 0,
        "prompt_eval_count": 0,
        "eval_count": 0,
        "error_message": "",
        "ontology_iri": "",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用本地Qwen生成受限OWL修复候选")
    parser.add_argument("--model", default="qwen3.5:9b", help="Ollama模型名")
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        help="Ollama服务地址",
    )
    parser.add_argument("--runs", type=int, default=1, help="每类错误重复生成次数")
    parser.add_argument("--seed", type=int, default=20260820, help="首个随机种子")
    parser.add_argument("--temperature", type=float, default=0.0, help="模型temperature")
    parser.add_argument("--timeout", type=int, default=600, help="单次Ollama请求超时秒数")
    parser.add_argument("--num-predict", type=int, default=1200, help="最大生成token数")
    parser.add_argument("--keep-alive", default="30m", help="Ollama模型驻留时间")
    parser.add_argument("--max-operations", type=int, default=1, help="单个候选最大操作数")
    parser.add_argument(
        "--prompt-mode",
        choices=sorted(PROMPT_MODES),
        default="evidence_only",
        help="提示消融模式；默认不给Qwen直接展示CQ标准答案",
    )
    parser.add_argument("--only", default="", help="只运行指定错误，如 E1,E3")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="候选清单输出位置")
    parser.add_argument("--skip-model-check", action="store_true", help="跳过/api/tags模型存在检查")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs必须大于等于1")
    if args.max_operations < 1:
        raise ValueError("--max-operations必须大于等于1")
    if not 0.0 <= args.temperature <= 2.0:
        raise ValueError("--temperature必须位于[0,2]")
    for path in (CQ_FILE, MANIFEST):
        if not path.is_file():
            print(f"缺少文件：{path}")
            return 2

    if not args.skip_model_check:
        print(f"[连接检查] Ollama={args.ollama_url}，模型={args.model}", flush=True)
        check_ollama(args.ollama_url, args.model, args.timeout)
        print("[连接检查] Ollama与模型均可用。\n", flush=True)

    cqs = load_csv(CQ_FILE)
    mutants = load_csv(MANIFEST)
    selected = parse_only(args.only)
    if selected:
        known = {row["error_id"].upper() for row in mutants}
        unknown = selected - known
        if unknown:
            raise ValueError(f"--only包含未知错误编号：{sorted(unknown)}")
        mutants = [row for row in mutants if row["error_id"].upper() in selected]
    evidence_path, evidence_rows = current_evidence_rows()

    mode_root = QWEN_CANDIDATE_ROOT / args.prompt_mode
    mode_root.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    logs = [
        "本地Qwen受限修复候选生成日志",
        f"模型：{args.model}",
        f"提示模式：{args.prompt_mode}",
        f"每类错误运行次数：{args.runs}",
        f"temperature：{args.temperature}",
        f"首个seed：{args.seed}",
        f"证据文件：{evidence_path}",
    ]
    total = len(mutants) * args.runs
    current = 0

    for mutant in mutants:
        error_id = mutant["error_id"]
        mutant_path = MUTANTS_DIR / mutant["file_name"]
        if not mutant_path.is_file():
            raise FileNotFoundError(f"缺少错误本体：{mutant_path}")
        graph = load_graph(mutant_path)
        violated = failed_cqs(graph, cqs)
        if not violated:
            raise RuntimeError(f"{error_id}没有触发任何独立CQ失败，拒绝盲目调用Qwen")

        system_prompt, user_prompt, iri_set, literal_set = build_prompt(
            graph,
            error_id,
            violated,
            evidence_rows,
            args.prompt_mode,
            args.max_operations,
        )
        for run_id in range(1, args.runs + 1):
            current += 1
            seed = args.seed + run_id - 1
            candidate_id = f"qwen-{error_id}-run-{run_id:03d}"
            run_dir = mode_root / error_id / f"run-{run_id:03d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            raw_path = run_dir / "raw-response.txt"
            parsed_path = run_dir / "candidate.json"
            owl_path = run_dir / "candidate.owl"
            row = blank_row()
            row.update({
                "candidate_id": candidate_id,
                "error_id": error_id,
                "error_type": mutant["error_type"],
                "run_id": run_id,
                "seed": seed,
                "model": args.model,
                "prompt_mode": args.prompt_mode,
                "temperature": args.temperature,
                "source_mutant": str(mutant_path),
                "raw_response": str(raw_path),
                "candidate_json": str(parsed_path),
                "failed_cq_ids": " | ".join(cq["cq_id"] for cq in violated),
            })
            print(
                f"[{current}/{total}] {error_id} run={run_id} seed={seed}：正在调用Qwen……",
                flush=True,
            )
            raw_text = ""
            try:
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
                    response_schema(
                        args.max_operations,
                        {cq["cq_id"] for cq in violated},
                    ),
                )
                raw_path.write_text(raw_text, encoding="utf-8")
                row["llm_runtime_ms"] = runtime_ms
                row["prompt_eval_count"] = metadata.get("prompt_eval_count", 0)
                row["eval_count"] = metadata.get("eval_count", 0)

                payload = extract_json(raw_text)
                row["json_parse_pass"] = "true"
                parsed_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                operations, guards, confidence, abstained = validate_model_payload(
                    payload,
                    graph,
                    {cq["cq_id"] for cq in violated},
                    iri_set,
                    literal_set,
                    args.max_operations,
                )
                for key, value in guards.items():
                    row[key] = bool_text(value)
                row["confidence"] = confidence
                row["abstained"] = bool_text(abstained)
                row["operations_json"] = json.dumps(operations, ensure_ascii=False)
                row["operation_count"] = len(operations)
                row["operators"] = " | ".join(op["operator"] for op in operations)
                row["source_cq_ids"] = " | ".join(
                    sorted({cq_id for op in operations for cq_id in op["source_cq_ids"]})
                )

                if abstained:
                    row["error_message"] = "模型选择ABSTAIN：没有形成可应用候选"
                    status = "ABSTAIN"
                else:
                    repaired = apply_operations(graph, operations)
                    ontology_iri = set_ontology_identity(repaired, error_id, run_id, seed)
                    repaired.serialize(destination=owl_path, format="xml", encoding="utf-8")
                    row["candidate_owl"] = str(owl_path)
                    row["ontology_iri"] = ontology_iri
                    row["accepted"] = "true"
                    status = "ACCEPTED"
                message = (
                    f"  结果={status} | 操作={row['operators'] or '-'} | "
                    f"confidence={row['confidence']} | 耗时={row['llm_runtime_ms']}ms"
                )
            except Exception as exc:
                if isinstance(exc, CandidateValidationError):
                    for key, value in exc.guards.items():
                        row[key] = bool_text(value)
                if raw_text and not raw_path.exists():
                    raw_path.write_text(raw_text, encoding="utf-8")
                row["error_message"] = f"{type(exc).__name__}: {exc}"
                message = f"  结果=REJECTED | {row['error_message']}"
            rows.append(row)
            print(message, flush=True)
            logs.extend([f"[{candidate_id}]", message])

    if not rows:
        raise RuntimeError("没有产生任何实验记录")
    fieldnames = list(blank_row())
    with args.manifest.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    with GENERATION_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    with GENERATION_JSON.open("w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, indent=2)

    accepted = sum(row["accepted"] == "true" for row in rows)
    abstained = sum(row["abstained"] == "true" for row in rows)
    parsed = sum(row["json_parse_pass"] == "true" for row in rows)
    summary = (
        f"候选生成汇总：总尝试={len(rows)}，JSON可解析={parsed}，"
        f"候选已接受={accepted}，主动放弃={abstained}，程序拒绝={len(rows)-accepted-abstained}"
    )
    print(f"\n{summary}")
    print(f"候选清单：{args.manifest}")
    print(f"生成记录：{GENERATION_CSV}")
    print(f"JSON记录：{GENERATION_JSON}")
    logs.extend(["", summary, f"候选清单：{args.manifest}"])
    GENERATION_LOG.write_text("\n".join(logs) + "\n", encoding="utf-8")
    print(f"日志：{GENERATION_LOG}")
    print("\n注意：ACCEPTED只表示通过结构与白名单门禁，不代表语义修复成功。")
    print("下一步请运行：python .\\src\\compare_repair_methods.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已中断] 用户终止了Qwen候选生成。")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[Qwen候选生成停止] {type(exc).__name__}: {exc}")
        sys.exit(2)