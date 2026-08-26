from __future__ import annotations

import csv
import sys
from pathlib import Path
from urllib.parse import unquote

try:
    from rdflib import Graph, Literal, RDF, URIRef
    from rdflib.namespace import OWL, RDFS, XSD
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
BENCHMARK_DIR = PROJECT_DIR / "benchmark"
CLEAN_DIR = BENCHMARK_DIR / "clean"
MUTANTS_DIR = BENCHMARK_DIR / "mutants"
GROUND_TRUTH_DIR = BENCHMARK_DIR / "ground-truth"

SOURCE_CANDIDATES = [
    CLEAN_DIR / "insurance-clean.owl",
    DATA_DIR / "insurance-v2.owl",
]
VALIDATION_BASELINE = CLEAN_DIR / "insurance-validation-baseline.owl"
MANIFEST_CSV = GROUND_TRUTH_DIR / "error-manifest.csv"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/insurance-benchmark"


def local_name(value: object) -> str:
    text = unquote(str(value))
    if "#" in text:
        return text.rsplit("#", 1)[1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def choose_source() -> Path:
    for path in SOURCE_CANDIDATES:
        if path.is_file():
            return path
    print("没有找到正确本体。请确认以下任意一个文件存在：")
    for path in SOURCE_CANDIDATES:
        print(f"  - {path}")
    raise SystemExit(2)


def clone_graph(source: Graph) -> Graph:
    target = Graph(identifier=source.identifier)
    for prefix, namespace in source.namespaces():
        target.bind(prefix, namespace)
    for triple in source:
        target.add(triple)
    return target


def set_ontology_identity(graph: Graph, experiment_id: str) -> str:
    """为每个实验本体设置唯一Ontology IRI，避免Protégé/Reasoner缓存串扰。"""
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        # 旧Ontology节点上的版本号、导入声明和注释都属于旧文档身份。
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)

    ontology_iri = URIRef(f"{ONTOLOGY_BASE}/{experiment_id}")
    version_iri = URIRef(f"{ONTOLOGY_BASE}/{experiment_id}/1.0.0")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, version_iri))
    return str(ontology_iri)


def all_uris(graph: Graph) -> set[URIRef]:
    result: set[URIRef] = set()
    for subject, predicate, obj in graph:
        if isinstance(subject, URIRef):
            result.add(subject)
        if isinstance(predicate, URIRef):
            result.add(predicate)
        if isinstance(obj, URIRef):
            result.add(obj)
    return result


def find_entity(graph: Graph, label: str, expected_type: URIRef | None = None) -> URIRef:
    candidates = {uri for uri in all_uris(graph) if local_name(uri) == label}
    if expected_type is not None:
        typed = {uri for uri in candidates if (uri, RDF.type, expected_type) in graph}
        if typed:
            candidates = typed

    if not candidates:
        print(f"本体中没有找到实体：{label}")
        raise SystemExit(2)
    if len(candidates) > 1:
        print(f"实体局部名不唯一：{label}")
        for item in sorted(map(str, candidates)):
            print(f"  - {item}")
        raise SystemExit(2)
    return next(iter(candidates))


def require_triple(graph: Graph, subject: URIRef, predicate: URIRef, obj: object) -> None:
    if (subject, predicate, obj) not in graph:
        print("正确本体中缺少预期断言，不能可靠注入错误：")
        print(f"  {local_name(subject)} --{local_name(predicate)}--> {local_name(obj)}")
        raise SystemExit(2)


def literal_values(graph: Graph, subject: URIRef, predicate: URIRef) -> list[Literal]:
    return [obj for obj in graph.objects(subject, predicate) if isinstance(obj, Literal)]


def require_literal_value(
    graph: Graph, subject: URIRef, predicate: URIRef, lexical_value: str
) -> Literal:
    matches = [value for value in literal_values(graph, subject, predicate) if str(value) == lexical_value]
    if not matches:
        actual = ", ".join(str(value) for value in literal_values(graph, subject, predicate)) or "无"
        print(
            f"正确本体中的{local_name(predicate)}不是预期值{lexical_value}，"
            f"当前值：{actual}"
        )
        raise SystemExit(2)
    return matches[0]


def save_graph(graph: Graph, path: Path) -> None:
    graph.serialize(destination=path, format="xml", encoding="utf-8")
    if not path.is_file() or path.stat().st_size == 0:
        print(f"生成失败：{path}")
        raise SystemExit(2)


def verify_saved_identity(path: Path, expected_iri: str) -> None:
    """重新读取刚写出的OWL，确保磁盘文件中的Ontology IRI确实已经更新。"""
    saved = Graph()
    saved.parse(path, format="xml")
    actual = sorted(str(item) for item in saved.subjects(RDF.type, OWL.Ontology))
    if actual != [expected_iri]:
        print(f"生成后的Ontology IRI校验失败：{path}")
        print(f"  期望：{expected_iri}")
        print(f"  实际：{actual}")
        raise SystemExit(2)


def add_validation_constraints(graph: Graph, entities: dict[str, URIRef]) -> None:
    # 这些是验证约束，不是错误。它们让Reasoner能够识别E3、E4和E5。
    graph.add((entities["保险公司"], OWL.disjointWith, entities["疾病"]))
    graph.add((entities["人身保险"], OWL.disjointWith, entities["财产保险"]))

    for property_name in [
        "最低投保年龄",
        "最高投保年龄",
        "等待期天数",
        "保险期限月数",
        "保额",
    ]:
        graph.add((entities[property_name], RDF.type, OWL.FunctionalProperty))


def normalize_baseline_data_values(
    graph: Graph, product: URIRef, entities: dict[str, URIRef]
) -> None:
    """依据已经人工核验的v2证据，消除同词不同datatype的影子字面量。"""
    expectations = [
        ("最低投保年龄", "18", XSD.integer),
        ("最高投保年龄", "65", XSD.integer),
        ("等待期天数", "60", XSD.integer),
        ("保险期限月数", "12", XSD.integer),
        ("保额", "500000", XSD.decimal),
    ]

    for property_name, lexical_value, datatype in expectations:
        predicate = entities[property_name]
        existing = literal_values(graph, product, predicate)
        if not any(str(value) == lexical_value for value in existing):
            actual = ", ".join(value.n3() for value in existing) or "无"
            print(
                f"正确本体中的{property_name}没有证据要求的值{lexical_value}，"
                f"当前值：{actual}"
            )
            raise SystemExit(2)

        expected = Literal(lexical_value, datatype=datatype)
        needs_cleanup = len(existing) != 1 or existing[0] != expected
        if needs_cleanup:
            before = " | ".join(value.n3() for value in existing)
            for value in existing:
                graph.remove((product, predicate, value))
            graph.add((product, predicate, expected))
            print(
                f"[基线规范化] {property_name}：{before} -> {expected.n3()}"
            )


def remove_accidental_data_property_hierarchy(
    graph: Graph, entities: dict[str, URIRef]
) -> None:
    """删除五个独立业务字段之间误建的SubPropertyOf关系。"""
    property_names = [
        "最低投保年龄",
        "最高投保年龄",
        "等待期天数",
        "保险期限月数",
        "保额",
    ]
    data_properties = {entities[name] for name in property_names}
    removed: list[tuple[URIRef, URIRef]] = []

    for child, parent in list(graph.subject_objects(RDFS.subPropertyOf)):
        if child in data_properties and parent in data_properties:
            graph.remove((child, RDFS.subPropertyOf, parent))
            removed.append((child, parent))

    for child, parent in removed:
        print(
            f"[基线修复] 删除错误属性层级："
            f"{local_name(child)} SubPropertyOf {local_name(parent)}"
        )

    remaining = [
        (child, parent)
        for child, parent in graph.subject_objects(RDFS.subPropertyOf)
        if child in data_properties and parent in data_properties
    ]
    if remaining:
        print("数据属性层级修复未完成，停止生成实验文件。")
        raise SystemExit(2)


def main() -> int:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    MUTANTS_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)

    source_path = choose_source()
    clean = Graph()
    try:
        clean.parse(source_path, format="xml")
    except Exception as exc:
        print(f"无法读取正确本体：{source_path}")
        print(f"原因：{exc}")
        return 2

    print(f"[已读取] {source_path}：{len(clean)} 个RDF三元组")

    entities = {
        "产品A": find_entity(clean, "产品A", OWL.NamedIndividual),
        "保险公司甲": find_entity(clean, "保险公司甲", OWL.NamedIndividual),
        "癌症": find_entity(clean, "癌症", OWL.NamedIndividual),
        "保险公司": find_entity(clean, "保险公司", OWL.Class),
        "疾病": find_entity(clean, "疾病", OWL.Class),
        "人身保险": find_entity(clean, "人身保险", OWL.Class),
        "财产保险": find_entity(clean, "财产保险", OWL.Class),
        "由公司承保": find_entity(clean, "由公司承保", OWL.ObjectProperty),
        "最低投保年龄": find_entity(clean, "最低投保年龄", OWL.DatatypeProperty),
        "最高投保年龄": find_entity(clean, "最高投保年龄", OWL.DatatypeProperty),
        "等待期天数": find_entity(clean, "等待期天数", OWL.DatatypeProperty),
        "保险期限月数": find_entity(clean, "保险期限月数", OWL.DatatypeProperty),
        "保额": find_entity(clean, "保额", OWL.DatatypeProperty),
    }

    product = entities["产品A"]
    max_age = entities["最高投保年龄"]
    waiting_days = entities["等待期天数"]
    underwritten_by = entities["由公司承保"]
    company = entities["保险公司甲"]
    cancer = entities["癌症"]
    property_insurance = entities["财产保险"]

    # 这五个字段语义独立，不应互为子属性。原Protégé文件中的错误层级会
    # 把一个字段的值推导给另一个字段，并在功能属性约束下造成基线不一致。
    remove_accidental_data_property_hierarchy(clean, entities)

    # 原Protégé文件可能同时保留注释值和数据属性断言；二者词面相同但
    # datatype不同。先依据人工核验的v2证据规范化，再建立功能属性约束。
    normalize_baseline_data_values(clean, product, entities)

    max_age_65 = require_literal_value(clean, product, max_age, "65")
    waiting_60 = require_literal_value(clean, product, waiting_days, "60")
    require_triple(clean, product, underwritten_by, company)

    validation_baseline = clone_graph(clean)
    add_validation_constraints(validation_baseline, entities)
    baseline_iri = set_ontology_identity(validation_baseline, "baseline")
    save_graph(validation_baseline, VALIDATION_BASELINE)
    verify_saved_identity(VALIDATION_BASELINE, baseline_iri)
    print(
        f"[已生成] {VALIDATION_BASELINE.name}（包含验证约束，不含错误）\n"
        f"          Ontology IRI：{baseline_iri}"
    )

    manifest: list[dict[str, str]] = []

    # E1：把最高投保年龄65错误替换为70。逻辑上仍可满足，因此Reasoner不会报错。
    e1 = clone_graph(validation_baseline)
    e1.remove((product, max_age, max_age_65))
    e1.add((product, max_age, Literal(70, datatype=XSD.integer)))
    e1_iri = set_ontology_identity(e1, "E1-numeric-drift")
    e1_path = MUTANTS_DIR / "E1_numeric_drift.owl"
    save_graph(e1, e1_path)
    verify_saved_identity(e1_path, e1_iri)
    manifest.append({
        "error_id": "E1",
        "file_name": e1_path.name,
        "error_type": "数值漂移",
        "affected_subject": "产品A",
        "affected_predicate": "最高投保年龄",
        "old_value": "65",
        "new_value": "70",
        "injected_change": "将文档支持的65替换为70",
        "expected_reasoner_result": "CONSISTENT",
        "expected_evidence_result": "FAIL",
        "expected_shacl_result": "DEPENDS_ON_SHAPE",
        "expected_cq_result": "FAIL",
        "primary_detector": "证据校验/CQ",
        "ontology_iri": e1_iri,
    })

    # E2：删除等待期。开放世界下“没有写”不等于“不存在”，Reasoner通常仍判一致。
    e2 = clone_graph(validation_baseline)
    e2.remove((product, waiting_days, waiting_60))
    e2_iri = set_ontology_identity(e2, "E2-missing-axiom")
    e2_path = MUTANTS_DIR / "E2_missing_axiom.owl"
    save_graph(e2, e2_path)
    verify_saved_identity(e2_path, e2_iri)
    manifest.append({
        "error_id": "E2",
        "file_name": e2_path.name,
        "error_type": "公理缺失",
        "affected_subject": "产品A",
        "affected_predicate": "等待期天数",
        "old_value": "60",
        "new_value": "",
        "injected_change": "删除等待期天数断言",
        "expected_reasoner_result": "CONSISTENT",
        "expected_evidence_result": "FAIL",
        "expected_shacl_result": "FAIL",
        "expected_cq_result": "FAIL",
        "primary_detector": "证据校验/SHACL/CQ",
        "ontology_iri": e2_iri,
    })

    # E3：把承保对象从保险公司甲改成癌症。范围公理会推导癌症属于保险公司，
    # 又因为保险公司与疾病互斥，所以Reasoner应判不一致。
    e3 = clone_graph(validation_baseline)
    e3.remove((product, underwritten_by, company))
    e3.add((product, underwritten_by, cancer))
    e3_iri = set_ontology_identity(e3, "E3-wrong-relation-target")
    e3_path = MUTANTS_DIR / "E3_wrong_relation_target.owl"
    save_graph(e3, e3_path)
    verify_saved_identity(e3_path, e3_iri)
    manifest.append({
        "error_id": "E3",
        "file_name": e3_path.name,
        "error_type": "关系对象错误",
        "affected_subject": "产品A",
        "affected_predicate": "由公司承保",
        "old_value": "保险公司甲",
        "new_value": "癌症",
        "injected_change": "承保对象错误指向癌症",
        "expected_reasoner_result": "INCONSISTENT",
        "expected_evidence_result": "FAIL",
        "expected_shacl_result": "FAIL",
        "expected_cq_result": "FAIL",
        "primary_detector": "Reasoner/SHACL",
        "ontology_iri": e3_iri,
    })

    # E4：产品A被额外断言为财产保险。产品A原属医疗保险→人身保险，
    # 人身保险与财产保险互斥，因此Reasoner应判不一致。
    e4 = clone_graph(validation_baseline)
    e4.add((product, RDF.type, property_insurance))
    e4_iri = set_ontology_identity(e4, "E4-disjoint-class-conflict")
    e4_path = MUTANTS_DIR / "E4_disjoint_class_conflict.owl"
    save_graph(e4, e4_path)
    verify_saved_identity(e4_path, e4_iri)
    manifest.append({
        "error_id": "E4",
        "file_name": e4_path.name,
        "error_type": "互斥类别冲突",
        "affected_subject": "产品A",
        "affected_predicate": "rdf:type",
        "old_value": "医疗保险",
        "new_value": "额外增加财产保险",
        "injected_change": "产品A同时落入人身保险体系与财产保险",
        "expected_reasoner_result": "INCONSISTENT",
        "expected_evidence_result": "FAIL",
        "expected_shacl_result": "DEPENDS_ON_SHAPE",
        "expected_cq_result": "FAIL",
        "primary_detector": "Reasoner",
        "ontology_iri": e4_iri,
    })

    # E5：在保留65的同时再增加70。最高投保年龄已声明为功能属性，
    # 两个不同整数值违反最多一个值的约束，因此Reasoner应判不一致。
    e5 = clone_graph(validation_baseline)
    e5.add((product, max_age, Literal(70, datatype=XSD.integer)))
    e5_iri = set_ontology_identity(e5, "E5-conflicting-functional-value")
    e5_path = MUTANTS_DIR / "E5_conflicting_functional_value.owl"
    save_graph(e5, e5_path)
    verify_saved_identity(e5_path, e5_iri)
    manifest.append({
        "error_id": "E5",
        "file_name": e5_path.name,
        "error_type": "功能属性值冲突",
        "affected_subject": "产品A",
        "affected_predicate": "最高投保年龄",
        "old_value": "65",
        "new_value": "同时存在65和70",
        "injected_change": "为功能数据属性增加第二个冲突值",
        "expected_reasoner_result": "INCONSISTENT",
        "expected_evidence_result": "FAIL",
        "expected_shacl_result": "FAIL",
        "expected_cq_result": "FAIL",
        "primary_detector": "Reasoner/SHACL",
        "ontology_iri": e5_iri,
    })

    fieldnames = [
        "error_id",
        "file_name",
        "error_type",
        "affected_subject",
        "affected_predicate",
        "old_value",
        "new_value",
        "injected_change",
        "expected_reasoner_result",
        "expected_evidence_result",
        "expected_shacl_result",
        "expected_cq_result",
        "primary_detector",
        "ontology_iri",
    ]
    with MANIFEST_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(manifest)

    print("\n已生成5个错误本体：")
    for row in manifest:
        print(
            f"  [{row['error_id']}] {row['file_name']} | "
            f"{row['error_type']} | Reasoner预期：{row['expected_reasoner_result']}"
        )
    print(f"\n标准答案表：{MANIFEST_CSV}")
    print("[完成] 原始insurance-v2.owl没有被修改。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
