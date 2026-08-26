from __future__ import annotations

r"""
从失败CQ、当前有效证据和错误本体中枚举“可执行但未必语义正确”的有限修复候选。

本脚本不调用LLM，也不使用合成错误标准答案来选择候选。它只负责：
1. 将CQ定位到真实主体、属性、类别和RDF值；
2. 使用有限算子构造候选；
3. 通过 repair_operators.apply_operations 检查候选是否可执行；
4. 输出稳定候选ID，供 qwen_rank_candidates.py 选择。

默认运行：
    python .\src\enumerate_feasible_candidates.py
"""

import argparse
import csv
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

try:
    from rdflib import Graph, Literal, RDF, URIRef
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)

try:
    import run_cq_tests as cq_runner
    from repair_operators import (
        apply_operations,
        term_to_spec,
        validate_operation_schema,
    )
except ImportError as exc:
    print(
        "缺少项目模块。请把本文件放入 ontology-evolution\\src，"
        "并确认 run_cq_tests.py 与 repair_operators.py 位于同一目录。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
CQ_FILE = PROJECT_DIR / "benchmark" / "cq" / "cq-tests.csv"
MANIFEST = PROJECT_DIR / "benchmark" / "ground-truth" / "error-manifest.csv"
MUTANTS_DIR = PROJECT_DIR / "benchmark" / "mutants"
EVIDENCE_CANDIDATES = [
    PROJECT_DIR / "data" / "axiom-evidence.csv",
    PROJECT_DIR / "axiom-evidence.csv",
]
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
DEFAULT_JSON = REPAIR_DIR / "feasible-candidates.json"
DEFAULT_CSV = REPAIR_DIR / "feasible-candidates.csv"
OUTPUT_DIR = PROJECT_DIR / "output"
DEFAULT_LOG = OUTPUT_DIR / "feasible-candidate-enumeration.log"
XSD_PREFIX = "http://www.w3.org/2001/XMLSchema#"


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


def local_name(value: object) -> str:
    text = unquote(str(value))
    if "#" in text:
        return text.rsplit("#", 1)[1]
    return text.rstrip("/").rsplit("/", 1)[-1]


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
        raise RuntimeError(
            f"实体局部名必须唯一：{label}，实际={sorted(map(str, matches))}"
        )
    return next(iter(matches))


def expand_datatype(value: str) -> str:
    text = value.strip()
    if text.startswith("xsd:"):
        return XSD_PREFIX + text.split(":", 1)[1]
    return text


def choose_evidence() -> Path:
    for path in EVIDENCE_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("没有找到 data\\axiom-evidence.csv")


def current_evidence_rows(path: Path) -> list[dict[str, str]]:
    rows = load_csv(path)
    current = [
        row
        for row in rows
        if row.get("ontology_version", "").strip() in {"2", "2.0", "v2", "V2"}
        and row.get("document_status", "").strip() in {"", "当前有效"}
        and row.get("evidence_relation", "supports").strip().lower() == "supports"
    ]
    if not current:
        raise RuntimeError("证据表中没有当前有效的2.0版本支持证据")
    return current


def split_values(text: str) -> set[str]:
    return {item.strip() for item in text.split("|") if item.strip()}


def parse_only(text: str) -> set[str]:
    return {item.strip().upper() for item in text.split(",") if item.strip()}


def failed_cqs(graph: Graph, cqs: list[dict[str, str]]) -> list[dict[str, str]]:
    return [cq for cq in cqs if not cq_runner.evaluate_cq(graph, cq)[0]]


def cq_actual_values(graph: Graph, cq: dict[str, str]) -> list[str]:
    _, actual, _ = cq_runner.evaluate_cq(graph, cq)
    return sorted(actual)


def evidence_for_cq(
    evidence_rows: list[dict[str, str]], cq: dict[str, str]
) -> list[dict[str, str]]:
    subject = cq["subject_label"].strip()
    query_type = cq["query_type"].strip()
    if query_type in {"CLASS_PRESENT", "CLASS_ABSENT"}:
        expected = cq.get("expected_answer", "").strip()
        return [
            row
            for row in evidence_rows
            if row.get("subject_label", "").strip() == subject
            and row.get("axiom_kind", "").strip() == "ClassAssertion"
            and (not expected or row.get("object_or_value", "").strip() == expected)
        ]
    predicate = cq.get("predicate_label", "").strip()
    return [
        row
        for row in evidence_rows
        if row.get("subject_label", "").strip() == subject
        and row.get("predicate_label", "").strip() == predicate
    ]


def evidence_term(graph: Graph, row: dict[str, str]) -> URIRef | Literal:
    kind = row.get("axiom_kind", "").strip()
    if kind in {"ClassAssertion", "ObjectPropertyAssertion"}:
        label = row.get("object_or_value", "").strip()
        if not label:
            raise ValueError(f"{row.get('record_id', '')}缺少对象标签")
        return find_unique_iri(graph, label)
    lexical = row.get("object_or_value", "")
    datatype_text = expand_datatype(row.get("value_datatype", ""))
    datatype = URIRef(datatype_text) if datatype_text else None
    return Literal(lexical, datatype=datatype)


def canonical_operation(operation: dict[str, Any]) -> str:
    structural = {
        key: operation[key]
        for key in (
            "operator",
            "subject_iri",
            "predicate_iri",
            "class_iri",
            "old_value",
            "new_value",
        )
        if key in operation
    }
    return json.dumps(
        structural,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def value_label(spec: dict[str, str] | None) -> str:
    if not spec:
        return ""
    if spec.get("kind") == "iri":
        return local_name(spec.get("iri", ""))
    return spec.get("lexical", "")


def operation_description(operation: dict[str, Any]) -> str:
    operator = operation["operator"]
    subject = local_name(operation["subject_iri"])
    if operator in {"ADD_CLASS_ASSERTION", "REMOVE_CLASS_ASSERTION"}:
        action = "新增类别" if operator == "ADD_CLASS_ASSERTION" else "删除类别"
        return f"{action}：{subject} rdf:type {local_name(operation['class_iri'])}"
    predicate = local_name(operation["predicate_iri"])
    old_label = value_label(operation.get("old_value"))
    new_label = value_label(operation.get("new_value"))
    if operator == "REPLACE_PROPERTY_VALUE":
        return f"替换属性：{subject}—{predicate}，{old_label} → {new_label}"
    if operator == "ADD_PROPERTY_VALUE":
        return f"新增属性：{subject}—{predicate}—{new_label}"
    return f"删除属性：{subject}—{predicate}—{old_label}"


def add_candidate(
    graph: Graph,
    candidates: dict[str, dict[str, Any]],
    operation: dict[str, Any],
    cq_ids: set[str],
    evidence_ids: set[str],
    rationale: str,
) -> None:
    validate_operation_schema(operation)
    # 只有能被有限算子实际应用的候选才进入候选空间。
    apply_operations(graph, [operation])
    key = canonical_operation(operation)
    if key in candidates:
        existing = candidates[key]
        existing["source_cq_ids"] = sorted(
            set(existing["source_cq_ids"]) | cq_ids
        )
        existing["source_evidence_ids"] = sorted(
            set(existing["source_evidence_ids"]) | evidence_ids
        )
        if rationale not in existing["rationales"]:
            existing["rationales"].append(rationale)
        return
    candidates[key] = {
        "operation": deepcopy(operation),
        "source_cq_ids": sorted(cq_ids),
        "source_evidence_ids": sorted(evidence_ids),
        "rationales": [rationale],
    }


def enumerate_class_candidates(
    graph: Graph,
    cq: dict[str, str],
    evidence_rows: list[dict[str, str]],
    candidates: dict[str, dict[str, Any]],
) -> None:
    subject = find_unique_iri(graph, cq["subject_label"])
    target = find_unique_iri(graph, cq["expected_answer"])
    triple = (subject, RDF.type, target)
    evidence_ids = {row.get("record_id", "") for row in evidence_rows if row.get("record_id")}
    common = {"subject_iri": str(subject), "class_iri": str(target)}
    if cq["query_type"] == "CLASS_PRESENT" and triple not in graph:
        add_candidate(
            graph,
            candidates,
            {"operator": "ADD_CLASS_ASSERTION", **common},
            {cq["cq_id"]},
            evidence_ids,
            f"{cq['cq_id']}要求目标类别必须存在",
        )
    elif cq["query_type"] == "CLASS_ABSENT" and triple in graph:
        add_candidate(
            graph,
            candidates,
            {"operator": "REMOVE_CLASS_ASSERTION", **common},
            {cq["cq_id"]},
            evidence_ids,
            f"{cq['cq_id']}禁止目标类别存在",
        )


def property_context(
    graph: Graph,
    cq: dict[str, str],
    evidence_rows: list[dict[str, str]],
) -> tuple[URIRef, URIRef, list[URIRef | Literal], list[URIRef | Literal], set[str]]:
    subject = find_unique_iri(graph, cq["subject_label"])
    predicate = find_unique_iri(graph, cq["predicate_label"])
    actual = list(graph.objects(subject, predicate))
    expected: list[URIRef | Literal] = []
    seen: set[str] = set()
    evidence_ids: set[str] = set()
    for row in evidence_rows:
        evidence_ids.add(row.get("record_id", ""))
        term = evidence_term(graph, row)
        key = term.n3()
        if key not in seen:
            seen.add(key)
            expected.append(term)
    if not expected and cq.get("expected_answer", "").strip():
        raise RuntimeError(
            f"{cq['cq_id']}没有匹配到当前有效证据；为避免无证据生成，不从CQ文本猜测RDF类型"
        )
    return subject, predicate, actual, expected, {item for item in evidence_ids if item}


def enumerate_exact_set_candidates(
    graph: Graph,
    cq: dict[str, str],
    evidence_rows: list[dict[str, str]],
    candidates: dict[str, dict[str, Any]],
) -> None:
    subject, predicate, actual, expected, evidence_ids = property_context(
        graph, cq, evidence_rows
    )
    cq_ids = {cq["cq_id"]}
    # 删除候选：每个当前真实存在的值都可以安全地作为“可执行候选”。
    for old_term in actual:
        add_candidate(
            graph,
            candidates,
            {
                "operator": "REMOVE_PROPERTY_VALUE",
                "subject_iri": str(subject),
                "predicate_iri": str(predicate),
                "old_value": term_to_spec(old_term),
            },
            cq_ids,
            evidence_ids,
            f"{cq['cq_id']}的当前属性集合与要求不一致，尝试删除现有值",
        )
    # 新增候选：只允许增加来自当前有效证据且当前尚不存在的值。
    for new_term in expected:
        if (subject, predicate, new_term) in graph:
            continue
        add_candidate(
            graph,
            candidates,
            {
                "operator": "ADD_PROPERTY_VALUE",
                "subject_iri": str(subject),
                "predicate_iri": str(predicate),
                "new_value": term_to_spec(new_term),
            },
            cq_ids,
            evidence_ids,
            f"{cq['cq_id']}缺少当前有效证据支持的目标值",
        )
    # 替换候选：旧值必须存在，新值必须由当前有效证据提供。
    for old_term in actual:
        for new_term in expected:
            if old_term == new_term:
                continue
            add_candidate(
                graph,
                candidates,
                {
                    "operator": "REPLACE_PROPERTY_VALUE",
                    "subject_iri": str(subject),
                    "predicate_iri": str(predicate),
                    "old_value": term_to_spec(old_term),
                    "new_value": term_to_spec(new_term),
                },
                cq_ids,
                evidence_ids,
                f"{cq['cq_id']}允许用当前有效证据值替换现有值",
            )


def enumerate_cardinality_candidates(
    graph: Graph,
    cq: dict[str, str],
    evidence_rows: list[dict[str, str]],
    candidates: dict[str, dict[str, Any]],
) -> None:
    subject, predicate, actual, expected, evidence_ids = property_context(
        graph, cq, evidence_rows
    )
    target_count = int(cq["expected_count"])
    cq_ids = {cq["cq_id"]}
    if len(actual) > target_count:
        for old_term in actual:
            add_candidate(
                graph,
                candidates,
                {
                    "operator": "REMOVE_PROPERTY_VALUE",
                    "subject_iri": str(subject),
                    "predicate_iri": str(predicate),
                    "old_value": term_to_spec(old_term),
                },
                cq_ids,
                evidence_ids,
                f"{cq['cq_id']}要求基数为{target_count}，尝试删除一个现有值",
            )
    elif len(actual) < target_count:
        for new_term in expected:
            if (subject, predicate, new_term) in graph:
                continue
            add_candidate(
                graph,
                candidates,
                {
                    "operator": "ADD_PROPERTY_VALUE",
                    "subject_iri": str(subject),
                    "predicate_iri": str(predicate),
                    "new_value": term_to_spec(new_term),
                },
                cq_ids,
                evidence_ids,
                f"{cq['cq_id']}要求基数为{target_count}，尝试增加证据支持值",
            )


def enumerate_for_error(
    graph: Graph,
    failed: list[dict[str, str]],
    evidence_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for cq in failed:
        relevant_evidence = evidence_for_cq(evidence_rows, cq)
        query_type = cq["query_type"]
        if query_type in {"CLASS_PRESENT", "CLASS_ABSENT"}:
            enumerate_class_candidates(graph, cq, relevant_evidence, candidates)
        elif query_type == "PROPERTY_EXACT_SET":
            enumerate_exact_set_candidates(graph, cq, relevant_evidence, candidates)
        elif query_type == "PROPERTY_CARDINALITY":
            enumerate_cardinality_candidates(graph, cq, relevant_evidence, candidates)
        else:
            raise RuntimeError(f"不支持的CQ类型：{query_type}")

    result: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(candidates), start=1):
        item = candidates[key]
        operation = item["operation"]
        candidate_id = f"CAND_{index:03d}"
        result.append({
            "candidate_id": candidate_id,
            "description": operation_description(operation),
            "operator": operation["operator"],
            "operation": operation,
            "source_cq_ids": item["source_cq_ids"],
            "source_evidence_ids": item["source_evidence_ids"],
            "rationales": item["rationales"],
            "feasible": True,
        })
    return result


def write_csv(path: Path, errors: list[dict[str, Any]]) -> None:
    rows: list[dict[str, Any]] = []
    for error in errors:
        for candidate in error["candidates"]:
            operation = candidate["operation"]
            rows.append({
                "error_id": error["error_id"],
                "error_type": error["error_type"],
                "candidate_id": candidate["candidate_id"],
                "description": candidate["description"],
                "operator": candidate["operator"],
                "source_mutant": error["source_mutant"],
                "source_cq_ids": " | ".join(candidate["source_cq_ids"]),
                "source_evidence_ids": " | ".join(candidate["source_evidence_ids"]),
                "operation_json": json.dumps(operation, ensure_ascii=False),
                "feasible": "true",
            })
    if not rows:
        raise RuntimeError("没有生成任何候选")
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="枚举符号可执行的OWL有限修复候选")
    parser.add_argument("--only", default="", help="只处理指定错误，如 E1,E3")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--max-candidates-per-error", type=int, default=50)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    required = [CQ_FILE, MANIFEST]
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)
    if args.max_candidates_per_error < 1:
        raise ValueError("--max-candidates-per-error必须大于0")

    cqs = load_csv(CQ_FILE)
    manifest = load_csv(MANIFEST)
    selected = parse_only(args.only)
    if selected:
        known = {row["error_id"].upper() for row in manifest}
        unknown = selected - known
        if unknown:
            raise ValueError(f"未知错误编号：{sorted(unknown)}")
        manifest = [row for row in manifest if row["error_id"].upper() in selected]
    evidence_path = choose_evidence()
    evidence_rows = current_evidence_rows(evidence_path)

    errors: list[dict[str, Any]] = []
    logs = [
        "符号可执行修复候选枚举日志",
        f"CQ文件：{CQ_FILE}",
        f"证据文件：{evidence_path}",
        "边界：只保证候选可执行，不保证语义正确。",
        "",
    ]
    print("符号可执行修复候选枚举")
    for row in manifest:
        error_id = row["error_id"]
        mutant_path = MUTANTS_DIR / row["file_name"]
        if not mutant_path.is_file():
            raise FileNotFoundError(mutant_path)
        graph = load_graph(mutant_path)
        violated = failed_cqs(graph, cqs)
        if not violated:
            raise RuntimeError(f"{error_id}没有失败CQ，拒绝盲目生成候选")
        candidates = enumerate_for_error(graph, violated, evidence_rows)
        if not candidates:
            raise RuntimeError(f"{error_id}没有产生任何可执行候选")
        if len(candidates) > args.max_candidates_per_error:
            raise RuntimeError(
                f"{error_id}候选数{len(candidates)}超过上限{args.max_candidates_per_error}"
            )
        failed_payload = []
        relevant_evidence: dict[str, dict[str, str]] = {}
        for cq in violated:
            failed_payload.append({
                "cq_id": cq["cq_id"],
                "question": cq["question"],
                "query_type": cq["query_type"],
                "subject_label": cq["subject_label"],
                "predicate_label": cq["predicate_label"],
                "expected_answer": cq.get("expected_answer", ""),
                "expected_count": cq.get("expected_count", ""),
                "actual_values": cq_actual_values(graph, cq),
                "rationale": cq.get("rationale", ""),
            })
            for evidence in evidence_for_cq(evidence_rows, cq):
                relevant_evidence[evidence["record_id"]] = {
                    "record_id": evidence["record_id"],
                    "subject_label": evidence["subject_label"],
                    "predicate_label": evidence["predicate_label"],
                    "object_or_value": evidence["object_or_value"],
                    "value_datatype": evidence.get("value_datatype", ""),
                    "evidence_text": evidence.get("evidence_text", ""),
                    "authority_level": evidence.get("authority_level", ""),
                    "review_status": evidence.get("review_status", ""),
                    "effective_from": evidence.get("effective_from", ""),
                    "effective_to": evidence.get("effective_to", ""),
                    "document_status": evidence.get("document_status", ""),
                }
        errors.append({
            "error_id": error_id,
            "error_type": row["error_type"],
            "source_mutant": str(mutant_path.resolve()),
            "failed_cqs": failed_payload,
            "evidence_context": list(relevant_evidence.values()),
            "candidates": candidates,
        })
        operators = ", ".join(candidate["operator"] for candidate in candidates)
        line = (
            f"[{error_id}] 失败CQ={[cq['cq_id'] for cq in violated]} | "
            f"候选数={len(candidates)} | {operators}"
        )
        print(line)
        logs.append(line)

    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "enumerate_feasible_candidates.py",
        "evidence_path": str(evidence_path.resolve()),
        "candidate_semantics": "feasible_not_necessarily_correct",
        "errors": errors,
    }
    for path in (args.output_json, args.output_csv, args.log):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_csv(args.output_csv, errors)
    logs.extend([
        "",
        f"错误类型数：{len(errors)}",
        f"候选总数：{sum(len(error['candidates']) for error in errors)}",
        f"JSON：{args.output_json}",
        f"CSV：{args.output_csv}",
    ])
    args.log.write_text("\n".join(logs) + "\n", encoding="utf-8")
    print(f"\nJSON：{args.output_json}")
    print(f"CSV：{args.output_csv}")
    print(f"日志：{args.log}")
    print("[完成] 候选均可执行，但尚未经过Reasoner、证据和CQ语义验证。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已中断] 用户终止候选枚举。")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[候选枚举停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
