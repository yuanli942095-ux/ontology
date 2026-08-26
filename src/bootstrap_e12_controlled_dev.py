from __future__ import annotations

"""生成E12受控开发案例，只用于跑通流程，不得计入论文测试集。"""

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
CONTROLLED_NOTE = "受控开发用例，仅用于流程调试，不计入论文测试集"


OLD_TEXT = """文件名称：产品A保险条款（2025版）
文件类型：受控开发用正式条款
生效期间：2025年1月1日至2026年6月30日

第八条 投保年龄
产品A的最高投保年龄为60周岁。本版本仅适用于上述生效期间内签署的合同。
"""


NEW_TEXT = """文件名称：产品A保险条款（2026修订版）
文件类型：受控开发用正式条款
生效时间：2026年7月1日

第八条 投保年龄
自本修订版生效之日起，产品A的最高投保年龄调整为65周岁。
此前版本与本条不一致的，以本修订版为准。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化E12受控开发案例")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument(
        "--confirm-controlled-dev",
        action="store_true",
        help="确认该案例只用于开发流程调试，不作为论文测试样本",
    )
    return parser.parse_args()


def local_name(value: object) -> str:
    text = unquote(str(value))
    if "#" in text:
        return text.rsplit("#", 1)[1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def all_iris(graph: Graph) -> set[URIRef]:
    result: set[URIRef] = set()
    for triple in graph:
        for value in triple:
            if isinstance(value, URIRef):
                result.add(value)
    return result


def find_unique_iri(graph: Graph, label: str) -> URIRef:
    matches = sorted(
        [value for value in all_iris(graph) if local_name(value) == label],
        key=str,
    )
    if len(matches) != 1:
        raise RuntimeError(f"{label!r}匹配到{len(matches)}个IRI：{matches}")
    return matches[0]


def backup_inputs() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BENCHMARK_DIR / "backups" / f"before-e12-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in (EVENT_CSV, DOCUMENT_CSV, CANDIDATE_CSV, ORACLE_CSV):
        shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def upsert(rows: list[dict[str, str]], key: str, value: str, new_row: dict[str, str]) -> None:
    matches = [index for index, row in enumerate(rows) if row.get(key, "").strip() == value]
    if len(matches) > 1:
        raise RuntimeError(f"{key}={value}存在重复行")
    if matches:
        rows[matches[0]] = new_row
    else:
        rows.append(new_row)


def literal_spec(value: int) -> dict[str, str]:
    return {
        "kind": "literal",
        "lexical": str(value),
        "datatype": str(XSD.integer),
    }


def replacement_operation(
    product_iri: URIRef, property_iri: URIRef, new_value: int
) -> dict[str, object]:
    return {
        "operator": "REPLACE_PROPERTY_VALUE",
        "subject_iri": str(product_iri),
        "predicate_iri": str(property_iri),
        "old_value": literal_spec(70),
        "new_value": literal_spec(new_value),
    }


def main() -> int:
    args = parse_args()
    if not args.confirm_controlled_dev:
        raise RuntimeError(
            "请增加--confirm-controlled-dev，确认E12只用于开发调试而非论文测试集"
        )
    baseline = Path(args.baseline)
    if not baseline.is_absolute():
        baseline = PROJECT_DIR / baseline
    if not baseline.is_file():
        raise FileNotFoundError(f"找不到干净基线本体：{baseline}")
    for path in (EVENT_CSV, DOCUMENT_CSV, CANDIDATE_CSV, ORACLE_CSV):
        if not path.is_file():
            raise FileNotFoundError(path)

    backup_dir = backup_inputs()
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    MUTANT_DIR.mkdir(parents=True, exist_ok=True)
    old_path = DOCUMENT_DIR / "E12_2025版保险条款.txt"
    new_path = DOCUMENT_DIR / "E12_2026修订版保险条款.txt"
    old_path.write_text(OLD_TEXT, encoding="utf-8")
    new_path.write_text(NEW_TEXT, encoding="utf-8")

    graph = Graph()
    graph.parse(baseline, format="xml")
    product_a = find_unique_iri(graph, "产品A")
    max_age = find_unique_iri(graph, "最高投保年龄")
    existing_values = list(graph.objects(product_a, max_age))
    if not existing_values:
        raise RuntimeError("基线中没有产品A的最高投保年龄断言")
    for value in existing_values:
        graph.remove((product_a, max_age, value))
    graph.add((product_a, max_age, Literal("70", datatype=XSD.integer)))

    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef("https://w3id.org/ontology-evolution/semantic-v2/E12-mutant")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    mutant_path = MUTANT_DIR / "E12.owl"
    graph.serialize(destination=mutant_path, format="xml", encoding="utf-8")

    document_rows = load_csv(DOCUMENT_CSV)
    upsert(document_rows, "document_id", "DOC_E12_OLD", {
        "document_id": "DOC_E12_OLD",
        "file_name": old_path.name,
        "authority": "100",
        "effective_from": "2025-01-01",
        "effective_to": "2026-06-30",
        "issuer": "受控示例保险公司",
        "document_type": "正式保险条款",
        "source_url": "",
        "source_type": "CONTROLLED_DEV",
        "sha256": sha256_file(old_path),
        "status": "READY",
        "notes": CONTROLLED_NOTE,
    })
    upsert(document_rows, "document_id", "DOC_E12_NEW", {
        "document_id": "DOC_E12_NEW",
        "file_name": new_path.name,
        "authority": "100",
        "effective_from": "2026-07-01",
        "effective_to": "",
        "issuer": "受控示例保险公司",
        "document_type": "正式保险条款",
        "source_url": "",
        "source_type": "CONTROLLED_DEV",
        "sha256": sha256_file(new_path),
        "status": "READY",
        "notes": CONTROLLED_NOTE,
    })
    write_csv(DOCUMENT_CSV, document_rows)

    event_rows = load_csv(EVENT_CSV)
    upsert(event_rows, "event_id", "E12", {
        "event_id": "E12",
        "split": "dev",
        "semantic_type": "TEMPORAL_VERSION",
        "domain": "insurance",
        "title": "产品A最高投保年龄版本消歧",
        "case_context": "评估日期为2026年8月20日，需要修复产品A当前有效的最高投保年龄。",
        "subject_label": "产品A",
        "predicate_label": "最高投保年龄",
        "value_kind": "literal_integer",
        "allowed_min": "0",
        "allowed_max": "100",
        "document_ids": "DOC_E12_OLD|DOC_E12_NEW",
        "source_owl": "benchmark/semantic-v2/mutants/E12.owl",
        "status": "READY",
        "notes": CONTROLLED_NOTE,
    })
    write_csv(EVENT_CSV, event_rows)

    candidate_rows = [row for row in load_csv(CANDIDATE_CSV) if row.get("event_id") != "E12"]
    for candidate_id, value in (("CAND_001", 60), ("CAND_002", 65)):
        candidate_rows.append({
            "event_id": "E12",
            "candidate_id": candidate_id,
            "description": f"把产品A最高投保年龄从错误值70修复为{value}",
            "display_value": str(value),
            "operation_json": json.dumps(
                replacement_operation(product_a, max_age, value),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "status": "READY",
        })
    write_csv(CANDIDATE_CSV, candidate_rows)

    oracle_rows = load_csv(ORACLE_CSV)
    spans = [
        {
            "document_id": "DOC_E12_NEW",
            "start_line": 3,
            "end_line": 3,
            "quote": "生效时间：2026年7月1日",
        },
        {
            "document_id": "DOC_E12_NEW",
            "start_line": 6,
            "end_line": 7,
            "quote": "最高投保年龄调整为65周岁；此前版本不一致时以本修订版为准。",
        },
    ]
    upsert(oracle_rows, "event_id", "E12", {
        "event_id": "E12",
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "65",
        "evidence_document_ids": "DOC_E12_NEW",
        "evidence_spans_json": json.dumps(spans, ensure_ascii=False, separators=(",", ":")),
        "annotator_1": "CONTROLLED_CASE_AUTHOR",
        "annotator_2": "CONTROLLED_RULE_RECHECK",
        "adjudicator": "",
        "agreement_status": "AGREED",
        "adjudication_note": CONTROLLED_NOTE,
        "status": "READY",
    })
    write_csv(ORACLE_CSV, oracle_rows)

    print("E12受控开发案例已生成：")
    print(f"  旧条款：{old_path}")
    print(f"  新条款：{new_path}")
    print(f"  错误本体：{mutant_path}")
    print(f"  产品A IRI：{product_a}")
    print(f"  最高投保年龄 IRI：{max_age}")
    print(f"  CSV备份：{backup_dir}")
    print("  Oracle：CAND_002（65）")
    print("\n[重要] E12是受控开发样本，只用于跑通流程，不得计入论文测试集。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[E12初始化停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
