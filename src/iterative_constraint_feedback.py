from __future__ import annotations

r"""
反例驱动的LLM本体修复闭环。

同一个Qwen先从符号候选中选择；程序在本体副本上执行候选，并根据实验层级
只返回不泄露标准答案的形式化反例。模型可以在剩余候选中重新选择。

实验层级：
    candidate_only          只做候选白名单与可执行性约束
    reasoner                增加逻辑一致性反馈
    reasoner_evidence       增加证据不匹配反馈
    reasoner_evidence_cq    增加CQ与最小修改反馈

默认运行：
    python .\src\iterative_constraint_feedback.py --runs 5 --max-rounds 3
"""

import argparse
import csv
import json
import os
import random
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from rdflib import Graph, RDF, URIRef
    from rdflib.namespace import OWL
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)

try:
    import qwen_rank_survivors as qwen_io
    import run_cq_tests as cq_runner
    import validate_benchmark as benchmark_validator
    import validate_ranked_repairs as ranked_validator
    from repair_operators import apply_operations, validate_operation_schema
except ImportError as exc:
    print(
        "缺少项目模块。请确认 qwen_rank_survivors.py、run_cq_tests.py、"
        "validate_benchmark.py、validate_ranked_repairs.py 和 repair_operators.py "
        "均位于 ontology-evolution\\src。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
DEFAULT_CATALOG = REPAIR_DIR / "feasible-candidates.json"
FEEDBACK_ROOT = REPAIR_DIR / "constraint-feedback"
MANIFEST_ROOT = FEEDBACK_ROOT / "manifests"
CANDIDATE_ROOT = FEEDBACK_ROOT / "candidates"
OUTPUT_DIR = PROJECT_DIR / "output"
GENERATION_JSON = OUTPUT_DIR / "constraint-feedback-generation.json"
GENERATION_CSV = OUTPUT_DIR / "constraint-feedback-generation.csv"
GENERATION_LOG = OUTPUT_DIR / "constraint-feedback-generation.log"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/insurance-benchmark/constraint-feedback"
LEVELS = (
    "candidate_only",
    "reasoner",
    "reasoner_evidence",
    "reasoner_evidence_cq",
)


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


def parse_csv_set(text: str) -> set[str]:
    return {item.strip().lower() for item in text.split(",") if item.strip()}


def parse_only(text: str) -> set[str]:
    return {item.strip().upper() for item in text.split(",") if item.strip()}


def resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_DIR / path


def set_ontology_identity(
    graph: Graph,
    level: str,
    error_id: str,
    run_id: int,
    round_id: int,
    seed: int,
) -> str:
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(
        f"{ONTOLOGY_BASE}/{level}/{error_id}/run-{run_id:03d}/"
        f"round-{round_id:02d}/seed-{seed}"
    )
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    return str(ontology_iri)


def visible_failed_cqs(error: dict[str, Any]) -> list[dict[str, Any]]:
    # expected_answer与expected_count不进入提示，避免把标准答案伪装成反馈。
    result: list[dict[str, Any]] = []
    for cq in error.get("failed_cqs", []):
        result.append({
            "cq_id": cq.get("cq_id", ""),
            "question": cq.get("question", ""),
            "query_type": cq.get("query_type", ""),
            "subject_label": cq.get("subject_label", ""),
            "predicate_label": cq.get("predicate_label", ""),
            "current_actual_values": cq.get("actual_values", []),
            "rationale": cq.get("rationale", ""),
        })
    return result


def candidate_prompt_item(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"],
        "description": candidate["description"],
        "operator": candidate["operator"],
        "source_cq_ids": candidate.get("source_cq_ids", []),
        "source_evidence_ids": candidate.get("source_evidence_ids", []),
    }


def build_prompt(
    error: dict[str, Any],
    candidates: list[dict[str, Any]],
    feedback_history: list[dict[str, Any]],
) -> tuple[str, str]:
    system_prompt = (
        "你是受形式化约束的OWL本体修复候选选择器。"
        "只能选择给定candidate_id或严格ABSTAIN；禁止生成IRI、字面量、"
        "算子、OWL文本或新候选。形式化反馈只说明上一候选违反了什么约束，"
        "不会直接给出标准答案。请结合当前有效证据重新判断。"
        "只输出JSON对象，不要Markdown和前后说明。"
    )
    context = {
        "error_id": error["error_id"],
        "error_type": error["error_type"],
        "failed_competency_questions": visible_failed_cqs(error),
        "current_effective_evidence": error.get("evidence_context", []),
        "remaining_symbolic_candidates": [
            candidate_prompt_item(candidate) for candidate in candidates
        ],
        "previous_formal_counterexamples": feedback_history,
    }
    user_prompt = (
        "请选择最可能恢复正确业务语义的候选。严格输出：\n"
        '{"abstain": false, "candidate_id": "CAND_...", '
        '"confidence": 0.0, "rationale": "..."}\n'
        "若现有信息不足，则abstain=true且candidate_id为空字符串。"
        "不得输出额外字段。\n\n输入上下文：\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )
    return system_prompt, user_prompt


def evidence_mismatch_locations(
    graph: Graph, evidence_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    locations: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in evidence_rows:
        expected = {row["object_or_value"].strip()}
        actual = benchmark_validator.actual_values(graph, row)
        if actual == expected:
            continue
        key = (
            row.get("subject_label", ""),
            row.get("predicate_label", ""),
            row.get("axiom_kind", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        locations.append({
            "subject_label": key[0],
            "predicate_label": key[1],
            "axiom_kind": key[2],
        })
    return locations


def evaluate_candidate(
    source_graph: Graph,
    candidate_graph: Graph,
    candidate_path: Path,
    operation: dict[str, Any],
    all_cqs: list[dict[str, str]],
    evidence_rows: list[dict[str, str]],
    timeout: int,
) -> dict[str, Any]:
    reasoner = benchmark_validator.run_reasoner(candidate_path, timeout)
    evidence_status, _, differences = benchmark_validator.validate_evidence_and_cq(
        candidate_graph, evidence_rows
    )
    cq_status, failed_cqs = ranked_validator.run_cqs(candidate_graph, all_cqs)
    removed, added = ranked_validator.graph_delta(source_graph, candidate_graph)
    expected_removed, expected_added = ranked_validator.expected_delta(
        operation, source_graph
    )
    result = {
        "reasoner_result": reasoner["status"],
        "reasoner_runtime_ms": reasoner["runtime_ms"],
        "reasoner_gate": reasoner["status"] == "CONSISTENT",
        "evidence_result": evidence_status,
        "evidence_gate": evidence_status == "PASS",
        "evidence_differences_for_analysis": differences,
        "evidence_mismatch_locations": evidence_mismatch_locations(
            candidate_graph, evidence_rows
        ),
        "cq_result": cq_status,
        "cq_gate": cq_status == "PASS",
        "failed_cqs": failed_cqs,
        "triples_removed": removed,
        "triples_added": added,
        "expected_triples_removed": expected_removed,
        "expected_triples_added": expected_added,
        "minimal_edit_gate": (removed, added) == (expected_removed, expected_added),
    }
    result["full_safety_pass_for_analysis"] = all(
        result[name]
        for name in (
            "reasoner_gate",
            "evidence_gate",
            "cq_gate",
            "minimal_edit_gate",
        )
    )
    return result


def exposed_feedback(
    level: str,
    candidate: dict[str, Any],
    evaluation: dict[str, Any],
    cq_index: dict[str, dict[str, str]],
) -> tuple[bool, list[dict[str, Any]]]:
    violations: list[dict[str, Any]] = []
    if level in {"reasoner", "reasoner_evidence", "reasoner_evidence_cq"}:
        if not evaluation["reasoner_gate"]:
            violations.append({
                "code": "REASONER_INCONSISTENT",
                "message": "候选导致本体逻辑不一致。",
            })
    if level in {"reasoner_evidence", "reasoner_evidence_cq"}:
        if not evaluation["evidence_gate"]:
            violations.append({
                "code": "EVIDENCE_MISMATCH",
                "message": "候选结果与当前有效证据不匹配。",
                "affected_locations": evaluation["evidence_mismatch_locations"],
            })
    if level == "reasoner_evidence_cq":
        if not evaluation["cq_gate"]:
            failed = []
            for cq_id in evaluation["failed_cqs"]:
                cq = cq_index.get(cq_id, {})
                failed.append({
                    "cq_id": cq_id,
                    "question": cq.get("question", ""),
                })
            violations.append({
                "code": "CQ_FAILED",
                "message": "候选修复后仍有能力问题失败。",
                "failed_questions": failed,
            })
        if not evaluation["minimal_edit_gate"]:
            violations.append({
                "code": "NON_MINIMAL_EDIT",
                "message": "候选产生了超出有限算子预期的图变化。",
            })
    # candidate_only没有语义门禁，因此第一个可执行白名单候选即被该层接受。
    passed = not violations
    feedback = [{
        "rejected_candidate_id": candidate["candidate_id"],
        "violations": violations,
    }] if violations else []
    return passed, feedback


def manifest_row() -> dict[str, Any]:
    row = qwen_io.blank_row()
    row.update({
        "feedback_level": "",
        "rounds_used": 0,
        "llm_calls": 0,
        "selection_source": "",
        "candidate_count": 0,
        "first_candidate_id": "",
        "first_choice_full_safety_pass_for_analysis": "false",
        "final_full_safety_pass_for_analysis": "false",
    })
    return row


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if not rows:
        raise RuntimeError(f"没有数据可写入：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = fields or list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行反例驱动的LLM本体修复闭环")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument(
        "--levels",
        default=",".join(LEVELS),
        help="逗号分隔的实验层级",
    )
    parser.add_argument("--only", default="", help="只处理指定错误，如 E1,E3")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
    )
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--reasoner-timeout", type=int, default=120)
    parser.add_argument("--num-predict", type=int, default=300)
    parser.add_argument("--keep-alive", default="30m")
    parser.add_argument("--skip-model-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.catalog.is_file():
        raise FileNotFoundError(args.catalog)
    if args.runs < 1 or args.max_rounds < 1:
        raise ValueError("--runs与--max-rounds必须大于0")
    if not 0 <= args.temperature <= 2:
        raise ValueError("--temperature必须位于[0,2]")
    levels = parse_csv_set(args.levels)
    unknown_levels = levels - set(LEVELS)
    if unknown_levels or not levels:
        raise ValueError(f"未知或空实验层级：{sorted(unknown_levels)}")
    ordered_levels = [level for level in LEVELS if level in levels]

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

    needs_llm = any(len(error.get("candidates", [])) > 1 for error in errors)
    if needs_llm and not args.skip_model_check:
        print(f"[连接检查] Ollama={args.ollama_url}，模型={args.model}", flush=True)
        qwen_io.check_ollama(args.ollama_url, args.model, args.timeout)
        print("[连接检查] Ollama与模型均可用。\n", flush=True)

    all_cqs = load_csv(cq_runner.CQ_FILE)
    cq_index = {cq["cq_id"]: cq for cq in all_cqs}
    evidence_rows = benchmark_validator.current_evidence_rows(
        benchmark_validator.choose_evidence()
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_ROOT.mkdir(parents=True, exist_ok=True)
    CANDIDATE_ROOT.mkdir(parents=True, exist_ok=True)

    manifest_rows: dict[str, list[dict[str, Any]]] = {
        level: [] for level in ordered_levels
    }
    attempts: list[dict[str, Any]] = []
    logs = [
        "反例驱动LLM本体修复闭环日志",
        f"模型：{args.model}",
        f"层级：{ordered_levels}",
        f"每个事件重复：{args.runs}",
        f"最大反馈轮数：{args.max_rounds}",
        "反馈隐私：不向模型返回expected值、干净本体或Oracle结果。",
        "",
    ]
    total = len(ordered_levels) * len(errors) * args.runs
    position = 0

    for level in ordered_levels:
        for error in errors:
            error_id = str(error["error_id"])
            source_path = resolve_project_path(str(error["source_mutant"]))
            if not source_path.is_file():
                raise FileNotFoundError(source_path)
            source_graph = load_graph(source_path)
            candidates = error.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                raise RuntimeError(f"{error_id}没有符号候选")
            candidate_map = {
                str(candidate["candidate_id"]): candidate for candidate in candidates
            }
            if len(candidate_map) != len(candidates):
                raise RuntimeError(f"{error_id}候选ID重复")

            for run_id in range(1, args.runs + 1):
                position += 1
                seed = args.seed + run_id - 1
                attempt_id = f"feedback-{level}-{error_id}-run-{run_id:03d}"
                row = manifest_row()
                row.update({
                    "attempt_id": attempt_id,
                    "error_id": error_id,
                    "error_type": error["error_type"],
                    "run_id": run_id,
                    "seed": seed,
                    "model": args.model,
                    "prompt_mode": "formal_counterexample_feedback",
                    "temperature": args.temperature,
                    "source_catalog": str(args.catalog.resolve()),
                    "source_mutant": str(source_path.resolve()),
                    "feedback_level": level,
                    "candidate_count": len(candidates),
                })
                print(
                    f"[{position}/{total}] level={level} | {error_id} | run={run_id}",
                    flush=True,
                )
                remaining = list(candidates)
                feedback_history: list[dict[str, Any]] = []
                round_records: list[dict[str, Any]] = []
                final_candidate: dict[str, Any] | None = None
                final_round_path: Path | None = None
                final_selection_path: Path | None = None
                final_raw_path: Path | None = None
                final_ontology_iri = ""
                final_rationale = ""
                total_llm_ms = 0
                total_prompt_tokens = 0
                total_eval_tokens = 0
                llm_calls = 0
                abstained = False

                try:
                    for round_id in range(1, args.max_rounds + 1):
                        if not remaining:
                            abstained = True
                            final_rationale = "所有候选均被当前约束层级拒绝。"
                            break
                        round_dir = (
                            CANDIDATE_ROOT
                            / level
                            / error_id
                            / f"run-{run_id:03d}"
                            / f"round-{round_id:02d}"
                        )
                        round_dir.mkdir(parents=True, exist_ok=True)
                        raw_path = round_dir / "raw-response.txt"
                        selection_path = round_dir / "selection.json"
                        owl_path = round_dir / "candidate.owl"
                        selection_source = "DETERMINISTIC_REMAINDER"
                        raw_text = ""
                        confidence = 1.0

                        if len(remaining) == 1:
                            selected_candidate = remaining[0]
                            selected_id = str(selected_candidate["candidate_id"])
                            rationale = "约束反馈后只剩一个候选，执行确定性选择。"
                            payload = {
                                "abstain": False,
                                "candidate_id": selected_id,
                                "confidence": confidence,
                                "rationale": rationale,
                            }
                            selection_path.write_text(
                                json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                            raw_path.write_text(
                                json.dumps(payload, ensure_ascii=False),
                                encoding="utf-8",
                            )
                        else:
                            selection_source = "QWEN_SELECTION"
                            ordered = list(remaining)
                            random.Random(seed + round_id - 1).shuffle(ordered)
                            system_prompt, user_prompt = build_prompt(
                                error, ordered, feedback_history
                            )
                            raw_text, metadata, runtime_ms = qwen_io.call_ollama(
                                args.ollama_url,
                                args.model,
                                system_prompt,
                                user_prompt,
                                args.temperature,
                                seed + round_id - 1,
                                args.timeout,
                                args.num_predict,
                                args.keep_alive,
                            )
                            llm_calls += 1
                            total_llm_ms += runtime_ms
                            total_prompt_tokens += int(metadata.get("prompt_eval_count", 0))
                            total_eval_tokens += int(metadata.get("eval_count", 0))
                            raw_path.write_text(raw_text, encoding="utf-8")
                            payload = qwen_io.extract_json(raw_text)
                            selection_path.write_text(
                                json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                            is_abstain, selected_id, confidence, rationale = (
                                qwen_io.validate_selection(payload, set(candidate_map) & {
                                    str(candidate["candidate_id"]) for candidate in remaining
                                })
                            )
                            if is_abstain:
                                abstained = True
                                final_rationale = rationale
                                round_records.append({
                                    "round_id": round_id,
                                    "selection_source": selection_source,
                                    "abstained": True,
                                    "confidence": confidence,
                                    "rationale": rationale,
                                    "raw_response": str(raw_path.resolve()),
                                })
                                break
                            selected_candidate = candidate_map[selected_id]

                        if not row["first_candidate_id"]:
                            row["first_candidate_id"] = selected_id
                        operation = deepcopy(selected_candidate["operation"])
                        validate_operation_schema(operation)
                        candidate_graph = apply_operations(source_graph, [operation])
                        ontology_iri = set_ontology_identity(
                            candidate_graph,
                            level,
                            error_id,
                            run_id,
                            round_id,
                            seed,
                        )
                        candidate_graph.serialize(
                            destination=owl_path, format="xml", encoding="utf-8"
                        )
                        evaluation = evaluate_candidate(
                            source_graph,
                            candidate_graph,
                            owl_path,
                            operation,
                            all_cqs,
                            evidence_rows,
                            args.reasoner_timeout,
                        )
                        if round_id == 1:
                            row["first_choice_full_safety_pass_for_analysis"] = (
                                qwen_io.bool_text(
                                    evaluation["full_safety_pass_for_analysis"]
                                )
                            )
                        passed_level, new_feedback = exposed_feedback(
                            level, selected_candidate, evaluation, cq_index
                        )
                        record = {
                            "round_id": round_id,
                            "selected_candidate_id": selected_id,
                            "selection_source": selection_source,
                            "confidence": confidence,
                            "rationale": rationale,
                            "candidate_owl": str(owl_path.resolve()),
                            "evaluation_for_analysis": evaluation,
                            "exposed_feedback": new_feedback,
                            "passed_configured_level": passed_level,
                        }
                        round_records.append(record)
                        if passed_level:
                            final_candidate = selected_candidate
                            final_round_path = owl_path
                            final_selection_path = selection_path
                            final_raw_path = raw_path
                            final_ontology_iri = ontology_iri
                            final_rationale = rationale
                            row["selection_source"] = selection_source
                            row["final_full_safety_pass_for_analysis"] = qwen_io.bool_text(
                                evaluation["full_safety_pass_for_analysis"]
                            )
                            break

                        feedback_history.extend(new_feedback)
                        remaining = [
                            candidate
                            for candidate in remaining
                            if str(candidate["candidate_id"]) != selected_id
                        ]

                    if final_candidate is None and not abstained:
                        abstained = True
                        final_rationale = (
                            f"达到最大反馈轮数{args.max_rounds}仍未通过当前约束。"
                        )

                    row.update({
                        "rounds_used": len(round_records),
                        "llm_calls": llm_calls,
                        "llm_runtime_ms": total_llm_ms,
                        "prompt_eval_count": total_prompt_tokens,
                        "eval_count": total_eval_tokens,
                        "abstained": qwen_io.bool_text(abstained),
                        "rationale": final_rationale,
                        "llm_called": qwen_io.bool_text(llm_calls > 0),
                    })
                    if final_candidate is not None:
                        operation = deepcopy(final_candidate["operation"])
                        row.update({
                            "selected_candidate_id": final_candidate["candidate_id"],
                            "selected_description": final_candidate["description"],
                            "selection_json": str(final_selection_path.resolve()),
                            "raw_response": str(final_raw_path.resolve()),
                            "candidate_owl": str(final_round_path.resolve()),
                            "operations_json": json.dumps([operation], ensure_ascii=False),
                            "operation_count": 1,
                            "operators": operation["operator"],
                            "accepted": "true",
                            "json_parse_pass": "true",
                            "strict_selection_pass": "true",
                            "candidate_membership_pass": "true",
                            "apply_pass": "true",
                            "confidence": round_records[-1].get("confidence", 1.0),
                            "ollama_format_mode": (
                                "plain_json_nothink" if llm_calls else "not_called"
                            ),
                            "ontology_iri": final_ontology_iri,
                        })
                        status = (
                            f"ACCEPT {final_candidate['candidate_id']} | "
                            f"rounds={len(round_records)} | LLM={llm_calls} | "
                            f"full_safe={row['final_full_safety_pass_for_analysis']}"
                        )
                    else:
                        row.update({
                            "accepted": "false",
                            "selection_source": "ABSTAIN",
                            "ollama_format_mode": (
                                "plain_json_nothink" if llm_calls else "not_called"
                            ),
                        })
                        status = (
                            f"ABSTAIN | rounds={len(round_records)} | LLM={llm_calls}"
                        )
                except Exception as exc:
                    row["error_message"] = f"{type(exc).__name__}: {exc}"
                    row["rounds_used"] = len(round_records)
                    row["llm_calls"] = llm_calls
                    row["llm_runtime_ms"] = total_llm_ms
                    status = f"ERROR {row['error_message']}"

                manifest_rows[level].append(row)
                attempt = {
                    "attempt_id": attempt_id,
                    "feedback_level": level,
                    "error_id": error_id,
                    "error_type": error["error_type"],
                    "run_id": run_id,
                    "seed": seed,
                    "candidate_count": len(candidates),
                    "loop_accepted": row["accepted"] == "true",
                    "abstained": row["abstained"] == "true",
                    "first_candidate_id": row["first_candidate_id"],
                    "final_candidate_id": row["selected_candidate_id"],
                    "first_choice_full_safety_pass_for_analysis": (
                        row["first_choice_full_safety_pass_for_analysis"] == "true"
                    ),
                    "final_full_safety_pass_for_analysis": (
                        row["final_full_safety_pass_for_analysis"] == "true"
                    ),
                    "rounds_used": row["rounds_used"],
                    "llm_calls": row["llm_calls"],
                    "llm_runtime_ms": row["llm_runtime_ms"],
                    "rounds": round_records,
                    "error_message": row["error_message"],
                }
                attempts.append(attempt)
                line = f"  {status}"
                print(line, flush=True)
                logs.extend([f"[{attempt_id}]", line])

    fields = list(manifest_row())
    for level, rows in manifest_rows.items():
        write_csv(MANIFEST_ROOT / f"{level}.csv", rows, fields)
    GENERATION_JSON.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "generator": "iterative_constraint_feedback.py",
                "config": {
                    "catalog": str(args.catalog.resolve()),
                    "levels": ordered_levels,
                    "runs": args.runs,
                    "max_rounds": args.max_rounds,
                    "model": args.model,
                    "temperature": args.temperature,
                    "seed": args.seed,
                },
                "attempts": attempts,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary_rows = [{
        "attempt_id": attempt["attempt_id"],
        "feedback_level": attempt["feedback_level"],
        "error_id": attempt["error_id"],
        "run_id": attempt["run_id"],
        "candidate_count": attempt["candidate_count"],
        "first_candidate_id": attempt["first_candidate_id"],
        "final_candidate_id": attempt["final_candidate_id"],
        "first_choice_full_safety_pass_for_analysis": attempt[
            "first_choice_full_safety_pass_for_analysis"
        ],
        "final_full_safety_pass_for_analysis": attempt[
            "final_full_safety_pass_for_analysis"
        ],
        "loop_accepted": attempt["loop_accepted"],
        "abstained": attempt["abstained"],
        "rounds_used": attempt["rounds_used"],
        "llm_calls": attempt["llm_calls"],
        "llm_runtime_ms": attempt["llm_runtime_ms"],
        "error_message": attempt["error_message"],
    } for attempt in attempts]
    write_csv(GENERATION_CSV, summary_rows)
    accepted = sum(attempt["loop_accepted"] for attempt in attempts)
    final_safe = sum(
        attempt["final_full_safety_pass_for_analysis"] for attempt in attempts
    )
    llm_calls = sum(int(attempt["llm_calls"]) for attempt in attempts)
    summary = (
        f"闭环汇总：尝试={len(attempts)}，层级接受={accepted}，"
        f"分析用完整安全通过={final_safe}，LLM调用={llm_calls}"
    )
    logs.extend(["", summary, f"JSON：{GENERATION_JSON}", f"CSV：{GENERATION_CSV}"])
    GENERATION_LOG.write_text("\n".join(logs) + "\n", encoding="utf-8")
    print(f"\n{summary}")
    print(f"各层Manifest：{MANIFEST_ROOT}")
    print(f"JSON：{GENERATION_JSON}")
    print(f"CSV：{GENERATION_CSV}")
    print("[完成] 下一步运行 python .\\src\\compare_constraint_levels.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已中断] 用户终止约束反馈实验。")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[约束反馈实验停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
