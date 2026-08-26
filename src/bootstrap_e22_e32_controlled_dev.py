from __future__ import annotations

"""生成E22与E32受控开发案例，只用于流程调试，不计入论文测试集。"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

try:
    from rdflib import Graph, Literal, RDF, URIRef
    from rdflib.namespace import OWL, XSD
except ImportError:
    print("缺少rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)

from semantic_v2_common import (
    BENCHMARK_DIR,
    CANDIDATE_CSV,
    DOCUMENT_CSV,
    DOCUMENT_DIR,
    EVENT_CSV,
    ORACLE_CSV,
    PROJECT_DIR,
    load_csv,
    sha256_file,
    write_csv,
)


DEFAULT_BASELINE = PROJECT_DIR / "benchmark" / "clean" / "insurance-validation-baseline.owl"
MUTANT_DIR = BENCHMARK_DIR / "mutants"
NOTE = "受控开发用例，仅用于流程调试，不计入论文测试集"


E22_TEXT = """文件名称：产品A等待期及续保特别约定
文件类型：受控开发用正式合同附件
生效时间：2026年7月1日

第三条 一般等待期
首次投保或者中断后重新投保产品A的，等待期为60天。

第四条 连续续保例外
被保险人在上一保险期间届满后连续续保，且保障未发生中断的，不重新计算等待期，等待期按0天处理。
本例外优先于第三条的一般规定。
"""


E32_TEXT = """文件名称：产品A保险期限与附加责任说明
文件类型：受控开发用正式合同附件
生效时间：2026年7月1日

第六条 保险期限
产品A基础方案的保险期限为12个月。
投保方案包含长期医疗附加责任时，整个方案的保险期限调整为24个月。

投保确认记录
本次产品A投保方案已经选择长期医疗附加责任。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化E22和E32受控开发案例")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--confirm-controlled-dev", action="store_true")
    return parser.parse_args()


def local_name(value: object) -> str:
    text = unquote(str(value))
    return text.rsplit("#", 1)[-1] if "#" in text else text.rstrip("/").rsplit("/", 1)[-1]


def all_iris(graph: Graph) -> set[URIRef]:
    return {
        value
        for triple in graph
        for value in triple
        if isinstance(value, URIRef)
    }


def find_unique_iri(graph: Graph, label: str) -> URIRef:
    matches = sorted([iri for iri in all_iris(graph) if local_name(iri) == label], key=str)
    if len(matches) != 1:
        raise RuntimeError(f"{label!r}匹配到{len(matches)}个IRI：{matches}")
    return matches[0]


def upsert(rows: list[dict[str, str]], key: str, value: str, new_row: dict[str, str]) -> None:
    matches = [index for index, row in enumerate(rows) if row.get(key, "").strip() == value]
    if len(matches) > 1:
        raise RuntimeError(f"{key}={value}存在重复行")
    if matches:
        rows[matches[0]] = new_row
    else:
        rows.append(new_row)


def backup_inputs() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BENCHMARK_DIR / "backups" / f"before-e22-e32-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    for path in (EVENT_CSV, DOCUMENT_CSV, CANDIDATE_CSV, ORACLE_CSV):
        shutil.copy2(path, target / path.name)
    return target


def literal_spec(value: int) -> dict[str, str]:
    return {
        "kind": "literal",
        "lexical": str(value),
        "datatype": str(XSD.integer),
    }


def replace_operation(
    product: URIRef, predicate: URIRef, old_value: int, new_value: int
) -> dict[str, object]:
    return {
        "operator": "REPLACE_PROPERTY_VALUE",
        "subject_iri": str(product),
        "predicate_iri": str(predicate),
        "old_value": literal_spec(old_value),
        "new_value": literal_spec(new_value),
    }


def make_mutant(
    baseline: Path,
    event_id: str,
    predicate_label: str,
    wrong_value: int,
) -> tuple[Path, URIRef, URIRef]:
    graph = Graph()
    graph.parse(baseline, format="xml")
    product = find_unique_iri(graph, "产品A")
    predicate = find_unique_iri(graph, predicate_label)
    existing = list(graph.objects(product, predicate))
    if not existing:
        raise RuntimeError(f"基线缺少产品A—{predicate_label}断言")
    for value in existing:
        graph.remove((product, predicate, value))
    graph.add((product, predicate, Literal(str(wrong_value), datatype=XSD.integer)))
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(f"https://w3id.org/ontology-evolution/semantic-v2/{event_id}-mutant")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    path = MUTANT_DIR / f"{event_id}.owl"
    graph.serialize(destination=path, format="xml", encoding="utf-8")
    return path, product, predicate


def main() -> int:
    args = parse_args()
    if not args.confirm_controlled_dev:
        raise RuntimeError("请增加--confirm-controlled-dev确认这些案例只用于开发调试")
    baseline = Path(args.baseline)
    if not baseline.is_absolute():
        baseline = PROJECT_DIR / baseline
    if not baseline.is_file():
        raise FileNotFoundError(baseline)
    for path in (EVENT_CSV, DOCUMENT_CSV, CANDIDATE_CSV, ORACLE_CSV):
        if not path.is_file():
            raise FileNotFoundError(path)

    backup = backup_inputs()
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    MUTANT_DIR.mkdir(parents=True, exist_ok=True)
    e22_doc = DOCUMENT_DIR / "E22_等待期及续保例外条款.txt"
    e32_doc = DOCUMENT_DIR / "E32_保险期限与附加责任条款.txt"
    e22_doc.write_text(E22_TEXT, encoding="utf-8")
    e32_doc.write_text(E32_TEXT, encoding="utf-8")
    e22_owl, product, waiting = make_mutant(baseline, "E22", "等待期天数", 30)
    e32_owl, product_32, duration = make_mutant(baseline, "E32", "保险期限月数", 18)

    document_rows = load_csv(DOCUMENT_CSV)
    for document_id, path, document_type in (
        ("DOC_E22_TERMS", e22_doc, "等待期及续保特别约定"),
        ("DOC_E32_TERMS", e32_doc, "保险期限与附加责任说明"),
    ):
        upsert(document_rows, "document_id", document_id, {
            "document_id": document_id,
            "file_name": path.name,
            "authority": "100",
            "effective_from": "2026-07-01",
            "effective_to": "",
            "issuer": "受控示例保险公司",
            "document_type": document_type,
            "source_url": "",
            "source_type": "CONTROLLED_DEV",
            "sha256": sha256_file(path),
            "status": "READY",
            "notes": NOTE,
        })
    write_csv(DOCUMENT_CSV, document_rows)

    event_rows = load_csv(EVENT_CSV)
    upsert(event_rows, "event_id", "E22", {
        "event_id": "E22",
        "split": "dev",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "domain": "insurance",
        "title": "连续续保等待期例外消歧",
        "case_context": "本次投保属于连续续保，上一保险期间与本次保险期间没有中断，需要确定产品A适用的等待期。",
        "subject_label": "产品A",
        "predicate_label": "等待期天数",
        "value_kind": "literal_integer",
        "allowed_min": "0",
        "allowed_max": "365",
        "document_ids": "DOC_E22_TERMS",
        "source_owl": "benchmark/semantic-v2/mutants/E22.owl",
        "status": "READY",
        "notes": NOTE,
    })
    upsert(event_rows, "event_id", "E32", {
        "event_id": "E32",
        "split": "dev",
        "semantic_type": "CROSS_SENTENCE_SCOPE",
        "domain": "insurance",
        "title": "附加责任条件下保险期限消歧",
        "case_context": "需要根据条款和投保确认记录，确定本次产品A投保方案的保险期限。",
        "subject_label": "产品A",
        "predicate_label": "保险期限月数",
        "value_kind": "literal_integer",
        "allowed_min": "1",
        "allowed_max": "120",
        "document_ids": "DOC_E32_TERMS",
        "source_owl": "benchmark/semantic-v2/mutants/E32.owl",
        "status": "READY",
        "notes": NOTE,
    })
    write_csv(EVENT_CSV, event_rows)

    candidate_rows = [
        row for row in load_csv(CANDIDATE_CSV)
        if row.get("event_id") not in {"E22", "E32"}
    ]
    candidate_specs = [
        ("E22", "CAND_001", "把错误等待期30修复为一般规则60天", 60, replace_operation(product, waiting, 30, 60)),
        ("E22", "CAND_002", "把错误等待期30修复为连续续保例外0天", 0, replace_operation(product, waiting, 30, 0)),
        ("E32", "CAND_001", "把错误期限18修复为基础方案12个月", 12, replace_operation(product_32, duration, 18, 12)),
        ("E32", "CAND_002", "把错误期限18修复为附加责任方案24个月", 24, replace_operation(product_32, duration, 18, 24)),
    ]
    for event_id, candidate_id, description, value, operation in candidate_specs:
        candidate_rows.append({
            "event_id": event_id,
            "candidate_id": candidate_id,
            "description": description,
            "display_value": str(value),
            "operation_json": json.dumps(operation, ensure_ascii=False, separators=(",", ":")),
            "status": "READY",
        })
    write_csv(CANDIDATE_CSV, candidate_rows)

    oracle_rows = load_csv(ORACLE_CSV)
    e22_spans = [
        {"document_id": "DOC_E22_TERMS", "start_line": 6, "end_line": 6, "quote": "首次投保或者中断后重新投保的等待期为60天。"},
        {"document_id": "DOC_E22_TERMS", "start_line": 9, "end_line": 10, "quote": "连续续保且保障未中断时等待期按0天处理，例外优先于一般规定。"},
    ]
    e32_spans = [
        {"document_id": "DOC_E32_TERMS", "start_line": 6, "end_line": 7, "quote": "基础方案12个月，包含长期医疗附加责任时调整为24个月。"},
        {"document_id": "DOC_E32_TERMS", "start_line": 10, "end_line": 10, "quote": "本次投保方案已经选择长期医疗附加责任。"},
    ]
    for event_id, candidate_id, value, document_id, spans in (
        ("E22", "CAND_002", "0", "DOC_E22_TERMS", e22_spans),
        ("E32", "CAND_002", "24", "DOC_E32_TERMS", e32_spans),
    ):
        upsert(oracle_rows, "event_id", event_id, {
            "event_id": event_id,
            "oracle_candidate_id": candidate_id,
            "oracle_value": value,
            "evidence_document_ids": document_id,
            "evidence_spans_json": json.dumps(spans, ensure_ascii=False, separators=(",", ":")),
            "annotator_1": "CONTROLLED_CASE_AUTHOR",
            "annotator_2": "CONTROLLED_RULE_RECHECK",
            "adjudicator": "",
            "agreement_status": "AGREED",
            "adjudication_note": NOTE,
            "status": "READY",
        })
    write_csv(ORACLE_CSV, oracle_rows)

    print("E22和E32受控开发案例已生成：")
    print(f"  E22文档：{e22_doc}")
    print(f"  E22错误本体：{e22_owl}")
    print(f"  E32文档：{e32_doc}")
    print(f"  E32错误本体：{e32_owl}")
    print(f"  CSV备份：{backup}")
    print("  E22 Oracle：CAND_002（0天）")
    print("  E32 Oracle：CAND_002（24个月）")
    print("\n[重要] 这些是受控开发样本，不得计入论文测试集。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[E22/E32初始化停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
