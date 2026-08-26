from __future__ import annotations

r"""比较完全不用 LLM 的候选选择基线，并可附带已有 Qwen 闭环结果。

默认运行：
    python .\src\compare_no_llm_baselines.py --random-runs 100 --seed 20260820

默认读取：
    benchmark\repairs\candidate-effects.json          （E1-E5，如存在）
    benchmark\repairs\tbox-candidate-effects.json     （E6-E8）
    output\constraint-level-comparison.json            （Qwen完整反馈，如存在）

注意：FIRST_CANDIDATE会受到候选文件排列顺序影响，只作为偏差诊断，不应作为
主要论文基线。HASH_FIRST和RANDOM不依赖“正确候选是否恰好编号为001”。
"""

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from rdflib import Graph, RDF
    from rdflib.compare import isomorphic
    from rdflib.namespace import OWL
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
BASELINE = PROJECT_DIR / "benchmark" / "clean" / "insurance-validation-baseline.owl"
DEFAULT_CATALOGS = [
    REPAIR_DIR / "candidate-effects.json",
    REPAIR_DIR / "tbox-candidate-effects.json",
]
DEFAULT_QWEN = PROJECT_DIR / "output" / "constraint-level-comparison.json"
OUTPUT_DIR = PROJECT_DIR / "output"
SUMMARY_CSV = OUTPUT_DIR / "no-llm-baseline-comparison.csv"
DETAIL_CSV = OUTPUT_DIR / "no-llm-baseline-details.csv"
OUTPUT_JSON = OUTPUT_DIR / "no-llm-baseline-comparison.json"
LOG_FILE = OUTPUT_DIR / "no-llm-baseline-comparison.log"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON顶层必须是对象：{path}")
    return value


def load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="xml")
    return graph


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


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "pass"}


def as_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        p * (1 - p) / total + z * z / (4 * total * total)
    ) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def candidate_cost(candidate: dict[str, Any]) -> int:
    effect = candidate.get("effect", {})
    removed = as_int(effect.get("triples_removed", 9999), 9999)
    added = as_int(effect.get("triples_added", 9999), 9999)
    return removed + added


def full_safe(candidate: dict[str, Any]) -> bool:
    effect = candidate.get("effect", {})
    cq_gate = effect.get("cq_gate", effect.get("all_cq_gate", False))
    return all(
        as_bool(value)
        for value in (
            effect.get("execution_pass", False),
            effect.get("reasoner_gate", False),
            effect.get("evidence_gate", False),
            cq_gate,
            effect.get("minimal_edit_gate", False),
        )
    )


def candidate_oracle_match(
    candidate: dict[str, Any], baseline_graph: Graph
) -> bool | None:
    effect = candidate.get("effect", {})
    if "oracle_match" in effect:
        return as_bool(effect["oracle_match"])
    candidate_path_text = str(effect.get("candidate_owl", "")).strip()
    if not candidate_path_text:
        return None
    candidate_path = Path(candidate_path_text)
    if not candidate_path.is_file():
        return None
    candidate_graph = load_graph(candidate_path)
    return isomorphic(
        strip_ontology_metadata(candidate_graph),
        strip_ontology_metadata(baseline_graph),
    )


def load_events(catalog_paths: list[Path], baseline_graph: Graph) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in catalog_paths:
        payload = load_json(path)
        errors = payload.get("errors")
        if not isinstance(errors, list):
            raise RuntimeError(f"候选目录缺少errors数组：{path}")
        for error in errors:
            error_id = str(error.get("error_id", "")).strip().upper()
            if not error_id:
                raise RuntimeError(f"候选目录存在空error_id：{path}")
            if error_id in seen:
                raise RuntimeError(f"多个候选目录重复定义{error_id}")
            seen.add(error_id)
            candidates = error.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                raise RuntimeError(f"{error_id}没有候选")
            normalized: list[dict[str, Any]] = []
            for candidate in candidates:
                item = dict(candidate)
                item["full_safe"] = full_safe(item)
                item["oracle_match"] = candidate_oracle_match(item, baseline_graph)
                item["edit_cost"] = candidate_cost(item)
                normalized.append(item)
            events.append({
                "error_id": error_id,
                "error_type": error.get("error_type", ""),
                "source_catalog": str(path.resolve()),
                "candidates": normalized,
            })
    return sorted(events, key=lambda row: row["error_id"])


def hash_score(error_id: str, candidate_id: str, seed: int) -> str:
    value = f"{seed}|{error_id}|{candidate_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def first_candidate(event: dict[str, Any], _: int) -> dict[str, Any] | None:
    return event["candidates"][0]


def hash_first(event: dict[str, Any], seed: int) -> dict[str, Any] | None:
    return min(
        event["candidates"],
        key=lambda item: hash_score(event["error_id"], str(item["candidate_id"]), seed),
    )


def random_choice(event: dict[str, Any], seed: int) -> dict[str, Any] | None:
    return random.Random(f"{seed}|{event['error_id']}").choice(event["candidates"])


def unique_min_edit(event: dict[str, Any], _: int) -> dict[str, Any] | None:
    minimum = min(candidate["edit_cost"] for candidate in event["candidates"])
    winners = [
        candidate for candidate in event["candidates"] if candidate["edit_cost"] == minimum
    ]
    return winners[0] if len(winners) == 1 else None


def reasoner_unique(event: dict[str, Any], _: int) -> dict[str, Any] | None:
    winners = [
        candidate
        for candidate in event["candidates"]
        if as_bool(candidate.get("effect", {}).get("reasoner_gate", False))
        and as_bool(candidate.get("effect", {}).get("minimal_edit_gate", False))
    ]
    return winners[0] if len(winners) == 1 else None


def symbolic_unique_full_gate(event: dict[str, Any], _: int) -> dict[str, Any] | None:
    winners = [candidate for candidate in event["candidates"] if candidate["full_safe"]]
    return winners[0] if len(winners) == 1 else None


def symbolic_full_gate_tiebreak(
    event: dict[str, Any], seed: int
) -> dict[str, Any] | None:
    winners = [candidate for candidate in event["candidates"] if candidate["full_safe"]]
    if not winners:
        return None
    return min(
        winners,
        key=lambda item: (
            item["edit_cost"],
            hash_score(event["error_id"], str(item["candidate_id"]), seed),
        ),
    )


POLICIES: dict[str, Callable[[dict[str, Any], int], dict[str, Any] | None]] = {
    "FIRST_CANDIDATE_ORDER_BIASED": first_candidate,
    "HASH_FIRST": hash_first,
    "RANDOM": random_choice,
    "UNIQUE_MIN_EDIT": unique_min_edit,
    "REASONER_UNIQUE_ONLY": reasoner_unique,
    "SYMBOLIC_UNIQUE_FULL_GATE": symbolic_unique_full_gate,
    "SYMBOLIC_FULL_GATE_TIEBREAK": symbolic_full_gate_tiebreak,
}


def run_policy(
    method: str,
    chooser: Callable[[dict[str, Any], int], dict[str, Any] | None],
    events: list[dict[str, Any]],
    runs: int,
    seed: int,
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for run_id in range(1, runs + 1):
        run_seed = seed + run_id - 1
        for event in events:
            selected = chooser(event, run_seed)
            details.append({
                "method": method,
                "run_id": run_id,
                "seed": run_seed,
                "error_id": event["error_id"],
                "error_type": event["error_type"],
                "candidate_count": len(event["candidates"]),
                "selected": selected is not None,
                "abstained": selected is None,
                "selected_candidate_id": (
                    str(selected["candidate_id"]) if selected is not None else ""
                ),
                "full_safe": bool(selected and selected["full_safe"]),
                "unsafe_accepted": bool(selected and not selected["full_safe"]),
                "oracle_match": bool(selected and selected["oracle_match"] is True),
                "oracle_available": bool(
                    selected is not None and selected["oracle_match"] is not None
                ),
                "edit_cost": selected["edit_cost"] if selected is not None else "",
                "source_catalog": event["source_catalog"],
            })
    return details


def summarize(method: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = len(rows)
    selected = sum(row["selected"] for row in rows)
    abstained = sum(row["abstained"] for row in rows)
    safe = sum(row["full_safe"] for row in rows)
    unsafe = sum(row["unsafe_accepted"] for row in rows)
    oracle_available = sum(row["oracle_available"] for row in rows)
    oracle = sum(row["oracle_match"] for row in rows)
    low, high = wilson_interval(safe, attempts)
    return {
        "method": method,
        "independent_error_events": len({row["error_id"] for row in rows}),
        "attempts": attempts,
        "selected": selected,
        "abstained": abstained,
        "coverage": selected / attempts if attempts else 0.0,
        "safe_accepts": safe,
        "safe_accept_rate": safe / attempts if attempts else 0.0,
        "safe_accept_95ci_low": low,
        "safe_accept_95ci_high": high,
        "unsafe_accepts": unsafe,
        "unsafe_accept_rate": unsafe / attempts if attempts else 0.0,
        "accepted_precision": safe / selected if selected else 0.0,
        "oracle_available": oracle_available,
        "oracle_successes": oracle,
        "oracle_success_rate": oracle / attempts if attempts else 0.0,
    }


def qwen_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    payload = load_json(path)
    details = payload.get("details")
    if not isinstance(details, list):
        return []
    source = [
        row
        for row in details
        if row.get("feedback_level") == "reasoner_evidence_cq"
    ]
    result: list[dict[str, Any]] = []
    for row in source:
        selected = as_bool(row.get("loop_accepted", False))
        safe = as_bool(row.get("final_safe_accept", False))
        oracle = as_bool(row.get("dev_oracle_match", safe))
        result.append({
            "method": "QWEN_FORMAL_FEEDBACK_OBSERVED",
            "run_id": row.get("run_id", ""),
            "seed": "",
            "error_id": row.get("error_id", ""),
            "error_type": "",
            "candidate_count": row.get("candidate_count", ""),
            "selected": selected,
            "abstained": as_bool(row.get("abstained", False)),
            "selected_candidate_id": row.get("final_candidate_id", ""),
            "full_safe": safe,
            "unsafe_accepted": selected and not safe,
            "oracle_match": oracle,
            "oracle_available": True,
            "edit_cost": "",
            "source_catalog": str(path.resolve()),
        })
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"没有数据可写入：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比较无LLM候选选择基线")
    parser.add_argument(
        "--catalog",
        action="append",
        type=Path,
        default=[],
        help="可重复提供候选效果JSON；未提供时自动读取E1-E8默认文件",
    )
    parser.add_argument("--qwen-results", type=Path, default=DEFAULT_QWEN)
    parser.add_argument("--random-runs", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260820)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.random_runs < 1:
        raise ValueError("--random-runs必须大于0")
    if not BASELINE.is_file():
        raise FileNotFoundError(BASELINE)
    catalogs = args.catalog or [path for path in DEFAULT_CATALOGS if path.is_file()]
    if not catalogs:
        raise FileNotFoundError(
            "没有找到候选效果目录。请先运行annotate_candidate_effects.py和"
            "enumerate_tbox_candidates.py。"
        )
    for path in catalogs:
        if not path.is_file():
            raise FileNotFoundError(path)

    baseline_graph = load_graph(BASELINE)
    events = load_events(catalogs, baseline_graph)
    if not events:
        raise RuntimeError("没有可比较的错误事件")

    all_details: list[dict[str, Any]] = []
    for method, chooser in POLICIES.items():
        runs = args.random_runs if method in {"RANDOM", "HASH_FIRST"} else 1
        all_details.extend(run_policy(method, chooser, events, runs, args.seed))
    observed_qwen = qwen_rows(args.qwen_results)
    all_details.extend(observed_qwen)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_details:
        grouped[row["method"]].append(row)
    summaries = [summarize(method, rows) for method, rows in grouped.items()]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(SUMMARY_CSV, summaries)
    write_csv(DETAIL_CSV, all_details)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "compare_no_llm_baselines.py",
        "catalogs": [str(path.resolve()) for path in catalogs],
        "independent_error_events": len(events),
        "random_runs": args.random_runs,
        "seed": args.seed,
        "method_notes": {
            "FIRST_CANDIDATE_ORDER_BIASED": "受候选生成顺序影响，只用于偏差诊断",
            "HASH_FIRST": "以哈希稳定打乱候选，不读取语义",
            "RANDOM": "均匀随机选择候选",
            "UNIQUE_MIN_EDIT": "仅在最小编辑候选唯一时接受，否则ABSTAIN",
            "REASONER_UNIQUE_ONLY": "仅在Reasoner通过候选唯一时接受",
            "SYMBOLIC_UNIQUE_FULL_GATE": "仅在完整门禁通过候选唯一时接受",
            "SYMBOLIC_FULL_GATE_TIEBREAK": "完整门禁后按编辑代价和哈希确定性选择",
            "QWEN_FORMAL_FEEDBACK_OBSERVED": "已有E1-E5完整形式化反馈实验，覆盖范围单独报告",
        },
        "summary": summaries,
        "details": all_details,
    }
    OUTPUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "无LLM修复候选选择基线对比日志",
        f"独立错误事件：{len(events)}（{', '.join(event['error_id'] for event in events)}）",
        f"候选效果目录：{', '.join(path.name for path in catalogs)}",
        "",
    ]
    print("无LLM修复候选选择基线对比")
    for row in summaries:
        line = (
            f"[{row['method']}] 尝试={row['attempts']} | "
            f"覆盖={row['coverage']:.2%} | 安全接受={row['safe_accept_rate']:.2%} | "
            f"错误接受={row['unsafe_accept_rate']:.2%} | "
            f"接受精度={row['accepted_precision']:.2%} | "
            f"Oracle={row['oracle_success_rate']:.2%}"
        )
        print(line)
        lines.append(line)
    lines.extend([
        "",
        "解释注意：重复随机运行衡量策略稳定性，不等同于独立真实事件。",
        "FIRST_CANDIDATE若很高，首先检查正确候选是否总被生成器排在第一位。",
        f"CSV：{SUMMARY_CSV}",
        f"明细：{DETAIL_CSV}",
        f"JSON：{OUTPUT_JSON}",
    ])
    LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nCSV：{SUMMARY_CSV}")
    print(f"明细：{DETAIL_CSV}")
    print(f"JSON：{OUTPUT_JSON}")
    print(f"日志：{LOG_FILE}")
    print("[完成] 请重点比较覆盖率、错误接受率和Oracle成功率，不要只看单一准确率。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[基线对比停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
