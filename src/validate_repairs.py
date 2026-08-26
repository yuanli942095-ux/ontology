from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from rdflib import Graph, RDF
from rdflib.compare import graph_diff, isomorphic, to_isomorphic
from rdflib.namespace import OWL

import run_cq_tests as cq_runner
import validate_benchmark as benchmark_validator


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
CANDIDATE_MANIFEST = REPAIR_DIR / "repair-candidates.csv"
BASELINE = PROJECT_DIR / "benchmark" / "clean" / "insurance-validation-baseline.owl"
OUTPUT_DIR = PROJECT_DIR / "output"
RESULT_CSV = OUTPUT_DIR / "repair-validation.csv"
RESULT_JSON = OUTPUT_DIR / "repair-validation.json"
LOG_FILE = OUTPUT_DIR / "repair-validation.log"


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="xml")
    return graph


def strip_ontology_metadata(source: Graph) -> Graph:
    """去掉实验文档身份后比较业务公理；该比较只用于合成基准评估。"""
    result = Graph()
    ontology_nodes = set(source.subjects(RDF.type, OWL.Ontology))
    for triple in source:
        subject, predicate, _ = triple
        if subject in ontology_nodes or predicate == OWL.versionIRI:
            continue
        result.add(triple)
    return result


def graph_delta(source: Graph, target: Graph) -> tuple[int, int]:
    left = strip_ontology_metadata(source)
    right = strip_ontology_metadata(target)
    _, only_left, only_right = graph_diff(to_isomorphic(left), to_isomorphic(right))
    return len(only_left), len(only_right)


def run_independent_cqs(graph: Graph, cqs: list[dict[str, str]]) -> tuple[str, list[str]]:
    failed: list[str] = []
    for cq in cqs:
        passed, _, _ = cq_runner.evaluate_cq(graph, cq)
        if not passed:
            failed.append(cq["cq_id"])
    return ("PASS" if not failed else "FAIL"), failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证有限算子修复候选")
    parser.add_argument("--timeout", type=int, default=120, help="单个候选的HermiT超时秒数")
    parser.add_argument("--max-operations", type=int, default=1, help="最小修改允许的最大逻辑操作数")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for path in (CANDIDATE_MANIFEST, BASELINE, cq_runner.CQ_FILE):
        if not path.is_file():
            print(f"缺少文件：{path}")
            return 2

    candidates = load_csv(CANDIDATE_MANIFEST)
    cqs = load_csv(cq_runner.CQ_FILE)
    evidence_rows = benchmark_validator.current_evidence_rows(
        benchmark_validator.choose_evidence()
    )
    baseline_graph = load_graph(BASELINE)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    log: list[str] = ["有限算子修复候选：三重门禁验证"]

    print(log[0])
    for row in candidates:
        candidate_path = Path(row["candidate_owl"])
        candidate_json_path = Path(row["candidate_json"])
        source_path = Path(row["source_mutant"])
        for path in (candidate_path, candidate_json_path, source_path):
            if not path.is_file():
                print(f"缺少候选相关文件：{path}")
                return 2

        with candidate_json_path.open("r", encoding="utf-8") as file:
            candidate_payload = json.load(file)
        operation_count = len(candidate_payload["operations"])
        candidate_graph = load_graph(candidate_path)
        source_graph = load_graph(source_path)

        reasoner = benchmark_validator.run_reasoner(candidate_path, args.timeout)
        evidence_status, _, evidence_differences = benchmark_validator.validate_evidence_and_cq(
            candidate_graph, evidence_rows
        )
        cq_status, failed_cqs = run_independent_cqs(candidate_graph, cqs)
        removed_count, added_count = graph_delta(source_graph, candidate_graph)
        baseline_equal = isomorphic(
            strip_ontology_metadata(candidate_graph),
            strip_ontology_metadata(baseline_graph),
        )
        minimal = operation_count <= args.max_operations

        gates = {
            "reasoner_gate": reasoner["status"] == "CONSISTENT",
            "evidence_gate": evidence_status == "PASS",
            "cq_gate": cq_status == "PASS",
            "minimal_edit_gate": minimal,
            "synthetic_oracle_gate": baseline_equal,
        }
        overall = "PASS" if all(gates.values()) else "FAIL"
        result: dict[str, object] = {
            "candidate_id": row["candidate_id"],
            "error_id": row["error_id"],
            "error_type": row["error_type"],
            "candidate_owl": str(candidate_path),
            "operators": row["operators"],
            "operation_count": operation_count,
            "triples_removed": removed_count,
            "triples_added": added_count,
            "reasoner_result": reasoner["status"],
            "reasoner_runtime_ms": reasoner["runtime_ms"],
            "evidence_result": evidence_status,
            "cq_result": cq_status,
            "failed_cqs": " | ".join(failed_cqs),
            "evidence_differences": "；".join(evidence_differences),
            "minimal_edit": minimal,
            "equals_clean_baseline": baseline_equal,
            **gates,
            "pass_or_fail": overall,
        }
        results.append(result)
        message = (
            f"[{row['error_id']}] Reasoner={reasoner['status']} | "
            f"证据={evidence_status} | CQ={cq_status} | "
            f"操作数={operation_count} | 基线同构={baseline_equal} | {overall}"
        )
        print(message)
        log.append(message)
        if reasoner["status"] == "ERROR":
            detail = f"  Reasoner错误：{reasoner['message']}"
            print(detail)
            log.append(detail)

    with RESULT_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    with RESULT_JSON.open("w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=2)

    passed = sum(row["pass_or_fail"] == "PASS" for row in results)
    summary = f"修复验证汇总：{passed}/{len(results)} 通过"
    print(f"\n{summary}")
    print(f"CSV：{RESULT_CSV}")
    print(f"JSON：{RESULT_JSON}")
    log.extend(["", summary, f"CSV：{RESULT_CSV}", f"JSON：{RESULT_JSON}"])
    with LOG_FILE.open("w", encoding="utf-8") as file:
        file.write("\n".join(log) + "\n")
    print(f"日志：{LOG_FILE}")

    if passed == len(results):
        print("\n[修复实验通过] E1-E5均以最小有限算子恢复，并通过三重门禁。")
        return 0
    print("\n[修复实验未通过] 请查看repair-validation.csv。")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[修复验证停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
