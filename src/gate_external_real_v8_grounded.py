from __future__ import annotations

"""Offline KEEP/REPLACE/DROP gate for external-real-v8-grounded.

Builder/retrieval never read private Oracle or write policy tokens into excerpts.
This gate may compare stored windows against formal-policy conclusion tokens.
WARN and FAIL are never KEEP.
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import audit_external_real_v3_naturalized_evidence_support as support
import build_external_real_v8_grounded as v8
from adjudicate_external_real_v8_semantic_support import adjudicate_semantic_support
from external_real_v8_layout import BenchmarkLayout, SUPPORT_GATE_VERSION


ROOT = v8.ROOT
DST = v8.DST
OUTPUT_DIR = v8.OUTPUT_DIR
V4 = ROOT / "benchmark" / "external-real-v4-evidence-aligned"
DEFAULT_POLICY_DIR = V4 / "rules"
DEFAULT_CANDIDATE_CSV = V4 / "input" / "external-real-candidate-template.csv"

VALUE_SURFACE = (
    (r"\bspring\b", "春播"),
    (r"\bsummer[_ ]autumn\b", "夏播 秋播"),
    (r"\bsummer\b", "夏播"),
    (r"\bautumn\b", "秋播"),
    (r"\bwinter\b", "冬播"),
    (r"\btotal[_ ]only\b", "合计"),
    (r"\btotal\b", "合计 保险金额"),
    (r"\badded\b", "新增"),
    (r"\bremoved\b", "删除"),
    (r"\bexception\b", "除外 例外"),
)

TOKEN_ALIASES = {
    "spring": {"春播", "春季"},
    "summer": {"夏播", "夏季"},
    "autumn": {"秋播", "秋季"},
    "winter": {"冬播", "冬季"},
    "total": {"合计", "总额", "保险金额"},
    "added": {"新增", "增加", "add"},
    "removed": {"删除", "取消"},
    "exception": {"除外", "例外", "except"},
    "five": {"五", "5"},
    "lodging": {"倒伏"},
    "definition": {"释义", "定义"},
    "level": {"级", "等级"},
    "below": {"以下", "低于"},
    "above": {"以上", "高于"},
    "no": {"不倒", "未"},
    "scope": {"范围", "适用"},
    "formula": {"公式", "计算"},
    "accident": {"事故"},
    "loss": {"损失"},
    "rate": {"费率", "比例"},
    "times": {"倍", "次数"},
    "limit": {"限额", "上限"},
    "date": {"日期", "当日"},
    "amount": {"金额", "保险金额"},
    "split": {"分项", "拆分"},
    "grade": {"级"},
    "weather": {"天气", "气象", "灾害"},
    "events": {"事故", "灾害"},
    "specified": {"列明", "约定"},
    "stage": {"阶段"},
    "table": {"表", "附表"},
    "frost": {"霜冻", "霜"},
    "late": {"晚", "后期"},
    "named": {"列明"},
    "listed": {"列明", "下列"},
    "epidemics": {"疫病", "疫情"},
    "storm": {"暴风", "风灾", "大风"},
    "damage": {"损坏", "损失"},
    "trigger": {"因", "造成"},
    "verified": {"核定", "确认"},
    "ratio": {"比例"},
    "coverage": {"保险责任", "保障"},
    "replanting": {"补种", "改种"},
    "cost": {"费用"},
    "deductible": {"免赔"},
    "specific": {"特定", "专项"},
    "categories": {"类别", "种类"},
    "rotation": {"轮作", "轮种"},
    "period": {"期间", "保险期间"},
    "year": {"年"},
    "effective": {"生效"},
    "deadline": {"期限", "截止"},
    "notice": {"通知"},
    "window": {"期限"},
    "evidence": {"证明", "材料"},
    "field": {"田间", "实地"},
    "verification": {"查勘", "核定"},
    "adjustment": {"调整", "赔偿"},
    "area": {"面积"},
    "eligible": {"符合", "可保"},
    "agricultural": {"农业", "农"},
    "policy": {"条款", "保险"},
}


_BASE_TOKENS = support.tokens


def tokens_with_cjk_grams(text: str) -> set[str]:
    """Keep compact policy tokens and 2-character CJK grams from official prose."""
    base = _BASE_TOKENS(text)
    extra: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", str(text).lower()):
        extra.add(run)
        extra.update(run[index : index + 2] for index in range(len(run) - 1))
    return base | extra


def expand_conclusion_values(values: list[str]) -> list[str]:
    """Map compact policy codes onto words that official sources actually use."""
    expanded: list[str] = []
    for value in values:
        text = str(value).replace("=", " ").replace(";", " ").replace("_", " ")
        for pattern, replacement in VALUE_SURFACE:
            text = re.sub(pattern, replacement, text, flags=re.I)
        expanded.append(text)
    return expanded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="gate external-real-v8-grounded events")
    parser.add_argument("--only", default="")
    parser.add_argument("--policy-dir", type=Path, default=DEFAULT_POLICY_DIR)
    parser.add_argument("--candidate-csv", type=Path, default=DEFAULT_CANDIDATE_CSV)
    parser.add_argument("--query-mode", choices=("full", "light"), default="full")
    parser.add_argument("--max-replacements", type=int, default=4)
    return parser.parse_args()


def candidates_by_event(path: Path) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    if not path.is_file():
        return grouped
    for row in v8.read_csv(path):
        if str(row.get("status", "")).upper() == "READY":
            grouped[row["event_id"]].append(row.get("display_value", ""))
    return grouped


def token_supported(token: str, source_tokens: set[str]) -> bool:
    if token in source_tokens:
        return True
    return bool(TOKEN_ALIASES.get(token, set()) & source_tokens)


def score_event_v8(
    event: dict[str, str],
    evidence_text: str,
    allowed_values: list[str],
    candidate_values: list[str],
) -> dict[str, Any]:
    body = support.evidence_body(evidence_text)
    source_title_match = re.search(
        r"^- (?:Current )?source title:\s*(.+)$",
        evidence_text,
        flags=re.M | re.I,
    )
    source_title = source_title_match.group(1).strip() if source_title_match else ""
    source_tokens = tokens_with_cjk_grams(f"{source_title}\n{body}")
    expected_tokens = _BASE_TOKENS(" ".join(allowed_values)) | _BASE_TOKENS(
        " ".join(expand_conclusion_values(allowed_values))
    )
    compact_expected = _BASE_TOKENS(" ".join(allowed_values))
    distractor_tokens = _BASE_TOKENS(" ".join(value for value in candidate_values if value not in allowed_values))
    distinctive = compact_expected - distractor_tokens
    matched = {token for token in compact_expected if token_supported(token, source_tokens)}
    missing = compact_expected - matched
    critical_numeric = support.numeric_tokens(compact_expected)
    missing_numeric = critical_numeric - source_tokens
    ratio = len(matched) / len(compact_expected) if compact_expected else 0.0
    if not allowed_values:
        status, reason = "FAIL", "no unique executable policy conclusion"
    elif not body:
        status, reason = "FAIL", "no raw evidence body"
    elif missing_numeric:
        status, reason = "FAIL", "critical numeric/date/entity tokens missing"
    elif distinctive and not any(token_supported(token, source_tokens) for token in distinctive):
        status, reason = "FAIL", "no distinctive conclusion token in source window"
    elif ratio >= 0.60:
        status, reason = "PASS", "strong lexical support"
    elif ratio >= 0.30:
        status, reason = "WARN", "partial lexical support; manual review required"
    else:
        status, reason = "FAIL", "weak event-level lexical support"
    return {
        "event_id": event["event_id"],
        "domain": event["domain"],
        "semantic_type": event["semantic_type"],
        "source_title": source_title,
        "status": status,
        "reason": reason,
        "support_ratio": round(ratio, 4),
        "expected_token_count": len(compact_expected),
        "matched_token_count": len(matched),
        "distinctive_token_count": len(distinctive),
        "missing_tokens": "|".join(sorted(missing)),
        "missing_numeric_tokens": "|".join(sorted(missing_numeric)),
        "evidence_body_chars": len(body),
        "surface_token_count": len(expected_tokens),
    }


def score_stored_event(
    event: dict[str, str],
    policy_dir: Path,
    candidate_values: list[str],
) -> dict[str, Any]:
    event_id = event["event_id"]
    layout = BenchmarkLayout(DST)
    excerpt = layout.public_excerpts / f"{event_id}-evidence.md"
    if not excerpt.is_file():
        excerpt = DST / "documents" / "excerpts" / f"{event_id}-evidence.md"
    if not excerpt.is_file():
        return {
            "event_id": event_id,
            "status": "FAIL",
            "reason": "missing evidence excerpt",
            "support_ratio": 0.0,
        }
    policy_path = policy_dir / f"{event_id}-formal-policy.json"
    allowed, error = support.selected_policy_values(policy_path) if policy_path.is_file() else ([], "missing policy")
    row = score_event_v8(
        event,
        excerpt.read_text(encoding="utf-8-sig"),
        allowed,
        candidate_values,
    )
    if error:
        row["status"] = "FAIL"
        row["reason"] = error
    return row


def keepable(retrieval_status: str, semantic_support: str) -> bool:
    return retrieval_status == "RETRIEVAL_READY" and str(semantic_support).upper() in {
        "PASS",
        "SEMANTIC_PASS",
    }


def alternative_source_ids(
    registry: dict[str, dict[str, str]],
    domain: str,
    current_id: str,
) -> list[str]:
    return [source_id for source_id in v8.domain_source_ids(registry, domain) if source_id != current_id]


def apply_source(
    event: dict[str, str],
    docs: list[dict[str, str]],
    source_id: str,
    family: dict[str, str],
    cache: dict[str, dict[str, str]],
    query_mode: str,
) -> dict[str, Any]:
    retrieved = v8.retrieve_family_windows(event, family, cache, query_mode)
    v8.write_event_files(event, docs, family, retrieved)
    return retrieved


def excerpt_path(event_id: str) -> Path:
    layout = BenchmarkLayout(DST)
    staged = layout.public_excerpts / f"{event_id}-evidence.md"
    if staged.is_file() or layout.public_excerpts.is_dir():
        return staged
    return DST / "documents" / "excerpts" / f"{event_id}-evidence.md"


def document_file(doc: dict[str, str]) -> Path:
    layout = BenchmarkLayout(DST)
    staged = layout.public_documents / doc["file_name"]
    if staged.exists() or layout.public_documents.is_dir():
        return staged
    return DST / "documents" / doc["file_name"]


def adjudicate_current(event: dict[str, str], support_row: dict[str, Any]) -> dict[str, Any]:
    path = excerpt_path(event["event_id"])
    text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    return adjudicate_semantic_support(event, text, support_row)


def gate(args: argparse.Namespace) -> list[dict[str, Any]]:
    only = v8.parse_only(args.only)
    layout = BenchmarkLayout(DST)
    if layout.event_csv.is_file():
        events_path = layout.event_csv
        docs_path = layout.document_csv
        retrieval_path = layout.retrieval_csv
        registry_path = layout.registry_csv
        excerpt_root = layout.public_excerpts
        document_root = layout.public_documents
        policy_dir = layout.rules if layout.rules.is_dir() else args.policy_dir
        candidate_csv = layout.candidate_csv if layout.candidate_csv.is_file() else args.candidate_csv
    else:
        events_path = DST / "input" / "external-real-event-template.csv"
        docs_path = DST / "input" / "external-real-document-template.csv"
        retrieval_path = DST / "source-intake" / "external-real-v8-event-retrieval.csv"
        registry_path = DST / "source-intake" / "normative-source-registry.csv"
        excerpt_root = DST / "documents" / "excerpts"
        document_root = DST / "documents"
        policy_dir = args.policy_dir
        candidate_csv = args.candidate_csv
    events = v8.read_csv(events_path)
    docs = v8.read_csv(docs_path)
    retrieval_by_event = {row["event_id"]: row for row in v8.read_csv(retrieval_path)} if retrieval_path.is_file() else {}
    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for doc in docs:
        docs_by_event[doc["event_id"]].append(doc)
    registry = v8.load_registry(registry_path)
    cache = v8.load_cache(v8.reuse_v6_cache())
    candidate_map = candidates_by_event(candidate_csv)
    dispositions: list[dict[str, Any]] = []
    selected = [event for event in events if not only or event["event_id"] in only]

    for event in selected:
        event_id = event["event_id"]
        retrieval = dict(retrieval_by_event.get(event_id, {}))
        current_id = str(retrieval.get("source_id") or v8.locked_source_id(event, {}, registry))
        tried = [current_id]
        support_row = score_stored_event(event, policy_dir, candidate_map[event_id])
        adjudication = adjudicate_current(event, support_row)
        retrieval_status = retrieval.get("retrieval_status", "RETRIEVAL_FAILED")
        reason = adjudication.get("reason", support_row.get("reason", ""))
        replacement_source_id = ""
        if not keepable(retrieval_status, adjudication["semantic_support"]):
            for source_id in alternative_source_ids(registry, event["domain"], current_id)[: args.max_replacements]:
                family = registry[source_id]
                retrieved = apply_source(
                    event,
                    docs_by_event[event_id],
                    source_id,
                    family,
                    cache,
                    args.query_mode,
                )
                retrieval = v8.retrieval_row(event, source_id, family, retrieved, args.query_mode)
                support_row = score_stored_event(event, policy_dir, candidate_map[event_id])
                adjudication = adjudicate_current(event, support_row)
                retrieval_status = retrieval["retrieval_status"]
                tried.append(source_id)
                replacement_source_id = source_id
                if keepable(retrieval_status, adjudication["semantic_support"]):
                    reason = f"replaced source {current_id} -> {source_id}"
                    break
                reason = adjudication.get("reason", support_row.get("reason", ""))
                current_id = source_id

        event["lexical_status"] = adjudication["lexical_status"]
        event["support_status"] = adjudication["support_status"]
        event["semantic_support"] = adjudication["semantic_support"]
        event["support_adjudication_method"] = adjudication["support_adjudication_method"]
        event["support_checked_before_model_run"] = "true"
        event["support_gate_version"] = SUPPORT_GATE_VERSION
        if keepable(retrieval_status, adjudication["semantic_support"]):
            disposition = "KEEP"
            event["status"] = "READY"
            for doc in docs_by_event[event_id]:
                doc["status"] = "READY"
        else:
            disposition = "DROP"
            event["status"] = "DROPPED"
            for doc in docs_by_event[event_id]:
                doc["status"] = "DROPPED"
            excerpt = excerpt_path(event_id)
            if excerpt.exists():
                excerpt.unlink()
            for doc in docs_by_event[event_id]:
                path = document_file(doc)
                if path.exists():
                    path.unlink()
            if not reason:
                reason = "semantic_support is not PASS after replacement"

        retrieval_by_event[event_id] = retrieval or retrieval_by_event.get(event_id, {"event_id": event_id})
        retrieval_by_event[event_id]["retrieval_status"] = retrieval_status
        retrieval_by_event[event_id]["source_id"] = tried[-1]
        retrieval_by_event[event_id]["fallback_used"] = False
        retrieval_by_event[event_id]["candidate_used"] = False
        retrieval_by_event[event_id]["oracle_used"] = False
        retrieval_by_event[event_id]["note_used"] = False
        dispositions.append(
            {
                "event_id": event_id,
                "domain": event["domain"],
                "semantic_type": event["semantic_type"],
                "retrieval_status": retrieval_status,
                "lexical_prefilter_status": support_row["status"],
                "support_status": adjudication["support_status"],
                "lexical_status": adjudication["lexical_status"],
                "semantic_support": adjudication["semantic_support"],
                "support_adjudication_method": adjudication["support_adjudication_method"],
                "support_checked_before_model_run": True,
                "support_gate_version": SUPPORT_GATE_VERSION,
                "support_ratio": support_row.get("support_ratio", 0),
                "disposition": disposition,
                "reason": reason,
                "replacement_event_id": "",
                "replacement_source_id": replacement_source_id if len(tried) > 1 else "",
                "tried_source_ids": "|".join(tried),
            }
        )

    ready_ids = {row["event_id"] for row in events if row.get("status") == "READY"}
    public_events = [row for row in events if row["event_id"] in ready_ids]
    public_docs = [row for row in docs if row["event_id"] in ready_ids]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    v8.write_csv(OUTPUT_DIR / "all-events-including-dropped.csv", events)
    if only:
        v8.write_csv(events_path, events)
        v8.write_csv(docs_path, docs)
    else:
        v8.write_csv(events_path, public_events)
        v8.write_csv(docs_path, public_docs)
        dropped_docs = {doc["file_name"] for doc in docs if doc["event_id"] not in ready_ids}
        for path in document_root.glob("*"):
            if path.is_file() and path.name in dropped_docs:
                path.unlink()

    v8.write_csv(retrieval_path, [retrieval_by_event[event_id] for event_id in sorted(retrieval_by_event)])
    layout.public_retrieval.mkdir(parents=True, exist_ok=True)
    v8.write_csv(layout.support_adjudication_csv, dispositions)
    v8.write_csv(OUTPUT_DIR / "disposition.csv", dispositions)
    summary = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "events": len(dispositions),
        "keep": sum(row["disposition"] == "KEEP" for row in dispositions),
        "drop": sum(row["disposition"] == "DROP" for row in dispositions),
        "support": dict(Counter(row["support_status"] for row in dispositions)),
        "lexical_prefilter": dict(Counter(row.get("lexical_prefilter_status", "") for row in dispositions)),
        "semantic_support": dict(Counter(row["semantic_support"] for row in dispositions)),
        "retrieval": dict(Counter(row["retrieval_status"] for row in dispositions)),
        "support_gate_version": SUPPORT_GATE_VERSION,
        "support_checked_before_model_run": True,
        "warn_kept": 0,
        "fail_kept": 0,
        "candidate_written_to_excerpt": False,
        "oracle_written_to_excerpt": False,
        "curation_not_inference": True,
    }
    (OUTPUT_DIR / "disposition-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return dispositions


def main() -> int:
    args = parse_args()
    layout = BenchmarkLayout(DST)
    if not layout.event_csv.is_file() and not (DST / "input" / "external-real-event-template.csv").is_file():
        raise SystemExit("v8 benchmark is missing; run build_external_real_v8_grounded.py first")
    gate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
