from __future__ import annotations

r"""候选顺序、编号随机化鲁棒性实验。

默认运行：
    python .\src\test_candidate_order_robustness.py --runs 1000 --seed 20260820

目的：
1. 反复打乱每个错误事件的候选顺序；
2. 同时测试“保留原编号”和“按新顺序重新编号”两种扰动；
3. 判断 FIRST/CAND_001/确定性平局策略的高分是否来自顺序泄漏；
4. 检查完整符号门禁在顺序改变后能否保持零错误接受。

本脚本不调用 Ollama、不重新运行 Reasoner，也不修改本体；它读取已经完成
反事实标注的 candidate-effects.json 和 tbox-candidate-effects.json。
"""

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import compare_no_llm_baselines as baseline
except ImportError as exc:
    print(
        "缺少 compare_no_llm_baselines.py。请把两个脚本都放入"
        "ontology-evolution\\src。"
    )
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_DIR / "output"
SUMMARY_CSV = OUTPUT_DIR / "candidate-order-robustness-summary.csv"
DETAIL_CSV = OUTPUT_DIR / "candidate-order-robustness-details.csv"
BY_ERROR_CSV = OUTPUT_DIR / "candidate-order-robustness-by-error.csv"
OUTPUT_JSON = OUTPUT_DIR / "candidate-order-robustness.json"
LOG_FILE = OUTPUT_DIR / "candidate-order-robustness.log"

SCENARIOS = ("ORDER_ONLY", "ORDER_AND_ID_RELABEL")


def stable_seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def operation_fingerprint(candidate: dict[str, Any]) -> str:
    """只基于修复操作内容生成指纹，不使用候选顺序和候选编号。"""
    operation = candidate.get("operation", {})
    canonical = json.dumps(
        operation,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def shuffled_event(
    event: dict[str, Any], scenario: str, run_seed: int
) -> dict[str, Any]:
    candidates = [deepcopy(candidate) for candidate in event["candidates"]]
    rng = random.Random(stable_seed(run_seed, scenario, event["error_id"], "shuffle"))
    rng.shuffle(candidates)
    for position, candidate in enumerate(candidates, start=1):
        candidate["original_candidate_id"] = str(candidate["candidate_id"])
        candidate["permuted_position"] = position
        if scenario == "ORDER_AND_ID_RELABEL":
            candidate["candidate_id"] = f"CAND_{position:03d}"
        candidate["permuted_candidate_id"] = str(candidate["candidate_id"])
        candidate["operation_fingerprint"] = operation_fingerprint(candidate)
    return {
        "error_id": event["error_id"],
        "error_type": event.get("error_type", ""),
        "source_catalog": event.get("source_catalog", ""),
        "candidates": candidates,
    }


def min_edit_candidates(event: dict[str, Any]) -> list[dict[str, Any]]:
    minimum = min(candidate["edit_cost"] for candidate in event["candidates"])
    return [
        candidate
        for candidate in event["candidates"]
        if candidate["edit_cost"] == minimum
    ]


def reasoner_candidates(event: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        candidate
        for candidate in event["candidates"]
        if baseline.as_bool(candidate.get("effect", {}).get("reasoner_gate", False))
        and baseline.as_bool(
            candidate.get("effect", {}).get("minimal_edit_gate", False)
        )
    ]


def full_gate_candidates(event: dict[str, Any]) -> list[dict[str, Any]]:
    return [candidate for candidate in event["candidates"] if candidate["full_safe"]]


def first_position(event: dict[str, Any], _: int) -> dict[str, Any] | None:
    return event["candidates"][0]


def lowest_current_id(event: dict[str, Any], _: int) -> dict[str, Any] | None:
    return min(event["candidates"], key=lambda item: str(item["candidate_id"]))


def random_all(event: dict[str, Any], chooser_seed: int) -> dict[str, Any] | None:
    rng = random.Random(stable_seed(chooser_seed, event["error_id"], "random-all"))
    return rng.choice(event["candidates"])


def unique_min_edit(event: dict[str, Any], _: int) -> dict[str, Any] | None:
    winners = min_edit_candidates(event)
    return winners[0] if len(winners) == 1 else None


def reasoner_unique(event: dict[str, Any], _: int) -> dict[str, Any] | None:
    winners = reasoner_candidates(event)
    return winners[0] if len(winners) == 1 else None


def full_gate_unique(event: dict[str, Any], _: int) -> dict[str, Any] | None:
    winners = full_gate_candidates(event)
    return winners[0] if len(winners) == 1 else None


def full_gate_first(event: dict[str, Any], _: int) -> dict[str, Any] | None:
    winners = full_gate_candidates(event)
    return winners[0] if winners else None


def full_gate_random(
    event: dict[str, Any], chooser_seed: int
) -> dict[str, Any] | None:
    winners = full_gate_candidates(event)
    if not winners:
        return None
    rng = random.Random(stable_seed(chooser_seed, event["error_id"], "safe-random"))
    return rng.choice(winners)


def full_gate_content_hash(
    event: dict[str, Any], chooser_seed: int
) -> dict[str, Any] | None:
    """顺序、编号无关的确定性平局策略。"""
    winners = full_gate_candidates(event)
    if not winners:
        return None
    return min(
        winners,
        key=lambda candidate: hashlib.sha256(
            (
                f"{chooser_seed}|{event['error_id']}|"
                f"{candidate['operation_fingerprint']}"
            ).encode("utf-8")
        ).hexdigest(),
    )


POLICIES: dict[
    str, Callable[[dict[str, Any], int], dict[str, Any] | None]
] = {
    "FIRST_POSITION": first_position,
    "LOWEST_CURRENT_CANDIDATE_ID": lowest_current_id,
    "RANDOM_ALL": random_all,
    "UNIQUE_MIN_EDIT": unique_min_edit,
    "REASONER_UNIQUE_ONLY": reasoner_unique,
    "FULL_GATE_UNIQUE_ONLY": full_gate_unique,
    "FULL_GATE_FIRST_SURVIVOR": full_gate_first,
    "FULL_GATE_RANDOM_SURVIVOR": full_gate_random,
    "FULL_GATE_CONTENT_HASH": full_gate_content_hash,
}


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / total
        + z * z / (4 * total * total)
    ) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def evaluate_selection(
    scenario: str,
    method: str,
    run_id: int,
    run_seed: int,
    event: dict[str, Any],
    selected: dict[str, Any] | None,
) -> dict[str, Any]:
    selected_flag = selected is not None
    safe = bool(selected and selected["full_safe"])
    oracle_available = bool(
        selected is not None and selected.get("oracle_match") is not None
    )
    oracle = bool(selected and selected.get("oracle_match") is True)
    first = event["candidates"][0]
    return {
        "scenario": scenario,
        "method": method,
        "run_id": run_id,
        "seed": run_seed,
        "error_id": event["error_id"],
        "error_type": event["error_type"],
        "candidate_count": len(event["candidates"]),
        "full_gate_candidate_count": len(full_gate_candidates(event)),
        "first_original_candidate_id": first["original_candidate_id"],
        "first_is_safe": bool(first["full_safe"]),
        "first_is_oracle": first.get("oracle_match") is True,
        "selected": selected_flag,
        "abstained": not selected_flag,
        "selected_original_candidate_id": (
            selected["original_candidate_id"] if selected else ""
        ),
        "selected_permuted_candidate_id": (
            selected["permuted_candidate_id"] if selected else ""
        ),
        "selected_position": selected["permuted_position"] if selected else "",
        "safe_accept": safe,
        "unsafe_accept": selected_flag and not safe,
        "oracle_available": oracle_available,
        "oracle_success": oracle,
        "edit_cost": selected["edit_cost"] if selected else "",
        "operation_fingerprint": (
            selected["operation_fingerprint"] if selected else ""
        ),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = len(rows)
    selected = sum(bool(row["selected"]) for row in rows)
    abstained = attempts - selected
    safe = sum(bool(row["safe_accept"]) for row in rows)
    unsafe = sum(bool(row["unsafe_accept"]) for row in rows)
    oracle_available = sum(bool(row["oracle_available"]) for row in rows)
    oracle = sum(bool(row["oracle_success"]) for row in rows)
    first_safe = sum(bool(row["first_is_safe"]) for row in rows)
    first_oracle = sum(bool(row["first_is_oracle"]) for row in rows)
    safe_low, safe_high = wilson_interval(safe, attempts)

    selection_stabilities: list[float] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["error_id"])].append(row)
    for event_rows in grouped.values():
        ids = [
            str(row["selected_original_candidate_id"])
            for row in event_rows
            if row["selected"]
        ]
        if ids:
            counts = Counter(ids)
            selection_stabilities.append(max(counts.values()) / len(ids))

    return {
        "scenario": rows[0]["scenario"],
        "method": rows[0]["method"],
        "independent_error_events": len(grouped),
        "permutation_runs": len({row["run_id"] for row in rows}),
        "attempts": attempts,
        "selected": selected,
        "abstained": abstained,
        "coverage": selected / attempts if attempts else 0.0,
        "safe_accepts": safe,
        "safe_accept_rate": safe / attempts if attempts else 0.0,
        "safe_accept_95ci_low": safe_low,
        "safe_accept_95ci_high": safe_high,
        "unsafe_accepts": unsafe,
        "unsafe_accept_rate": unsafe / attempts if attempts else 0.0,
        "accepted_precision": safe / selected if selected else 0.0,
        "oracle_available": oracle_available,
        "oracle_successes": oracle,
        "oracle_success_rate": oracle / attempts if attempts else 0.0,
        "first_position_safe_rate": first_safe / attempts if attempts else 0.0,
        "first_position_oracle_rate": first_oracle / attempts if attempts else 0.0,
        "mean_selection_stability": (
            sum(selection_stabilities) / len(selection_stabilities)
            if selection_stabilities
            else 0.0
        ),
    }


def summarize_by_error(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["scenario"], row["method"], row["error_id"])].append(row)
    output: list[dict[str, Any]] = []
    for (scenario, method, error_id), event_rows in sorted(grouped.items()):
        attempts = len(event_rows)
        selected = sum(bool(row["selected"]) for row in event_rows)
        safe = sum(bool(row["safe_accept"]) for row in event_rows)
        oracle = sum(bool(row["oracle_success"]) for row in event_rows)
        output.append({
            "scenario": scenario,
            "method": method,
            "error_id": error_id,
            "attempts": attempts,
            "coverage": selected / attempts,
            "safe_accept_rate": safe / attempts,
            "unsafe_accept_rate": (selected - safe) / attempts,
            "oracle_success_rate": oracle / attempts,
            "full_gate_candidate_count": event_rows[0]["full_gate_candidate_count"],
        })
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"没有数据可写入：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="候选顺序和编号随机化鲁棒性实验")
    parser.add_argument(
        "--catalog",
        action="append",
        type=Path,
        default=[],
        help="可重复提供候选效果JSON；默认读取E1-E8两个候选目录",
    )
    parser.add_argument("--runs", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260820)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs必须大于0")
    if not baseline.BASELINE.is_file():
        raise FileNotFoundError(baseline.BASELINE)

    catalogs = args.catalog or [
        path for path in baseline.DEFAULT_CATALOGS if path.is_file()
    ]
    if not catalogs:
        raise FileNotFoundError(
            "没有找到candidate-effects.json或tbox-candidate-effects.json。"
        )
    for path in catalogs:
        if not path.is_file():
            raise FileNotFoundError(path)

    baseline_graph = baseline.load_graph(baseline.BASELINE)
    events = baseline.load_events(catalogs, baseline_graph)
    if not events:
        raise RuntimeError("没有可测试的错误事件")

    details: list[dict[str, Any]] = []
    print("候选顺序与编号随机化鲁棒性实验")
    print(
        f"错误事件={len(events)}，扰动场景={len(SCENARIOS)}，"
        f"每场景运行={args.runs}\n"
    )
    for scenario in SCENARIOS:
        for run_id in range(1, args.runs + 1):
            run_seed = args.seed + run_id - 1
            for source_event in events:
                event = shuffled_event(source_event, scenario, run_seed)
                for method, chooser in POLICIES.items():
                    selected = chooser(
                        event,
                        stable_seed(run_seed, scenario, method),
                    )
                    details.append(
                        evaluate_selection(
                            scenario,
                            method,
                            run_id,
                            run_seed,
                            event,
                            selected,
                        )
                    )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        grouped[(row["scenario"], row["method"])].append(row)
    summaries = [summarize(rows) for _, rows in sorted(grouped.items())]
    by_error = summarize_by_error(details)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(SUMMARY_CSV, summaries)
    write_csv(DETAIL_CSV, details)
    write_csv(BY_ERROR_CSV, by_error)

    interpretation: list[str] = []
    for row in summaries:
        if row["method"] == "FIRST_POSITION" and row["safe_accept_rate"] < 0.9:
            interpretation.append(
                f"{row['scenario']}：FIRST_POSITION降至"
                f"{row['safe_accept_rate']:.2%}，原先100%存在明显顺序泄漏。"
            )
        if (
            row["method"] == "FULL_GATE_CONTENT_HASH"
            and row["safe_accept_rate"] == 1.0
            and row["unsafe_accepts"] == 0
        ):
            interpretation.append(
                f"{row['scenario']}：完整门禁在顺序/编号扰动后仍为100%安全接受，"
                "当前基准尚不能证明LLM是必要组件。"
            )

    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "test_candidate_order_robustness.py",
        "catalogs": [str(path.resolve()) for path in catalogs],
        "independent_error_events": len(events),
        "permutation_runs_per_scenario": args.runs,
        "seed": args.seed,
        "scenarios": list(SCENARIOS),
        "method_notes": {
            "FIRST_POSITION": "始终选择打乱后的第一个候选，检测位置泄漏",
            "LOWEST_CURRENT_CANDIDATE_ID": "始终选择当前最小编号，检测编号泄漏",
            "RANDOM_ALL": "不读取语义，随机选择全部候选",
            "UNIQUE_MIN_EDIT": "最小编辑唯一时才接受",
            "REASONER_UNIQUE_ONLY": "Reasoner与最小修改通过候选唯一时才接受",
            "FULL_GATE_UNIQUE_ONLY": "完整符号门禁候选唯一时才接受，否则ABSTAIN",
            "FULL_GATE_FIRST_SURVIVOR": "在完整门禁存活候选中取当前位置最前者",
            "FULL_GATE_RANDOM_SURVIVOR": "在完整门禁存活候选中随机选择",
            "FULL_GATE_CONTENT_HASH": "在存活候选中按操作内容哈希选择，顺序/编号无关",
        },
        "summary": summaries,
        "by_error": by_error,
        "interpretation": interpretation,
        "details": details,
    }
    OUTPUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "候选顺序与编号随机化鲁棒性实验日志",
        f"独立错误事件：{len(events)}",
        f"每个场景的随机排列次数：{args.runs}",
        "注意：排列重复用于测试鲁棒性，不等于增加独立错误样本。",
        "",
    ]
    for row in summaries:
        line = (
            f"[{row['scenario']}/{row['method']}] "
            f"覆盖={row['coverage']:.2%} | 安全接受={row['safe_accept_rate']:.2%} | "
            f"错误接受={row['unsafe_accept_rate']:.2%} | "
            f"Oracle={row['oracle_success_rate']:.2%} | "
            f"选择稳定性={row['mean_selection_stability']:.2%}"
        )
        print(line)
        lines.append(line)
    lines.extend(["", *interpretation, "", f"汇总：{SUMMARY_CSV}", f"明细：{DETAIL_CSV}", f"按错误：{BY_ERROR_CSV}", f"JSON：{OUTPUT_JSON}"])
    LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n汇总：{SUMMARY_CSV}")
    print(f"明细：{DETAIL_CSV}")
    print(f"按错误：{BY_ERROR_CSV}")
    print(f"JSON：{OUTPUT_JSON}")
    print(f"日志：{LOG_FILE}")
    print("[完成] 请重点查看FIRST_POSITION是否下降，以及完整门禁是否仍为零错误接受。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[鲁棒性实验停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
