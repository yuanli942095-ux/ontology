from __future__ import annotations

"""生成E13、E23、E33受控开发案例。

这些案例只用于开发、提示词选择和流程调试，不得计入论文最终测试集。
"""

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
NOTE = "受控开发用例，仅用于流程调试和提示词开发，不计入论文测试集"


E13_OLD_TEXT = """文件名称：产品A投保规则（2026年现行版）
文件类型：受控开发用正式条款
生效时间：2026年7月1日
失效时间：2026年8月31日

第八条 投保年龄
在本版本有效期间，产品A的最高投保年龄为65周岁。
"""


E13_FUTURE_TEXT = """文件名称：产品A投保规则（2026年9月修订版）
文件类型：受控开发用正式条款
发布日期：2026年8月15日
生效时间：2026年9月1日

第八条 投保年龄
自2026年9月1日起，产品A的最高投保年龄调整为70周岁。
本文件在生效日前不适用于投保年龄判定。
"""


E23_TEXT = """文件名称：产品A等待期、续保与复效特别约定
文件类型：受控开发用正式合同附件
生效时间：2026年7月1日

第三条 一般等待期
首次投保产品A的，等待期为60天。

第四条 连续续保例外
上一保险期间届满后连续续保且保障从未中断的，等待期为0天。

第五条 中断后复效的再例外
续保资格曾经中断，后来经保险公司批准恢复保障的，等待期为30天。
第五条优先于第四条；不符合第四条和第五条时适用第三条。
"""


E33_TEXT = """文件名称：产品A多方案保险期限说明
文件类型：受控开发用正式合同附件
生效时间：2026年7月1日

第六条 基础期限
仅投保产品A基础保障的，保险期限为12个月。

第七条 长期医疗附加责任
投保方案选择长期医疗附加责任时，保险期限调整为24个月。

第八条 海外医疗扩展责任
同时选择长期医疗附加责任和海外医疗扩展责任时，保险期限调整为36个月。

投保申请记录
申请人在产品A投保页面选择了长期医疗附加责任。

核保确认记录
核保人员确认本次申请未选择海外医疗扩展责任。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化E13、E23、E33受控开发案例")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument(
        "--confirm-controlled-dev",
        action="store_true",
        help="确认三个案例只用于开发调试，不作为论文测试样本",
    )
    return parser.parse_args()


def local_name(value: object) -> str:
    text = unquote(str(value))
    return text.rsplit("#", 1)[-1] if "#" in text else text.rstrip("/").rsplit("/", 1)[-1]


def all_iris(graph: Graph) -> set[URIRef]:
    return {value for triple in graph for value in triple if isinstance(value, URIRef)}


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
    target = BENCHMARK_DIR / "backups" / f"before-e13-e23-e33-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    for path in (EVENT_CSV, DOCUMENT_CSV, CANDIDATE_CSV, ORACLE_CSV):
        shutil.copy2(path, target / path.name)
    return target


def literal_spec(value: int) -> dict[str, str]:
    return {"kind": "literal", "lexical": str(value), "datatype": str(XSD.integer)}


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
    baseline: Path, event_id: str, predicate_label: str, wrong_value: int
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


def document_row(
    document_id: str,
    path: Path,
    effective_from: str,
    effective_to: str,
    document_type: str,
) -> dict[str, str]:
    return {
        "document_id": document_id,
        "file_name": path.name,
        "authority": "100",
        "effective_from": effective_from,
        "effective_to": effective_to,
        "issuer": "受控示例保险公司",
        "document_type": document_type,
        "source_url": "",
        "source_type": "CONTROLLED_DEV",
        "sha256": sha256_file(path),
        "status": "READY",
        "notes": NOTE,
    }


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

    doc_specs = [
        ("DOC_E13_CURRENT", "E13_当前生效条款.txt", E13_OLD_TEXT, "2026-07-01", "2026-08-31", "正式保险条款"),
        ("DOC_E13_FUTURE", "E13_已发布未生效条款.txt", E13_FUTURE_TEXT, "2026-09-01", "", "正式保险条款"),
        ("DOC_E23_TERMS", "E23_等待期续保与复效条款.txt", E23_TEXT, "2026-07-01", "", "等待期与复效特别约定"),
        ("DOC_E33_TERMS", "E33_多方案保险期限条款.txt", E33_TEXT, "2026-07-01", "", "多方案保险期限说明"),
    ]
    document_paths: dict[str, Path] = {}
    for document_id, filename, content, _start, _end, _type in doc_specs:
        path = DOCUMENT_DIR / filename
        path.write_text(content, encoding="utf-8")
        document_paths[document_id] = path

    e13_owl, product_13, age = make_mutant(baseline, "E13", "最高投保年龄", 68)
    e23_owl, product_23, waiting = make_mutant(baseline, "E23", "等待期天数", 45)
    e33_owl, product_33, duration = make_mutant(baseline, "E33", "保险期限月数", 18)

    document_rows = load_csv(DOCUMENT_CSV)
    for document_id, _filename, _content, start, end, document_type in doc_specs:
        upsert(
            document_rows,
            "document_id",
            document_id,
            document_row(document_id, document_paths[document_id], start, end, document_type),
        )
    write_csv(DOCUMENT_CSV, document_rows)

    event_rows = load_csv(EVENT_CSV)
    event_specs = [
        {
            "event_id": "E13",
            "split": "dev",
            "semantic_type": "TEMPORAL_VERSION",
            "domain": "insurance",
            "title": "已发布但未生效版本的投保年龄消歧",
            "case_context": "评估日期为2026年8月20日。9月修订版虽然已经发布，但尚未生效，需要确定产品A当日有效的最高投保年龄。",
            "subject_label": "产品A",
            "predicate_label": "最高投保年龄",
            "value_kind": "literal_integer",
            "allowed_min": "0",
            "allowed_max": "100",
            "document_ids": "DOC_E13_CURRENT|DOC_E13_FUTURE",
            "source_owl": "benchmark/semantic-v2/mutants/E13.owl",
            "status": "READY",
            "notes": NOTE,
        },
        {
            "event_id": "E23",
            "split": "dev",
            "semantic_type": "GENERAL_RULE_EXCEPTION",
            "domain": "insurance",
            "title": "中断续保复效的嵌套例外消歧",
            "case_context": "该客户不是首次投保；其续保资格曾中断，之后经保险公司批准恢复保障。需要确定产品A本次适用的等待期。",
            "subject_label": "产品A",
            "predicate_label": "等待期天数",
            "value_kind": "literal_integer",
            "allowed_min": "0",
            "allowed_max": "365",
            "document_ids": "DOC_E23_TERMS",
            "source_owl": "benchmark/semantic-v2/mutants/E23.owl",
            "status": "READY",
            "notes": NOTE,
        },
        {
            "event_id": "E33",
            "split": "dev",
            "semantic_type": "CROSS_SENTENCE_SCOPE",
            "domain": "insurance",
            "title": "附加责任与海外扩展的跨句范围消歧",
            "case_context": "需要结合条款、投保申请记录和核保确认记录，确定本次产品A方案的保险期限。",
            "subject_label": "产品A",
            "predicate_label": "保险期限月数",
            "value_kind": "literal_integer",
            "allowed_min": "1",
            "allowed_max": "120",
            "document_ids": "DOC_E33_TERMS",
            "source_owl": "benchmark/semantic-v2/mutants/E33.owl",
            "status": "READY",
            "notes": NOTE,
        },
    ]
    for row in event_specs:
        upsert(event_rows, "event_id", row["event_id"], row)
    write_csv(EVENT_CSV, event_rows)

    candidate_rows = [
        row
        for row in load_csv(CANDIDATE_CSV)
        if row.get("event_id") not in {"E13", "E23", "E33"}
    ]
    candidate_specs = [
        ("E13", "CAND_001", "按评估日当前生效版本修复为65周岁", 65, replace_operation(product_13, age, 68, 65)),
        ("E13", "CAND_002", "按已发布但尚未生效版本修复为70周岁", 70, replace_operation(product_13, age, 68, 70)),
        ("E23", "CAND_001", "按一般首次投保规则修复为60天", 60, replace_operation(product_23, waiting, 45, 60)),
        ("E23", "CAND_002", "按连续且未中断续保规则修复为0天", 0, replace_operation(product_23, waiting, 45, 0)),
        ("E23", "CAND_003", "按中断后复效规则修复为30天", 30, replace_operation(product_23, waiting, 45, 30)),
        ("E33", "CAND_001", "按基础保障修复为12个月", 12, replace_operation(product_33, duration, 18, 12)),
        ("E33", "CAND_002", "按长期医疗附加责任修复为24个月", 24, replace_operation(product_33, duration, 18, 24)),
        ("E33", "CAND_003", "按同时选择海外扩展责任修复为36个月", 36, replace_operation(product_33, duration, 18, 36)),
    ]
    for event_id, candidate_id, description, value, operation in candidate_specs:
        candidate_rows.append(
            {
                "event_id": event_id,
                "candidate_id": candidate_id,
                "description": description,
                "display_value": str(value),
                "operation_json": json.dumps(operation, ensure_ascii=False, separators=(",", ":")),
                "status": "READY",
            }
        )
    write_csv(CANDIDATE_CSV, candidate_rows)

    oracle_rows = load_csv(ORACLE_CSV)
    oracle_specs = [
        (
            "E13",
            "CAND_001",
            "65",
            "DOC_E13_CURRENT|DOC_E13_FUTURE",
            [
                {"document_id": "DOC_E13_CURRENT", "start_line": 3, "end_line": 7, "quote": "现行版有效至2026年8月31日，最高投保年龄为65周岁。"},
                {"document_id": "DOC_E13_FUTURE", "start_line": 3, "end_line": 8, "quote": "修订版2026年9月1日生效，生效日前不适用。"},
            ],
        ),
        (
            "E23",
            "CAND_003",
            "30",
            "DOC_E23_TERMS",
            [
                {"document_id": "DOC_E23_TERMS", "start_line": 12, "end_line": 14, "quote": "续保资格中断后恢复保障的等待期为30天，且第五条优先于第四条。"},
            ],
        ),
        (
            "E33",
            "CAND_002",
            "24",
            "DOC_E33_TERMS",
            [
                {"document_id": "DOC_E33_TERMS", "start_line": 9, "end_line": 10, "quote": "选择长期医疗附加责任时期限为24个月。"},
                {"document_id": "DOC_E33_TERMS", "start_line": 16, "end_line": 19, "quote": "本次选择长期医疗附加责任，但未选择海外医疗扩展责任。"},
            ],
        ),
    ]
    for event_id, candidate_id, value, document_ids, spans in oracle_specs:
        upsert(
            oracle_rows,
            "event_id",
            event_id,
            {
                "event_id": event_id,
                "oracle_candidate_id": candidate_id,
                "oracle_value": value,
                "evidence_document_ids": document_ids,
                "evidence_spans_json": json.dumps(spans, ensure_ascii=False, separators=(",", ":")),
                "annotator_1": "CONTROLLED_CASE_AUTHOR",
                "annotator_2": "CONTROLLED_RULE_RECHECK",
                "adjudicator": "",
                "agreement_status": "AGREED",
                "adjudication_note": NOTE,
                "status": "READY",
            },
        )
    write_csv(ORACLE_CSV, oracle_rows)

    print("E13、E23、E33受控开发案例已补齐：")
    print(f"  E13错误本体：{e13_owl} | Oracle=CAND_001（65周岁）")
    print(f"  E23错误本体：{e23_owl} | Oracle=CAND_003（30天）")
    print(f"  E33错误本体：{e33_owl} | Oracle=CAND_002（24个月）")
    print(f"  条款文档：{DOCUMENT_DIR}")
    print(f"  CSV备份：{backup}")
    print("\n下一步依次运行：")
    print("  python .\\src\\update_semantic_document_hashes.py")
    print("  python .\\src\\validate_semantic_benchmark_v2.py --require-ready --min-events 6 --min-per-type 2")
    print("  python .\\src\\build_semantic_benchmark_v2.py --allow-less --timeout 120")
    print("\n[重要] 这些是受控开发样本，不得计入论文最终测试集。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[E13/E23/E33初始化停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
