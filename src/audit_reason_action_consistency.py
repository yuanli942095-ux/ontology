#!/usr/bin/env python3
"""审计大模型“推理理由—最终动作”的一致性。

默认读取 output/candidate-information-ablation-details.csv，并生成：
  output/reason-action-consistency-details.csv
  output/reason-action-consistency-summary.csv
  output/reason-action-consistency-by-event.csv
  output/reason-action-consistency.json
  output/reason-action-consistency.log

本脚本不调用大模型、不读取形式策略和 Oracle 文件；Oracle 仅来自已经完成
离线评估的明细 CSV。自动分类属于可重复的诊断性启发式，低置信度记录会标记
MANUAL_REVIEW，不会强行归为语义错误。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CLASSIFICATIONS = (
    "SUCCESS",
    "ACTION_MAPPING_ERROR",
    "SEMANTIC_ERROR",
    "OUTPUT_CONTRADICTION",
    "ABSTAIN_VALID",
    "ABSTAIN_UNNECESSARY",
    "INVALID_OUTPUT",
    "MANUAL_REVIEW",
)

POSITIVE_CUES = (
    "允许值", "适用值", "正确值", "目标值", "应为", "应选", "故选", "选择",
    "因此为", "结果为", "调整为", "期限为", "值为", "适用", "匹配",
)
NEGATIVE_CUES = (
    "不适用", "不匹配", "不是", "不应", "错误", "排除", "未满足", "否则",
)
ABSTAIN_TOKENS = {"ABSTAIN", "REJECTED", "NONE", "NULL", ""}
SELECTED_STATUSES = {"SELECTED", "SAFE_ACCEPT", "DETERMINISTIC_SELECT", "QWEN_SELECT"}


def norm(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if re.fullmatch(r"[-+]?\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def truthy(value: Any) -> bool:
    return norm(value).lower() in {"1", "true", "yes", "y", "pass"}


def numeric_token_pattern(value: str) -> re.Pattern[str] | None:
    value = norm(value)
    if not value:
        return None
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        return re.compile(rf"(?<![\d.]){re.escape(value)}(?![\d.])")
    return re.compile(re.escape(value), re.IGNORECASE)


@dataclass(frozen=True)
class EvidenceScore:
    value: str
    mentions: int
    positive: int
    negative: int
    score: int
    excerpts: tuple[str, ...]


def score_value(reason: str, value: str, radius: int = 36) -> EvidenceScore:
    pattern = numeric_token_pattern(value)
    if not pattern or not reason:
        return EvidenceScore(value, 0, 0, 0, 0, ())
    positives = negatives = 0
    excerpts: list[str] = []
    matches = list(pattern.finditer(reason))
    for match in matches:
        start = max(0, match.start() - radius)
        end = min(len(reason), match.end() + radius)
        context = reason[start:end]
        excerpts.append(context.replace("\r", " ").replace("\n", " "))
        positives += sum(cue in context for cue in POSITIVE_CUES)
        negatives += sum(cue in context for cue in NEGATIVE_CUES)
    score = len(matches) + 2 * positives - 3 * negatives
    return EvidenceScore(value, len(matches), positives, negatives, score, tuple(excerpts[:3]))


def raw_declares_abstain(row: dict[str, str]) -> bool:
    raw = row.get("raw_content", "") or ""
    if not raw:
        return False
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            if payload.get("abstain") is True:
                return True
            # 候选选择接口通常只返回 option_id，不包含 value。字段缺失不等于
            # 空值/ABSTAIN；只有模型确实返回了 value 字段时才检查其内容。
            if "value" in payload:
                return norm(payload.get("value")).upper() in ABSTAIN_TOKENS
            return False
    except (json.JSONDecodeError, TypeError):
        pass
    return bool(re.search(r'"abstain"\s*:\s*true', raw, re.I))


def raw_contradicts_status(row: dict[str, str]) -> bool:
    raw = row.get("raw_content", "") or ""
    status = norm(row.get("status")).upper()
    selected = norm(row.get("selected_value")).upper()
    if status == "ABSTAIN":
        # abstain=false 与最终 ABSTAIN 冲突；候选/数值字段若仍给出普通值也冲突。
        return bool(re.search(r'"abstain"\s*:\s*false', raw, re.I)) or selected not in ABSTAIN_TOKENS
    return raw_declares_abstain(row)


def classify(row: dict[str, str]) -> tuple[str, str, EvidenceScore, EvidenceScore, str]:
    status = norm(row.get("status")).upper()
    selected = norm(row.get("selected_value"))
    oracle = norm(row.get("oracle_value"))
    reason = row.get("reason", "") or ""
    oracle_score = score_value(reason, oracle)
    selected_score = score_value(reason, selected)

    if raw_contradicts_status(row):
        return (
            "OUTPUT_CONTRADICTION",
            "结构化输出中的abstain/value与程序记录的状态或动作冲突",
            oracle_score,
            selected_score,
            "HIGH",
        )

    if status == "ABSTAIN":
        if oracle_score.positive > 0 and oracle_score.score > 0:
            return (
                "ABSTAIN_UNNECESSARY",
                "理由已正向支持Oracle值，但最终仍主动放弃",
                oracle_score,
                selected_score,
                "HIGH",
            )
        return (
            "ABSTAIN_VALID",
            "未检测到理由已明确支持Oracle值；保守记为有效放弃",
            oracle_score,
            selected_score,
            "LOW",
        )

    invalid = status not in SELECTED_STATUSES or not selected or selected.upper() in ABSTAIN_TOKENS
    if invalid:
        return "INVALID_OUTPUT", f"状态或最终值无效：status={status}, value={selected}", oracle_score, selected_score, "HIGH"

    is_correct = truthy(row.get("oracle_correct")) or (oracle != "" and selected == oracle)
    if is_correct:
        return "SUCCESS", "最终动作与Oracle一致", oracle_score, selected_score, "HIGH"

    # 错误动作，但理由明确支持Oracle，属于典型“想对、点错”。
    if oracle_score.positive > 0 and oracle_score.score >= max(2, selected_score.score + 1):
        return (
            "ACTION_MAPPING_ERROR",
            "理由对Oracle值的正向支持强于对最终所选值的支持",
            oracle_score,
            selected_score,
            "HIGH",
        )
    if oracle_score.positive > 0 and selected_score.positive == 0:
        return (
            "ACTION_MAPPING_ERROR",
            "理由明确正向支持Oracle值，却输出了另一个候选",
            oracle_score,
            selected_score,
            "MEDIUM",
        )
    if selected_score.positive > 0 and selected_score.score > oracle_score.score:
        return (
            "SEMANTIC_ERROR",
            "理由更支持错误的最终值，属于语义判断错误",
            oracle_score,
            selected_score,
            "MEDIUM",
        )
    return (
        "MANUAL_REVIEW",
        "理由中数值证据不足或相互冲突，自动规则不强行分类",
        oracle_score,
        selected_score,
        "LOW",
    )


def percentage(n: int, d: int) -> str:
    return f"{(100.0 * n / d if d else 0.0):.2f}%"


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def make_group_rows(rows: list[dict[str, str]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row.get(key, "")].append(row)
    result: list[dict[str, Any]] = []
    for group_name in sorted(groups):
        items = groups[group_name]
        counts = Counter(item["audit_class"] for item in items)
        total = len(items)
        record: dict[str, Any] = {key: group_name, "attempts": total}
        for label in CLASSIFICATIONS:
            record[label.lower()] = counts[label]
        record["action_mapping_error_rate"] = percentage(counts["ACTION_MAPPING_ERROR"], total)
        record["semantic_error_rate"] = percentage(counts["SEMANTIC_ERROR"], total)
        record["manual_review_rate"] = percentage(counts["MANUAL_REVIEW"], total)
        result.append(record)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计推理理由与最终候选动作是否一致")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("output/candidate-information-ablation-details.csv"),
        help="消融实验逐次明细CSV",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--only-method", action="append", default=[], help="只审计指定方法，可重复传入")
    parser.add_argument("--only-event", action="append", default=[], help="只审计指定事件，可重复传入")
    parser.add_argument("--fail-on-review", action="store_true", help="存在MANUAL_REVIEW时返回退出码2")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"找不到输入文件：{input_path}")

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    required = {"event_id", "method", "status", "selected_value", "oracle_value", "reason"}
    missing = required - set(source_rows[0] if source_rows else {})
    if missing:
        raise ValueError(f"输入CSV缺少字段：{sorted(missing)}")

    methods = set(args.only_method)
    events = set(args.only_event)
    if methods:
        source_rows = [row for row in source_rows if row.get("method") in methods]
    if events:
        source_rows = [row for row in source_rows if row.get("event_id") in events]

    audited: list[dict[str, str]] = []
    for row in source_rows:
        label, explanation, oracle_score, selected_score, confidence = classify(row)
        enriched = dict(row)
        enriched.update(
            {
                "audit_class": label,
                "audit_confidence": confidence,
                "audit_explanation": explanation,
                "reason_oracle_mentions": str(oracle_score.mentions),
                "reason_oracle_positive": str(oracle_score.positive),
                "reason_oracle_negative": str(oracle_score.negative),
                "reason_oracle_score": str(oracle_score.score),
                "reason_selected_mentions": str(selected_score.mentions),
                "reason_selected_positive": str(selected_score.positive),
                "reason_selected_negative": str(selected_score.negative),
                "reason_selected_score": str(selected_score.score),
                "oracle_excerpts": " || ".join(oracle_score.excerpts),
                "selected_excerpts": " || ".join(selected_score.excerpts),
            }
        )
        audited.append(enriched)

    out = args.output_dir.resolve()
    details_path = out / "reason-action-consistency-details.csv"
    summary_path = out / "reason-action-consistency-summary.csv"
    event_path = out / "reason-action-consistency-by-event.csv"
    json_path = out / "reason-action-consistency.json"
    log_path = out / "reason-action-consistency.log"

    extra_fields = [
        "audit_class", "audit_confidence", "audit_explanation",
        "reason_oracle_mentions", "reason_oracle_positive", "reason_oracle_negative", "reason_oracle_score",
        "reason_selected_mentions", "reason_selected_positive", "reason_selected_negative", "reason_selected_score",
        "oracle_excerpts", "selected_excerpts",
    ]
    original_fields = list(source_rows[0]) if source_rows else []
    write_csv(details_path, original_fields + extra_fields, audited)
    method_rows = make_group_rows(audited, "method")
    event_rows = make_group_rows(audited, "event_id")
    summary_fields = ["method", "attempts"] + [x.lower() for x in CLASSIFICATIONS] + [
        "action_mapping_error_rate", "semantic_error_rate", "manual_review_rate"
    ]
    event_fields = ["event_id", "attempts"] + [x.lower() for x in CLASSIFICATIONS] + [
        "action_mapping_error_rate", "semantic_error_rate", "manual_review_rate"
    ]
    write_csv(summary_path, summary_fields, method_rows)
    write_csv(event_path, event_fields, event_rows)

    totals = Counter(row["audit_class"] for row in audited)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path),
        "attempts": len(audited),
        "classification_counts": {label: totals[label] for label in CLASSIFICATIONS},
        "method_summary": method_rows,
        "event_summary": event_rows,
        "boundary": "自动分类为诊断性启发式；MANUAL_REVIEW必须人工复核。Oracle仅用于离线审计。",
    }
    out.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["推理—动作一致性审计", f"输入：{input_path}", f"记录数：{len(audited)}", ""]
    for label in CLASSIFICATIONS:
        lines.append(f"{label}：{totals[label]} ({percentage(totals[label], len(audited))})")
    lines.extend(["", "按方法："])
    for item in method_rows:
        lines.append(
            f"[{item['method']}] 尝试={item['attempts']} | 成功={item['success']} | "
            f"动作映射错误={item['action_mapping_error']} | 语义错误={item['semantic_error']} | "
            f"输出矛盾={item['output_contradiction']} | 人工复核={item['manual_review']}"
        )
    lines.extend(
        [
            "",
            f"明细：{details_path}",
            f"按方法：{summary_path}",
            f"按事件：{event_path}",
            f"JSON：{json_path}",
            "[边界] 自动分类为诊断性启发式；MANUAL_REVIEW必须人工复核。",
        ]
    )
    log_text = "\n".join(lines) + "\n"
    log_path.write_text(log_text, encoding="utf-8")
    print(log_text, end="")
    return 2 if args.fail_on_review and totals["MANUAL_REVIEW"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
