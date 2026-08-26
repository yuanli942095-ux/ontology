from __future__ import annotations

r"""生成只应由 CQ 揭示的 TBox 错误 E6-E8。

默认运行：
    python .\src\inject_cq_specific_errors.py

输入：
    benchmark\clean\insurance-validation-baseline.owl

输出：
    benchmark\mutants\E6_missing_subclass_link.owl
    benchmark\mutants\E7_wrong_superclass.owl
    benchmark\mutants\E8_missing_property_range.owl
    benchmark\ground-truth\cq-specific-error-manifest.csv
    benchmark\cq\cq-tbox-tests.csv

脚本不会修改原有 E1-E5、主 error-manifest.csv 或 cq-tests.csv。
"""

import argparse
import csv
import sys
from pathlib import Path
from urllib.parse import unquote

try:
    from rdflib import Graph, RDF, RDFS, URIRef
    from rdflib.namespace import OWL
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)


PROJECT_DIR = Path(__file__).resolve().parents[1]
BASELINE = PROJECT_DIR / "benchmark" / "clean" / "insurance-validation-baseline.owl"
MUTANTS_DIR = PROJECT_DIR / "benchmark" / "mutants"
GROUND_TRUTH_DIR = PROJECT_DIR / "benchmark" / "ground-truth"
CQ_DIR = PROJECT_DIR / "benchmark" / "cq"
MANIFEST = GROUND_TRUTH_DIR / "cq-specific-error-manifest.csv"
CQ_FILE = CQ_DIR / "cq-tbox-tests.csv"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/insurance-benchmark"


def local_name(value: object) -> str:
    text = unquote(str(value))
    if "#" in text:
        return text.rsplit("#", 1)[1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="xml")
    return graph


def clone_graph(source: Graph) -> Graph:
    target = Graph(identifier=source.identifier)
    for prefix, namespace in source.namespaces():
        target.bind(prefix, namespace)
    for triple in source:
        target.add(triple)
    return target


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


def remove_existing(graph: Graph, triple: tuple[object, object, object], label: str) -> None:
    if triple not in graph:
        raise RuntimeError(f"基线缺少预期{label}：{triple}")
    graph.remove(triple)


def set_ontology_identity(graph: Graph, experiment_id: str) -> str:
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(f"{ONTOLOGY_BASE}/{experiment_id}")
    version_iri = URIRef(f"{ontology_iri}/1.0.0")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, version_iri))
    return str(ontology_iri)


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise RuntimeError(f"没有数据可写入：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成CQ专属TBox错误E6-E8")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline_path = args.baseline.resolve()
    if not baseline_path.is_file():
        raise FileNotFoundError(
            f"缺少基线本体：{baseline_path}。请先完成E1-E5基准生成。"
        )

    baseline = load_graph(baseline_path)
    medical = find_unique_iri(baseline, "医疗保险")
    disease_insurance = find_unique_iri(baseline, "疾病保险")
    health = find_unique_iri(baseline, "健康保险")
    personal = find_unique_iri(baseline, "人身保险")
    coverage_disease = find_unique_iri(baseline, "保障疾病")
    disease = find_unique_iri(baseline, "疾病")

    specifications = [
        {
            "error_id": "E6",
            "file_name": "E6_missing_subclass_link.owl",
            "error_type": "类层级链路缺失",
            "affected_subject": "医疗保险",
            "affected_predicate": "rdfs:subClassOf",
            "old_value": "健康保险",
            "new_value": "",
            "injected_change": "删除 医疗保险 SubClassOf 健康保险",
            "mutate": lambda graph: remove_existing(
                graph,
                (medical, RDFS.subClassOf, health),
                "医疗保险父类公理",
            ),
        },
        {
            "error_id": "E7",
            "file_name": "E7_wrong_superclass.owl",
            "error_type": "错误直接父类",
            "affected_subject": "疾病保险",
            "affected_predicate": "rdfs:subClassOf",
            "old_value": "健康保险",
            "new_value": "人身保险",
            "injected_change": "将 疾病保险 的直接父类由健康保险替换为人身保险",
            "mutate": lambda graph: (
                remove_existing(
                    graph,
                    (disease_insurance, RDFS.subClassOf, health),
                    "疾病保险父类公理",
                ),
                graph.add((disease_insurance, RDFS.subClassOf, personal)),
            ),
        },
        {
            "error_id": "E8",
            "file_name": "E8_missing_property_range.owl",
            "error_type": "对象属性值域缺失",
            "affected_subject": "保障疾病",
            "affected_predicate": "rdfs:range",
            "old_value": "疾病",
            "new_value": "",
            "injected_change": "删除 保障疾病 Range 疾病",
            "mutate": lambda graph: remove_existing(
                graph,
                (coverage_disease, RDFS.range, disease),
                "保障疾病值域公理",
            ),
        },
    ]

    MUTANTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, str]] = []
    print("CQ专属TBox错误注入")
    print(f"基线：{baseline_path}（{len(baseline)}个RDF三元组）\n")

    for spec in specifications:
        mutant = clone_graph(baseline)
        spec["mutate"](mutant)
        ontology_iri = set_ontology_identity(mutant, f"{spec['error_id']}-cq-specific")
        output_path = MUTANTS_DIR / str(spec["file_name"])
        mutant.serialize(destination=output_path, format="xml", encoding="utf-8")
        manifest_rows.append({
            "error_id": str(spec["error_id"]),
            "file_name": str(spec["file_name"]),
            "error_type": str(spec["error_type"]),
            "affected_subject": str(spec["affected_subject"]),
            "affected_predicate": str(spec["affected_predicate"]),
            "old_value": str(spec["old_value"]),
            "new_value": str(spec["new_value"]),
            "injected_change": str(spec["injected_change"]),
            "expected_reasoner_result": "CONSISTENT",
            "expected_evidence_result": "PASS",
            "expected_cq_result": "FAIL",
            "primary_detector": "CQ",
            "ontology_iri": ontology_iri,
        })
        print(
            f"[{spec['error_id']}] {output_path.name} | {spec['error_type']} | "
            "Reasoner预期=CONSISTENT | 证据预期=PASS | CQ预期=FAIL"
        )

    cq_rows = [
        {
            "cq_id": "CQ013",
            "question": "产品A的预期继承分类是否仍可完整推导？",
            "query_type": "INSTANCE_TYPE_ENTAILED",
            "subject_label": "产品A",
            "predicate_label": "rdf:type/rdfs:subClassOf*",
            "expected_answer": "健康保险",
            "expected_count": "",
            "hard_constraint": "true",
            "requirement_origin": "stakeholder_requirement",
            "rationale": "保护产品分类的继承语义，而不只检查直接rdf:type",
            "expected_fail_on": "E6",
        },
        {
            "cq_id": "CQ014",
            "question": "产品B的预期中间业务分类是否仍可推导？",
            "query_type": "INSTANCE_TYPE_ENTAILED",
            "subject_label": "产品B",
            "predicate_label": "rdf:type/rdfs:subClassOf*",
            "expected_answer": "健康保险",
            "expected_count": "",
            "hard_constraint": "true",
            "requirement_origin": "stakeholder_requirement",
            "rationale": "防止错误直接父类造成关键中间分类丢失",
            "expected_fail_on": "E7",
        },
        {
            "cq_id": "CQ015",
            "question": "保障疾病关系是否仍满足预期的直接值域约束？",
            "query_type": "PROPERTY_RANGE_EXACT_SET",
            "subject_label": "",
            "predicate_label": "保障疾病",
            "expected_answer": "疾病",
            "expected_count": "",
            "hard_constraint": "true",
            "requirement_origin": "ontology_schema_requirement",
            "rationale": "防止值域被无依据地收窄或放宽，保护数据质量约束",
            "expected_fail_on": "E8",
        },
    ]
    write_csv(MANIFEST, manifest_rows)
    write_csv(CQ_FILE, cq_rows)

    print(f"\n标准答案表：{MANIFEST}")
    print(f"TBox CQ：{CQ_FILE}")
    print("[完成] E6-E8均为逻辑一致、ABox证据不变、CQ应失败的专属测试。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[错误注入停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
