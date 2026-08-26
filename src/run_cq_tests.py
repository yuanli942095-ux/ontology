from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from urllib.parse import unquote

try:
    from rdflib import Graph, Literal, RDF, URIRef
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)


PROJECT_DIR = Path(__file__).resolve().parents[1]
CQ_FILE = PROJECT_DIR / "benchmark" / "cq" / "cq-tests.csv"
BASELINE = PROJECT_DIR / "benchmark" / "clean" / "insurance-validation-baseline.owl"
MUTANTS_DIR = PROJECT_DIR / "benchmark" / "mutants"
MANIFEST = PROJECT_DIR / "benchmark" / "ground-truth" / "error-manifest.csv"
OUTPUT_DIR = PROJECT_DIR / "output"
DETAIL_CSV = OUTPUT_DIR / "cq-results.csv"
SUMMARY_CSV = OUTPUT_DIR / "cq-summary.csv"
SUMMARY_JSON = OUTPUT_DIR / "cq-summary.json"

SUPPORTED_QUERY_TYPES = {
    "CLASS_PRESENT",
    "CLASS_ABSENT",
    "PROPERTY_EXACT_SET",
    "PROPERTY_CARDINALITY",
}


def local_name(value: object) -> str:
    text = unquote(str(value))
    if "#" in text:
        return text.rsplit("#", 1)[1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def require_file(path: Path, hint: str = "") -> None:
    if not path.is_file():
        print(f"缺少文件：{path}")
        if hint:
            print(hint)
        raise SystemExit(2)


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
    for subject, predicate, obj in graph:
        for value in (subject, predicate, obj):
            if isinstance(value, URIRef):
                result.add(value)
    return result


def find_entities(graph: Graph, label: str) -> set[URIRef]:
    return {value for value in all_uris(graph) if local_name(value) == label}


def property_values(graph: Graph, subject_label: str, predicate_label: str) -> set[str]:
    subjects = find_entities(graph, subject_label)
    predicates = find_entities(graph, predicate_label)
    result: set[str] = set()
    for subject in subjects:
        for predicate in predicates:
            for obj in graph.objects(subject, predicate):
                if isinstance(obj, URIRef):
                    result.add(local_name(obj))
                elif isinstance(obj, Literal):
                    result.add(str(obj))
    return result


def class_values(graph: Graph, subject_label: str) -> set[str]:
    return {
        local_name(obj)
        for subject in find_entities(graph, subject_label)
        for obj in graph.objects(subject, RDF.type)
        if isinstance(obj, URIRef)
    }


def split_values(text: str) -> set[str]:
    return {item.strip() for item in text.split("|") if item.strip()}


def evaluate_cq(graph: Graph, cq: dict[str, str]) -> tuple[bool, set[str], str]:
    query_type = cq["query_type"].strip()
    if query_type not in SUPPORTED_QUERY_TYPES:
        raise RuntimeError(f"{cq['cq_id']}使用了未知query_type：{query_type}")

    if query_type in {"CLASS_PRESENT", "CLASS_ABSENT"}:
        actual = class_values(graph, cq["subject_label"])
        target = cq["expected_answer"].strip()
        passed = target in actual if query_type == "CLASS_PRESENT" else target not in actual
        expected_text = f"包含{target}" if query_type == "CLASS_PRESENT" else f"不包含{target}"
        return passed, actual, expected_text

    actual = property_values(graph, cq["subject_label"], cq["predicate_label"])
    if query_type == "PROPERTY_EXACT_SET":
        expected = split_values(cq["expected_answer"])
        return actual == expected, actual, str(sorted(expected))

    expected_count = int(cq["expected_count"])
    return len(actual) == expected_count, actual, f"数量={expected_count}"


def experiment_rows() -> list[dict[str, str]]:
    rows = [{
        "error_id": "BASELINE",
        "file_name": BASELINE.name,
        "error_type": "无错误基线",
        "expected_cq_result": "PASS",
    }]
    rows.extend(load_csv(MANIFEST))
    return rows


def experiment_path(row: dict[str, str]) -> Path:
    return BASELINE if row["error_id"] == "BASELINE" else MUTANTS_DIR / row["file_name"]


def expected_failed_cqs(cqs: list[dict[str, str]], error_id: str) -> set[str]:
    if error_id == "BASELINE":
        return set()
    return {
        cq["cq_id"]
        for cq in cqs
        if error_id in split_values(cq.get("expected_fail_on", ""))
    }


def main() -> int:
    require_file(CQ_FILE, "请将cq-tests.csv放入 benchmark\\cq 文件夹")
    require_file(BASELINE, "请先运行 inject_errors.py")
    require_file(MANIFEST, "请先运行 inject_errors.py")
    cqs = load_csv(CQ_FILE)

    required_columns = {
        "cq_id", "question", "query_type", "subject_label", "predicate_label",
        "expected_answer", "expected_count", "hard_constraint", "requirement_origin",
        "rationale", "expected_fail_on",
    }
    missing = required_columns - set(cqs[0])
    if missing:
        raise RuntimeError(f"cq-tests.csv缺少字段：{sorted(missing)}")
    duplicate_ids = {item for item in (cq["cq_id"] for cq in cqs) if sum(row["cq_id"] == item for row in cqs) > 1}
    if duplicate_ids:
        raise RuntimeError(f"CQ编号重复：{sorted(duplicate_ids)}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    details: list[dict[str, str]] = []
    summaries: list[dict[str, str]] = []

    print("独立CQ回归实验")
    print(f"CQ文件：{CQ_FILE}")
    print(f"CQ数量：{len(cqs)}")
    print("说明：本脚本不读取axiom-evidence.csv。\n")

    for experiment in experiment_rows():
        path = experiment_path(experiment)
        require_file(path)
        graph = load_graph(path)
        failed: set[str] = set()

        print(f"[{experiment['error_id']}] {path.name}")
        for cq in cqs:
            passed, actual, expected_text = evaluate_cq(graph, cq)
            status = "PASS" if passed else "FAIL"
            if not passed:
                failed.add(cq["cq_id"])
                print(
                    f"  [{status}] {cq['cq_id']} {cq['question']} "
                    f"| 期望={expected_text} | 实际={sorted(actual)}"
                )
            details.append({
                "error_id": experiment["error_id"],
                "file_name": path.name,
                "cq_id": cq["cq_id"],
                "question": cq["question"],
                "query_type": cq["query_type"],
                "expected": expected_text,
                "actual": " | ".join(sorted(actual)),
                "result": status,
            })

        expected_failed = expected_failed_cqs(cqs, experiment["error_id"])
        actual_result = "PASS" if not failed else "FAIL"
        expected_result = experiment.get("expected_cq_result", "FAIL")
        mapping_match = failed == expected_failed
        overall = "PASS" if actual_result == expected_result and mapping_match else "FAIL"
        summary = {
            "error_id": experiment["error_id"],
            "file_name": path.name,
            "error_type": experiment["error_type"],
            "expected_cq_result": expected_result,
            "actual_cq_result": actual_result,
            "expected_failed_cqs": " | ".join(sorted(expected_failed)),
            "actual_failed_cqs": " | ".join(sorted(failed)),
            "passed_cq_count": str(len(cqs) - len(failed)),
            "failed_cq_count": str(len(failed)),
            "mapping_match": str(mapping_match).lower(),
            "pass_or_fail": overall,
        }
        summaries.append(summary)
        print(
            f"  汇总：CQ结果={actual_result}，失败项={sorted(failed)}，"
            f"错误映射正确={mapping_match}，实验={overall}\n"
        )

    with DETAIL_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(details[0]))
        writer.writeheader()
        writer.writerows(details)
    with SUMMARY_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    with SUMMARY_JSON.open("w", encoding="utf-8") as file:
        json.dump(summaries, file, ensure_ascii=False, indent=2)

    passed_experiments = sum(row["pass_or_fail"] == "PASS" for row in summaries)
    print(f"实验汇总：{passed_experiments}/{len(summaries)} 通过")
    print(f"逐项结果：{DETAIL_CSV}")
    print(f"汇总结果：{SUMMARY_CSV}")
    print(f"JSON结果：{SUMMARY_JSON}")

    if passed_experiments == len(summaries):
        print("\n[独立CQ实验通过] 基线全部通过，E1-E5均触发了预先指定的CQ失败项。")
        return 0
    print("\n[独立CQ实验未通过] 请查看cq-summary.csv。")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[CQ实验停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
