from __future__ import annotations

r"""枚举并反事实验证 E6-E8 的 TBox 修复候选。

默认运行：
    python .\src\enumerate_tbox_candidates.py --timeout 120

前置步骤：
    python .\src\inject_cq_specific_errors.py

本脚本的三个干扰候选均使用本体中的真实 IRI。设计目标是让 Reasoner 和
ABox 证据无法区分正确候选与语义干扰候选，而由 TBox CQ 给出增量判别。
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
    from rdflib import Graph, RDF, RDFS, URIRef
    from rdflib.compare import isomorphic, to_canonical_graph
    from rdflib.namespace import OWL
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)

try:
    import validate_benchmark as benchmark_validator
    from repair_operators import (
        apply_operations,
        expected_graph_delta,
        operation_key,
        validate_operation_schema,
    )
except ImportError as exc:
    print(
        "缺少项目模块。请把本文件、扩展版repair_operators.py放入"
        "ontology-evolution\\src，并保留现有validate_benchmark.py。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
BASELINE = PROJECT_DIR / "benchmark" / "clean" / "insurance-validation-baseline.owl"
MUTANTS_DIR = PROJECT_DIR / "benchmark" / "mutants"
MANIFEST = PROJECT_DIR / "benchmark" / "ground-truth" / "cq-specific-error-manifest.csv"
CQ_FILE = PROJECT_DIR / "benchmark" / "cq" / "cq-tbox-tests.csv"
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
EFFECT_ROOT = REPAIR_DIR / "tbox-candidate-effects"
OUTPUT_JSON = REPAIR_DIR / "tbox-candidate-effects.json"
OUTPUT_CSV = REPAIR_DIR / "tbox-candidate-effects.csv"
LOG_FILE = PROJECT_DIR / "output" / "tbox-candidate-enumeration.log"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/insurance-benchmark/tbox-candidate"


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
    for triple in graph:
        for value in triple:
            if isinstance(value, URIRef):
                result.add(value)
    return result


def find_unique_iri(graph: Graph, label: str) -> URIRef:
    matches = sorted(
        (value for value in all_uris(graph) if local_name(value) == label),
        key=str,
    )
    if len(matches) != 1:
        raise RuntimeError(f"实体标签{label!r}匹配到{len(matches)}个IRI：{matches}")
    return matches[0]


def strip_ontology_metadata(source: Graph) -> Graph:
    result = Graph()
    for prefix, namespace in source.namespaces():
        result.bind(prefix, namespace)
    ontology_subjects = set(source.subjects(RDF.type, OWL.Ontology))
    for subject, predicate, obj in source:
        if subject in ontology_subjects or predicate == OWL.versionIRI:
            continue
        result.add((subject, predicate, obj))
    return result


def graph_delta(source: Graph, target: Graph) -> tuple[int, int]:
    source_set = set(to_canonical_graph(strip_ontology_metadata(source)))
    target_set = set(to_canonical_graph(strip_ontology_metadata(target)))
    return len(source_set - target_set), len(target_set - source_set)


def set_ontology_identity(graph: Graph, error_id: str, candidate_id: str) -> str:
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(f"{ONTOLOGY_BASE}/{error_id}/{candidate_id}")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    return str(ontology_iri)


def superclass_closure(graph: Graph, initial: set[URIRef]) -> set[URIRef]:
    closure = set(initial)
    agenda = list(initial)
    while agenda:
        current = agenda.pop()
        for parent in graph.objects(current, RDFS.subClassOf):
            if isinstance(parent, URIRef) and parent not in closure:
                closure.add(parent)
                agenda.append(parent)
    return closure


def entailed_types(graph: Graph, individual_label: str) -> set[str]:
    individuals = [
        value for value in all_uris(graph) if local_name(value) == individual_label
    ]
    types: set[URIRef] = set()
    for individual in individuals:
        explicit = {
            value
            for value in graph.objects(individual, RDF.type)
            if isinstance(value, URIRef)
        }
        types |= superclass_closure(graph, explicit)
    return {local_name(value) for value in types}


def property_ranges(graph: Graph, predicate_label: str) -> set[str]:
    predicates = [
        value for value in all_uris(graph) if local_name(value) == predicate_label
    ]
    return {
        local_name(value)
        for predicate in predicates
        for value in graph.objects(predicate, RDFS.range)
        if isinstance(value, URIRef)
    }


def property_domains(graph: Graph, predicate_label: str) -> set[str]:
    predicates = [
        value for value in all_uris(graph) if local_name(value) == predicate_label
    ]
    return {
        local_name(value)
        for predicate in predicates
        for value in graph.objects(predicate, RDFS.domain)
        if isinstance(value, URIRef)
    }


def subclass_targets(graph: Graph, class_label: str) -> set[str]:
    classes = [value for value in all_uris(graph) if local_name(value) == class_label]
    closure = superclass_closure(graph, set(classes))
    return {local_name(value) for value in closure}


def evaluate_cq(graph: Graph, cq: dict[str, str]) -> tuple[bool, set[str], str]:
    query_type = cq["query_type"].strip()
    expected = cq["expected_answer"].strip()
    exact_set = False
    if query_type == "INSTANCE_TYPE_ENTAILED":
        actual = entailed_types(graph, cq["subject_label"].strip())
    elif query_type == "SUBCLASS_PATH_PRESENT":
        actual = subclass_targets(graph, cq["subject_label"].strip())
    elif query_type == "PROPERTY_RANGE_PRESENT":
        actual = property_ranges(graph, cq["predicate_label"].strip())
    elif query_type == "PROPERTY_RANGE_EXACT_SET":
        actual = property_ranges(graph, cq["predicate_label"].strip())
        exact_set = True
    elif query_type == "PROPERTY_DOMAIN_PRESENT":
        actual = property_domains(graph, cq["predicate_label"].strip())
    elif query_type == "PROPERTY_DOMAIN_EXACT_SET":
        actual = property_domains(graph, cq["predicate_label"].strip())
        exact_set = True
    else:
        raise RuntimeError(f"{cq['cq_id']}使用了未知TBox query_type：{query_type}")
    if exact_set:
        return actual == {expected}, actual, f"精确等于{{{expected}}}"
    return expected in actual, actual, f"包含{expected}"


def evaluate_all_cqs(
    graph: Graph, cqs: list[dict[str, str]]
) -> tuple[str, list[str], list[dict[str, Any]]]:
    failed: list[str] = []
    states: list[dict[str, Any]] = []
    for cq in cqs:
        passed, actual, expected = evaluate_cq(graph, cq)
        if not passed:
            failed.append(cq["cq_id"])
        states.append({
            "cq_id": cq["cq_id"],
            "question": cq["question"],
            "query_type": cq["query_type"],
            "expected": expected,
            "actual_values": sorted(actual),
            "passed": passed,
        })
    return ("PASS" if not failed else "FAIL"), failed, states


def candidate_definitions(graph: Graph, error_id: str) -> list[dict[str, Any]]:
    medical = str(find_unique_iri(graph, "医疗保险"))
    disease_insurance = str(find_unique_iri(graph, "疾病保险"))
    health = str(find_unique_iri(graph, "健康保险"))
    personal = str(find_unique_iri(graph, "人身保险"))
    insurance_product = str(find_unique_iri(graph, "保险产品"))
    coverage_disease = str(find_unique_iri(graph, "保障疾病"))
    disease = str(find_unique_iri(graph, "疾病"))
    major_disease = str(find_unique_iri(graph, "重大疾病"))
    owl_thing = str(OWL.Thing)

    definitions: dict[str, list[tuple[str, str, dict[str, Any]]]] = {
        "E6": [
            (
                "新增父类：医疗保险 → 健康保险",
                "ADD_SUBCLASS_AXIOM",
                {
                    "operator": "ADD_SUBCLASS_AXIOM",
                    "subclass_iri": medical,
                    "superclass_iri": health,
                },
            ),
            (
                "新增父类：医疗保险 → 人身保险",
                "ADD_SUBCLASS_AXIOM",
                {
                    "operator": "ADD_SUBCLASS_AXIOM",
                    "subclass_iri": medical,
                    "superclass_iri": personal,
                },
            ),
            (
                "新增父类：医疗保险 → 保险产品",
                "ADD_SUBCLASS_AXIOM",
                {
                    "operator": "ADD_SUBCLASS_AXIOM",
                    "subclass_iri": medical,
                    "superclass_iri": insurance_product,
                },
            ),
        ],
        "E7": [
            (
                "替换父类：疾病保险，人身保险 → 健康保险",
                "REPLACE_SUPERCLASS",
                {
                    "operator": "REPLACE_SUPERCLASS",
                    "subclass_iri": disease_insurance,
                    "old_superclass_iri": personal,
                    "new_superclass_iri": health,
                },
            ),
            (
                "删除父类：疾病保险 → 人身保险",
                "REMOVE_SUBCLASS_AXIOM",
                {
                    "operator": "REMOVE_SUBCLASS_AXIOM",
                    "subclass_iri": disease_insurance,
                    "superclass_iri": personal,
                },
            ),
            (
                "替换父类：疾病保险，人身保险 → 保险产品",
                "REPLACE_SUPERCLASS",
                {
                    "operator": "REPLACE_SUPERCLASS",
                    "subclass_iri": disease_insurance,
                    "old_superclass_iri": personal,
                    "new_superclass_iri": insurance_product,
                },
            ),
        ],
        "E8": [
            (
                "新增值域：保障疾病 → 疾病",
                "ADD_PROPERTY_RANGE",
                {
                    "operator": "ADD_PROPERTY_RANGE",
                    "property_iri": coverage_disease,
                    "range_iri": disease,
                },
            ),
            (
                "新增值域：保障疾病 → 重大疾病",
                "ADD_PROPERTY_RANGE",
                {
                    "operator": "ADD_PROPERTY_RANGE",
                    "property_iri": coverage_disease,
                    "range_iri": major_disease,
                },
            ),
            (
                "新增值域：保障疾病 → owl:Thing",
                "ADD_PROPERTY_RANGE",
                {
                    "operator": "ADD_PROPERTY_RANGE",
                    "property_iri": coverage_disease,
                    "range_iri": owl_thing,
                },
            ),
        ],
    }
    if error_id not in definitions:
        raise RuntimeError(f"没有为{error_id}定义TBox候选")

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, (description, operator, operation) in enumerate(
        definitions[error_id], start=1
    ):
        validate_operation_schema(operation)
        key = operation_key(operation)
        if key in seen:
            raise RuntimeError(f"{error_id}生成了重复候选：{description}")
        seen.add(key)
        result.append({
            "candidate_id": f"CAND_{position:03d}",
            "description": description,
            "operator": operator,
            "operation": operation,
            "source_cq_ids": [f"CQ{7 + int(error_id[1:]):03d}"],
            "source_evidence_ids": [],
            "rationales": ["由本体模式与能力问题生成；ABox证据故意不覆盖该TBox公理"],
            "feasible": True,
        })
    return result


def evaluate_candidate(
    source_graph: Graph,
    baseline_graph: Graph,
    candidate: dict[str, Any],
    error_id: str,
    cqs: list[dict[str, str]],
    evidence_rows: list[dict[str, str]],
    timeout: int,
) -> dict[str, Any]:
    candidate_id = str(candidate["candidate_id"])
    operation = deepcopy(candidate["operation"])
    owl_path = EFFECT_ROOT / error_id / f"{candidate_id}.owl"
    owl_path.parent.mkdir(parents=True, exist_ok=True)
    effect: dict[str, Any] = {
        "execution_pass": False,
        "reasoner_result": "NOT_RUN",
        "reasoner_gate": False,
        "evidence_result": "NOT_RUN",
        "evidence_gate": False,
        "cq_result": "NOT_RUN",
        "cq_gate": False,
        "minimal_edit_gate": False,
        "oracle_match": False,
        "hard_gate_pass": False,
        "candidate_owl": str(owl_path.resolve()),
        "error_message": "",
    }
    try:
        repaired = apply_operations(source_graph, [operation])
        ontology_iri = set_ontology_identity(repaired, error_id, candidate_id)
        repaired.serialize(destination=owl_path, format="xml", encoding="utf-8")

        reasoner = benchmark_validator.run_reasoner(owl_path, timeout)
        evidence_status, _, evidence_differences = (
            benchmark_validator.validate_evidence_and_cq(repaired, evidence_rows)
        )
        cq_status, failed_cqs, cq_states = evaluate_all_cqs(repaired, cqs)
        removed, added = graph_delta(source_graph, repaired)
        expected_removed, expected_added = expected_graph_delta(operation, source_graph)
        oracle_match = isomorphic(
            strip_ontology_metadata(repaired),
            strip_ontology_metadata(baseline_graph),
        )

        effect.update({
            "execution_pass": True,
            "reasoner_result": reasoner["status"],
            "reasoner_runtime_ms": reasoner.get("runtime_ms", 0),
            "reasoner_message": reasoner.get("message", ""),
            "reasoner_gate": reasoner["status"] == "CONSISTENT",
            "evidence_result": evidence_status,
            "evidence_gate": evidence_status == "PASS",
            "evidence_differences": evidence_differences,
            "cq_result": cq_status,
            "cq_gate": cq_status == "PASS",
            "all_cq_result": cq_status,
            "all_cq_gate": cq_status == "PASS",
            "failed_cqs": failed_cqs,
            "all_failed_cqs": failed_cqs,
            "cq_states": cq_states,
            "triples_removed": removed,
            "triples_added": added,
            "expected_triples_removed": expected_removed,
            "expected_triples_added": expected_added,
            "minimal_edit_gate": (removed, added) == (expected_removed, expected_added),
            "oracle_match": oracle_match,
            "ontology_iri": ontology_iri,
        })
        effect["hard_gate_pass"] = all(
            effect[key]
            for key in (
                "reasoner_gate",
                "evidence_gate",
                "cq_gate",
                "minimal_edit_gate",
            )
        )
    except Exception as exc:
        effect["error_message"] = f"{type(exc).__name__}: {exc}"
    return effect


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"没有数据可写入：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="枚举并验证E6-E8的TBox修复候选")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for path in (BASELINE, MANIFEST, CQ_FILE):
        if not path.is_file():
            raise FileNotFoundError(
                f"缺少文件：{path}。请先运行 inject_cq_specific_errors.py。"
            )
    if args.timeout < 1:
        raise ValueError("--timeout必须大于0")

    baseline_graph = load_graph(BASELINE)
    manifest_rows = load_csv(MANIFEST)
    cqs = load_csv(CQ_FILE)
    baseline_cq_status, baseline_failed, _ = evaluate_all_cqs(baseline_graph, cqs)
    if baseline_cq_status != "PASS":
        raise RuntimeError(f"干净基线未通过TBox CQ：{baseline_failed}")

    evidence_rows = benchmark_validator.current_evidence_rows(
        benchmark_validator.choose_evidence()
    )
    EFFECT_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, Any]] = []
    flat_rows: list[dict[str, Any]] = []
    logs = [
        "TBox CQ专属候选枚举与反事实验证日志",
        f"基线：{BASELINE}",
        f"CQ：{CQ_FILE}",
        "设计：干扰候选应通过Reasoner与ABox证据，但由CQ过滤。",
        "",
    ]

    total = len(manifest_rows) * 3
    current = 0
    print("TBox修复候选枚举与反事实验证")
    for manifest in manifest_rows:
        error_id = manifest["error_id"]
        source_path = MUTANTS_DIR / manifest["file_name"]
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        source_graph = load_graph(source_path)
        source_status, source_failed, source_states = evaluate_all_cqs(source_graph, cqs)
        expected_failed = {
            cq["cq_id"]
            for cq in cqs
            if error_id in {
                item.strip() for item in cq.get("expected_fail_on", "").split("|")
            }
        }
        if source_status != "FAIL" or set(source_failed) != expected_failed:
            raise RuntimeError(
                f"{error_id}的CQ失败映射错误：期望={sorted(expected_failed)}，"
                f"实际={source_failed}"
            )

        candidates = candidate_definitions(source_graph, error_id)
        error_record: dict[str, Any] = {
            "error_id": error_id,
            "error_type": manifest["error_type"],
            "source_mutant": str(source_path.resolve()),
            "failed_cqs": [
                state for state in source_states if not bool(state["passed"])
            ],
            "evidence_context": [],
            "candidates": [],
        }
        for candidate in candidates:
            current += 1
            effect = evaluate_candidate(
                source_graph,
                baseline_graph,
                candidate,
                error_id,
                cqs,
                evidence_rows,
                args.timeout,
            )
            candidate["effect"] = effect
            error_record["candidates"].append(candidate)
            state = "SURVIVE" if effect["hard_gate_pass"] else "FILTER_OUT"
            line = (
                f"[{current}/{total}] {error_id}/{candidate['candidate_id']} | "
                f"{candidate['operator']} | Reasoner={effect['reasoner_result']} | "
                f"证据={effect['evidence_result']} | CQ={effect['cq_result']} | "
                f"Oracle={effect['oracle_match']} | {state}"
            )
            print(line)
            logs.append(line)
            flat_rows.append({
                "error_id": error_id,
                "error_type": manifest["error_type"],
                "candidate_id": candidate["candidate_id"],
                "operator": candidate["operator"],
                "description": candidate["description"],
                "reasoner_result": effect["reasoner_result"],
                "reasoner_gate": effect["reasoner_gate"],
                "evidence_result": effect["evidence_result"],
                "evidence_gate": effect["evidence_gate"],
                "cq_result": effect["cq_result"],
                "cq_gate": effect["cq_gate"],
                "failed_cqs": " | ".join(effect.get("failed_cqs", [])),
                "minimal_edit_gate": effect["minimal_edit_gate"],
                "hard_gate_pass": effect["hard_gate_pass"],
                "oracle_match": effect["oracle_match"],
                "triples_removed": effect.get("triples_removed", ""),
                "triples_added": effect.get("triples_added", ""),
                "candidate_owl": effect["candidate_owl"],
                "error_message": effect["error_message"],
            })
        errors.append(error_record)

    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "enumerate_tbox_candidates.py",
        "source_manifest": str(MANIFEST.resolve()),
        "cq_file": str(CQ_FILE.resolve()),
        "evidence_path": str(benchmark_validator.choose_evidence().resolve()),
        "candidate_semantics": "tbox_feasible_counterfactually_annotated",
        "hard_gate_definition": [
            "reasoner_gate",
            "evidence_gate",
            "cq_gate",
            "minimal_edit_gate",
        ],
        "errors": errors,
    }
    OUTPUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_csv(OUTPUT_CSV, flat_rows)
    survivor_count = sum(bool(row["hard_gate_pass"]) for row in flat_rows)
    oracle_count = sum(bool(row["oracle_match"]) for row in flat_rows)
    summary = (
        f"候选总数={len(flat_rows)}，完整门禁通过={survivor_count}，"
        f"开发集Oracle匹配={oracle_count}"
    )
    logs.extend(["", summary, f"JSON：{OUTPUT_JSON}", f"CSV：{OUTPUT_CSV}"])
    LOG_FILE.write_text("\n".join(logs) + "\n", encoding="utf-8")

    print(f"\n{summary}")
    print(f"JSON：{OUTPUT_JSON}")
    print(f"CSV：{OUTPUT_CSV}")
    print(f"日志：{LOG_FILE}")
    print("[完成] 正确候选应由CQ唯一保留；下一步运行compare_no_llm_baselines.py。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[候选枚举停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
