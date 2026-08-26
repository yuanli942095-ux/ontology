from __future__ import annotations

r"""
依据反事实效果的硬门禁结果过滤候选。

默认运行：
    python .\src\filter_feasible_candidates.py
"""

import argparse
import csv
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPAIR_DIR = PROJECT_DIR / "benchmark" / "repairs"
DEFAULT_INPUT = REPAIR_DIR / "candidate-effects.json"
DEFAULT_JSON = REPAIR_DIR / "surviving-candidates.json"
DEFAULT_CSV = REPAIR_DIR / "surviving-candidates.csv"
DEFAULT_LOG = PROJECT_DIR / "output" / "candidate-filtering.log"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON顶层必须是对象：{path}")
    return value


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "pass"}


def parse_only(text: str) -> set[str]:
    return {item.strip().upper() for item in text.split(",") if item.strip()}


def decision_policy(count: int) -> str:
    if count == 0:
        return "ABSTAIN"
    if count == 1:
        return "DETERMINISTIC_SELECT"
    return "LLM_RANK"


def rejection_reasons(effect: dict[str, Any]) -> list[str]:
    names = [
        "execution_pass",
        "reasoner_gate",
        "evidence_gate",
        "affected_cq_gate",
        "all_cq_gate",
        "minimal_edit_gate",
    ]
    return [name for name in names if not as_bool(effect.get(name, False))]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="过滤未通过反事实硬门禁的OWL修复候选")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--only", default="", help="只处理指定错误，如 E1,E3")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    source = load_json(args.input)
    errors = source.get("errors")
    if not isinstance(errors, list) or not errors:
        raise RuntimeError("效果目录没有errors数组")
    selected = parse_only(args.only)
    if selected:
        known = {str(error["error_id"]).upper() for error in errors}
        unknown = selected - known
        if unknown:
            raise ValueError(f"未知错误编号：{sorted(unknown)}")
        errors = [error for error in errors if str(error["error_id"]).upper() in selected]

    output_errors: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    original_total = 0
    survivor_total = 0
    deterministic_errors = 0
    llm_errors = 0
    abstain_errors = 0
    print("反事实硬门禁候选过滤", flush=True)
    logs = ["反事实硬门禁候选过滤日志", f"输入：{args.input.resolve()}", ""]

    for source_error in errors:
        error = deepcopy(source_error)
        candidates = error.get("candidates")
        if not isinstance(candidates, list):
            raise RuntimeError(f"{error['error_id']}的candidates不是数组")
        original_total += len(candidates)
        survivors: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for candidate in candidates:
            effect = candidate.get("effect")
            if not isinstance(effect, dict):
                raise RuntimeError(
                    f"{error['error_id']}/{candidate.get('candidate_id')}缺少effect；"
                    "请先运行annotate_candidate_effects.py"
                )
            if as_bool(effect.get("hard_gate_pass", False)):
                survivors.append(candidate)
            else:
                item = {
                    "candidate_id": candidate.get("candidate_id", ""),
                    "operator": candidate.get("operator", ""),
                    "description": candidate.get("description", ""),
                    "rejection_reasons": rejection_reasons(effect),
                    "error_message": effect.get("error_message", ""),
                }
                rejected.append(item)
                rejected_rows.append({"error_id": error["error_id"], **item})
        policy = decision_policy(len(survivors))
        survivor_total += len(survivors)
        deterministic_errors += policy == "DETERMINISTIC_SELECT"
        llm_errors += policy == "LLM_RANK"
        abstain_errors += policy == "ABSTAIN"
        error["original_candidate_count"] = len(candidates)
        error["survivor_count"] = len(survivors)
        error["decision_policy"] = policy
        error["rejected_candidates"] = rejected
        error["candidates"] = survivors
        output_errors.append(error)
        line = (
            f"[{error['error_id']}] 原候选={len(candidates)} | 存活={len(survivors)} | "
            f"策略={policy}"
        )
        print(line, flush=True)
        logs.append(line)
        if not survivors:
            csv_rows.append({
                "error_id": error["error_id"],
                "error_type": error["error_type"],
                "decision_policy": policy,
                "original_candidate_count": len(candidates),
                "survivor_count": 0,
                "candidate_id": "",
                "operator": "",
                "description": "",
                "operation_json": "",
                "post_state_json": "",
            })
        for candidate in survivors:
            csv_rows.append({
                "error_id": error["error_id"],
                "error_type": error["error_type"],
                "decision_policy": policy,
                "original_candidate_count": len(candidates),
                "survivor_count": len(survivors),
                "candidate_id": candidate["candidate_id"],
                "operator": candidate["operator"],
                "description": candidate["description"],
                "operation_json": json.dumps(candidate["operation"], ensure_ascii=False),
                "post_state_json": json.dumps(
                    candidate["effect"].get("post_state", []), ensure_ascii=False
                ),
            })

    reduction = 0.0 if original_total == 0 else 1 - survivor_total / original_total
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "filter_feasible_candidates.py",
        "source_effect_catalog": str(args.input.resolve()),
        "candidate_semantics": "hard_gate_survivors_only",
        "selection_policy": {
            "zero_survivors": "ABSTAIN",
            "one_survivor": "DETERMINISTIC_SELECT",
            "multiple_survivors": "LLM_RANK",
        },
        "summary": {
            "errors": len(output_errors),
            "original_candidates": original_total,
            "surviving_candidates": survivor_total,
            "candidate_reduction_rate": round(reduction, 6),
            "deterministic_select_errors": deterministic_errors,
            "llm_rank_errors": llm_errors,
            "abstain_errors": abstain_errors,
        },
        "errors": output_errors,
    }
    for path in (args.output_json, args.output_csv, args.log):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not csv_rows:
        raise RuntimeError("没有候选过滤结果可写入")
    with args.output_csv.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    logs.extend([
        "",
        f"候选压缩：{original_total} -> {survivor_total}（减少{reduction:.2%}）",
        f"确定性选择错误数：{deterministic_errors}",
        f"需要LLM排序错误数：{llm_errors}",
        f"必须ABSTAIN错误数：{abstain_errors}",
        f"JSON：{args.output_json}",
        f"CSV：{args.output_csv}",
    ])
    args.log.write_text("\n".join(logs) + "\n", encoding="utf-8")
    print(f"\n候选压缩：{original_total} -> {survivor_total}（减少{reduction:.2%}）")
    print(
        f"确定性选择={deterministic_errors}，LLM排序={llm_errors}，"
        f"ABSTAIN={abstain_errors}"
    )
    print(f"JSON：{args.output_json}")
    print(f"CSV：{args.output_csv}")
    print(f"日志：{args.log}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已中断] 用户终止候选过滤。")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[候选过滤停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
