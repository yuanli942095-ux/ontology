from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from rdflib import Graph, Literal, RDF, URIRef
from rdflib.namespace import OWL, RDFS

from repair_operators import apply_operations, operation_key, term_to_spec


PROJECT_DIR = Path(__file__).resolve().parents[1]
CQ_FILE = PROJECT_DIR / "benchmark" / "cq" / "cq-tests.csv"
MANIFEST = PROJECT_DIR / "benchmark" / "ground-truth" / "error-manifest.csv"
MUTANTS_DIR = PROJECT_DIR / "benchmark" / "mutants"
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
CANDIDATE_DIR = REPAIR_DIR / "candidates"
CANDIDATE_MANIFEST = REPAIR_DIR / "repair-candidates.csv"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/insurance-benchmark/repair"


def local_name(value: object) -> str:
    text = unquote(str(value))
    if "#" in text:
        return text.rsplit("#", 1)[1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


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


def values(graph: Graph, subject: URIRef, predicate: URIRef) -> set[URIRef | Literal]:
    return {
        obj for obj in graph.objects(subject, predicate)
        if isinstance(obj, (URIRef, Literal))
    }


def split_values(text: str) -> set[str]:
    return {item.strip() for item in text.split("|") if item.strip()}


def infer_literal(graph: Graph, subject: URIRef, predicate: URIRef, lexical: str) -> Literal:
    existing = [item for item in values(graph, subject, predicate) if isinstance(item, Literal)]
    if existing:
        datatypes = {item.datatype for item in existing}
        languages = {item.language for item in existing}
        if len(datatypes) == 1 and len(languages) == 1:
            return Literal(lexical, datatype=next(iter(datatypes)), lang=next(iter(languages)))

    ranges = [item for item in graph.objects(predicate, RDFS.range) if isinstance(item, URIRef)]
    if len(ranges) == 1:
        return Literal(lexical, datatype=ranges[0])
    raise RuntimeError(
        f"无法为{local_name(predicate)}的期望值{lexical}确定datatype；"
        "为防止生成错误字面量，候选生成已停止。"
    )


def expected_terms(
    graph: Graph, subject: URIRef, predicate: URIRef, expected_labels: set[str]
) -> set[URIRef | Literal]:
    is_data_property = (predicate, RDF.type, OWL.DatatypeProperty) in graph
    is_object_property = (predicate, RDF.type, OWL.ObjectProperty) in graph
    actual = values(graph, subject, predicate)
    if not is_data_property and not is_object_property:
        is_data_property = any(isinstance(item, Literal) for item in actual)
        is_object_property = any(isinstance(item, URIRef) for item in actual)
    if is_data_property == is_object_property:
        raise RuntimeError(f"无法唯一判断属性类型：{predicate}")
    if is_data_property:
        return {infer_literal(graph, subject, predicate, label) for label in expected_labels}
    return {find_unique_iri(graph, label) for label in expected_labels}


def make_property_operations(
    graph: Graph, cq: dict[str, str]
) -> list[dict[str, Any]]:
    subject = find_unique_iri(graph, cq["subject_label"])
    predicate = find_unique_iri(graph, cq["predicate_label"])
    actual = values(graph, subject, predicate)
    expected = expected_terms(graph, subject, predicate, split_values(cq["expected_answer"]))
    unexpected = sorted(actual - expected, key=str)
    missing = sorted(expected - actual, key=str)
    common = {
        "subject_iri": str(subject),
        "predicate_iri": str(predicate),
        "source_cq_ids": [cq["cq_id"]],
        "rationale": cq["rationale"],
    }

    if len(unexpected) == 1 and len(missing) == 1:
        return [{
            **common,
            "operator": "REPLACE_PROPERTY_VALUE",
            "old_value": term_to_spec(unexpected[0]),
            "new_value": term_to_spec(missing[0]),
        }]

    operations: list[dict[str, Any]] = []
    for item in unexpected:
        operations.append({
            **common,
            "operator": "REMOVE_PROPERTY_VALUE",
            "old_value": term_to_spec(item),
        })
    for item in missing:
        operations.append({
            **common,
            "operator": "ADD_PROPERTY_VALUE",
            "new_value": term_to_spec(item),
        })
    return operations


def make_class_operation(graph: Graph, cq: dict[str, str]) -> list[dict[str, Any]]:
    subject = find_unique_iri(graph, cq["subject_label"])
    target_class = find_unique_iri(graph, cq["expected_answer"])
    triple_exists = (subject, RDF.type, target_class) in graph
    if cq["query_type"] == "CLASS_ABSENT" and triple_exists:
        operator = "REMOVE_CLASS_ASSERTION"
    elif cq["query_type"] == "CLASS_PRESENT" and not triple_exists:
        operator = "ADD_CLASS_ASSERTION"
    else:
        return []
    return [{
        "operator": operator,
        "subject_iri": str(subject),
        "class_iri": str(target_class),
        "source_cq_ids": [cq["cq_id"]],
        "rationale": cq["rationale"],
    }]


def cq_fails(graph: Graph, cq: dict[str, str]) -> bool:
    query_type = cq["query_type"]
    subject = find_unique_iri(graph, cq["subject_label"])
    if query_type in {"CLASS_PRESENT", "CLASS_ABSENT"}:
        target = find_unique_iri(graph, cq["expected_answer"])
        present = (subject, RDF.type, target) in graph
        return not present if query_type == "CLASS_PRESENT" else present
    predicate = find_unique_iri(graph, cq["predicate_label"])
    actual = values(graph, subject, predicate)
    if query_type == "PROPERTY_EXACT_SET":
        expected = expected_terms(graph, subject, predicate, split_values(cq["expected_answer"]))
        return actual != expected
    if query_type == "PROPERTY_CARDINALITY":
        return len(actual) != int(cq["expected_count"])
    raise RuntimeError(f"不支持的query_type：{query_type}")


def merge_operation(operations: dict[str, dict[str, Any]], operation: dict[str, Any]) -> None:
    key = operation_key(operation)
    if key not in operations:
        operations[key] = operation
        return
    existing_ids = set(operations[key].get("source_cq_ids", []))
    existing_ids.update(operation.get("source_cq_ids", []))
    operations[key]["source_cq_ids"] = sorted(existing_ids)


def set_ontology_identity(graph: Graph, error_id: str, candidate_id: str) -> str:
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(f"{ONTOLOGY_BASE}/{error_id}/{candidate_id}")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    return str(ontology_iri)


def main() -> int:
    for path in (CQ_FILE, MANIFEST):
        if not path.is_file():
            print(f"缺少文件：{path}")
            return 2
    cqs = load_csv(CQ_FILE)
    mutants = load_csv(MANIFEST)
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, str]] = []

    print("有限算子修复候选生成")
    print("约束：只允许使用本体中的真实IRI；不允许直接生成任意OWL文本。\n")

    for mutant in mutants:
        error_id = mutant["error_id"]
        mutant_path = MUTANTS_DIR / mutant["file_name"]
        if not mutant_path.is_file():
            print(f"缺少错误本体：{mutant_path}")
            return 2
        graph = load_graph(mutant_path)
        violated = [cq for cq in cqs if cq_fails(graph, cq)]
        operations_by_key: dict[str, dict[str, Any]] = {}
        unresolved: list[str] = []

        for cq in violated:
            query_type = cq["query_type"]
            if query_type in {"CLASS_PRESENT", "CLASS_ABSENT"}:
                generated = make_class_operation(graph, cq)
            elif query_type == "PROPERTY_EXACT_SET":
                generated = make_property_operations(graph, cq)
            else:
                # Cardinality只指出数量错误；保留哪个值必须由同字段的精确CQ决定。
                generated = []
                unresolved.append(f"{cq['cq_id']}仅提供基数约束，不能单独决定保留值")
            for operation in generated:
                merge_operation(operations_by_key, operation)

        operations = list(operations_by_key.values())
        if not operations:
            print(f"[{error_id}] 没有生成安全候选，未解决原因：{unresolved}")
            return 1

        candidate_id = "candidate-001"
        repaired = apply_operations(graph, operations)
        ontology_iri = set_ontology_identity(repaired, error_id, candidate_id)
        error_dir = CANDIDATE_DIR / error_id
        error_dir.mkdir(parents=True, exist_ok=True)
        owl_path = error_dir / f"{candidate_id}.owl"
        json_path = error_dir / f"{candidate_id}.json"
        repaired.serialize(destination=owl_path, format="xml", encoding="utf-8")

        payload = {
            "candidate_id": candidate_id,
            "error_id": error_id,
            "source_mutant": str(mutant_path),
            "candidate_owl": str(owl_path),
            "ontology_iri": ontology_iri,
            "generator": "finite_operator_cq_baseline",
            "violated_cq_ids": [cq["cq_id"] for cq in violated],
            "unresolved_notes": unresolved,
            "operations": operations,
        }
        with json_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

        manifest_rows.append({
            "candidate_id": candidate_id,
            "error_id": error_id,
            "error_type": mutant["error_type"],
            "source_mutant": str(mutant_path),
            "candidate_owl": str(owl_path),
            "candidate_json": str(json_path),
            "operation_count": str(len(operations)),
            "operators": " | ".join(operation["operator"] for operation in operations),
            "source_cq_ids": " | ".join(sorted({cq_id for op in operations for cq_id in op["source_cq_ids"]})),
            "ontology_iri": ontology_iri,
        })
        print(
            f"[{error_id}] 失败CQ={[cq['cq_id'] for cq in violated]} | "
            f"操作数={len(operations)} | {manifest_rows[-1]['operators']}"
        )

    with CANDIDATE_MANIFEST.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"\n候选清单：{CANDIDATE_MANIFEST}")
    print("[候选生成完成] E1-E5均已生成受限修复候选。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[候选生成停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
