from __future__ import annotations

r"""
验证“符号枚举 + Qwen候选选择”产生的修复。

最终安全接受门禁：
1. 候选成员身份与操作完整性；
2. HermiT逻辑一致性；
3. 当前有效证据精确匹配；
4. 独立CQ全部通过；
5. 单步最小修改。

合成开发集额外报告与干净基线同构，但该oracle不应部署到真实场景。

默认运行：
    python .\src\validate_ranked_repairs.py
"""

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from rdflib import Graph, RDF, URIRef
    from rdflib.compare import isomorphic, to_canonical_graph
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
        "缺少项目模块。请把本文件放入 ontology-evolution\\src，"
        "并确认 run_cq_tests.py、validate_benchmark.py、repair_operators.py 均存在。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_DIR / "benchmark" / "repairs" / "qwen-ranked-candidates.csv"
DEFAULT_CATALOG = PROJECT_DIR / "benchmark" / "repairs" / "feasible-candidates.json"
BASELINE = PROJECT_DIR / "benchmark" / "clean" / "insurance-validation-baseline.owl"
OUTPUT_DIR = PROJECT_DIR / "output"
RESULT_CSV = OUTPUT_DIR / "ranked-repair-validation.csv"
RESULT_JSON = OUTPUT_DIR / "ranked-repair-validation.json"
SUMMARY_CSV = OUTPUT_DIR / "ranked-repair-summary-by-error.csv"
LOG_FILE = OUTPUT_DIR / "ranked-repair-validation.log"


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise RuntimeError(f"CSV为空：{path}")
    return rows


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON顶层必须是对象：{path}")
    return value


def load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="xml")
    return graph


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "pass"}


def as_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_float(value: object) -> float | None:
    try:
        text = str(value).strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_DIR / path


def strip_ontology_metadata(source: Graph) -> Graph:
    result = Graph()
    for prefix, namespace in source.namespaces():
        result.bind(prefix, namespace)
    ontology_subjects = set(source.subjects(RDF.type, OWL.Ontology))
    for triple in source:
        subject, predicate, obj = triple
        if subject in ontology_subjects:
            continue
        if predicate == OWL.versionIRI:
            continue
        result.add(triple)
    return result


def graph_delta(source: Graph, target: Graph) -> tuple[int, int]:
    # RDF/XML重新序列化后，匿名节点标识会变化；直接比较三元组集合会把同一
    # 个OWL列表误判成几十项修改。先进行RDF图规范化，再计算真实的增删量。
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
        # RDF是集合：若目标值在源图中已经存在，“替换”为该值只会删除旧值，
        # 不会再新增一个重复三元组（E5正是这种情况）。
        subject = URIRef(operation["subject_iri"])
        predicate = URIRef(operation["predicate_iri"])
        new_spec = operation["new_value"]
        new_term = spec_to_term(
            source,
            new_spec,
            require_existing_iri=new_spec.get("kind") == "iri",
        )
        if (subject, predicate, new_term) in source:
            return 1, 0
        return 1, 1
    raise ValueError(f"未知算子：{operator}")


def run_cqs(graph: Graph, cqs: list[dict[str, str]]) -> tuple[str, list[str]]:
    failed = [cq["cq_id"] for cq in cqs if not cq_runner.evaluate_cq(graph, cq)[0]]
    return ("PASS" if not failed else "FAIL"), failed


def canonical_operation(operation: dict[str, Any]) -> str:
    structural = {
        key: operation[key]
        for key in (
            "operator",
            "subject_iri",
            "predicate_iri",
            "class_iri",
            "old_value",
            "new_value",
        )
        if key in operation
    }
    return json.dumps(
        structural,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def catalog_index(catalog: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    errors = catalog.get("errors")
    if not isinstance(errors, list):
        raise RuntimeError("候选目录缺少errors数组")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for error in errors:
        error_id = str(error["error_id"])
        for candidate in error.get("candidates", []):
            key = (error_id, str(candidate["candidate_id"]))
            if key in result:
                raise RuntimeError(f"候选目录存在重复键：{key}")
            result[key] = candidate
    return result


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def validate_rows(
    rows: list[dict[str, str]],
    index: dict[tuple[str, str], dict[str, Any]],
    timeout: int,
) -> list[dict[str, Any]]:
    if not BASELINE.is_file():
        raise FileNotFoundError(BASELINE)
    baseline_graph = load_graph(BASELINE)
    cqs = load_csv(cq_runner.CQ_FILE)
    evidence_rows = benchmark_validator.current_evidence_rows(
        benchmark_validator.choose_evidence()
    )
    results: list[dict[str, Any]] = []
    print("符号枚举 + Qwen排序：统一安全门禁验证")

    for position, row in enumerate(rows, start=1):
        accepted = as_bool(row.get("accepted", "false"))
        abstained = as_bool(row.get("abstained", "false"))
        result: dict[str, Any] = {
            "attempt_id": row.get("attempt_id", f"row-{position:03d}"),
            "error_id": row.get("error_id", ""),
            "error_type": row.get("error_type", ""),
            "run_id": row.get("run_id", ""),
            "seed": row.get("seed", ""),
            "model": row.get("model", ""),
            "prompt_mode": row.get("prompt_mode", ""),
            "selected_candidate_id": row.get("selected_candidate_id", ""),
            "operators": row.get("operators", ""),
            "confidence": as_float(row.get("confidence", "")),
            "llm_runtime_ms": as_int(row.get("llm_runtime_ms", 0)),
            "accepted_by_ranker": accepted,
            "abstained": abstained,
            "generation_error": row.get("error_message", ""),
            "candidate_membership_gate": False,
            "candidate_integrity_gate": False,
            "reasoner_result": "NOT_RUN",
            "reasoner_runtime_ms": 0,
            "reasoner_gate": False,
            "evidence_result": "NOT_RUN",
            "evidence_gate": False,
            "cq_result": "NOT_RUN",
            "failed_cqs": "",
            "cq_gate": False,
            "triples_removed": 0,
            "triples_added": 0,
            "minimal_edit_gate": False,
            "safe_accept": False,
            "dev_oracle_match": False,
            "benchmark_success": False,
            "validation_error": "",
        }
        if not accepted:
            result["validation_error"] = (
                "模型主动ABSTAIN" if abstained else row.get("error_message", "排序候选未接受")
            )
            results.append(result)
            state = "ABSTAIN" if abstained else "RANK_REJECTED"
            print(
                f"[{position}/{len(rows)}] {result['error_id']} run={result['run_id']} | "
                f"{state} | SAFE_REJECT"
            )
            continue

        try:
            error_id = result["error_id"]
            selected_id = result["selected_candidate_id"]
            key = (error_id, selected_id)
            if key not in index:
                raise ValueError(f"选择结果不属于候选目录：{key}")
            result["candidate_membership_gate"] = True
            candidate = index[key]
            catalog_operation = candidate["operation"]
            operations = json.loads(row.get("operations_json", "[]"))
            if not isinstance(operations, list) or len(operations) != 1:
                raise ValueError("排序清单必须恰好包含一个有限修复操作")
            operation = operations[0]
            if canonical_operation(operation) != canonical_operation(catalog_operation):
                raise ValueError("清单中的操作与符号候选目录不一致")
            result["candidate_integrity_gate"] = True

            source_path = resolve_path(row["source_mutant"])
            candidate_path = resolve_path(row["candidate_owl"])
            for path in (source_path, candidate_path):
                if not path.is_file():
                    raise FileNotFoundError(path)
            source_graph = load_graph(source_path)
            candidate_graph = load_graph(candidate_path)
            reconstructed = apply_operations(source_graph, [catalog_operation])
            if not isomorphic(
                strip_ontology_metadata(reconstructed),
                strip_ontology_metadata(candidate_graph),
            ):
                raise ValueError("候选OWL与符号操作重放结果不一致")

            reasoner = benchmark_validator.run_reasoner(candidate_path, timeout)
            evidence_status, _, differences = benchmark_validator.validate_evidence_and_cq(
                candidate_graph, evidence_rows
            )
            cq_status, failed = run_cqs(candidate_graph, cqs)
            removed, added = graph_delta(source_graph, candidate_graph)
            minimal = (removed, added) == expected_delta(catalog_operation, source_graph)
            oracle = isomorphic(
                strip_ontology_metadata(candidate_graph),
                strip_ontology_metadata(baseline_graph),
            )
            result.update({
                "reasoner_result": reasoner["status"],
                "reasoner_runtime_ms": reasoner["runtime_ms"],
                "reasoner_gate": reasoner["status"] == "CONSISTENT",
                "evidence_result": evidence_status,
                "evidence_gate": evidence_status == "PASS",
                "evidence_differences": "；".join(differences),
                "cq_result": cq_status,
                "failed_cqs": " | ".join(failed),
                "cq_gate": cq_status == "PASS",
                "triples_removed": removed,
                "triples_added": added,
                "minimal_edit_gate": minimal,
                "dev_oracle_match": oracle,
            })
            result["safe_accept"] = all(
                result[name]
                for name in (
                    "candidate_membership_gate",
                    "candidate_integrity_gate",
                    "reasoner_gate",
                    "evidence_gate",
                    "cq_gate",
                    "minimal_edit_gate",
                )
            )
            result["benchmark_success"] = result["safe_accept"] and oracle
        except Exception as exc:
            result["validation_error"] = f"{type(exc).__name__}: {exc}"
        results.append(result)
        print(
            f"[{position}/{len(rows)}] {result['error_id']} run={result['run_id']} | "
            f"Reasoner={result['reasoner_result']} | 证据={result['evidence_result']} | "
            f"CQ={result['cq_result']} | 最小修改={result['minimal_edit_gate']} | "
            f"Oracle={result['dev_oracle_match']} | "
            f"{'SAFE_ACCEPT' if result['safe_accept'] else 'SAFE_REJECT'}"
        )
    return results


def summaries_by_error(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        groups[str(row["error_id"])].append(row)
    summaries: list[dict[str, Any]] = []
    for error_id in sorted(groups):
        group = groups[error_id]
        selected = sum(as_bool(row["accepted_by_ranker"]) for row in group)
        safe = sum(as_bool(row["safe_accept"]) for row in group)
        success = sum(as_bool(row["benchmark_success"]) for row in group)
        abstained = sum(as_bool(row["abstained"]) for row in group)
        summaries.append({
            "error_id": error_id,
            "error_type": group[0]["error_type"],
            "attempts": len(group),
            "selected": selected,
            "abstained": abstained,
            "safe_accepts": safe,
            "benchmark_successes": success,
            "selection_rate": round(selected / len(group), 6),
            "safe_accept_rate": round(safe / len(group), 6),
            "benchmark_success_rate": round(success / len(group), 6),
        })
    return summaries


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"没有可写入的数据：{path}")
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证符号枚举与Qwen排序产生的本体修复")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for path in (args.manifest, args.catalog, BASELINE, cq_runner.CQ_FILE):
        if not path.is_file():
            raise FileNotFoundError(path)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_csv(args.manifest)
    index = catalog_index(load_json(args.catalog))
    results = validate_rows(rows, index, args.timeout)
    summaries = summaries_by_error(results)
    write_csv(RESULT_CSV, results)
    write_csv(SUMMARY_CSV, summaries)
    RESULT_JSON.write_text(
        json.dumps(
            {"results": results, "by_error": summaries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    total = len(results)
    selected = sum(as_bool(row["accepted_by_ranker"]) for row in results)
    abstained = sum(as_bool(row["abstained"]) for row in results)
    safe = sum(as_bool(row["safe_accept"]) for row in results)
    successes = sum(as_bool(row["benchmark_success"]) for row in results)
    low, high = wilson_interval(successes, total)
    unsafe_accepted = sum(
        as_bool(row["safe_accept"]) and not as_bool(row["dev_oracle_match"])
        for row in results
    )
    mean_confidence = statistics.fmean(
        value
        for row in results
        if (value := as_float(row.get("confidence"))) is not None
    ) if any(as_float(row.get("confidence")) is not None for row in results) else 0.0
    log = [
        "符号枚举 + Qwen排序修复验证日志",
        "",
        f"总尝试：{total}",
        f"Qwen选择候选：{selected}/{total} ({selected / total:.2%})",
        f"Qwen主动ABSTAIN：{abstained}/{total} ({abstained / total:.2%})",
        f"安全门禁接受：{safe}/{total} ({safe / total:.2%})",
        f"开发集Oracle成功：{successes}/{total} ({successes / total:.2%})",
        f"Oracle成功率95%CI：[{low:.2%}, {high:.2%}]",
        f"安全接受但不匹配开发集Oracle：{unsafe_accepted}",
        f"平均模型置信度：{mean_confidence:.3f}",
        "",
        "说明：部署时不能使用干净基线Oracle；最终接受只依赖成员身份、完整性、Reasoner、证据、CQ和最小修改门禁。",
        "",
        "按错误类型：",
    ]
    for row in summaries:
        log.append(
            f"[{row['error_id']}] 选择={row['selected']}/{row['attempts']} | "
            f"安全接受={row['safe_accepts']}/{row['attempts']} | "
            f"Oracle成功={row['benchmark_successes']}/{row['attempts']}"
        )
    LOG_FILE.write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\n" + "\n".join(log[:9]))
    print(f"\n逐次结果：{RESULT_CSV}")
    print(f"按错误类型：{SUMMARY_CSV}")
    print(f"JSON：{RESULT_JSON}")
    print(f"日志：{LOG_FILE}")
    print("[验证完成] 请同时报告ABSTAIN、SAFE_REJECT、SAFE_ACCEPT与Oracle成功率。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已中断] 用户终止修复验证。")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[排序修复验证停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
