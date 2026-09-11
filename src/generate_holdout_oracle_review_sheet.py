from __future__ import annotations

"""Generate Chinese oracle review sheet for manual confirmation of hold-out events."""

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from holdout_metadata_enrichment import (
    bool_zh,
    case_context_for_zh,
    display_source_title,
    domain_zh,
    enrich_event_row,
    first_window,
    load_source_family_by_event,
    parse_display_value,
    predicate_label_zh,
    semantic_type_zh,
    source_title_from_event,
)
from semantic_v2_common import PROJECT_DIR, write_csv

BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded"

REVIEW_COLUMNS = [
    ("event_id", "事件ID"),
    ("domain", "领域代码"),
    ("domain_zh", "领域"),
    ("semantic_type", "语义类型代码"),
    ("semantic_type_zh", "语义类型"),
    ("subject_label", "主体标签"),
    ("predicate_label_current", "谓词标签（当前）"),
    ("predicate_label_current_zh", "谓词标签说明（当前）"),
    ("predicate_label_proposed", "谓词标签（建议）"),
    ("predicate_label_proposed_zh", "谓词标签说明（建议）"),
    ("case_context_current", "案情上下文（当前）"),
    ("case_context_proposed", "案情上下文（建议）"),
    ("source_url", "来源链接"),
    ("source_window_1", "证据窗口1（原文）"),
    ("cand_002_display_value", "候选002（完整）"),
    ("cand_002_value_only", "候选002（取值）"),
    ("cand_003_display_value", "候选003（完整）"),
    ("oracle_candidate_id", "标准答案候选ID"),
    ("oracle_value", "标准答案取值"),
    ("oracle_evidence_span", "标准证据片段（原文）"),
    ("oracle_span_matches_window1", "证据片段与窗口1一致"),
    ("oracle_span_in_evidence", "证据片段在摘录中"),
    ("review_predicate_ok", "复核：谓词标签（填 是/否/修改）"),
    ("review_case_context_ok", "复核：案情上下文（填 是/否/修改）"),
    ("review_five_consistency_ok", "复核：五一致性（填 是/否/修改）"),
    ("review_oracle_span_ok", "复核：标准证据片段（填 是/否/修改）"),
    ("review_cand003_plausible", "复核：干扰项003合理（填 是/否/修改）"),
    ("reviewer_notes", "复核备注"),
    ("reviewer_name", "复核人"),
    ("reviewed_at", "复核时间"),
    ("agreement_status_after_review", "复核后一致状态"),
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def oracle_quote(oracle_row: dict[str, str]) -> str:
    spans = json.loads(oracle_row.get("evidence_spans_json", "[]") or "[]")
    if not spans:
        return ""
    return str(spans[0].get("quote", ""))


def review_status_blank() -> str:
    return ""


def build_row(
    event: dict[str, str],
    *,
    enriched: dict[str, str],
    window1: str,
    evidence: str,
    c2: dict[str, str],
    c3: dict[str, str],
    oracle: dict[str, str],
    source_family: str,
    source_title: str,
) -> dict[str, Any]:
    event_id = event["event_id"]
    quote = oracle_quote(oracle)
    _, c2_value = parse_display_value(c2.get("display_value", ""))
    predicate_current = event.get("predicate_label", "")
    predicate_proposed = enriched["predicate_label"]
    case_context_zh = case_context_for_zh(
        semantic_type=event.get("semantic_type", ""),
        domain=event.get("domain", ""),
        subject_label=event.get("subject_label", ""),
        predicate_label=predicate_current,
        source_family=source_family,
        source_title=source_title,
        source_url=event.get("source_url", ""),
        evidence_window=window1,
    )
    return {
        "event_id": event_id,
        "domain": event.get("domain", ""),
        "domain_zh": domain_zh(event.get("domain", "")),
        "semantic_type": event.get("semantic_type", ""),
        "semantic_type_zh": semantic_type_zh(event.get("semantic_type", "")),
        "subject_label": event.get("subject_label", ""),
        "predicate_label_current": predicate_current,
        "predicate_label_current_zh": predicate_label_zh(predicate_current),
        "predicate_label_proposed": predicate_proposed,
        "predicate_label_proposed_zh": predicate_label_zh(predicate_proposed),
        "case_context_current": case_context_zh,
        "case_context_proposed": case_context_zh,
        "source_url": event.get("source_url", ""),
        "source_window_1": window1,
        "cand_002_display_value": c2.get("display_value", ""),
        "cand_002_value_only": c2_value,
        "cand_003_display_value": c3.get("display_value", ""),
        "oracle_candidate_id": oracle.get("oracle_candidate_id", ""),
        "oracle_value": oracle.get("oracle_value", ""),
        "oracle_evidence_span": quote,
        "oracle_span_matches_window1": bool_zh(quote == window1),
        "oracle_span_in_evidence": bool_zh(bool(quote and quote in evidence)),
        "review_predicate_ok": review_status_blank(),
        "review_case_context_ok": review_status_blank(),
        "review_five_consistency_ok": review_status_blank(),
        "review_oracle_span_ok": review_status_blank(),
        "review_cand003_plausible": review_status_blank(),
        "reviewer_notes": review_status_blank(),
        "reviewer_name": review_status_blank(),
        "reviewed_at": review_status_blank(),
        "agreement_status_after_review": review_status_blank(),
    }


def write_chinese_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [header for _, header in REVIEW_COLUMNS]
    key_by_header = {header: key for key, header in REVIEW_COLUMNS}
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(key_by_header[header], "") for header in fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()

    args.benchmark_dir = args.benchmark_dir.resolve()
    args.output_dir = args.output_dir.resolve()

    event_csv = args.benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    doc_csv = args.benchmark_dir / "public" / "documents" / "external-real-document-template.csv"
    candidate_csv = args.benchmark_dir / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    oracle_csv = args.benchmark_dir / "private" / "oracle" / "external-real-oracle-template.csv"

    events = read_csv(event_csv)
    documents = {row["document_id"]: row for row in read_csv(doc_csv)}
    families_by_event = load_source_family_by_event(args.benchmark_dir)
    candidates_by_event: dict[str, list[dict[str, str]]] = {}
    for row in read_csv(candidate_csv):
        candidates_by_event.setdefault(row["event_id"], []).append(row)
    oracles = {row["event_id"]: row for row in read_csv(oracle_csv)}

    rows: list[dict[str, Any]] = []
    for event in events:
        event_id = event["event_id"]
        excerpt_path = args.benchmark_dir / "public" / "excerpts" / f"{event_id}-evidence.md"
        evidence = excerpt_path.read_text(encoding="utf-8", errors="replace") if excerpt_path.is_file() else ""
        window1 = first_window(evidence)
        source_title = source_title_from_event(event, documents)
        subject_source_title = display_source_title(event, documents)
        source_family = families_by_event.get(event_id, event.get("domain", ""))
        enriched = enrich_event_row(
            event,
            evidence=evidence,
            source_title=source_title,
            source_family=source_family,
            subject_source_title=subject_source_title,
        )
        cands = sorted(candidates_by_event.get(event_id, []), key=lambda row: row["candidate_id"])
        c2 = next((row for row in cands if row["candidate_id"] == "CAND_002"), {})
        c3 = next((row for row in cands if row["candidate_id"] == "CAND_003"), {})
        oracle = oracles.get(event_id, {})
        rows.append(
            build_row(
                event,
                enriched=enriched,
                window1=window1,
                evidence=evidence,
                c2=c2,
                c3=c3,
                oracle=oracle,
                source_family=source_family,
                source_title=source_title,
            )
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "oracle-review-sheet.csv"
    zh_csv_path = args.output_dir / "oracle-review-sheet-zh.csv"
    try:
        write_chinese_csv(csv_path, rows)
    except PermissionError:
        write_chinese_csv(zh_csv_path, rows)
        csv_path = zh_csv_path

    md_path = args.output_dir / "oracle-review-sheet.md"
    lines = [
        "# 保留集 Oracle 人工复核表",
        "",
        f"生成时间：{datetime.now(timezone.utc).isoformat()}",
        "",
        "请在 `{csv_path.name}` 中填写「复核」列：`是` / `否` / `修改`。",
        "证据窗口与标准证据片段保留英文原文；机器候选取值保持原样。",
        "",
    ]
    for row in rows[:5]:
        lines.extend(
            [
                f"## {row['event_id']}（{row['domain_zh']} / {row['semantic_type_zh']}）",
                "",
                f"- 主体标签：{row['subject_label']}",
                f"- 谓词标签说明：{row['predicate_label_proposed_zh']}",
                f"- 案情上下文：{row['case_context_proposed']}",
                f"- 候选002：`{row['cand_002_display_value']}`",
                f"- 候选003：`{row['cand_003_display_value']}`",
                f"- 证据片段与窗口1一致：{row['oracle_span_matches_window1']}",
                "",
                "### 证据窗口1（原文）",
                "",
                row["source_window_1"],
                "",
                "### 标准证据片段（原文）",
                "",
                row["oracle_evidence_span"],
                "",
                "---",
                "",
            ]
        )
    lines.append(f"\n完整可编辑表格：`{csv_path.name}`（共 {len(rows)} 条事件）。")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "benchmark": args.benchmark_dir.name,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "events": len(rows),
        "language": "zh-CN",
        "csv": str(csv_path.resolve().relative_to(PROJECT_DIR.resolve())),
        "markdown_preview": str(md_path.resolve().relative_to(PROJECT_DIR.resolve())),
        "instructions": "在 oracle-review-sheet.csv 中填写复核列：是/否/修改，并填写复核后一致状态。",
    }
    (args.output_dir / "oracle-review-sheet-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
