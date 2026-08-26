from __future__ import annotations

r"""
比较“确定性有限算子基线”和“本地Qwen受限候选”的修复效果。

Qwen候选必须经过与基线一致的门禁：
    HermiT一致性 + 当前有效证据 + 独立CQ + 最小修改 + 合成基准同构

默认运行：
    python .\src\compare_repair_methods.py
"""

import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from rdflib import Graph
    from rdflib.compare import isomorphic
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)

try:
    import run_cq_tests as cq_runner
    import validate_benchmark as benchmark_validator
    import validate_repairs as repair_validator
    from repair_operators import operation_key
except ImportError as exc:
    print(
        "缺少现有项目模块。请把本文件放到 ontology-evolution\\src，"
        "并确认 run_cq_tests.py、validate_benchmark.py、validate_repairs.py、"
        "repair_operators.py 均在同一目录。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_QWEN_MANIFEST = PROJECT_DIR / "benchmark" / "repairs" / "qwen-repair-candidates.csv"
DEFAULT_BASELINE_RESULTS = PROJECT_DIR / "output" / "repair-validation.csv"
BASELINE_OWL = PROJECT_DIR / "benchmark" / "clean" / "insurance-validation-baseline.owl"
OUTPUT_DIR = PROJECT_DIR / "output"
QWEN_VALIDATION_CSV = OUTPUT_DIR / "qwen-repair-validation.csv"
QWEN_VALIDATION_JSON = OUTPUT_DIR / "qwen-repair-validation.json"
COMPARISON_CSV = OUTPUT_DIR / "repair-method-comparison.csv"
COMPARISON_JSON = OUTPUT_DIR / "repair-method-comparison.json"
BY_ERROR_CSV = OUTPUT_DIR / "repair-method-comparison-by-error.csv"
LOG_FILE = OUTPUT_DIR / "repair-method-comparison.log"


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


def resolve_record_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_DIR / path


def rate(count: int, total: int) -> float:
    return count / total if total else 0.0


def mean_or_blank(values: Iterable[float | int | None]) -> float | str:
    cleaned = [float(value) for value in values if value is not None]
    return round(statistics.fmean(cleaned), 3) if cleaned else ""


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    ) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def operation_signature(operations_json: str) -> str:
    try:
        operations = json.loads(operations_json or "[]")
    except json.JSONDecodeError:
        return "INVALID_JSON"
    if not isinstance(operations, list):
        return "INVALID_JSON"
    try:
        return " || ".join(sorted(operation_key(operation) for operation in operations))
    except Exception:
        return "INVALID_OPERATION"


def run_qwen_validation(
    rows: list[dict[str, str]], timeout: int, max_operations: int
) -> list[dict[str, Any]]:
    if not BASELINE_OWL.is_file():
        raise FileNotFoundError(f"缺少干净基线：{BASELINE_OWL}")
    if not cq_runner.CQ_FILE.is_file():
        raise FileNotFoundError(f"缺少独立CQ文件：{cq_runner.CQ_FILE}")
    baseline_graph = load_graph(BASELINE_OWL)
    cqs = load_csv(cq_runner.CQ_FILE)
    evidence_rows = benchmark_validator.current_evidence_rows(
        benchmark_validator.choose_evidence()
    )
    results: list[dict[str, Any]] = []
    print("Qwen候选：统一五重门禁验证")

    for index, row in enumerate(rows, start=1):
        accepted = as_bool(row.get("accepted", "false"))
        result: dict[str, Any] = {
            "method": "qwen_constrained",
            "candidate_id": row.get("candidate_id", f"qwen-row-{index:03d}"),
            "error_id": row.get("error_id", ""),
            "error_type": row.get("error_type", ""),
            "run_id": row.get("run_id", ""),
            "seed": row.get("seed", ""),
            "model": row.get("model", ""),
            "prompt_mode": row.get("prompt_mode", ""),
            "source_mutant": row.get("source_mutant", ""),
            "candidate_owl": row.get("candidate_owl", ""),
            "operators": row.get("operators", ""),
            "operations_json": row.get("operations_json", "[]"),
            "operation_signature": operation_signature(row.get("operations_json", "[]")),
            "operation_count": as_int(row.get("operation_count", 0)),
            "json_parse_pass": as_bool(row.get("json_parse_pass", "false")),
            "schema_pass": as_bool(row.get("schema_pass", "false")),
            "iri_guard_pass": as_bool(row.get("iri_guard_pass", "false")),
            "literal_guard_pass": as_bool(row.get("literal_guard_pass", "false")),
            "apply_pass": as_bool(row.get("apply_pass", "false")),
            "accepted": accepted,
            "abstained": as_bool(row.get("abstained", "false")),
            "confidence": as_float(row.get("confidence", "")),
            "llm_runtime_ms": as_int(row.get("llm_runtime_ms", 0)),
            "generation_error": row.get("error_message", ""),
            "reasoner_result": "NOT_RUN",
            "reasoner_runtime_ms": 0,
            "evidence_result": "NOT_RUN",
            "cq_result": "NOT_RUN",
            "triples_removed": 0,
            "triples_added": 0,
            "failed_cqs": "",
            "evidence_differences": "",
            "minimal_edit": False,
            "equals_clean_baseline": False,
            "reasoner_gate": False,
            "evidence_gate": False,
            "cq_gate": False,
            "minimal_edit_gate": False,
            "synthetic_oracle_gate": False,
            "repair_success": False,
            "validation_error": "",
        }

        if not accepted:
            result["validation_error"] = row.get("error_message", "候选未通过生成门禁")
            results.append(result)
            print(
                f"[{index}/{len(rows)}] {result['error_id']} run={result['run_id']} "
                f"| 候选未接受 | REPAIR_FAIL"
            )
            continue

        try:
            candidate_path = resolve_record_path(row["candidate_owl"])
            source_path = resolve_record_path(row["source_mutant"])
            for path in (candidate_path, source_path):
                if not path.is_file():
                    raise FileNotFoundError(path)
            candidate_graph = load_graph(candidate_path)
            source_graph = load_graph(source_path)
            reasoner = benchmark_validator.run_reasoner(candidate_path, timeout)
            evidence_status, _, evidence_differences = (
                benchmark_validator.validate_evidence_and_cq(candidate_graph, evidence_rows)
            )
            cq_status, failed_cqs = repair_validator.run_independent_cqs(candidate_graph, cqs)
            minimal = result["operation_count"] <= max_operations
            baseline_equal = isomorphic(
                repair_validator.strip_ontology_metadata(candidate_graph),
                repair_validator.strip_ontology_metadata(baseline_graph),
            )
            removed_count, added_count = repair_validator.graph_delta(source_graph, candidate_graph)
            result.update({
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
                "reasoner_gate": reasoner["status"] == "CONSISTENT",
                "evidence_gate": evidence_status == "PASS",
                "cq_gate": cq_status == "PASS",
                "minimal_edit_gate": minimal,
                "synthetic_oracle_gate": baseline_equal,
            })
            result["repair_success"] = all(
                result[name]
                for name in (
                    "reasoner_gate",
                    "evidence_gate",
                    "cq_gate",
                    "minimal_edit_gate",
                    "synthetic_oracle_gate",
                )
            )
        except Exception as exc:
            result["validation_error"] = f"{type(exc).__name__}: {exc}"
        results.append(result)
        print(
            f"[{index}/{len(rows)}] {result['error_id']} run={result['run_id']} | "
            f"Reasoner={result['reasoner_result']} | 证据={result['evidence_result']} | "
            f"CQ={result['cq_result']} | 同构={result['equals_clean_baseline']} | "
            f"{'REPAIR_PASS' if result['repair_success'] else 'REPAIR_FAIL'}"
        )
    return results


def deterministic_attempts(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for row in rows:
        success = row.get("pass_or_fail", "").strip().upper() == "PASS"
        operations = row.get("operators", "")
        attempts.append({
            "method": "deterministic_finite_operator",
            "candidate_id": row.get("candidate_id", ""),
            "error_id": row.get("error_id", ""),
            "error_type": row.get("error_type", ""),
            "operation_signature": operations,
            "operation_count": as_int(row.get("operation_count", 0)),
            "json_parse_pass": True,
            "schema_pass": True,
            "iri_guard_pass": True,
            "literal_guard_pass": True,
            "apply_pass": True,
            "accepted": True,
            "abstained": False,
            "confidence": None,
            "llm_runtime_ms": None,
            "reasoner_gate": as_bool(row.get("reasoner_gate", row.get("reasoner_result") == "CONSISTENT")),
            "evidence_gate": as_bool(row.get("evidence_gate", row.get("evidence_result") == "PASS")),
            "cq_gate": as_bool(row.get("cq_gate", row.get("cq_result") == "PASS")),
            "minimal_edit_gate": as_bool(row.get("minimal_edit_gate", row.get("minimal_edit", "false"))),
            "synthetic_oracle_gate": as_bool(
                row.get("synthetic_oracle_gate", row.get("equals_clean_baseline", "false"))
            ),
            "repair_success": success,
        })
    return attempts


def summarize_method(method: str, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(attempts)
    successes = sum(as_bool(row.get("repair_success")) for row in attempts)
    accepted = sum(as_bool(row.get("accepted")) for row in attempts)
    parsed = sum(as_bool(row.get("json_parse_pass")) for row in attempts)
    schema = sum(as_bool(row.get("schema_pass")) for row in attempts)
    iri_guard = sum(as_bool(row.get("iri_guard_pass")) for row in attempts)
    literal_guard = sum(as_bool(row.get("literal_guard_pass")) for row in attempts)
    applied = sum(as_bool(row.get("apply_pass")) for row in attempts)
    abstained = sum(as_bool(row.get("abstained")) for row in attempts)
    low, high = wilson_interval(successes, total)

    by_error: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        by_error[str(row.get("error_id", ""))].append(row)
    error_rates = [
        rate(sum(as_bool(row.get("repair_success")) for row in group), len(group))
        for group in by_error.values()
    ]
    consensus_values: list[float] = []
    for group in by_error.values():
        signatures = [
            str(row.get("operation_signature", ""))
            for row in group
            if as_bool(row.get("json_parse_pass")) and str(row.get("operation_signature", ""))
        ]
        if signatures:
            consensus_values.append(max(Counter(signatures).values()) / len(signatures))

    return {
        "method": method,
        "attempts": total,
        "errors_covered": len(by_error),
        "accepted_candidates": accepted,
        "repair_successes": successes,
        "json_parse_rate": round(rate(parsed, total), 6),
        "schema_pass_rate": round(rate(schema, total), 6),
        "iri_guard_pass_rate": round(rate(iri_guard, total), 6),
        "literal_guard_pass_rate": round(rate(literal_guard, total), 6),
        "apply_pass_rate": round(rate(applied, total), 6),
        "abstention_rate": round(rate(abstained, total), 6),
        "accepted_candidate_rate": round(rate(accepted, total), 6),
        "reasoner_gate_rate": round(
            rate(sum(as_bool(row.get("reasoner_gate")) for row in attempts), total), 6
        ),
        "evidence_gate_rate": round(
            rate(sum(as_bool(row.get("evidence_gate")) for row in attempts), total), 6
        ),
        "cq_gate_rate": round(
            rate(sum(as_bool(row.get("cq_gate")) for row in attempts), total), 6
        ),
        "minimal_edit_gate_rate": round(
            rate(sum(as_bool(row.get("minimal_edit_gate")) for row in attempts), total), 6
        ),
        "synthetic_oracle_gate_rate": round(
            rate(sum(as_bool(row.get("synthetic_oracle_gate")) for row in attempts), total), 6
        ),
        "repair_success_rate": round(rate(successes, total), 6),
        "repair_success_ci95_low": round(low, 6),
        "repair_success_ci95_high": round(high, 6),
        "conditional_success_given_accepted": round(rate(successes, accepted), 6),
        "macro_error_success_rate": round(statistics.fmean(error_rates), 6) if error_rates else 0.0,
        "mean_operation_count": mean_or_blank(
            as_int(row.get("operation_count", 0)) for row in attempts if as_bool(row.get("accepted"))
        ),
        "mean_generation_runtime_ms": mean_or_blank(
            as_float(row.get("llm_runtime_ms")) for row in attempts
        ),
        "mean_confidence": mean_or_blank(as_float(row.get("confidence")) for row in attempts),
        "mean_operation_consensus": round(statistics.fmean(consensus_values), 6)
        if consensus_values else "",
    }


def compare_by_error(
    deterministic: list[dict[str, Any]], qwen: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    det_by_error: dict[str, list[dict[str, Any]]] = defaultdict(list)
    qwen_by_error: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in deterministic:
        det_by_error[str(row.get("error_id", ""))].append(row)
    for row in qwen:
        qwen_by_error[str(row.get("error_id", ""))].append(row)
    result: list[dict[str, Any]] = []
    for error_id in sorted(set(det_by_error) | set(qwen_by_error)):
        det_group = det_by_error.get(error_id, [])
        qwen_group = qwen_by_error.get(error_id, [])
        det_success = sum(as_bool(row.get("repair_success")) for row in det_group)
        qwen_success = sum(as_bool(row.get("repair_success")) for row in qwen_group)
        accepted = sum(as_bool(row.get("accepted")) for row in qwen_group)
        signatures = [
            str(row.get("operation_signature", ""))
            for row in qwen_group
            if as_bool(row.get("json_parse_pass")) and str(row.get("operation_signature", ""))
        ]
        consensus = max(Counter(signatures).values()) / len(signatures) if signatures else 0.0
        result.append({
            "error_id": error_id,
            "error_type": (qwen_group or det_group)[0].get("error_type", ""),
            "deterministic_attempts": len(det_group),
            "deterministic_successes": det_success,
            "deterministic_success_rate": round(rate(det_success, len(det_group)), 6),
            "qwen_attempts": len(qwen_group),
            "qwen_accepted": accepted,
            "qwen_successes": qwen_success,
            "qwen_success_rate": round(rate(qwen_success, len(qwen_group)), 6),
            "qwen_conditional_success": round(rate(qwen_success, accepted), 6),
            "qwen_operation_consensus": round(consensus, 6),
            "success_rate_delta_qwen_minus_baseline": round(
                rate(qwen_success, len(qwen_group)) - rate(det_success, len(det_group)), 6
            ),
        })
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比较确定性有限算子与本地Qwen修复方法")
    parser.add_argument(
        "--qwen-manifest", type=Path, default=DEFAULT_QWEN_MANIFEST, help="Qwen候选清单"
    )
    parser.add_argument(
        "--baseline-results",
        type=Path,
        default=DEFAULT_BASELINE_RESULTS,
        help="确定性有限算子验证结果",
    )
    parser.add_argument("--timeout", type=int, default=120, help="单个候选HermiT超时秒数")
    parser.add_argument("--max-operations", type=int, default=1, help="最小修改最大操作数")
    return parser.parse_args()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"没有可写入的数据：{path}")
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    for path in (args.qwen_manifest, args.baseline_results):
        if not path.is_file():
            print(f"缺少文件：{path}")
            if path == args.qwen_manifest:
                print("请先运行 python .\\src\\qwen_repair_candidates.py")
            else:
                print("请先运行 python .\\src\\validate_repairs.py")
            return 2
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    qwen_rows = load_csv(args.qwen_manifest)
    baseline_rows = load_csv(args.baseline_results)
    qwen_results = run_qwen_validation(qwen_rows, args.timeout, args.max_operations)
    deterministic = deterministic_attempts(baseline_rows)
    summaries = [
        summarize_method("deterministic_finite_operator", deterministic),
        summarize_method("qwen_constrained", qwen_results),
    ]
    by_error = compare_by_error(deterministic, qwen_results)

    write_csv(QWEN_VALIDATION_CSV, qwen_results)
    with QWEN_VALIDATION_JSON.open("w", encoding="utf-8") as file:
        json.dump(qwen_results, file, ensure_ascii=False, indent=2)
    write_csv(COMPARISON_CSV, summaries)
    write_csv(BY_ERROR_CSV, by_error)

    det = summaries[0]
    qwen = summaries[1]
    payload = {
        "method_summaries": summaries,
        "by_error": by_error,
        "headline": {
            "deterministic_repair_success_rate": det["repair_success_rate"],
            "qwen_repair_success_rate": qwen["repair_success_rate"],
            "delta_qwen_minus_deterministic": round(
                float(qwen["repair_success_rate"]) - float(det["repair_success_rate"]), 6
            ),
            "qwen_success_ci95": [
                qwen["repair_success_ci95_low"],
                qwen["repair_success_ci95_high"],
            ],
            "interpretation_warning": (
                "该结果仅针对当前五类合成错误；不能外推为真实跨领域本体的总体修复率。"
            ),
        },
    }
    with COMPARISON_JSON.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    log = [
        "本体修复方法对比实验日志",
        "",
        (
            "确定性有限算子："
            f"成功={det['repair_successes']}/{det['attempts']}，"
            f"成功率={det['repair_success_rate']:.2%}"
        ),
        (
            "受限Qwen："
            f"成功={qwen['repair_successes']}/{qwen['attempts']}，"
            f"成功率={qwen['repair_success_rate']:.2%}，"
            f"95%CI=[{qwen['repair_success_ci95_low']:.2%}, "
            f"{qwen['repair_success_ci95_high']:.2%}]"
        ),
        f"Qwen候选接受率={qwen['accepted_candidate_rate']:.2%}",
        f"Qwen JSON可解析率={qwen['json_parse_rate']:.2%}",
        f"Qwen IRI白名单通过率={qwen['iri_guard_pass_rate']:.2%}",
        f"Qwen 字面量白名单通过率={qwen['literal_guard_pass_rate']:.2%}",
        f"Qwen 候选接受后的条件成功率={qwen['conditional_success_given_accepted']:.2%}",
        "",
        "按错误类型：",
    ]
    for row in by_error:
        log.append(
            f"[{row['error_id']}] 基线={row['deterministic_success_rate']:.2%} | "
            f"Qwen={row['qwen_successes']}/{row['qwen_attempts']} "
            f"({row['qwen_success_rate']:.2%}) | "
            f"操作一致率={row['qwen_operation_consensus']:.2%}"
        )
    log.extend([
        "",
        "解释边界：当前结果只证明五类合成错误上的受限候选生成与验证能力。",
        "发表级结论还需要更多真实变化事件、多个领域、专家盲评和消融实验。",
    ])
    LOG_FILE.write_text("\n".join(log) + "\n", encoding="utf-8")

    print("\n" + "\n".join(log[:8]))
    print(f"\nQwen逐次验证：{QWEN_VALIDATION_CSV}")
    print(f"方法汇总：{COMPARISON_CSV}")
    print(f"按错误类型：{BY_ERROR_CSV}")
    print(f"JSON汇总：{COMPARISON_JSON}")
    print(f"日志：{LOG_FILE}")
    print("\n[对比实验完成] 请同时报告成功率、95%置信区间和失败/拒绝案例。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已中断] 用户终止了对比实验。")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[对比实验停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
