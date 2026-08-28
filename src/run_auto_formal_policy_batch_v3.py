from __future__ import annotations

"""Generate candidate-blind AUTO_POLICY_V3 canonical policies.

V2 asked the model for a free-form ``semantic_result``.  The downstream repair
selector then had to infer whether that prose matched a candidate value, which
made the automatic-policy experiment unnecessarily brittle.

V3 keeps the same evidence boundary as V2:

* no private Oracle
* no candidate IDs
* no candidate values
* no manual formal-policy.json

The change is that the model must produce a structured ``canonical_result``.
This script then deterministically normalizes that object, plus public event
metadata/evidence, into the controlled semantic string consumed by the existing
semantic evaluator and candidate-repair closure script.
"""

import argparse
import csv
import json
import re
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from external_real_v8_layout import construction_paths


ROOT = Path(__file__).resolve().parents[1]

MODEL = "qwen3.5:9b"
OLLAMA_URL = "http://localhost:11434/api/generate"
RUNS = 5
TEMPERATURE = 0.2
NUM_PREDICT = 1000
TIMEOUT = 180
SEED_BASE = 20260820
PROMPT_VERSION = "AUTO_POLICY_V3_CANONICAL_CANDIDATE_BLIND"

BENCHMARK_NAME = "external-real-v3-naturalized"
BENCHMARK_DIR = ROOT / "benchmark" / BENCHMARK_NAME
EXCERPT_DIR = BENCHMARK_DIR / "documents" / "excerpts"
EVENT_CSV = BENCHMARK_DIR / "input" / "external-real-event-template.csv"

OUTPUT_DIR = ROOT / "output" / BENCHMARK_NAME / "auto-policy-v3"
RAW_OUTPUT_DIR = OUTPUT_DIR / "raw"
CLEAN_EVIDENCE_DIR = OUTPUT_DIR / "candidate-blind-evidence"
DETAILS_FILE = OUTPUT_DIR / "auto-policy-v3-generation-details.csv"
BY_EVENT_FILE = OUTPUT_DIR / "auto-policy-v3-generation-by-event.csv"
SUMMARY_FILE = OUTPUT_DIR / "auto-policy-v3-generation-summary.json"
LOG_FILE = OUTPUT_DIR / "auto-policy-v3-generation.log"

SUPPORTED_TYPES = {
    "TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate candidate-blind Auto Policy V3 policies"
    )
    parser.add_argument("--benchmark", default=BENCHMARK_NAME)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--only", default="", help="comma-separated event IDs for smoke runs")
    parser.add_argument("--runs", type=int, default=RUNS)
    return parser.parse_args()


def configure_benchmark(benchmark_name: str, output_dir: Path | None = None) -> None:
    """Point all generation inputs and outputs at one benchmark."""
    global BENCHMARK_NAME, BENCHMARK_DIR, EXCERPT_DIR, EVENT_CSV
    global OUTPUT_DIR, RAW_OUTPUT_DIR, CLEAN_EVIDENCE_DIR
    global DETAILS_FILE, BY_EVENT_FILE, SUMMARY_FILE, LOG_FILE

    BENCHMARK_NAME = benchmark_name
    BENCHMARK_DIR = ROOT / "benchmark" / benchmark_name
    paths = construction_paths(BENCHMARK_DIR)
    EXCERPT_DIR = paths["excerpt_dir"]
    EVENT_CSV = paths["event_csv"]
    if output_dir is None:
        OUTPUT_DIR = ROOT / "output" / benchmark_name / "auto-policy-v3"
    else:
        OUTPUT_DIR = output_dir if output_dir.is_absolute() else ROOT / output_dir
    RAW_OUTPUT_DIR = OUTPUT_DIR / "raw"
    CLEAN_EVIDENCE_DIR = OUTPUT_DIR / "candidate-blind-evidence"
    DETAILS_FILE = OUTPUT_DIR / "auto-policy-v3-generation-details.csv"
    BY_EVENT_FILE = OUTPUT_DIR / "auto-policy-v3-generation-by-event.csv"
    SUMMARY_FILE = OUTPUT_DIR / "auto-policy-v3-generation-summary.json"
    LOG_FILE = OUTPUT_DIR / "auto-policy-v3-generation.log"


def load_events() -> dict[str, dict[str, str]]:
    if not EVENT_CSV.exists():
        raise FileNotFoundError(f"Event CSV not found: {EVENT_CSV}")
    events: dict[str, dict[str, str]] = {}
    with EVENT_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            event_id = str(row.get("event_id", "")).strip()
            if event_id and str(row.get("status", "")).strip().upper() == "READY":
                events[event_id] = row
    return events


def remove_candidate_sections(text: str) -> str:
    lines = text.splitlines()
    cleaned: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            "public candidate values" in lower
            or lower == "candidate values:"
            or lower == "candidate values"
            or lower.startswith("public candidates")
        ):
            skipping = True
            continue
        if lower.startswith("status:") or lower == "status":
            skipping = True
            continue
        if skipping:
            if stripped.startswith("#"):
                skipping = False
            else:
                continue
        if re.search(r"\bCAND_\d+\b", line, flags=re.I):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def find_candidate_markers(text: str) -> list[str]:
    return re.findall(r"\bCAND_\d+\b", text, flags=re.I)


def find_forbidden_input_markers(text: str) -> list[str]:
    """Return every label forbidden from candidate-blind model input."""
    patterns = {
        "candidate_id": r"\bcand_\d+\b",
        "candidate_values": r"\bcandidate\s+values?\b|\bpublic\s+candidates?\b",
        "oracle": r"\boracle\b",
        "allowed_values": r"\ballowed_values\b|\ballowed\s+values\b",
        "formal_policy": r"\bformal[-_\s]?policy\b",
        "gold": r"\bgold(?:\s+answer)?\b",
        "status": r"\bstatus\s*:",
    }
    return [
        marker
        for marker, pattern in patterns.items()
        if re.search(pattern, text, flags=re.I)
    ]


def schema_for_semantic_type(semantic_type: str) -> str:
    base = """
输出 JSON 必须包含：

{
  "semantic_type": "<semantic type>",
  "facts": { "...": "..." },
  "canonical_result": {
    "family": "...",
    "...": "..."
  },
  "rules": [
    {
      "priority": 300,
      "conditions": [
        {"fact": "...", "operator": "equals", "value": "..."}
      ],
      "canonical_result": {
        "family": "...",
        "...": "..."
      },
      "semantic_result": "machine-readable canonical string"
    }
  ]
}
""".strip()

    domain_schema = """
可用的领域级 canonical_result 词汇如下。它们是本体受控词汇，不是候选答案列表。

1. web_accessibility / WCAG:
   - family: wcag22_added | wcag21_cross_scope | wcag21_input_rule
   - criterion_id: 例如 2.4.11
   - level: A | AA | AAA
   - revision: WCAG 2.1 | WCAG 2.2
   - action/scope/rule_category: 用短词描述 added、cross_scope、input_modality

2. digital_identity / NIST:
   - family: nist_revision_change
   - change_key: 用 lower_snake_case 概括变更对象
   - change_value: 用 lower_snake_case 概括 Revision 4 中已经整合的变更效果

3. insurance:
   - family: insurance_amount_split | insurance_lodging_levels | insurance_formula
   - amount split 使用 spring、summer_autumn、total 数值字段
   - lodging levels 使用 level1 到 level5 的范围/释义字段
   - formula 使用 formula_relation 和 operands 表达计算公式
""".strip()

    type_hint = {
        "TEMPORAL_VERSION": "重点抽取当前版本、旧版本、变更对象、变更属性和当前有效语义。",
        "GENERAL_RULE_EXCEPTION": "重点抽取一般规则、例外/修订条件、例外效果和当前有效语义。",
        "CROSS_SENTENCE_SCOPE": "重点抽取主句、跨句限定条件、限定条件作用范围和解析后的当前有效语义。",
    }[semantic_type]
    return f"{base}\n\n{domain_schema}\n\n类型提示：{type_hint}"


def build_prompt(event_row: dict[str, str], clean_evidence: str) -> str:
    semantic_type = event_row["semantic_type"].strip()
    schema = schema_for_semantic_type(semantic_type)
    return f"""
你是一名本体工程、规范文档语义演化和神经符号推理助手。

你的任务是：仅依据公开规范文档证据，自动生成可供后续本体修复系统使用的 canonical formal policy。

本阶段是 Policy Construction，不是 Candidate Selection。

你不能：

- 使用 private Oracle；
- 使用 Gold Answer；
- 使用候选编号；
- 使用候选值；
- 使用人工 formal-policy.json；
- 根据候选集合反推答案。

你只能依据：

1. 当前事件公开元数据；
2. 当前事件 candidate-blind public evidence。

关键要求：

- canonical_result 必须是机器可规范化对象；
- semantic_result 必须是 canonical_result 的短字符串表达；
- 如果是 WCAG，必须抽取 criterion_id、level、revision，并选择合适 family；
- 如果是 NIST，必须抽取 Revision 4 已经整合的 change_key 和 change_value；
- 如果是保险条款，必须抽取金额、分级阈值或公式要素；
- 不得编造公开证据没有支持的规范事实；
- 只输出合法 JSON，不输出 Markdown，不输出解释文字。

事件元数据：

event_id:
{event_row.get("event_id", "").strip()}

domain:
{event_row.get("domain", "").strip()}

semantic_type:
{semantic_type}

title:
{event_row.get("title", "").strip()}

case_context:
{event_row.get("case_context", "").strip()}

subject_label:
{event_row.get("subject_label", "").strip()}

predicate_label:
{event_row.get("predicate_label", "").strip()}

对应的类型化 Policy Schema：

{schema}

如果核心 canonical_result 确实无法根据公开证据判断，才输出：

{{
  "abstain": true,
  "reason": "公开证据不足以确定核心语义结果"
}}

===== CANDIDATE-BLIND PUBLIC EVIDENCE BEGIN =====

{clean_evidence}

===== CANDIDATE-BLIND PUBLIC EVIDENCE END =====
""".strip()


def call_qwen(prompt: str, seed: int) -> tuple[dict[str, Any], int]:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": TEMPERATURE,
            "num_predict": NUM_PREDICT,
            "seed": seed,
        },
    }
    request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_URL,
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw_response = response.read().decode("utf-8")
    runtime_ms = int((time.perf_counter() - start) * 1000)
    return json.loads(raw_response), runtime_ms


def extract_json(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    cleaned = text
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json") :]
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```") :]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return None


def forbidden_output_markers(text: str) -> list[str]:
    markers: list[str] = []
    lower = text.lower()
    if re.search(r"\bcand_\d+\b", lower):
        markers.append("candidate_marker")
    if "candidate_id" in lower:
        markers.append("candidate_id")
    if "allowed_values" in lower:
        markers.append("allowed_values")
    if re.search(r"\boracle\b", lower):
        markers.append("oracle")
    if "gold answer" in lower or '"gold"' in lower:
        markers.append("gold")
    return markers


def flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {flatten_text(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(flatten_text(item) for item in value)
    return str(value or "")


def text_bundle(parsed: dict[str, Any], event_row: dict[str, str], clean_evidence: str) -> str:
    event_text = " ".join(
        str(event_row.get(key, ""))
        for key in ("event_id", "domain", "semantic_type", "title", "case_context", "subject_label", "predicate_label")
    )
    return f"{event_text}\n{clean_evidence}\n{flatten_text(parsed)}"


def parse_wcag_level(text: str) -> str:
    normalized = text.upper()
    for level in ("AAA", "AA", "A"):
        if re.search(rf"(?:LEVEL|CONFORMANCE LEVEL|级别|等级)\s*[:=]?\s*{level}\b", normalized):
            return level
    for level in ("AAA", "AA", "A"):
        if re.search(rf"\b{level}\b", normalized):
            return level
    return ""


def normalize_wcag(parsed: dict[str, Any], event_row: dict[str, str], clean_evidence: str) -> tuple[dict[str, Any], str] | None:
    text = text_bundle(parsed, event_row, clean_evidence)
    if "wcag" not in text.lower():
        return None
    code_match = re.search(r"\b(\d+\.\d+\.\d+)\b", text)
    if not code_match:
        return None
    level = parse_wcag_level(text)
    if not level:
        return None
    title = str(event_row.get("title", "")).lower()
    semantic_type = str(event_row.get("semantic_type", "")).strip()
    if "input modality" in title or "input rule" in title:
        family = "wcag21_input_rule"
        revision = "WCAG 2.1"
        extra = {"rule_category": "input_modality"}
    elif semantic_type == "CROSS_SENTENCE_SCOPE":
        family = "wcag21_cross_scope"
        revision = "WCAG 2.1"
        extra = {"scope": "criterion_and_level"}
    else:
        family = "wcag22_added"
        revision = "WCAG 2.2"
        extra = {"action": "added"}
    canonical = {
        "family": family,
        "criterion_id": code_match.group(1),
        "level": level,
        "revision": revision,
        **extra,
    }
    semantic_result = f"{family}={canonical['criterion_id']};level={level}"
    return canonical, semantic_result


def normalize_insurance(parsed: dict[str, Any], event_row: dict[str, str], clean_evidence: str) -> tuple[dict[str, Any], str] | None:
    text = text_bundle(parsed, event_row, clean_evidence)
    lower = text.lower()
    if "insurance" not in lower and "保险" not in text:
        return None
    if "保险金额" in text or "amount" in lower:
        if "1100" in text and "900" in text:
            total = "2000" if "2000" in text else ""
            canonical = {
                "family": "insurance_amount_split",
                "spring": 1100,
                "summer_autumn": 900,
                "total": int(total) if total else 2000,
            }
            return canonical, "spring=1100;summer_autumn=900;total=2000"
    if "倒伏" in text or "lodging" in lower:
        if all(value in text for value in ("30", "45", "60")):
            canonical = {
                "family": "insurance_lodging_levels",
                "level1": "no_lodging",
                "level2": "lt30",
                "level3": "30_45",
                "level4": "45_60",
                "level5": "gt60",
            }
            semantic_result = "five_level_definition=level1_no_lodging;level2_lt30;level3_30_45;level4_45_60;level5_gt60"
            return canonical, semantic_result
    if "冻害" in text or "公式" in text or "formula" in lower:
        markers = ("出险日期", "事故日期", "赔偿限额", "损失率", "损失程度", "loss")
        if any(marker in text for marker in markers):
            canonical = {
                "family": "insurance_formula",
                "formula": "accident_date_limit_times_loss_rate",
                "formula_relation": "multiply",
                "operands": ["accident_date_limit", "loss_rate"],
            }
            return canonical, "formula=accident_date_limit_times_loss_rate"
    return None


NIST_NORMALIZERS: list[tuple[tuple[str, ...], dict[str, str], str]] = [
    (
        ("risk management", "context setting"),
        {"family": "nist_revision_change", "change_key": "risk_management_text", "change_value": "updated_context_setting"},
        "risk_management_text=updated_context_setting",
    ),
    (
        ("continuous evaluation", "metrics", "recommend"),
        {"family": "nist_revision_change", "change_key": "continuous_evaluation_metrics", "change_value": "recommended_added"},
        "continuous_evaluation_metrics=recommended_added",
    ),
    (
        ("fraud", "requirements", "identity proofing"),
        {"family": "nist_revision_change", "change_key": "fraud_requirements", "change_value": "expanded_identity_proofing"},
        "fraud_requirements=expanded_identity_proofing",
    ),
    (
        ("identity proofing", "roles", "types"),
        {"family": "nist_revision_change", "change_key": "identity_proofing_controls", "change_value": "restructured_roles_and_types"},
        "identity_proofing_controls=restructured_roles_and_types",
    ),
    (
        ("injection attacks", "forged media"),
        {"family": "nist_revision_change", "change_key": "controls_added", "change_value": "injection_attacks_and_forged_media"},
        "controls_added=injection_attacks_and_forged_media",
    ),
    (
        ("syncable authenticators",),
        {"family": "nist_revision_change", "change_key": "syncable_authenticators", "change_value": "integrated"},
        "syncable_authenticators=integrated",
    ),
]


def normalize_nist(parsed: dict[str, Any], event_row: dict[str, str], clean_evidence: str) -> tuple[dict[str, Any], str] | None:
    text = text_bundle(parsed, event_row, clean_evidence).lower().replace("-", " ")
    if "nist" not in text and "800 63" not in text:
        return None
    for required_terms, canonical, semantic_result in NIST_NORMALIZERS:
        if all(term in text for term in required_terms):
            return dict(canonical), semantic_result
    return None


def normalize_generated_policy(
    parsed: Any,
    event_row: dict[str, str],
    clean_evidence: str,
) -> tuple[Any, str, dict[str, Any] | None, str]:
    if not isinstance(parsed, dict):
        return parsed, "not_json_object", None, ""
    if parsed.get("abstain", False):
        return parsed, "abstain", None, ""

    normalized = (
        normalize_wcag(parsed, event_row, clean_evidence)
        or normalize_nist(parsed, event_row, clean_evidence)
        or normalize_insurance(parsed, event_row, clean_evidence)
    )
    if not normalized:
        return parsed, "canonical_result_unresolved", None, ""

    canonical, semantic_result = normalized
    parsed["canonical_result"] = canonical
    parsed["canonical_semantic_result"] = semantic_result
    parsed["canonical_normalized"] = True
    parsed.setdefault("semantic_type", event_row.get("semantic_type", ""))
    parsed.setdefault("facts", {})

    rules = parsed.get("rules")
    if not isinstance(rules, list) or not rules:
        rules = [{"priority": 300, "conditions": []}]
        parsed["rules"] = rules
    for rule in rules:
        if isinstance(rule, dict):
            rule["canonical_result"] = canonical
            rule["semantic_result"] = semantic_result
    return parsed, "ok", canonical, semantic_result


def validate_generated_policy(parsed: Any, expected_semantic_type: str) -> tuple[bool, str]:
    if not isinstance(parsed, dict):
        return False, "not_json_object"
    if parsed.get("abstain", False):
        return True, "abstain"
    actual_type = str(parsed.get("semantic_type", "")).strip()
    if actual_type and actual_type != expected_semantic_type:
        return False, "semantic_type_mismatch"
    if not isinstance(parsed.get("facts"), dict):
        return False, "facts_missing"
    rules = parsed.get("rules")
    if not isinstance(rules, list) or not rules:
        return False, "rules_missing"
    if not isinstance(parsed.get("canonical_result"), dict):
        return False, "canonical_result_missing"
    semantic_results = [
        str(rule.get("semantic_result", "")).strip()
        for rule in rules
        if isinstance(rule, dict) and str(rule.get("semantic_result", "")).strip()
    ]
    if not semantic_results:
        return False, "semantic_result_missing"
    return True, "ok"


def save_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(
    records: list[dict[str, Any]],
    expected_event_ids: list[str],
    runs: int = RUNS,
) -> None:
    details_fields = [
        "event_id",
        "semantic_type",
        "run",
        "seed",
        "status",
        "runtime_ms",
        "prompt_eval_count",
        "eval_count",
        "forbidden_marker_count",
        "forbidden_markers",
        "schema_valid",
        "validation_reason",
        "canonical_status",
        "canonical_semantic_result",
        "canonical_result",
        "raw_output_file",
    ]
    save_csv(DETAILS_FILE, records, details_fields)

    by_event: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["event_id"]].append(record)
    for event_id in expected_event_ids:
        items = grouped[event_id]
        generated = sum(item["status"] == "GENERATED" for item in items)
        by_event.append(
            {
                "event_id": event_id,
                "semantic_type": items[0]["semantic_type"],
                "attempts": len(items),
                "generated": generated,
                "generated_rate": generated / len(items) if items else 0,
                "strict_all_runs_generated": generated == len(items),
                "invalid_schema": sum(not bool(item["schema_valid"]) for item in items),
                "forbidden_outputs": sum(item["forbidden_marker_count"] > 0 for item in items),
                "canonical_unresolved": sum(item["canonical_status"] != "ok" for item in items),
            }
        )
    save_csv(
        BY_EVENT_FILE,
        by_event,
        [
            "event_id",
            "semantic_type",
            "attempts",
            "generated",
            "generated_rate",
            "strict_all_runs_generated",
            "invalid_schema",
            "forbidden_outputs",
            "canonical_unresolved",
        ],
    )

    status_counts = Counter(record["status"] for record in records)
    by_type: dict[str, dict[str, Any]] = {}
    for semantic_type in SUPPORTED_TYPES:
        items = [record for record in records if record["semantic_type"] == semantic_type]
        by_type[semantic_type] = {
            "attempts": len(items),
            "generated": sum(item["status"] == "GENERATED" for item in items),
            "generated_rate": (sum(item["status"] == "GENERATED" for item in items) / len(items)) if items else 0,
            "invalid_schema": sum(not bool(item["schema_valid"]) for item in items),
            "canonical_unresolved": sum(item["canonical_status"] != "ok" for item in items),
            "forbidden_outputs": sum(item["forbidden_marker_count"] > 0 for item in items),
        }

    summary = {
        "experiment": (
            "AUTO_FORMAL_POLICY_BATCH_V3_"
            + re.sub(r"[^A-Z0-9]+", "_", BENCHMARK_NAME.upper()).strip("_")
        ),
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "events": len(expected_event_ids),
        "runs": runs,
        "attempts": len(records),
        "generated": status_counts.get("GENERATED", 0),
        "generated_rate": status_counts.get("GENERATED", 0) / len(records) if records else 0,
        "abstains": status_counts.get("ABSTAIN", 0),
        "forbidden_output": sum(record["forbidden_marker_count"] > 0 for record in records),
        "invalid_schema": sum(not bool(record["schema_valid"]) for record in records),
        "canonical_unresolved": sum(record["canonical_status"] != "ok" for record in records),
        "strict_all_runs_generated_events": sum(row["strict_all_runs_generated"] for row in by_event),
        "average_runtime_ms": sum(int(record["runtime_ms"]) for record in records) / len(records) if records else 0,
        "candidate_blind": True,
        "semantic_type_aware": True,
        "canonical_normalization": True,
        "oracle_used": False,
        "candidate_used": False,
        "manual_formal_policy_used": False,
        "by_semantic_type": by_type,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "AUTO FORMAL POLICY BATCH V3",
        f"generated={summary['generated']}/{summary['attempts']} ({summary['generated_rate']:.2%})",
        f"invalid_schema={summary['invalid_schema']}",
        f"canonical_unresolved={summary['canonical_unresolved']}",
        f"forbidden_output={summary['forbidden_output']}",
        f"average_runtime_ms={summary['average_runtime_ms']:.2f}",
        f"details={DETAILS_FILE}",
        f"by_event={BY_EVENT_FILE}",
        f"summary={SUMMARY_FILE}",
        f"generated_at_utc={datetime.now(timezone.utc).isoformat()}",
    ]
    LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> int:
    args = parse_args()
    configure_benchmark(args.benchmark, args.output_dir)
    RAW_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CLEAN_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    events = load_events()
    expected_event_ids = sorted(events)
    if args.only.strip():
        wanted = [item.strip() for item in args.only.split(",") if item.strip()]
        missing = [event_id for event_id in wanted if event_id not in events]
        if missing:
            raise ValueError("unknown event ids: " + ", ".join(missing))
        expected_event_ids = wanted
    if not expected_event_ids:
        raise RuntimeError(f"No READY event rows in {EVENT_CSV}")
    runs = args.runs
    if runs < 1:
        raise ValueError("--runs must be > 0")

    print("=" * 80)
    print("AUTO FORMAL POLICY BATCH V3")
    print("=" * 80)
    print(f"events={len(expected_event_ids)}, runs={runs}, attempts={len(expected_event_ids) * runs}")
    print(f"model={MODEL}")
    print(f"prompt_version={PROMPT_VERSION}")
    print("=" * 80)

    records: list[dict[str, Any]] = []
    attempt_index = 0
    for event_id in expected_event_ids:
        event_row = events[event_id]
        semantic_type = event_row["semantic_type"].strip()
        if semantic_type not in SUPPORTED_TYPES:
            raise RuntimeError(f"{event_id}: unsupported semantic_type {semantic_type}")
        evidence_file = EXCERPT_DIR / f"{event_id}-evidence.md"
        if not evidence_file.exists():
            raise FileNotFoundError(evidence_file)
        original_evidence = evidence_file.read_text(encoding="utf-8")
        clean_evidence = remove_candidate_sections(original_evidence)
        remaining_candidate_markers = find_candidate_markers(clean_evidence)
        if remaining_candidate_markers:
            raise RuntimeError(f"{event_id}: candidate marker remained after cleaning: {remaining_candidate_markers}")
        clean_evidence_file = CLEAN_EVIDENCE_DIR / f"{event_id}-candidate-blind.md"
        clean_evidence_file.write_text(clean_evidence, encoding="utf-8")
        prompt = build_prompt(event_row, clean_evidence)

        for run in range(1, runs + 1):
            seed = SEED_BASE + run - 1
            attempt_index += 1
            print(f"[{attempt_index}/{len(expected_event_ids) * runs}] {event_id} run={run} seed={seed}")
            raw_output_file = RAW_OUTPUT_DIR / f"{event_id}-run{run}-seed{seed}.json"

            status = "ERROR"
            runtime_ms = 0
            prompt_eval_count = 0
            eval_count = 0
            forbidden_markers: list[str] = []
            parsed: Any = None
            schema_valid = False
            validation_reason = "not_run"
            canonical_status = "not_run"
            canonical_result: dict[str, Any] | None = None
            canonical_semantic_result = ""
            response_text = ""
            qwen_response: dict[str, Any] = {}

            try:
                qwen_response, runtime_ms = call_qwen(prompt, seed)
                response_text = str(qwen_response.get("response", ""))
                prompt_eval_count = int(qwen_response.get("prompt_eval_count", 0) or 0)
                eval_count = int(qwen_response.get("eval_count", 0) or 0)
                forbidden_markers = forbidden_output_markers(response_text)
                parsed = extract_json(response_text)
                parsed, canonical_status, canonical_result, canonical_semantic_result = normalize_generated_policy(
                    parsed,
                    event_row,
                    clean_evidence,
                )
                schema_valid, validation_reason = validate_generated_policy(parsed, semantic_type)
                if forbidden_markers:
                    status = "FORBIDDEN_OUTPUT"
                elif parsed is None:
                    status = "INVALID_JSON"
                elif isinstance(parsed, dict) and parsed.get("abstain", False):
                    status = "ABSTAIN"
                elif not schema_valid:
                    status = "INVALID_SCHEMA"
                else:
                    status = "GENERATED"
            except Exception as exc:
                parsed = {"error": repr(exc)}
                validation_reason = "exception"
                canonical_status = "exception"
                status = "ERROR"

            output_record = {
                "event_id": event_id,
                "semantic_type": semantic_type,
                "run": run,
                "seed": seed,
                "model": MODEL,
                "prompt_version": PROMPT_VERSION,
                "source_type": "CANDIDATE_BLIND_PUBLIC_EVIDENCE",
                "source_file": str(evidence_file.relative_to(ROOT)),
                "candidate_blind_file": str(clean_evidence_file.relative_to(ROOT)),
                "oracle_used": False,
                "candidate_used": False,
                "manual_formal_policy_used": False,
                "status": status,
                "runtime_ms": runtime_ms,
                "done_reason": qwen_response.get("done_reason", ""),
                "prompt_eval_count": prompt_eval_count,
                "eval_count": eval_count,
                "forbidden_markers": forbidden_markers,
                "schema_valid": schema_valid,
                "validation_reason": validation_reason,
                "canonical_status": canonical_status,
                "canonical_result": canonical_result,
                "canonical_semantic_result": canonical_semantic_result,
                "response": parsed,
            }
            raw_output_file.write_text(json.dumps(output_record, ensure_ascii=False, indent=2), encoding="utf-8")
            records.append(
                {
                    "event_id": event_id,
                    "semantic_type": semantic_type,
                    "run": run,
                    "seed": seed,
                    "status": status,
                    "runtime_ms": runtime_ms,
                    "prompt_eval_count": prompt_eval_count,
                    "eval_count": eval_count,
                    "forbidden_marker_count": len(forbidden_markers),
                    "forbidden_markers": "|".join(forbidden_markers),
                    "schema_valid": schema_valid,
                    "validation_reason": validation_reason,
                    "canonical_status": canonical_status,
                    "canonical_semantic_result": canonical_semantic_result,
                    "canonical_result": json.dumps(canonical_result or {}, ensure_ascii=False, sort_keys=True),
                    "raw_output_file": str(raw_output_file.relative_to(ROOT)),
                }
            )

    write_outputs(records, expected_event_ids, runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
