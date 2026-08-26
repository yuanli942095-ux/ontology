from __future__ import annotations

r"""
对符号枚举候选做反事实执行，并标注执行后的可验证效果。

默认运行：
    python .\src\annotate_candidate_effects.py

输入：benchmark\repairs\feasible-candidates.json
输出：
    benchmark\repairs\candidate-effects.json
    benchmark\repairs\candidate-effects.csv
    benchmark\repairs\candidate-effects\<EID>\<CAND_ID>.owl
    output\candidate-effect-annotation.log
"""

import argparse
import csv
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from rdflib import Graph, RDF, URIRef
    from rdflib.compare import to_canonical_graph
    from rdflib.namespace import OWL
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)

try:
    import run_cq_tests as cq_runner
    import validate_benchmark as benchmark_validator
    from repair_operators import apply_operations, spec_to_term
except ImportError as exc:
    print(
        "缺少项目模块。请把本文件放入 ontology-evolution\\src，并确认 "
        "run_cq_tests.py、validate_benchmark.py、repair_operators.py 位于同一目录。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
DEFAULT_CATALOG = REPAIR_DIR / "feasible-candidates.json"
DEFAULT_JSON = REPAIR_DIR / "candidate-effects.json"
DEFAULT_CSV = REPAIR_DIR / "candidate-effects.csv"
DEFAULT_OWL_ROOT = REPAIR_DIR / "candidate-effects"
OUTPUT_DIR = PROJECT_DIR / "output"
DEFAULT_LOG = OUTPUT_DIR / "candidate-effect-annotation.log"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/insurance-benchmark/candidate-effect"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON顶层必须是对象：{path}")
    return value


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


def parse_only(text: str) -> set[str]:
    return {item.strip().upper() for item in text.split(",") if item.strip()}


def resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_DIR / path


def strip_ontology_metadata(source: Graph) -> Graph:
    result = Graph()
    for prefix, namespace in source.namespaces():
        result.bind(prefix, namespace)
    ontology_subjects = set(source.subjects(RDF.type, OWL.Ontology))
    for subject, predicate, obj in source:
        if subject in ontology_subjects or predicate == OWL.versionIRI:
            continue
        result.add((subject, predicate, obj))
    return result


def graph_delta(source: Graph, target: Graph) -> tuple[int, int]:
    source_set = set(to_canonical_graph(strip_ontology_metadata(source)))
    target_set = set(to_canonical_graph(strip_ontology_metadata(target)))
    return len(source_set - target_set), len(target_set - source_set)


def expected_delta(operation: dict[str, Any], source: Graph) -> tuple[int, int]:
    operator = operation["operator"]
    if operator in {"ADD_PROPERTY_VALUE", "ADD_CLASS_ASSERTION"}:
        return 0, 1
    if operator in {"REMOVE_PROPERTY_VALUE", "REMOVE_CLASS_ASSERTION"}:
        return 1, 0
    if operator == "REPLACE_PROPERTY_VALUE":
        subject = URIRef(operation["subject_iri"])
        predicate = URIRef(operation["predicate_iri"])
        new_spec = operation["new_value"]
        new_term = spec_to_term(
            source,
            new_spec,
            require_existing_iri=new_spec.get("kind") == "iri",
        )
        return (1, 0) if (subject, predicate, new_term) in source else (1, 1)
    raise ValueError(f"未知算子：{operator}")


def set_ontology_identity(graph: Graph, error_id: str, candidate_id: str) -> str:
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(f"{ONTOLOGY_BASE}/{error_id}/{candidate_id}")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    return str(ontology_iri)


def evaluate_cqs(
    graph: Graph,
    cqs: list[dict[str, str]],
) -> tuple[str, list[str], list[dict[str, Any]]]:
    failed: list[str] = []
    details: list[dict[str, Any]] = []
    for cq in cqs:
        passed, actual, expected = cq_runner.evaluate_cq(graph, cq)
        if not passed:
            failed.append(cq["cq_id"])
        details.append({
            "cq_id": cq["cq_id"],
            "question": cq["question"],
            "query_type": cq["query_type"],
            "expected": expected,
            "actual_values": sorted(actual),
            "passed": passed,
        })
    return ("PASS" if not failed else "FAIL"), failed, details


def annotate_candidate(
    error: dict[str, Any],
    candidate: dict[str, Any],
    source_graph: Graph,
    all_cqs: list[dict[str, str]],
    evidence_rows: list[dict[str, str]],
    owl_root: Path,
    timeout: int,
) -> dict[str, Any]:
    error_id = str(error["error_id"])
    candidate_id = str(candidate["candidate_id"])
    operation = deepcopy(candidate["operation"])
    failed_ids = {
        str(item["cq_id"])
        for item in error.get("failed_cqs", [])
        if item.get("cq_id")
    }
    affected_cqs = [cq for cq in all_cqs if cq["cq_id"] in failed_ids]
    if not affected_cqs:
        raise RuntimeError(f"{error_id}/{candidate_id}没有可定位的失败CQ")

    owl_path = owl_root / error_id / f"{candidate_id}.owl"
    owl_path.parent.mkdir(parents=True, exist_ok=True)
    effect: dict[str, Any] = {
        "execution_pass": False,
        "reasoner_result": "NOT_RUN",
        "reasoner_runtime_ms": 0,
        "reasoner_gate": False,
        "evidence_result": "NOT_RUN",
        "evidence_gate": False,
        "evidence_differences": [],
        "affected_cq_result": "NOT_RUN",
        "affected_failed_cqs": [],
        "affected_cq_gate": False,
        "all_cq_result": "NOT_RUN",
        "all_failed_cqs": [],
        "all_cq_gate": False,
        "post_state": [],
        "triples_removed": 0,
        "triples_added": 0,
        "expected_triples_removed": 0,
        "expected_triples_added": 0,
        "minimal_edit_gate": False,
        "hard_gate_pass": False,
        "candidate_owl": str(owl_path.resolve()),
        "ontology_iri": "",
        "error_message": "",
    }
    try:
        repaired = apply_operations(source_graph, [operation])
        effect["execution_pass"] = True
        ontology_iri = set_ontology_identity(repaired, error_id, candidate_id)
        repaired.serialize(destination=owl_path, format="xml", encoding="utf-8")
        effect["ontology_iri"] = ontology_iri

        reasoner = benchmark_validator.run_reasoner(owl_path, timeout)
        evidence_status, _, evidence_differences = (
            benchmark_validator.validate_evidence_and_cq(repaired, evidence_rows)
        )
        affected_status, affected_failed, post_state = evaluate_cqs(
            repaired, affected_cqs
        )
        all_status, all_failed, _ = evaluate_cqs(repaired, all_cqs)
        removed, added = graph_delta(source_graph, repaired)
        expected_removed, expected_added = expected_delta(operation, source_graph)

        effect.update({
            "reasoner_result": reasoner["status"],
            "reasoner_runtime_ms": reasoner["runtime_ms"],
            "reasoner_message": reasoner.get("message", ""),
            "reasoner_gate": reasoner["status"] == "CONSISTENT",
            "evidence_result": evidence_status,
            "evidence_gate": evidence_status == "PASS",
            "evidence_differences": evidence_differences,
            "affected_cq_result": affected_status,
            "affected_failed_cqs": affected_failed,
            "affected_cq_gate": affected_status == "PASS",
            "all_cq_result": all_status,
            "all_failed_cqs": all_failed,
            "all_cq_gate": all_status == "PASS",
            "post_state": post_state,
            "triples_removed": removed,
            "triples_added": added,
            "expected_triples_removed": expected_removed,
            "expected_triples_added": expected_added,
            "minimal_edit_gate": (removed, added) == (expected_removed, expected_added),
        })
        effect["hard_gate_pass"] = all(
            effect[name]
            for name in (
                "execution_pass",
                "reasoner_gate",
                "evidence_gate",
                "affected_cq_gate",
                "all_cq_gate",
                "minimal_edit_gate",
            )
        )
    except Exception as exc:
        effect["error_message"] = f"{type(exc).__name__}: {exc}"
    return effect


def csv_rows(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for error in errors:
        for candidate in error["candidates"]:
            effect = candidate["effect"]
            rows.append({
                "error_id": error["error_id"],
                "error_type": error["error_type"],
                "candidate_id": candidate["candidate_id"],
                "operator": candidate["operator"],
                "description": candidate["description"],
                "execution_pass": effect["execution_pass"],
                "reasoner_result": effect["reasoner_result"],
                "reasoner_gate": effect["reasoner_gate"],
                "evidence_result": effect["evidence_result"],
                "evidence_gate": effect["evidence_gate"],
                "affected_cq_result": effect["affected_cq_result"],
                "affected_failed_cqs": " | ".join(effect["affected_failed_cqs"]),
                "all_cq_result": effect["all_cq_result"],
                "all_failed_cqs": " | ".join(effect["all_failed_cqs"]),
                "triples_removed": effect["triples_removed"],
                "triples_added": effect["triples_added"],
                "minimal_edit_gate": effect["minimal_edit_gate"],
                "hard_gate_pass": effect["hard_gate_pass"],
                "post_state_json": json.dumps(effect["post_state"], ensure_ascii=False),
                "candidate_owl": effect["candidate_owl"],
                "error_message": effect["error_message"],
            })
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError("没有候选效果可写入")
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="反事实执行并标注符号OWL修复候选")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--owl-root", type=Path, default=DEFAULT_OWL_ROOT)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--only", default="", help="只处理指定错误，如 E1,E3")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.catalog.is_file():
        raise FileNotFoundError(args.catalog)
    if args.timeout < 1:
        raise ValueError("--timeout必须大于0")
    catalog = load_json(args.catalog)
    errors = catalog.get("errors")
    if not isinstance(errors, list) or not errors:
        raise RuntimeError("候选目录没有errors数组")
    selected = parse_only(args.only)
    if selected:
        known = {str(error["error_id"]).upper() for error in errors}
        unknown = selected - known
        if unknown:
            raise ValueError(f"未知错误编号：{sorted(unknown)}")
        errors = [error for error in errors if str(error["error_id"]).upper() in selected]

    all_cqs = load_csv(cq_runner.CQ_FILE)
    evidence_rows = benchmark_validator.current_evidence_rows(
        benchmark_validator.choose_evidence()
    )
    output_errors: list[dict[str, Any]] = []
    total = sum(len(error.get("candidates", [])) for error in errors)
    current = 0
    print("符号候选反事实执行与效果标注", flush=True)
    logs = [
        "符号候选反事实执行与效果标注日志",
        f"输入目录：{args.catalog.resolve()}",
        f"候选总数：{total}",
        "硬门禁：可执行 + Reasoner一致 + 当前证据匹配 + 受影响CQ通过 + 全部CQ通过 + 单步最小修改",
        "",
    ]
    for source_error in errors:
        error = deepcopy(source_error)
        source_path = resolve_project_path(str(error["source_mutant"]))
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        source_graph = load_graph(source_path)
        candidates = error.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise RuntimeError(f"{error['error_id']}没有候选")
        for candidate in candidates:
            current += 1
            effect = annotate_candidate(
                error,
                candidate,
                source_graph,
                all_cqs,
                evidence_rows,
                args.owl_root,
                args.timeout,
            )
            candidate["effect"] = effect
            state = "SURVIVE" if effect["hard_gate_pass"] else "FILTER_OUT"
            line = (
                f"[{current}/{total}] {error['error_id']}/{candidate['candidate_id']} | "
                f"Reasoner={effect['reasoner_result']} | 证据={effect['evidence_result']} | "
                f"CQ={effect['all_cq_result']} | 最小修改={effect['minimal_edit_gate']} | {state}"
            )
            print(line, flush=True)
            logs.append(line)
        output_errors.append(error)

    rows = csv_rows(output_errors)
    for path in (args.output_json, args.output_csv, args.log):
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "annotate_candidate_effects.py",
        "source_catalog": str(args.catalog.resolve()),
        "candidate_semantics": "counterfactually_executed_and_hard_gate_annotated",
        "hard_gate_definition": [
            "execution_pass",
            "reasoner_gate",
            "evidence_gate",
            "affected_cq_gate",
            "all_cq_gate",
            "minimal_edit_gate",
        ],
        "errors": output_errors,
    }
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_csv(args.output_csv, rows)
    survived = sum(row["hard_gate_pass"] for row in rows)
    logs.extend([
        "",
        f"通过硬门禁：{survived}/{len(rows)}",
        f"JSON：{args.output_json}",
        f"CSV：{args.output_csv}",
    ])
    args.log.write_text("\n".join(logs) + "\n", encoding="utf-8")
    print(f"\n效果标注完成：硬门禁通过={survived}/{len(rows)}")
    print(f"JSON：{args.output_json}")
    print(f"CSV：{args.output_csv}")
    print(f"日志：{args.log}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已中断] 用户终止候选效果标注。")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[候选效果标注停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
