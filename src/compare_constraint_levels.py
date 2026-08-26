from __future__ import annotations

r"""
统一验证并比较反例反馈实验的不同约束层级。

默认运行：
    python .\src\compare_constraint_levels.py
"""

import argparse
import csv
import io
import json
import statistics
import sys
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

try:
    import validate_ranked_repairs as ranked_validator
except ImportError as exc:
    print("缺少 validate_ranked_repairs.py，请把本文件放入 ontology-evolution\\src。")
    raise SystemExit(2) from exc


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
DEFAULT_CATALOG = REPAIR_DIR / "feasible-candidates.json"
DEFAULT_MANIFEST_ROOT = REPAIR_DIR / "constraint-feedback" / "manifests"
DEFAULT_GENERATION = PROJECT_DIR / "output" / "constraint-feedback-generation.json"
OUTPUT_DIR = PROJECT_DIR / "output"
RESULT_CSV = OUTPUT_DIR / "constraint-level-comparison.csv"
DETAIL_CSV = OUTPUT_DIR / "constraint-level-validation-details.csv"
RESULT_JSON = OUTPUT_DIR / "constraint-level-comparison.json"
LOG_FILE = OUTPUT_DIR / "constraint-level-comparison.log"
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


def parse_levels(text: str) -> list[str]:
    requested = {item.strip().lower() for item in text.split(",") if item.strip()}
    unknown = requested - set(LEVELS)
    if unknown or not requested:
        raise ValueError(f"未知或空实验层级：{sorted(unknown)}")
    return [level for level in LEVELS if level in requested]


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "pass"}


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"没有数据可写入：{path}")
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比较不同形式化约束反馈层级")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--manifest-root", type=Path, default=DEFAULT_MANIFEST_ROOT)
    parser.add_argument("--generation", type=Path, default=DEFAULT_GENERATION)
    parser.add_argument("--levels", default=",".join(LEVELS))
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    levels = parse_levels(args.levels)
    for path in (args.catalog, args.generation):
        if not path.is_file():
            raise FileNotFoundError(path)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    catalog = load_json(args.catalog)
    catalog_index = ranked_validator.catalog_index(catalog)
    generation = load_json(args.generation)
    generation_attempts = generation.get("attempts")
    if not isinstance(generation_attempts, list):
        raise RuntimeError("生成记录缺少attempts数组")
    generation_index = {
        str(attempt["attempt_id"]): attempt for attempt in generation_attempts
    }

    summaries: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    logs = [
        "形式化约束反馈层级对比日志",
        "说明：first-choice衡量首次候选；多候选子集的first-choice才衡量LLM选择。",
        "final衡量约束反馈后的最终结果。",
        "完整安全验证和开发集Oracle仅用于评估，不进入模型反馈。",
        "",
    ]
    print("形式化约束反馈层级对比")

    for level in levels:
        manifest = args.manifest_root / f"{level}.csv"
        if not manifest.is_file():
            raise FileNotFoundError(manifest)
        rows = load_csv(manifest)
        # 复用论文主实验的统一门禁验证，但抑制逐行打印，由本脚本统一汇总。
        with redirect_stdout(io.StringIO()):
            validated = ranked_validator.validate_rows(rows, catalog_index, args.timeout)
        if len(validated) != len(rows):
            raise RuntimeError(f"{level}验证行数与Manifest不一致")

        joined: list[dict[str, Any]] = []
        for manifest_row, validation in zip(rows, validated):
            attempt_id = manifest_row["attempt_id"]
            generation_row = generation_index.get(attempt_id)
            if generation_row is None:
                raise RuntimeError(f"生成记录缺少attempt_id：{attempt_id}")
            first_safe = as_bool(
                generation_row.get("first_choice_full_safety_pass_for_analysis", False)
            )
            final_safe = as_bool(validation.get("safe_accept", False))
            loop_accepted = as_bool(generation_row.get("loop_accepted", False))
            multi_candidate = int(generation_row.get("candidate_count", 0)) > 1
            corrected = multi_candidate and (not first_safe) and final_safe
            unsafe_accepted = loop_accepted and not final_safe
            item = {
                "feedback_level": level,
                "attempt_id": attempt_id,
                "error_id": validation["error_id"],
                "run_id": validation["run_id"],
                "candidate_count": generation_row.get("candidate_count", 0),
                "multi_candidate": multi_candidate,
                "first_candidate_id": generation_row.get("first_candidate_id", ""),
                "final_candidate_id": generation_row.get("final_candidate_id", ""),
                "first_choice_full_safe": first_safe,
                "loop_accepted": loop_accepted,
                "abstained": as_bool(generation_row.get("abstained", False)),
                "final_safe_accept": final_safe,
                "dev_oracle_match": as_bool(validation.get("dev_oracle_match", False)),
                "benchmark_success": as_bool(validation.get("benchmark_success", False)),
                "unsafe_accepted": unsafe_accepted,
                "corrected_after_feedback": corrected,
                "rounds_used": int(generation_row.get("rounds_used", 0)),
                "llm_calls": int(generation_row.get("llm_calls", 0)),
                "llm_runtime_ms": int(generation_row.get("llm_runtime_ms", 0)),
                "reasoner_result": validation.get("reasoner_result", ""),
                "evidence_result": validation.get("evidence_result", ""),
                "cq_result": validation.get("cq_result", ""),
                "validation_error": validation.get("validation_error", ""),
            }
            joined.append(item)
            details.append(item)

        total = len(joined)
        multi = [row for row in joined if row["multi_candidate"]]
        first_safe = sum(row["first_choice_full_safe"] for row in joined)
        first_safe_multi = sum(row["first_choice_full_safe"] for row in multi)
        accepted = sum(row["loop_accepted"] for row in joined)
        abstained = sum(row["abstained"] for row in joined)
        final_safe = sum(row["final_safe_accept"] for row in joined)
        oracle = sum(row["benchmark_success"] for row in joined)
        unsafe = sum(row["unsafe_accepted"] for row in joined)
        correction_opportunities = sum(
            row["multi_candidate"] and not row["first_choice_full_safe"]
            for row in joined
        )
        corrections = sum(row["corrected_after_feedback"] for row in joined)
        total_llm_calls = sum(row["llm_calls"] for row in joined)
        summary = {
            "feedback_level": level,
            "attempts": total,
            "multi_candidate_attempts": len(multi),
            "first_choice_safe": first_safe,
            "first_choice_safe_rate": round(first_safe / total, 6),
            "multi_candidate_first_choice_safe": first_safe_multi,
            "multi_candidate_first_choice_safe_rate": round(
                first_safe_multi / len(multi), 6
            ) if multi else 0.0,
            "loop_accepted": accepted,
            "abstained": abstained,
            "final_safe_accepts": final_safe,
            "final_safe_accept_rate": round(final_safe / total, 6),
            "dev_oracle_successes": oracle,
            "dev_oracle_success_rate": round(oracle / total, 6),
            "unsafe_accepts": unsafe,
            "unsafe_accept_rate": round(unsafe / total, 6),
            "correction_opportunities": correction_opportunities,
            "corrected_after_feedback": corrections,
            "correction_rate": round(
                corrections / correction_opportunities, 6
            ) if correction_opportunities else 0.0,
            "total_llm_calls": total_llm_calls,
            "average_llm_calls": round(total_llm_calls / total, 6),
            "average_rounds": round(
                mean([float(row["rounds_used"]) for row in joined]), 6
            ),
            "average_llm_runtime_ms": round(
                mean([float(row["llm_runtime_ms"]) for row in joined]), 3
            ),
        }
        summaries.append(summary)
        line = (
            f"[{level}] 首选安全={first_safe}/{total} | "
            f"多候选LLM首选安全={first_safe_multi}/{len(multi)} | "
            f"反馈后安全={final_safe}/{total} | 错误接受={unsafe} | "
            f"纠正={corrections}/{correction_opportunities} | "
            f"LLM调用={total_llm_calls}"
        )
        print(line)
        logs.append(line)

    write_csv(RESULT_CSV, summaries)
    write_csv(DETAIL_CSV, details)
    RESULT_JSON.write_text(
        json.dumps(
            {
                "summary": summaries,
                "details": details,
                "interpretation": {
                    "first_choice_safe_rate": "LLM首次选择在完整隐藏安全门禁下通过的比例",
                    "final_safe_accept_rate": "当前反馈层级结束后通过完整安全门禁的比例",
                    "unsafe_accept_rate": "当前层级接受但完整安全门禁判定错误的比例",
                    "correction_rate": "首次选择错误后，经形式化反例反馈修正成功的比例",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logs.extend([
        "",
        "注意：重复seed运行衡量稳定性，不等同于独立真实错误事件。",
        "最终论文置信区间应按独立事件计算。",
        f"汇总CSV：{RESULT_CSV}",
        f"明细CSV：{DETAIL_CSV}",
        f"JSON：{RESULT_JSON}",
    ])
    LOG_FILE.write_text("\n".join(logs) + "\n", encoding="utf-8")
    print(f"\n汇总CSV：{RESULT_CSV}")
    print(f"明细CSV：{DETAIL_CSV}")
    print(f"JSON：{RESULT_JSON}")
    print(f"日志：{LOG_FILE}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已中断] 用户终止约束层级对比。")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[约束层级对比停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
