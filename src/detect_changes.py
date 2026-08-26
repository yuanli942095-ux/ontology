from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

try:
    from rdflib import Graph, Literal, RDF, URIRef
    from rdflib.namespace import OWL, RDF as RDF_NS, RDFS
except ImportError:
    print("缺少 rdflib，请先执行：python -m pip install rdflib")
    raise SystemExit(2)


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
OUTPUT_DIR = PROJECT_DIR / "output"

V1_OWL = DATA_DIR / "insurance-v1-fixed.owl"
V2_OWL = DATA_DIR / "insurance-v2.owl"
EVIDENCE_CSV = DATA_DIR / "axiom-evidence.csv"
REPORT_CSV = OUTPUT_DIR / "change-report.csv"


def local_name(value: object) -> str:
    """取得IRI的局部名称，并对中文百分号编码进行解码。"""
    text = unquote(str(value))
    if "#" in text:
        return text.rsplit("#", 1)[1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def require_files(paths: list[Path]) -> None:
    missing = [path for path in paths if not path.is_file()]
    if missing:
        print("以下文件不存在：")
        for path in missing:
            print(f"  - {path}")
        print("请检查文件名以及是否已经放入 data 文件夹。")
        raise SystemExit(2)


def load_ontology(path: Path) -> Graph:
    graph = Graph()
    try:
        graph.parse(path, format="xml")
    except Exception as exc:
        print(f"无法读取OWL文件：{path}")
        print(f"原因：{exc}")
        raise SystemExit(2) from exc
    print(f"[已读取] {path.name}：{len(graph)} 个RDF三元组")
    return graph


def load_evidence(path: Path) -> list[dict[str, str]]:
    # utf-8-sig 可以自动处理Excel友好的UTF-8 BOM。
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    required_columns = {
        "axiom_id",
        "axiom_family",
        "ontology_version",
        "axiom_kind",
        "subject_label",
        "predicate_label",
        "object_or_value",
    }
    actual_columns = set(rows[0].keys()) if rows else set()
    missing_columns = required_columns - actual_columns
    if not rows or missing_columns:
        print(f"证据表为空或缺少字段：{', '.join(sorted(missing_columns))}")
        raise SystemExit(2)

    print(f"[已读取] {path.name}：{len(rows)} 条公理—证据记录")
    return rows


def find_subjects(graph: Graph, label: str) -> set[URIRef]:
    return {
        subject
        for subject in graph.subjects()
        if isinstance(subject, URIRef) and local_name(subject) == label
    }


def is_structural_type(value: URIRef) -> bool:
    namespaces = (str(OWL), str(RDF_NS), str(RDFS))
    return str(value).startswith(namespaces)


def graph_values(graph: Graph, row: dict[str, str]) -> set[str]:
    """从OWL图中取得一条证据记录对应的实际断言值。"""
    subjects = find_subjects(graph, row["subject_label"])
    values: set[str] = set()

    if row["axiom_kind"] == "ClassAssertion":
        for subject in subjects:
            for obj in graph.objects(subject, RDF.type):
                if isinstance(obj, URIRef) and not is_structural_type(obj):
                    values.add(local_name(obj))
        return values

    predicate_label = row["predicate_label"]
    predicates = {
        predicate
        for predicate in graph.predicates()
        if isinstance(predicate, URIRef) and local_name(predicate) == predicate_label
    }

    for subject in subjects:
        for predicate in predicates:
            for obj in graph.objects(subject, predicate):
                if isinstance(obj, URIRef):
                    values.add(local_name(obj))
                elif isinstance(obj, Literal):
                    values.add(str(obj))
                else:
                    values.add(str(obj))
    return values


def version_key(version: str) -> str:
    normalized = version.strip()
    if normalized in {"1", "1.0", "v1", "V1"}:
        return "1.0"
    if normalized in {"2", "2.0", "v2", "V2"}:
        return "2.0"
    return normalized


def format_values(values: set[str]) -> str:
    return " | ".join(sorted(values))


def main() -> int:
    require_files([V1_OWL, V2_OWL, EVIDENCE_CSV])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    graph_v1 = load_ontology(V1_OWL)
    graph_v2 = load_ontology(V2_OWL)
    evidence_rows = load_evidence(EVIDENCE_CSV)

    grouped: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in evidence_rows:
        grouped[row["axiom_family"]][version_key(row["ontology_version"])] = row

    report_rows: list[dict[str, str]] = []
    changed_rows: list[dict[str, str]] = []
    error_rows: list[dict[str, str]] = []

    for family in sorted(grouped):
        versions = grouped[family]
        row_v1 = versions.get("1.0")
        row_v2 = versions.get("2.0")

        if not row_v1 or not row_v2:
            result = {
                "axiom_family": family,
                "subject": (row_v1 or row_v2 or {}).get("subject_label", ""),
                "predicate": (row_v1 or row_v2 or {}).get("predicate_label", ""),
                "v1_owl_value": "",
                "v2_owl_value": "",
                "v1_document_value": row_v1.get("object_or_value", "") if row_v1 else "",
                "v2_document_value": row_v2.get("object_or_value", "") if row_v2 else "",
                "change_detected": "",
                "v1_matches_evidence": "",
                "v2_matches_evidence": "",
                "result": "ERROR_MISSING_VERSION_RECORD",
            }
            report_rows.append(result)
            error_rows.append(result)
            continue

        owl_v1 = graph_values(graph_v1, row_v1)
        owl_v2 = graph_values(graph_v2, row_v2)
        doc_v1 = row_v1["object_or_value"].strip()
        doc_v2 = row_v2["object_or_value"].strip()

        v1_matches = doc_v1 in owl_v1
        v2_matches = doc_v2 in owl_v2
        changed = owl_v1 != owl_v2

        if not owl_v1 or not owl_v2:
            status = "ERROR_MISSING_OWL_VALUE"
        elif not v1_matches or not v2_matches:
            status = "ERROR_EVIDENCE_MISMATCH"
        elif changed:
            status = "CHANGED"
        else:
            status = "UNCHANGED"

        result = {
            "axiom_family": family,
            "subject": row_v2["subject_label"],
            "predicate": row_v2["predicate_label"],
            "v1_owl_value": format_values(owl_v1),
            "v2_owl_value": format_values(owl_v2),
            "v1_document_value": doc_v1,
            "v2_document_value": doc_v2,
            "change_detected": str(changed).lower(),
            "v1_matches_evidence": str(v1_matches).lower(),
            "v2_matches_evidence": str(v2_matches).lower(),
            "result": status,
        }
        report_rows.append(result)

        if status == "CHANGED":
            changed_rows.append(result)
        elif status.startswith("ERROR"):
            error_rows.append(result)

    fieldnames = [
        "axiom_family",
        "subject",
        "predicate",
        "v1_owl_value",
        "v2_owl_value",
        "v1_document_value",
        "v2_document_value",
        "change_detected",
        "v1_matches_evidence",
        "v2_matches_evidence",
        "result",
    ]
    with REPORT_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report_rows)

    print("\n检测到的变化：")
    if changed_rows:
        for row in changed_rows:
            print(
                f"  [CHANGED] {row['subject']}—{row['predicate']}："
                f"{row['v1_owl_value']} -> {row['v2_owl_value']}"
            )
    else:
        print("  未检测到变化。")

    unchanged_count = sum(row["result"] == "UNCHANGED" for row in report_rows)
    print("\n检测汇总：")
    print(f"  公理族总数：{len(report_rows)}")
    print(f"  发生变化：{len(changed_rows)}")
    print(f"  未发生变化：{unchanged_count}")
    print(f"  错误或证据不匹配：{len(error_rows)}")
    print(f"  报告位置：{REPORT_CSV}")

    expected = {
        ("最高投保年龄", "60", "65"),
        ("等待期天数", "90", "60"),
    }
    actual = {
        (row["predicate"], row["v1_owl_value"], row["v2_owl_value"])
        for row in changed_rows
    }

    if not error_rows and actual == expected:
        print("\n[实验通过] 两项预期变化均被准确识别，且OWL与文档证据一致。")
        return 0

    print("\n[实验未通过] 请打开 change-report.csv 查看错误或额外变化。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
