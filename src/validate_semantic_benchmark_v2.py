from __future__ import annotations

"""校验语义歧义基准 V2 的结构、Oracle隔离、人工标注与文件完整性。"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import (
    ALLOWED_OPERATORS,
    ALLOWED_SEMANTIC_TYPES,
    ALLOWED_SPLITS,
    ALLOWED_STATUSES,
    ALLOWED_VALUE_KINDS,
    CANDIDATE_CSV,
    DOCUMENT_CSV,
    DOCUMENT_DIR,
    EVENT_CSV,
    ORACLE_CSV,
    OUTPUT_DIR,
    load_csv,
    parse_json_object,
    resolve_project_path,
    sha256_file,
    split_ids,
    write_csv,
)


REPORT_CSV = OUTPUT_DIR / "semantic-v2-validation.csv"
REPORT_JSON = OUTPUT_DIR / "semantic-v2-validation.json"
LOG_FILE = OUTPUT_DIR / "semantic-v2-validation.log"

REQUIRED_EVENT_FIELDS = {
    "event_id", "split", "semantic_type", "title", "case_context",
    "subject_label", "predicate_label", "value_kind", "document_ids",
    "source_owl", "status",
}
REQUIRED_DOCUMENT_FIELDS = {
    "document_id", "file_name", "authority", "effective_from", "effective_to",
    "source_type", "sha256", "status",
}
REQUIRED_CANDIDATE_FIELDS = {
    "event_id", "candidate_id", "description", "display_value",
    "operation_json", "status",
}
REQUIRED_ORACLE_FIELDS = {
    "event_id", "oracle_candidate_id", "oracle_value", "evidence_document_ids",
    "evidence_spans_json", "annotator_1", "annotator_2", "adjudicator",
    "agreement_status", "status",
}

LEAK_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"正确答案\s*(是|为|:)" ,
        r"应选择\s*(CAND|OPTION)",
        r"oracle\s*(is|=|:)",
        r"gold\s*answer",
        r"ground\s*truth",
    )
]


class Auditor:
    def __init__(self) -> None:
        self.issues: list[dict[str, str]] = []

    def add(self, level: str, code: str, entity: str, message: str) -> None:
        self.issues.append({
            "level": level,
            "code": code,
            "entity": entity,
            "message": message,
        })

    def error(self, code: str, entity: str, message: str) -> None:
        self.add("ERROR", code, entity, message)

    def warn(self, code: str, entity: str, message: str) -> None:
        self.add("WARNING", code, entity, message)

    def info(self, code: str, entity: str, message: str) -> None:
        self.add("INFO", code, entity, message)


def headers(rows: list[dict[str, str]]) -> set[str]:
    return set(rows[0]) if rows else set()


def check_headers(
    audit: Auditor,
    rows: list[dict[str, str]],
    required: set[str],
    label: str,
) -> None:
    missing = sorted(required - headers(rows))
    if missing:
        audit.error("MISSING_COLUMNS", label, f"缺少字段：{missing}")


def unique_map(
    audit: Auditor,
    rows: list[dict[str, str]],
    key: str,
    label: str,
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows, start=2):
        value = row.get(key, "").strip()
        if not value:
            audit.error("EMPTY_ID", f"{label}:{index}", f"{key}为空")
        elif value in result:
            audit.error("DUPLICATE_ID", value, f"{label}中重复")
        else:
            result[value] = row
    return result


def check_leakage(audit: Auditor, rows: list[dict[str, str]], label: str) -> None:
    banned_headers = {
        name for name in headers(rows)
        if re.search(r"oracle|gold|ground.?truth|correct.?answer", name, re.I)
    }
    if banned_headers:
        audit.error("PUBLIC_ORACLE_COLUMN", label, f"公开表含答案字段：{sorted(banned_headers)}")
    for index, row in enumerate(rows, start=2):
        text = " ".join(str(value) for value in row.values())
        for pattern in LEAK_PATTERNS:
            if pattern.search(text):
                audit.error(
                    "ANSWER_HINT_IN_PUBLIC_TEXT",
                    f"{label}:{index}",
                    f"检测到答案提示：{pattern.pattern}",
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验语义歧义基准V2")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="要求READY子集达到数量和类型分布；允许其余模板继续保持DRAFT",
    )
    parser.add_argument(
        "--require-all-ready",
        action="store_true",
        help="论文最终冻结时使用：要求模板中的全部事件均为READY",
    )
    parser.add_argument("--min-events", type=int, default=30)
    parser.add_argument("--min-per-type", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audit = Auditor()
    event_rows = load_csv(EVENT_CSV)
    document_rows = load_csv(DOCUMENT_CSV)
    candidate_rows = load_csv(CANDIDATE_CSV)
    oracle_rows = load_csv(ORACLE_CSV)

    check_headers(audit, event_rows, REQUIRED_EVENT_FIELDS, "events")
    check_headers(audit, document_rows, REQUIRED_DOCUMENT_FIELDS, "documents")
    check_headers(audit, candidate_rows, REQUIRED_CANDIDATE_FIELDS, "candidates")
    check_headers(audit, oracle_rows, REQUIRED_ORACLE_FIELDS, "oracle")
    check_leakage(audit, event_rows, "events")
    check_leakage(audit, document_rows, "documents")
    check_leakage(audit, candidate_rows, "candidates")

    events = unique_map(audit, event_rows, "event_id", "events")
    documents = unique_map(audit, document_rows, "document_id", "documents")
    oracles = unique_map(audit, oracle_rows, "event_id", "oracle")

    candidates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    candidate_keys: set[tuple[str, str]] = set()
    for index, row in enumerate(candidate_rows, start=2):
        event_id = row.get("event_id", "").strip()
        candidate_id = row.get("candidate_id", "").strip()
        key = (event_id, candidate_id)
        if not event_id or not candidate_id:
            audit.error("EMPTY_CANDIDATE_KEY", f"candidates:{index}", str(key))
            continue
        if key in candidate_keys:
            audit.error("DUPLICATE_CANDIDATE", f"{event_id}/{candidate_id}", "候选重复")
        candidate_keys.add(key)
        candidates_by_event[event_id].append(row)
        if event_id not in events:
            audit.error("ORPHAN_CANDIDATE", f"{event_id}/{candidate_id}", "事件不存在")

    ready_events: list[dict[str, str]] = []
    for event_id, row in events.items():
        status = row.get("status", "").strip().upper()
        if status not in ALLOWED_STATUSES:
            audit.error("INVALID_STATUS", event_id, f"事件状态={status!r}")
        if status != "READY":
            level = audit.error if args.require_all_ready else audit.warn
            level("EVENT_NOT_READY", event_id, f"状态={status or '空'}")
            continue
        ready_events.append(row)

        split = row.get("split", "").strip().lower()
        semantic_type = row.get("semantic_type", "").strip().upper()
        value_kind = row.get("value_kind", "").strip().lower()
        if split not in ALLOWED_SPLITS:
            audit.error("INVALID_SPLIT", event_id, f"split={split!r}")
        if semantic_type not in ALLOWED_SEMANTIC_TYPES:
            audit.error("INVALID_SEMANTIC_TYPE", event_id, semantic_type)
        if value_kind not in ALLOWED_VALUE_KINDS:
            audit.error("INVALID_VALUE_KIND", event_id, value_kind)
        for field in (
            "title", "case_context", "subject_label", "predicate_label", "source_owl"
        ):
            if not row.get(field, "").strip() or "待填写" in row.get(field, ""):
                audit.error("INCOMPLETE_EVENT", event_id, f"{field}未填写")

        try:
            source_owl = resolve_project_path(row.get("source_owl", ""))
            if not source_owl.is_file():
                audit.error("MISSING_SOURCE_OWL", event_id, str(source_owl))
        except Exception as exc:
            audit.error("INVALID_SOURCE_OWL", event_id, str(exc))

        doc_ids = split_ids(row.get("document_ids", ""))
        if not doc_ids:
            audit.error("NO_DOCUMENT", event_id, "没有关联文档")
        for document_id in doc_ids:
            if document_id not in documents:
                audit.error("UNKNOWN_DOCUMENT", event_id, document_id)

        ready_candidates = [
            item for item in candidates_by_event.get(event_id, [])
            if item.get("status", "").strip().upper() == "READY"
        ]
        if not 2 <= len(ready_candidates) <= 4:
            audit.error(
                "INVALID_CANDIDATE_COUNT",
                event_id,
                f"READY候选数={len(ready_candidates)}，要求2—4",
            )
        for candidate in ready_candidates:
            candidate_id = candidate["candidate_id"]
            if not candidate.get("display_value", "").strip():
                audit.error("EMPTY_DISPLAY_VALUE", f"{event_id}/{candidate_id}", "")
            try:
                operation = parse_json_object(
                    candidate.get("operation_json", ""),
                    f"{event_id}/{candidate_id}/operation_json",
                )
                if operation.get("operator") not in ALLOWED_OPERATORS:
                    audit.error(
                        "INVALID_OPERATOR",
                        f"{event_id}/{candidate_id}",
                        str(operation.get("operator")),
                    )
            except Exception as exc:
                audit.error("INVALID_OPERATION_JSON", f"{event_id}/{candidate_id}", str(exc))

        oracle = oracles.get(event_id)
        if oracle is None:
            audit.error("MISSING_ORACLE", event_id, "缺少私有Oracle行")
        else:
            oracle_status = oracle.get("status", "").strip().upper()
            if oracle_status != "READY":
                audit.error("ORACLE_NOT_READY", event_id, f"状态={oracle_status or '空'}")
            oracle_candidate = oracle.get("oracle_candidate_id", "").strip()
            ready_candidate_ids = {item["candidate_id"] for item in ready_candidates}
            if oracle_candidate not in ready_candidate_ids:
                audit.error(
                    "ORACLE_NOT_IN_CANDIDATES", event_id, f"oracle={oracle_candidate!r}"
                )
            agreement = oracle.get("agreement_status", "").strip().upper()
            if agreement not in {"AGREED", "ADJUDICATED"}:
                audit.error("ANNOTATION_NOT_RESOLVED", event_id, f"agreement={agreement}")
            if not oracle.get("annotator_1", "").strip() or not oracle.get(
                "annotator_2", ""
            ).strip():
                audit.error("MISSING_ANNOTATORS", event_id, "需要两名独立标注者")
            if agreement == "ADJUDICATED" and not oracle.get("adjudicator", "").strip():
                audit.error("MISSING_ADJUDICATOR", event_id, "裁决事件缺少第三人")
            evidence_docs = split_ids(oracle.get("evidence_document_ids", ""))
            if not evidence_docs:
                audit.error("MISSING_EVIDENCE", event_id, "没有Oracle证据文档")
            elif not set(evidence_docs).issubset(set(doc_ids)):
                audit.error(
                    "EVIDENCE_OUTSIDE_EVENT_DOCUMENTS",
                    event_id,
                    f"evidence={evidence_docs}, event_docs={doc_ids}",
                )
            try:
                spans = json.loads(oracle.get("evidence_spans_json", ""))
                if not isinstance(spans, list) or not spans:
                    raise ValueError("证据范围必须是非空JSON数组")
            except Exception as exc:
                audit.error("INVALID_EVIDENCE_SPANS", event_id, str(exc))

    for document_id, row in documents.items():
        status = row.get("status", "").strip().upper()
        if status not in ALLOWED_STATUSES:
            audit.error("INVALID_STATUS", document_id, f"文档状态={status!r}")
        if status != "READY":
            if args.require_ready and any(
                document_id in split_ids(event.get("document_ids", ""))
                for event in ready_events
            ):
                audit.error("DOCUMENT_NOT_READY", document_id, f"状态={status or '空'}")
            continue
        file_name = row.get("file_name", "").strip()
        document_path = DOCUMENT_DIR / file_name
        if not file_name or not document_path.is_file():
            audit.error("MISSING_DOCUMENT_FILE", document_id, str(document_path))
            continue
        expected_hash = row.get("sha256", "").strip().lower()
        actual_hash = sha256_file(document_path)
        if not expected_hash:
            audit.warn("MISSING_DOCUMENT_HASH", document_id, f"实际={actual_hash}")
        elif expected_hash != actual_hash:
            audit.error(
                "DOCUMENT_HASH_MISMATCH", document_id,
                f"记录={expected_hash}，实际={actual_hash}",
            )
        try:
            int(row.get("authority", ""))
        except ValueError:
            audit.error("INVALID_AUTHORITY", document_id, row.get("authority", ""))

    ready_count = len(ready_events)
    type_counts = Counter(row.get("semantic_type", "").strip().upper() for row in ready_events)
    split_counts = Counter(row.get("split", "").strip().lower() for row in ready_events)
    enforce_ready_subset = args.require_ready or args.require_all_ready
    if enforce_ready_subset and ready_count < args.min_events:
        audit.error("TOO_FEW_READY_EVENTS", "benchmark", f"{ready_count} < {args.min_events}")
    if enforce_ready_subset:
        for semantic_type in sorted(ALLOWED_SEMANTIC_TYPES):
            if type_counts[semantic_type] < args.min_per_type:
                audit.error(
                    "TYPE_UNDERREPRESENTED", semantic_type,
                    f"{type_counts[semantic_type]} < {args.min_per_type}",
                )

    errors = sum(item["level"] == "ERROR" for item in audit.issues)
    warnings = sum(item["level"] == "WARNING" for item in audit.issues)
    if not audit.issues:
        audit.info("VALIDATION_PASS", "benchmark", "未发现结构或泄漏问题")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(REPORT_CSV, audit.issues)
    payload = {
        "schema_version": "2.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "require_ready": args.require_ready,
        "require_all_ready": args.require_all_ready,
        "events_total": len(events),
        "events_ready": ready_count,
        "type_counts": dict(type_counts),
        "split_counts": dict(split_counts),
        "errors": errors,
        "warnings": warnings,
        "issues": audit.issues,
    }
    REPORT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "语义歧义基准V2校验日志",
        f"事件总数={len(events)}，READY={ready_count}",
        f"类型分布={dict(type_counts)}",
        f"划分分布={dict(split_counts)}",
        f"错误={errors}，警告={warnings}",
        "",
    ] + [
        f"[{item['level']}] {item['code']} | {item['entity']} | {item['message']}"
        for item in audit.issues
    ]
    LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("语义歧义基准V2校验")
    print(f"事件总数={len(events)}，READY={ready_count}")
    print(f"类型分布={dict(type_counts)}")
    print(f"划分分布={dict(split_counts)}")
    print(f"错误={errors}，警告={warnings}")
    print(f"报告：{REPORT_CSV}")
    print(f"JSON：{REPORT_JSON}")
    print(f"日志：{LOG_FILE}")
    if errors:
        print("[校验未通过] 请根据ERROR记录继续补充或修正数据。")
        return 2
    print("[校验通过] 数据结构、Oracle隔离和文件完整性满足当前要求。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[校验停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
