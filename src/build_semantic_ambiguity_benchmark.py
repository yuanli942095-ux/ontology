from __future__ import annotations

r"""构造需要文档语义消歧的 E9-E11 本体修复基准。

默认运行：
    python .\src\build_semantic_ambiguity_benchmark.py --timeout 120

三个事件分别考察：
E9  新旧版本与生效时间；
E10 一般条款与续保例外；
E11 跨句条件与具体投保方案。

每个事件包含两个有限算子候选。所有候选都必须通过：可执行性、真实IRI、
Reasoner一致性、目标属性单值、数值范围和最小修改。正确答案只保存在独立
oracle CSV中，不写入给排序模型的events.json。
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
    from rdflib.namespace import OWL, XSD
except ImportError:
    print("缺少rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)

try:
    import validate_benchmark as benchmark_validator
    from repair_operators import apply_operations, expected_graph_delta
except ImportError as exc:
    print(
        "缺少validate_benchmark.py或扩展版repair_operators.py。"
        "请把本脚本放入ontology-evolution\\src。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
BASELINE = PROJECT_DIR / "benchmark" / "clean" / "insurance-validation-baseline.owl"
ROOT = PROJECT_DIR / "benchmark" / "semantic-ambiguity"
DOCUMENT_DIR = ROOT / "documents"
MUTANT_DIR = ROOT / "mutants"
CANDIDATE_ROOT = ROOT / "candidate-owls"
EVENT_FILE = ROOT / "semantic-events.json"
ORACLE_FILE = ROOT / "semantic-oracle.csv"
CATALOG_FILE = ROOT / "semantic-candidates.json"
CATALOG_CSV = ROOT / "semantic-candidates.csv"
LOG_FILE = PROJECT_DIR / "output" / "semantic-benchmark-build.log"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/insurance-semantic"


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
    result = Graph(identifier=source.identifier)
    for prefix, namespace in source.namespaces():
        result.bind(prefix, namespace)
    for triple in source:
        result.add(triple)
    return result


def all_uris(graph: Graph) -> set[URIRef]:
    result: set[URIRef] = set()
    for triple in graph:
        for value in triple:
            if isinstance(value, URIRef):
                result.add(value)
    return result


def find_unique_iri(graph: Graph, label: str) -> URIRef:
    matches = sorted(
        [value for value in all_uris(graph) if local_name(value) == label],
        key=str,
    )
    if len(matches) != 1:
        raise RuntimeError(f"{label!r}匹配到{len(matches)}个IRI：{matches}")
    return matches[0]


def integer_literal(value: int) -> Literal:
    return Literal(str(value), datatype=XSD.integer)


def remove_expected_value(
    graph: Graph, subject: URIRef, predicate: URIRef, value: int
) -> None:
    triple = (subject, predicate, integer_literal(value))
    if triple not in graph:
        actual = list(graph.objects(subject, predicate))
        raise RuntimeError(f"基线缺少预期三元组{triple}，实际值={actual}")
    graph.remove(triple)


def set_ontology_identity(graph: Graph, suffix: str) -> str:
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(f"{ONTOLOGY_BASE}/{suffix}")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    return str(ontology_iri)


def add_value_operation(
    subject: URIRef, predicate: URIRef, value: int
) -> dict[str, Any]:
    return {
        "operator": "ADD_PROPERTY_VALUE",
        "subject_iri": str(subject),
        "predicate_iri": str(predicate),
        "new_value": {
            "kind": "literal",
            "lexical": str(value),
            "datatype": str(XSD.integer),
        },
    }


def candidate_values(
    graph: Graph, subject: URIRef, predicate: URIRef
) -> list[int]:
    values: list[int] = []
    for value in graph.objects(subject, predicate):
        if isinstance(value, Literal):
            try:
                values.append(int(str(value)))
            except ValueError:
                continue
    return values


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"没有数据可写入：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def documents() -> dict[str, dict[str, Any]]:
    return {
        "DOC_E9_OLD": {
            "file_name": "E9_旧版正式条款.txt",
            "authority": 100,
            "effective_from": "2025-01-01",
            "effective_to": "2026-06-30",
            "text": """
文件名称：产品A保险合同条款（2025版）
文件类型：正式保险合同
生效期间：2025年1月1日至2026年6月30日

第八条 投保年龄
产品A的最高投保年龄为60周岁。本版本仅适用于上述生效期间内签署的合同。
""",
        },
        "DOC_E9_CURRENT": {
            "file_name": "E9_现行正式条款.txt",
            "authority": 100,
            "effective_from": "2026-07-01",
            "effective_to": "",
            "text": """
文件名称：产品A保险合同条款（2026修订版）
文件类型：正式保险合同
生效时间：2026年7月1日

第八条 投保年龄
自本修订版生效之日起，产品A的最高投保年龄调整为65周岁。此前版本与本条
不一致的，以本修订版为准。
""",
        },
        "DOC_E10_TERMS": {
            "file_name": "E10_等待期及续保例外条款.txt",
            "authority": 100,
            "effective_from": "2026-07-01",
            "effective_to": "",
            "text": """
文件名称：产品A等待期及续保特别约定
文件类型：正式保险合同附件
生效时间：2026年7月1日

第三条 一般等待期
首次投保或者中断后重新投保产品A的，等待期为60天。

第四条 连续续保例外
被保险人在上一保险期间届满后连续续保，且保障未发生中断的，不重新计算
等待期，等待期按0天处理。本例外优先于第三条的一般规定。
""",
        },
        "DOC_E11_TERMS": {
            "file_name": "E11_保险期限与附加责任条款.txt",
            "authority": 100,
            "effective_from": "2026-07-01",
            "effective_to": "",
            "text": """
文件名称：产品A保险期限与附加责任说明
文件类型：正式保险合同附件
生效时间：2026年7月1日

第六条 保险期限
产品A基础方案的保险期限为12个月。投保方案包含长期医疗附加责任时，整个
方案的保险期限调整为24个月。

投保确认记录
本次产品A投保方案已经选择长期医疗附加责任。
""",
        },
    }


def event_specs(graph: Graph) -> list[dict[str, Any]]:
    product_a = find_unique_iri(graph, "产品A")
    max_age = find_unique_iri(graph, "最高投保年龄")
    waiting = find_unique_iri(graph, "等待期天数")
    duration = find_unique_iri(graph, "保险期限月数")
    return [
        {
            "event_id": "E9",
            "semantic_type": "TEMPORAL_VERSION",
            "title": "现行条款版本消歧",
            "case_context": "评估日期为2026年8月20日，需要修复产品A当前有效的最高投保年龄。",
            "subject_label": "产品A",
            "predicate_label": "最高投保年龄",
            "subject_iri": product_a,
            "predicate_iri": max_age,
            "baseline_value": 65,
            "allowed_range": [0, 100],
            "documents": ["DOC_E9_OLD", "DOC_E9_CURRENT"],
            "candidate_values": [60, 65],
            "oracle_value": 65,
            "oracle_reason": "评估日期晚于2026修订版生效日，应采用现行正式条款。",
        },
        {
            "event_id": "E10",
            "semantic_type": "GENERAL_RULE_EXCEPTION",
            "title": "一般条款与续保例外消歧",
            "case_context": "产品A本次属于保障从未中断的连续续保，不是首次投保，也不是中断后重新投保。",
            "subject_label": "产品A",
            "predicate_label": "等待期天数",
            "subject_iri": product_a,
            "predicate_iri": waiting,
            "baseline_value": 60,
            "allowed_range": [0, 365],
            "documents": ["DOC_E10_TERMS"],
            "candidate_values": [0, 60],
            "oracle_value": 0,
            "oracle_reason": "连续续保且保障未中断，适用优先于一般规定的例外条款。",
        },
        {
            "event_id": "E11",
            "semantic_type": "CROSS_SENTENCE_SCOPE",
            "title": "附加责任条件与期限消歧",
            "case_context": "需要根据已经确认的产品A具体投保方案修复保险期限。",
            "subject_label": "产品A",
            "predicate_label": "保险期限月数",
            "subject_iri": product_a,
            "predicate_iri": duration,
            "baseline_value": 12,
            "allowed_range": [1, 120],
            "documents": ["DOC_E11_TERMS"],
            "candidate_values": [12, 24],
            "oracle_value": 24,
            "oracle_reason": "投保确认记录表明方案已包含长期医疗附加责任。",
        },
    ]


def evaluate_candidate(
    source_graph: Graph,
    operation: dict[str, Any],
    event: dict[str, Any],
    candidate_path: Path,
    timeout: int,
) -> tuple[dict[str, Any], Graph]:
    effect: dict[str, Any] = {
        "execution_pass": False,
        "iri_guard_pass": False,
        "reasoner_result": "NOT_RUN",
        "reasoner_gate": False,
        "single_value_gate": False,
        "numeric_range_gate": False,
        "minimal_edit_gate": False,
        "formal_gate_pass": False,
        "error_message": "",
    }
    repaired = clone_graph(source_graph)
    try:
        repaired = apply_operations(source_graph, [operation])
        candidate_value = int(operation["new_value"]["lexical"])
        effect["execution_pass"] = True
        effect["iri_guard_pass"] = all(
            URIRef(operation[key]) in all_uris(source_graph)
            for key in ("subject_iri", "predicate_iri")
        )
        set_ontology_identity(
            repaired,
            f"candidate/{event['event_id']}/{candidate_path.stem}",
        )
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        repaired.serialize(destination=candidate_path, format="xml", encoding="utf-8")
        reasoner = benchmark_validator.run_reasoner(candidate_path, timeout)
        values = candidate_values(
            repaired, event["subject_iri"], event["predicate_iri"]
        )
        minimum, maximum = event["allowed_range"]
        expected_removed, expected_added = expected_graph_delta(operation, source_graph)
        actual_removed = len(set(source_graph) - set(repaired))
        actual_added = len(set(repaired) - set(source_graph))
        # Ontology身份会额外产生元数据差异，因此最小修改直接在应用身份前的
        # 业务图差异上计算。这里重新执行一次不添加身份的候选。
        business_repaired = apply_operations(source_graph, [operation])
        business_removed = len(set(source_graph) - set(business_repaired))
        business_added = len(set(business_repaired) - set(source_graph))
        effect.update({
            "reasoner_result": reasoner["status"],
            "reasoner_runtime_ms": reasoner.get("runtime_ms", 0),
            "reasoner_message": reasoner.get("message", ""),
            "reasoner_gate": reasoner["status"] == "CONSISTENT",
            "property_values": values,
            "single_value_gate": values == [candidate_value],
            "numeric_range_gate": minimum <= candidate_value <= maximum,
            "expected_triples_removed": expected_removed,
            "expected_triples_added": expected_added,
            "business_triples_removed": business_removed,
            "business_triples_added": business_added,
            "serialized_triples_removed_including_metadata": actual_removed,
            "serialized_triples_added_including_metadata": actual_added,
            "minimal_edit_gate": (business_removed, business_added)
            == (expected_removed, expected_added),
        })
        effect["formal_gate_pass"] = all(
            effect[key]
            for key in (
                "execution_pass",
                "iri_guard_pass",
                "reasoner_gate",
                "single_value_gate",
                "numeric_range_gate",
                "minimal_edit_gate",
            )
        )
    except Exception as exc:
        effect["error_message"] = f"{type(exc).__name__}: {exc}"
    return effect, repaired


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构造E9-E11语义歧义修复基准")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout < 1:
        raise ValueError("--timeout必须大于0")
    if not BASELINE.is_file():
        raise FileNotFoundError(BASELINE)

    baseline = load_graph(BASELINE)
    doc_defs = documents()
    for doc_id, doc in doc_defs.items():
        write_text(DOCUMENT_DIR / doc["file_name"], doc["text"])

    events_for_model: list[dict[str, Any]] = []
    catalog_events: list[dict[str, Any]] = []
    oracle_rows: list[dict[str, Any]] = []
    flat_rows: list[dict[str, Any]] = []
    logs = [
        "语义歧义本体修复基准构建日志",
        f"基线：{BASELINE}",
        "安全门禁不包含语义Oracle；Oracle单独保存。",
        "",
    ]

    print("语义歧义修复基准 E9-E11")
    for event in event_specs(baseline):
        source = clone_graph(baseline)
        remove_expected_value(
            source,
            event["subject_iri"],
            event["predicate_iri"],
            event["baseline_value"],
        )
        mutant_path = MUTANT_DIR / f"{event['event_id']}_semantic_gap.owl"
        set_ontology_identity(source, f"mutant/{event['event_id']}")
        mutant_path.parent.mkdir(parents=True, exist_ok=True)
        source.serialize(destination=mutant_path, format="xml", encoding="utf-8")

        blinded_candidates: list[dict[str, Any]] = []
        catalog_candidates: list[dict[str, Any]] = []
        oracle_candidate_id = ""
        for index, value in enumerate(event["candidate_values"], start=1):
            candidate_id = f"CAND_{index:03d}"
            operation = add_value_operation(
                event["subject_iri"], event["predicate_iri"], value
            )
            candidate_path = CANDIDATE_ROOT / event["event_id"] / f"{candidate_id}.owl"
            effect, _ = evaluate_candidate(
                source,
                operation,
                event,
                candidate_path,
                args.timeout,
            )
            if not effect["formal_gate_pass"]:
                raise RuntimeError(
                    f"{event['event_id']}/{candidate_id}未通过形式安全门禁：{effect}"
                )
            candidate = {
                "candidate_id": candidate_id,
                "description": f"候选修复值{index}",
                "operator": "ADD_PROPERTY_VALUE",
                "operation": operation,
                "formal_effect": effect,
                "candidate_owl": str(candidate_path.resolve()),
            }
            catalog_candidates.append(candidate)
            blinded_candidates.append({
                "candidate_id": candidate_id,
                "description": candidate["description"],
                "proposed_value": str(value),
                "datatype": str(XSD.integer),
                "operator": "ADD_PROPERTY_VALUE",
                "formal_effect": {
                    "reasoner_result": effect["reasoner_result"],
                    "iri_guard_pass": effect["iri_guard_pass"],
                    "single_value_gate": effect["single_value_gate"],
                    "numeric_range_gate": effect["numeric_range_gate"],
                    "minimal_edit_gate": effect["minimal_edit_gate"],
                    "formal_gate_pass": effect["formal_gate_pass"],
                },
            })
            if value == event["oracle_value"]:
                oracle_candidate_id = candidate_id
            flat_rows.append({
                "event_id": event["event_id"],
                "semantic_type": event["semantic_type"],
                "candidate_id": candidate_id,
                "proposed_value": value,
                "reasoner_result": effect["reasoner_result"],
                "formal_gate_pass": effect["formal_gate_pass"],
                "candidate_owl": str(candidate_path.resolve()),
            })

        if not oracle_candidate_id:
            raise RuntimeError(f"{event['event_id']}没有Oracle候选")
        document_items = [
            {
                "document_id": doc_id,
                "file_name": doc_defs[doc_id]["file_name"],
                "authority": doc_defs[doc_id]["authority"],
                "effective_from": doc_defs[doc_id]["effective_from"],
                "effective_to": doc_defs[doc_id]["effective_to"],
            }
            for doc_id in event["documents"]
        ]
        model_event = {
            "event_id": event["event_id"],
            "semantic_type": event["semantic_type"],
            "title": event["title"],
            "case_context": event["case_context"],
            "subject_label": event["subject_label"],
            "predicate_label": event["predicate_label"],
            "allowed_numeric_range": event["allowed_range"],
            "documents": document_items,
            "candidates": blinded_candidates,
        }
        events_for_model.append(model_event)
        catalog_events.append({
            **model_event,
            "source_mutant": str(mutant_path.resolve()),
            "candidates": catalog_candidates,
        })
        oracle_rows.append({
            "event_id": event["event_id"],
            "oracle_candidate_id": oracle_candidate_id,
            "oracle_value": event["oracle_value"],
            "oracle_reason": event["oracle_reason"],
        })
        line = (
            f"[{event['event_id']}] {event['semantic_type']} | 候选=2 | "
            "形式门禁=2/2通过 | Oracle已隔离"
        )
        print(line)
        logs.append(line)

    EVENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVENT_FILE.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "generator": "build_semantic_ambiguity_benchmark.py",
                "oracle_included": False,
                "events": events_for_model,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    CATALOG_FILE.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "generator": "build_semantic_ambiguity_benchmark.py",
                "events": catalog_events,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_csv(ORACLE_FILE, oracle_rows)
    write_csv(CATALOG_CSV, flat_rows)
    logs.extend([
        "",
        "所有6个候选均通过形式安全门禁。",
        f"盲化事件：{EVENT_FILE}",
        f"候选目录：{CATALOG_FILE}",
        f"Oracle：{ORACLE_FILE}",
    ])
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("\n".join(logs) + "\n", encoding="utf-8")

    print(f"\n盲化事件：{EVENT_FILE}")
    print(f"候选目录：{CATALOG_FILE}")
    print(f"Oracle：{ORACLE_FILE}")
    print(f"日志：{LOG_FILE}")
    print("[完成] 所有候选形式上安全，但必须读取文档语义才能确定Oracle。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[基准构建停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
