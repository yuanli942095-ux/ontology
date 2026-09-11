from __future__ import annotations

"""Offline AUTO_POLICY_V4 Semantic-IR adapter and candidate ranking.

V4 does not call Qwen. It reads already frozen Auto Policy raw outputs, converts
them into a candidate-blind Semantic IR, normalizes deterministic surface forms,
and only then ranks public repair candidates.

V4.4 robust IR: empty ``rules`` does not fail-closed when facts/canonical_result
already suffice. CSS unique slot completion fills ``scope_relation`` only when
existing Auto Policy fields uniquely determine it. V4.3 ranking gates stay frozen.
"""

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import validate_benchmark as benchmark_validator
from auto_policy_v4_css_slot import complete_css_scope_relation
from evaluate_auto_formal_policy_v2_semantic import evaluate_semantic
from external_real_v8_layout import candidate_selection_paths, evaluation_paths, is_staged_layout
from rdflib import URIRef
from run_auto_policy_v2_candidate_repair import (
    graph_delta,
    load_graph,
    materialize_candidate_owl,
    repair_checks,
    resolve_candidate_owl_path,
    resolve_source_owl,
    term_from_spec,
)
from semantic_v2_common import PROJECT_DIR, write_csv
from method_experiment_guard import add_legacy_opt_in_arg, guard_frozen_v43_config, guard_holdout_benchmark


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
RAW_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-v8-grounded"
    / "preformal-r3"
    / "raw_window_metadata_light"
    / "raw"
)
OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v4-ir"


FIELD_ALIASES = {
    "subject": {
        "subject",
        "subject_label",
        "target",
        "scope_target",
        "entity",
        "resource",
        "audience",
        "application",
        "tool",
        "topic",
    },
    "old_value": {
        "old",
        "old_value",
        "previous",
        "previous_value",
        "prior_value",
        "old_rule",
        "previous_rule",
        "current_rule_before_revision",
        "predecessor",
    },
    "new_value": {
        "new",
        "new_value",
        "current",
        "current_value",
        "replacement",
        "updated_value",
        "new_rule",
        "current_rule",
        "successor",
    },
    "effective_time": {
        "effective_time",
        "effective_date",
        "effective_from",
        "revision_year",
        "source_revision_year",
        "assessment_year",
        "date",
        "version",
        "new_version",
    },
    "relation": {
        "relation",
        "change_type",
        "change_key",
        "action",
        "status",
        "semantics",
        "scope_relation",
    },
    "general_rule": {"general_rule", "default_rule", "baseline_rule", "rule", "requirement"},
    "exception_condition": {
        "exception_condition",
        "condition",
        "unless",
        "except",
        "exception",
        "exceptions",
    },
    "exception_rule": {"exception_rule", "override", "special_rule", "exception_value"},
    "priority": {"priority", "precedence", "rule_priority"},
    "statement": {"statement", "scope_statement", "requirement", "rule", "text"},
    "qualifier": {"qualifier", "condition", "context", "applies_when"},
    "scope_target": {"scope_target", "target", "applies_to", "coverage", "scope"},
    "scope_relation": {"scope_relation", "relation", "applies_to", "inheritance", "extends"},
    "result": {"result", "semantic_result", "change_value", "value", "canonical_value", "outcome"},
}

RELATION_LEXICON = [
    (("supersedes", "supersede", "successor", "takes the place of", "取代", "替代", "代替"), "SUPERSEDES"),
    (("replaces", "replaced by", "replacement", "replace", "replaced", "替换"), "REPLACES"),
    (("amends", "amended", "amendment", "amend", "修改", "修订"), "AMENDS"),
    (("effective from", "effective", "生效", "applies from"), "EFFECTIVE_FROM"),
    (("remains valid", "still valid", "continues", "保持有效"), "REMAINS_VALID"),
    (("exception", "except", "unless", "override", "overrides", "takes precedence", "例外", "优先"), "EXCEPTION_OVERRIDES"),
    (("scope", "applies to", "applicable to", "范围", "适用"), "APPLIES_TO"),
    (("added", "addition", "new criterion", "新增", "增加"), "ADDED"),
]

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "onto",
    "under",
    "must",
    "shall",
    "should",
    "will",
    "are",
    "was",
    "were",
    "has",
    "have",
    "had",
    "not",
    "old",
    "new",
    "current",
    "previous",
    "rule",
    "status",
    "revision",
    "change",
    "value",
}


@dataclass
class SemanticIR:
    event_id: str
    semantic_type: str
    ir_status: str
    ir_reason: str
    fields: dict[str, Any]
    normalized_terms: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    source_family: str = ""


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def ready(value: str) -> bool:
    return str(value or "").strip().upper() == "READY"


def parse_jsonish(value: str) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def norm(value: Any) -> str:
    text = str(value or "").lower()
    replacements = {
        "×": "*",
        "＝": "=",
        "：": ":",
        "；": ";",
        "，": ",",
        "（": "(",
        "）": ")",
        "-": "_",
        "/": "_",
        "%": " percent ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def compact(value: Any) -> str:
    return re.sub(r"\s+", "", norm(value))


def tokens(value: Any) -> set[str]:
    result = set()
    for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", norm(value)):
        if len(token) <= 1 or token in STOPWORDS:
            continue
        result.add(token)
        for suffix in ("ing", "ed", "es", "s"):
            if token.endswith(suffix) and len(token) > len(suffix) + 2:
                result.add(token[: -len(suffix)])
    return result


def flatten_pairs(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            next_key = f"{prefix}.{key}" if prefix else str(key)
            rows.append((next_key, item))
            rows.extend(flatten_pairs(item, next_key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            rows.extend(flatten_pairs(item, f"{prefix}.{index}" if prefix else str(index)))
    return rows


def flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {flatten_text(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(flatten_text(item) for item in value)
    return str(value or "")


def _nonempty_mapping(value: Any) -> bool:
    return isinstance(value, dict) and any(item not in (None, "", [], {}) for item in value.values())


def schema_payload_usable(response: dict[str, Any]) -> bool:
    """True when facts or canonical_result already carry IR-usable content.

    An empty ``rules`` list is not a death sentence by itself.
    """
    return _nonempty_mapping(response.get("facts")) or _nonempty_mapping(response.get("canonical_result"))


def result_from_canonical(canonical: Any) -> str:
    if not isinstance(canonical, dict):
        return ""
    for key in ("semantic_result", "change_value", "canonical_value", "value", "result"):
        item = canonical.get(key)
        if item not in (None, "", [], {}) and not isinstance(item, (dict, list)):
            return str(item).strip()
    return ""


def auto_semantic_result(record: dict[str, Any]) -> str:
    response = record.get("response", {})
    if not isinstance(response, dict):
        return ""
    rules = response.get("rules", [])
    if isinstance(rules, list) and rules:
        top = max(rules, key=lambda rule: int(rule.get("priority", 0)) if isinstance(rule, dict) else 0)
        if isinstance(top, dict):
            text = str(top.get("semantic_result", "")).strip()
            if text:
                return text
    return result_from_canonical(response.get("canonical_result"))


def relation_from_text(text: str) -> str:
    lower = norm(text)
    for variants, canonical in RELATION_LEXICON:
        if any(variant in lower for variant in variants):
            return canonical
    return "UNKNOWN"


def extract_dates(text: str) -> list[str]:
    lower = norm(text)
    dates = set(re.findall(r"\b20\d{2}[_/-](?:0?[1-9]|1[0-2])(?:[_/-]\d{1,2})?\b", lower))
    months = {
        "january": "01",
        "jan": "01",
        "february": "02",
        "feb": "02",
        "march": "03",
        "mar": "03",
        "april": "04",
        "apr": "04",
        "may": "05",
        "june": "06",
        "jun": "06",
        "july": "07",
        "jul": "07",
        "august": "08",
        "aug": "08",
        "september": "09",
        "sep": "09",
        "october": "10",
        "oct": "10",
        "november": "11",
        "nov": "11",
        "december": "12",
        "dec": "12",
    }
    for month, number in months.items():
        for year in re.findall(rf"\b{month}\.?\s+(20\d{{2}})\b", lower):
            dates.add(f"{year}_{number}")
    for year, month in re.findall(r"\b(20\d{2})年\s*(\d{1,2})月", lower):
        dates.add(f"{year}_{int(month):02d}")
    return sorted(dates)


def extract_numbers(text: str) -> list[str]:
    values = set()
    for item in re.findall(r"(?<![a-z])\d+(?:,\d{3})*(?:\.\d+)?(?![a-z])", norm(text)):
        if re.fullmatch(r"\d+\.\d+\.\d+", item):
            continue
        if item.endswith(".0"):
            item = item[:-2]
        values.add(item.replace(",", ""))
    return sorted(values, key=lambda value: (len(value), value))


def salient_numbers(values: list[str]) -> set[str]:
    result = set()
    for value in values:
        if len(value) >= 4:
            result.add(value)
            continue
        try:
            numeric = float(value)
        except ValueError:
            continue
        if numeric >= 10:
            result.add(str(int(numeric)) if numeric.is_integer() else value)
    return result


def extract_codes(text: str) -> list[str]:
    lower = norm(text)
    values = set(re.findall(r"\b\d+\.\d+\.\d+\b", lower))
    values.update(re.findall(r"\bsp\s*800[_ ]?63[a-z]?(?:[_ ]?4)?\b", lower))
    values.update(re.findall(r"\b[a-z]{1,4}\d{2,5}\b", lower))
    return sorted(values)


def canonicalize_value(value: Any) -> Any:
    if isinstance(value, str):
        text = norm(value)
        nums = extract_numbers(text)
        dates = extract_dates(text)
        if len(nums) == 1 and compact(text) in {nums[0], nums[0] + ".0"}:
            return nums[0]
        if len(dates) == 1 and re.fullmatch(r"(?:20\d{2}[_/-]\d{1,2}|[a-z]+\.?\s+20\d{2}|\d{4}年\d{1,2}月)", text):
            return dates[0]
        return text
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return value


def field_alias_for(key: str) -> str | None:
    leaf = key.split(".")[-1].lower()
    compact_leaf = compact(leaf)
    for target, aliases in FIELD_ALIASES.items():
        if compact_leaf in {compact(alias) for alias in aliases}:
            return target
    return None


def merge_field(fields: dict[str, Any], target: str, value: Any) -> None:
    if value in (None, "", [], {}):
        return
    canonical = canonicalize_value(value)
    if target not in fields or fields[target] in (None, "", [], {}):
        fields[target] = canonical
        return
    existing = fields[target]
    if isinstance(existing, list):
        if canonical not in existing:
            existing.append(canonical)
    elif canonical != existing:
        fields[target] = [existing, canonical]


def infer_source_family(raw_semantic: str, response: dict[str, Any]) -> str:
    text = compact(raw_semantic)
    if ":" in text:
        return text.split(":", 1)[0]
    if "=" in text:
        return text.split("=", 1)[0]
    canonical = response.get("canonical_result")
    if isinstance(canonical, dict) and canonical.get("family"):
        return compact(canonical.get("family"))
    return ""


def parse_semantic_ir(record: dict[str, Any], event: dict[str, str], *, robust_ir: bool = True) -> SemanticIR:
    status = str(record.get("status") or "")
    if status == "RETRIEVAL_FAILED":
        return SemanticIR(event["event_id"], event["semantic_type"], "RETRIEVAL_FAILED", "model_input_retrieval_failed", {})
    if status == "INPUT_CONSTRUCTION_ERROR":
        return SemanticIR(event["event_id"], event["semantic_type"], "INPUT_CONSTRUCTION_ERROR", "public_ready_empty_evidence", {})
    response = record.get("response", {})
    if not isinstance(response, dict):
        return SemanticIR(event["event_id"], event["semantic_type"], "INVALID_OUTPUT", "response_not_object", {})
    if status == "INVALID_JSON":
        return SemanticIR(event["event_id"], event["semantic_type"], "INVALID_OUTPUT", str(record.get("validation_reason", status)), {})
    if status == "INVALID_SCHEMA":
        if not robust_ir or not schema_payload_usable(response):
            return SemanticIR(event["event_id"], event["semantic_type"], "INVALID_OUTPUT", str(record.get("validation_reason", status)), {})
    if response.get("abstain") is True:
        return SemanticIR(event["event_id"], event["semantic_type"], "INCOMPLETE_IR", "model_abstained", {})

    raw_semantic = auto_semantic_result(record)
    bundle = " ".join(
        [
            str(event.get("domain", "")),
            str(event.get("semantic_type", "")),
            str(event.get("subject_label", "")),
            str(event.get("predicate_label", "")),
            str(event.get("title", "")),
            str(event.get("case_context", "")),
            raw_semantic,
            flatten_text(response),
        ]
    )
    fields: dict[str, Any] = {}
    for key, value in flatten_pairs(response):
        alias = field_alias_for(key)
        if alias:
            merge_field(fields, alias, value)
    fields.setdefault("subject", event.get("subject_label", ""))
    fields.setdefault("statement", event.get("predicate_label", ""))
    fields.setdefault("result", raw_semantic)
    fields["relation"] = relation_from_text(bundle if fields.get("relation") in (None, "", "UNKNOWN") else fields["relation"])

    semantic_type = event["semantic_type"]
    required = {
        "TEMPORAL_VERSION": ("subject", "relation", "result"),
        "GENERAL_RULE_EXCEPTION": ("subject", "result"),
        "CROSS_SENTENCE_SCOPE": ("subject", "statement", "scope_relation", "result"),
    }.get(semantic_type, ("subject", "result"))
    if semantic_type == "GENERAL_RULE_EXCEPTION" and "priority" not in fields:
        fields["priority"] = "EXCEPTION" if relation_from_text(bundle) == "EXCEPTION_OVERRIDES" else "UNKNOWN"
    if semantic_type == "CROSS_SENTENCE_SCOPE" and "scope_relation" not in fields:
        rel = relation_from_text(bundle)
        if rel in {"APPLIES_TO", "REMAINS_VALID", "ADDED", "AMENDS"}:
            fields["scope_relation"] = rel
        else:
            chosen, klass, _reason = complete_css_scope_relation(response, fields)
            if klass == "S1" and chosen:
                fields["scope_relation"] = chosen
    if semantic_type == "TEMPORAL_VERSION" and fields.get("relation") == "UNKNOWN":
        if any(marker in compact(bundle) for marker in ("revision", "revised", "updated", "amend", "effective", "生效", "修订", "修改")):
            fields["relation"] = "EFFECTIVE_FROM"
        elif raw_semantic:
            fields["relation"] = "REPLACES"

    missing = [key for key in required if not fields.get(key) or fields.get(key) == "UNKNOWN"]
    ir_status = "OK" if not missing else "INCOMPLETE_IR"
    reason = "ok" if not missing else "missing:" + "|".join(missing)
    return SemanticIR(
        event_id=event["event_id"],
        semantic_type=semantic_type,
        ir_status=ir_status,
        ir_reason=reason,
        fields=fields,
        normalized_terms=sorted(tokens(bundle)),
        numbers=extract_numbers(bundle),
        dates=extract_dates(bundle),
        codes=extract_codes(bundle),
        source_family=infer_source_family(raw_semantic, response),
    )


def semantic_ir_string(ir: SemanticIR) -> str:
    parts = [f"type={ir.semantic_type.lower()}"]
    for key in (
        "subject",
        "relation",
        "old_value",
        "new_value",
        "effective_time",
        "general_rule",
        "exception_condition",
        "exception_rule",
        "priority",
        "statement",
        "qualifier",
        "scope_target",
        "scope_relation",
        "result",
    ):
        if ir.fields.get(key) not in (None, "", [], {}):
            parts.append(f"{key}={ir.fields[key]}")
    if ir.codes:
        parts.append("codes=" + "|".join(ir.codes))
    if ir.numbers:
        parts.append("numbers=" + "|".join(ir.numbers))
    if ir.dates:
        parts.append("dates=" + "|".join(ir.dates))
    return ";".join(parts)


def candidate_family(value: str) -> str:
    text = compact(value)
    if "=" in text:
        return text.split("=", 1)[0]
    if ":" in text:
        return text.split(":", 1)[0]
    return ""


def parse_candidate_value(value: str) -> dict[str, Any]:
    text = norm(value)
    family = candidate_family(text)
    rest = text.split("=", 1)[1] if "=" in text else text
    return {
        "family": family,
        "tokens": tokens(text),
        "numbers": set(extract_numbers(text)),
        "dates": set(extract_dates(text)),
        "codes": set(extract_codes(text)),
        "is_negative_or_old": any(
            marker in text
            for marker in (
                "not_present",
                "not_integrated",
                "old_revision",
                "old_or_absent",
                "deferred",
                "total_only",
            )
        ),
        "raw": text,
        "rest": rest,
    }


def source_family_alias_score(ir: SemanticIR, candidate: dict[str, Any]) -> float:
    family = str(candidate["family"])
    source = ir.source_family
    semantic_type = ir.semantic_type
    if not family:
        return 0.0
    if family == source:
        return 1.0
    if source == "nist_revision_change" and (
        family.startswith("nist") or family in ir.normalized_terms or family.split("_")[0] in ir.normalized_terms
    ):
        return 0.45
    if source.startswith("wcag") and family.startswith("wcag"):
        if semantic_type == "TEMPORAL_VERSION" and family.startswith("wcag22_added"):
            return 0.9
        if semantic_type == "CROSS_SENTENCE_SCOPE" and "cross_scope" in family:
            return 0.9
        if semantic_type == "GENERAL_RULE_EXCEPTION" and ("input_rule" in family or "added" in family):
            return 0.65
        return 0.35
    if semantic_type == "CROSS_SENTENCE_SCOPE" and "scope" in family:
        return 0.55
    if semantic_type == "GENERAL_RULE_EXCEPTION" and any(part in family for part in ("requirement", "exception", "condition", "rule")):
        return 0.55
    if semantic_type == "TEMPORAL_VERSION" and any(part in family for part in ("revision", "status", "version", "effective")):
        return 0.55
    return 0.0


def score_candidate(ir: SemanticIR, event: dict[str, str], candidate: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_candidate_value(str(candidate["display_value"]))
    ir_tokens = set(ir.normalized_terms)
    result_tokens = tokens(ir.fields.get("result", ""))
    event_tokens = tokens(" ".join(str(event.get(key, "")) for key in ("title", "case_context", "subject_label", "predicate_label")))
    weighted_ir_tokens = ir_tokens | result_tokens | (event_tokens & ir_tokens)
    overlap = parsed["tokens"] & weighted_ir_tokens
    token_score = len(overlap) / max(1, min(len(parsed["tokens"]), len(weighted_ir_tokens)))
    candidate_numbers = salient_numbers(sorted(parsed["numbers"]))
    ir_numbers = salient_numbers(ir.numbers)
    number_overlap = candidate_numbers & ir_numbers
    code_overlap = parsed["codes"] & set(ir.codes)
    number_score = len(number_overlap) / max(1, len(candidate_numbers)) if candidate_numbers else 0.0
    code_score = len(code_overlap) / max(1, len(parsed["codes"])) if parsed["codes"] else 0.0
    family_score = source_family_alias_score(ir, parsed)
    exact_score = 1.0 if compact(candidate["display_value"]) in compact(semantic_ir_string(ir)) else 0.0
    relation = ir.fields.get("relation", "")
    negative_penalty = 0.18 if parsed["is_negative_or_old"] and relation in {"ADDED", "REPLACES", "SUPERSEDES", "AMENDS", "EFFECTIVE_FROM", "APPLIES_TO"} else 0.0
    if relation in {"SUPERSEDES", "REPLACES", "EFFECTIVE_FROM"} and any(
        marker in parsed["raw"] for marker in ("old_revision", "old_or_absent", "not_integrated", "not_present")
    ):
        negative_penalty += 0.12
    if relation in {"SUPERSEDES", "REPLACES", "EFFECTIVE_FROM"} and any(
        marker in parsed["raw"] for marker in ("rev4", "final", "current", "integrated", "superseded")
    ):
        score_bonus = 0.08
    else:
        score_bonus = 0.0
    score = (
        0.30 * family_score
        + 0.26 * token_score
        + 0.20 * max(code_score, number_score)
        + 0.12 * number_score
        + 0.08 * code_score
        + 0.04 * exact_score
        + score_bonus
        - negative_penalty
    )
    score = max(0.0, min(1.0, score))
    return {
        "candidate_id": candidate["candidate_id"],
        "display_value": candidate["display_value"],
        "score": round(score, 4),
        "family_score": round(family_score, 4),
        "token_score": round(token_score, 4),
        "number_score": round(number_score, 4),
        "code_score": round(code_score, 4),
        "exact_score": round(exact_score, 4),
        "negative_penalty": round(negative_penalty, 4),
        "semantic_bonus": round(score_bonus, 4),
        "matched_tokens": "|".join(sorted(overlap)),
        "matched_numbers": "|".join(sorted(number_overlap)),
        "matched_codes": "|".join(sorted(code_overlap)),
    }


def _jsonable_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in row.items() if key != "structure"} for row in rows]


def select_candidate_v4(
    event: dict[str, str],
    candidates: list[dict[str, Any]],
    ir: SemanticIR,
    *,
    min_score: float,
    min_margin: float,
    reranker: str = "lexical",
    temporal_unique_top1: bool = False,
) -> tuple[dict[str, Any] | None, str, str, list[dict[str, Any]], str]:
    if ir.ir_status != "OK":
        return None, "ABSTAIN", ir.ir_reason, [], "IR_FAIL_CLOSED"
    ir_text = semantic_ir_string(ir)
    exact_matches = []
    lexical_evidence = []
    for candidate in candidates:
        matched, match_evidence = evaluate_semantic(str(candidate["display_value"]), ir_text, event)
        scored = score_candidate(ir, event, candidate)
        scored["semantic_match"] = matched
        scored["semantic_match_evidence"] = match_evidence
        lexical_evidence.append(scored)
        if matched:
            exact_matches.append(candidate)
    if len(exact_matches) == 1:
        return exact_matches[0], "SELECTED", "unique semantic candidate match after IR normalization", lexical_evidence, "IR_EXACT_MATCH"
    if reranker == "constraint":
        from auto_policy_v4_constraint_rerank import rank_candidates, select_ranked

        if event.get("semantic_type") == "TEMPORAL_VERSION":
            ranked = rank_candidates(ir, candidates, event["semantic_type"])
            selected_row, status, reason, path = select_ranked(
                ranked,
                min_score=min_score,
                min_margin=min_margin,
                allow_unique_top1=temporal_unique_top1,
            )
            evidence = _jsonable_evidence(ranked)
            if selected_row is None:
                return None, status, reason, evidence, path
            selected = next(candidate for candidate in candidates if candidate["candidate_id"] == selected_row["candidate_id"])
            return selected, status, reason, evidence, path
        ranked = sorted(lexical_evidence, key=lambda item: -float(item["score"]))
        if not ranked:
            return None, "ABSTAIN", "no candidates", lexical_evidence, "NO_CANDIDATES"
        top = ranked[0]
        second_score = float(ranked[1]["score"]) if len(ranked) > 1 else 0.0
        margin = float(top["score"]) - second_score
        if len(ranked) > 1 and float(top["score"]) == second_score:
            return None, "ABSTAIN", f"tied top scores={top['score']}", ranked, "IR_RANK_TIE_ABSTAIN"
        if float(top["score"]) >= min_score and margin >= min_margin:
            selected = next(candidate for candidate in candidates if candidate["candidate_id"] == top["candidate_id"])
            return selected, "SELECTED", f"top scored candidate score={top['score']} margin={margin:.4f}", ranked, "IR_TOPK_RANK"
        return None, "ABSTAIN", f"no confident candidate score={top['score']} margin={margin:.4f}", ranked, "IR_RANK_ABSTAIN"
    if reranker == "constraint-all":
        from auto_policy_v4_constraint_rerank import rank_candidates, select_ranked

        ranked = rank_candidates(ir, candidates, event["semantic_type"])
        selected_row, status, reason, path = select_ranked(ranked, min_score=min_score, min_margin=min_margin)
        evidence = _jsonable_evidence(ranked)
        if selected_row is None:
            return None, status, reason, evidence, path
        selected = next(candidate for candidate in candidates if candidate["candidate_id"] == selected_row["candidate_id"])
        return selected, status, reason, evidence, path
    ranked = sorted(lexical_evidence, key=lambda item: (-float(item["score"]), item["candidate_id"]))
    if not ranked:
        return None, "ABSTAIN", "no candidates", lexical_evidence, "NO_CANDIDATES"
    top = ranked[0]
    second = ranked[1]["score"] if len(ranked) > 1 else 0.0
    margin = float(top["score"]) - float(second)
    if float(top["score"]) >= min_score and margin >= min_margin:
        selected = next(candidate for candidate in candidates if candidate["candidate_id"] == top["candidate_id"])
        return selected, "SELECTED", f"top scored candidate score={top['score']} margin={margin:.4f}", ranked, "IR_TOPK_RANK"
    return None, "ABSTAIN", f"no confident candidate score={top['score']} margin={margin:.4f}", ranked, "IR_RANK_ABSTAIN"


def configure_paths(benchmark_dir: Path) -> dict[str, Path]:
    if is_staged_layout(benchmark_dir):
        selection = candidate_selection_paths(benchmark_dir)
        evaluation = evaluation_paths(benchmark_dir)
        return {
            "event_csv": selection["event_csv"],
            "candidate_csv": selection["candidate_csv"],
            "oracle_csv": evaluation["oracle_csv"],
            "mutants_dir": selection["mutants_dir"],
            "built_dir": benchmark_dir / "repair-stage",
        }
    return {
        "event_csv": benchmark_dir / "input" / "external-real-event-template.csv",
        "candidate_csv": benchmark_dir / "input" / "external-real-candidate-template.csv",
        "oracle_csv": benchmark_dir / "private" / "external-real-oracle-template.csv",
        "mutants_dir": benchmark_dir / "mutants",
        "built_dir": benchmark_dir / "built",
    }


def load_candidates(path: Path) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_csv(path):
        if ready(row.get("status", "")):
            groups[row["event_id"]].append({**row, "operation": parse_jsonish(row.get("operation_json", ""))})
    return groups


def summarize(details: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_event_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_type_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        by_event_groups[row["event_id"]].append(row)
        by_type_groups[row["semantic_type"]].append(row)
    by_event = []
    for event_id, rows in sorted(by_event_groups.items()):
        closure = sum(bool(row.get("full_closure_success")) for row in rows)
        correct = sum(bool(row.get("selection_oracle_correct")) for row in rows)
        by_event.append(
            {
                "event_id": event_id,
                "semantic_type": rows[0]["semantic_type"],
                "domain": rows[0]["domain"],
                "attempts": len(rows),
                "selected": sum(row["selection_status"] == "SELECTED" for row in rows),
                "abstains": sum(row["selection_status"] == "ABSTAIN" for row in rows),
                "oracle_accuracy": correct / len(rows),
                "full_closure_accuracy": closure / len(rows),
                "strict_event_success": closure == len(rows),
            }
        )
    by_type = []
    for semantic_type, rows in [("ALL", details), *sorted(by_type_groups.items())]:
        event_rows = [row for row in by_event if semantic_type == "ALL" or row["semantic_type"] == semantic_type]
        correct = sum(bool(row.get("selection_oracle_correct")) for row in rows)
        closure = sum(bool(row.get("full_closure_success")) for row in rows)
        by_type.append(
            {
                "semantic_type": semantic_type,
                "events": len(event_rows),
                "attempts": len(rows),
                "selected": sum(row["selection_status"] == "SELECTED" for row in rows),
                "abstains": sum(row["selection_status"] == "ABSTAIN" for row in rows),
                "oracle_accuracy": correct / len(rows) if rows else 0,
                "full_closure_accuracy": closure / len(rows) if rows else 0,
                "strict_event_successes": sum(bool(row["strict_event_success"]) for row in event_rows),
                "strict_event_accuracy": sum(bool(row["strict_event_success"]) for row in event_rows) / len(event_rows) if event_rows else 0,
            }
        )
    return by_event, by_type, [by_type[0]]


def parse_only(value: str) -> set[str]:
    return {item.strip().upper() for item in value.split(",") if item.strip()}


RAW_NAME_RE = re.compile(r"^(?P<event_id>.+)-run(?P<run>\d+)-seed(?P<seed>\d+)\.json$")


def discover_raw_jobs(
    raw_dir: Path,
    *,
    only: set[str],
    ready_event_ids: set[str],
) -> list[tuple[str, int, int, Path]]:
    jobs: list[tuple[str, int, int, Path]] = []
    for path in sorted(raw_dir.glob("*.json")):
        match = RAW_NAME_RE.match(path.name)
        if not match:
            continue
        event_id = match.group("event_id")
        if event_id not in ready_event_ids:
            continue
        if only and event_id.upper() not in only:
            continue
        jobs.append((event_id, int(match.group("run")), int(match.group("seed")), path))
    return jobs


def main() -> int:
    parser = argparse.ArgumentParser(description="AUTO_POLICY_V4 Semantic-IR candidate repair")
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--prefix", default="auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--min-score", type=float, default=0.42)
    parser.add_argument("--min-margin", type=float, default=0.08)
    parser.add_argument(
        "--gre-css-min-score",
        type=float,
        default=None,
        help="Optional type-specific min_score for GENERAL_RULE_EXCEPTION and CROSS_SENTENCE_SCOPE.",
    )
    parser.add_argument("--reranker", choices=["lexical", "constraint", "constraint-all"], default="lexical")
    parser.add_argument(
        "--temporal-unique-top1",
        action="store_true",
        help="V4.3: TEMPORAL unique Top-1 with no contradiction may select below min_score",
    )
    parser.add_argument(
        "--robust-ir",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="V4.4: INVALID_SCHEMA with usable facts/canonical_result is parsed instead of fail-closed",
    )
    parser.add_argument("--only", default="")
    parser.add_argument("--method-name", default="")
    parser.add_argument(
        "--skip-missing-raw",
        action="store_true",
        help="skip attempts whose raw JSON file is absent (partial reruns)",
    )
    parser.add_argument(
        "--discover-raw",
        action="store_true",
        help="discover event/run/seed from raw filenames instead of seed+run-1 formula",
    )
    add_legacy_opt_in_arg(parser)
    args = parser.parse_args()
    guard_holdout_benchmark(args.benchmark_dir, __file__)
    guard_frozen_v43_config(args, script=__file__)
    args.benchmark_dir = args.benchmark_dir.resolve()
    args.raw_dir = args.raw_dir.resolve()
    args.output_dir = args.output_dir.resolve()

    paths = configure_paths(args.benchmark_dir)
    only = parse_only(args.only)
    events = [
        row
        for row in read_csv(paths["event_csv"])
        if ready(row.get("status", "")) and (not only or row["event_id"].upper() in only)
    ]
    candidates_by_event = load_candidates(paths["candidate_csv"])
    event_by_id = {row["event_id"]: row for row in events}
    details: list[dict[str, Any]] = []
    graph_cache: dict[Path, Any] = {}
    reasoner_cache: dict[Path, dict[str, Any]] = {}

    if args.discover_raw:
        raw_jobs = discover_raw_jobs(
            args.raw_dir,
            only=only,
            ready_event_ids=set(event_by_id),
        )
    else:
        raw_jobs = []
        for event in events:
            event_id = event["event_id"]
            for run in range(1, args.runs + 1):
                seed = args.seed + run - 1
                raw_path = args.raw_dir / f"{event_id}-run{run}-seed{seed}.json"
                if not raw_path.is_file():
                    if args.skip_missing_raw:
                        continue
                    raise FileNotFoundError(raw_path)
                raw_jobs.append((event_id, run, seed, raw_path))

    for event_id, run, seed, raw_path in raw_jobs:
        event = event_by_id[event_id]
        record = load_json(raw_path)
        ir = parse_semantic_ir(record, event, robust_ir=args.robust_ir)
        effective_min_score = args.min_score
        if (
            args.gre_css_min_score is not None
            and event.get("semantic_type") in {"GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"}
        ):
            effective_min_score = args.gre_css_min_score
        selected, status, reason, score_rows, decision_path = select_candidate_v4(
            event,
            candidates_by_event.get(event_id, []),
            ir,
            min_score=effective_min_score,
            min_margin=args.min_margin,
            reranker=args.reranker,
            temporal_unique_top1=args.temporal_unique_top1,
        )
        row: dict[str, Any] = {
            "event_id": event_id,
            "semantic_type": event["semantic_type"],
            "domain": event.get("domain", ""),
            "run": run,
            "seed": seed,
            "generation_status": record.get("status", ""),
            "ir_status": ir.ir_status,
            "ir_reason": ir.ir_reason,
            "ir_json": json.dumps(ir.fields, ensure_ascii=False, sort_keys=True),
            "ir_semantic_string": semantic_ir_string(ir),
            "source_family": ir.source_family,
            "normalized_numbers": "|".join(ir.numbers),
            "normalized_dates": "|".join(ir.dates),
            "normalized_codes": "|".join(ir.codes),
            "selection_status": status,
            "selected_candidate_id": selected["candidate_id"] if selected else "",
            "selected_value": selected["display_value"] if selected else "",
            "selection_reason": reason,
            "decision_path": decision_path,
            "candidate_scores_json": json.dumps(score_rows, ensure_ascii=False, sort_keys=True),
            "effective_min_score": effective_min_score,
            "effective_min_margin": args.min_margin,
            "runtime_ms": record.get("runtime_ms", 0),
            "prompt_eval_count": record.get("prompt_eval_count", 0),
            "eval_count": record.get("eval_count", 0),
            "generation_reliability_mode": record.get("generation_reliability_mode", ""),
            "format_constraint": record.get("format_constraint", ""),
            "raw_output_file": str(raw_path.relative_to(PROJECT_DIR)),
        }
        if selected:
            source_path = resolve_source_owl(event, mutants_dir=paths["mutants_dir"], project_dir=PROJECT_DIR)
            candidate_path = resolve_candidate_owl_path(
                event_id,
                selected["candidate_id"],
                benchmark_dir=args.benchmark_dir,
                built_dir=paths["built_dir"],
                output_dir=args.output_dir,
            )
            materialize_candidate_owl(source_path=source_path, operation=selected["operation"], dest_path=candidate_path)
            source_graph = graph_cache.setdefault(source_path, load_graph(source_path))
            candidate_graph = graph_cache.setdefault(candidate_path, load_graph(candidate_path))
            reasoner = reasoner_cache.setdefault(candidate_path, benchmark_validator.run_reasoner(candidate_path, args.timeout))
            source_trigger, candidate_repair, repair_message = repair_checks(source_graph, candidate_graph, selected["operation"])
            removed, added = graph_delta(source_graph, candidate_graph)
            row.update(
                {
                    "candidate_owl": str(candidate_path.relative_to(PROJECT_DIR)),
                    "triples_removed": removed,
                    "triples_added": added,
                    "minimal_edit_gate": removed == 1 and added == 1,
                    "reasoner_result": reasoner.get("status", ""),
                    "reasoner_runtime_ms": reasoner.get("runtime_ms", 0),
                    "reasoner_gate": reasoner.get("status") == "CONSISTENT",
                    "source_triggers_repair_cq": source_trigger,
                    "candidate_satisfies_repair_cq": candidate_repair,
                    "repair_cq_message": repair_message,
                }
            )
        details.append(row)

    if not details:
        raise RuntimeError(f"no raw records processed under {args.raw_dir}")

    oracles = {row["event_id"]: row for row in read_csv(paths["oracle_csv"]) if ready(row.get("status", ""))}
    for row in details:
        oracle = oracles[row["event_id"]]
        row["oracle_candidate_id"] = oracle["oracle_candidate_id"]
        row["oracle_value"] = oracle["oracle_value"]
        row["selection_oracle_correct"] = (
            row["selection_status"] == "SELECTED"
            and row["selected_candidate_id"] == oracle["oracle_candidate_id"]
        )
        row["full_closure_success"] = all(
            bool(row.get(key))
            for key in (
                "selection_oracle_correct",
                "minimal_edit_gate",
                "reasoner_gate",
                "source_triggers_repair_cq",
                "candidate_satisfies_repair_cq",
            )
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    by_event, by_type, summary = summarize(details)
    prefix = args.prefix
    write_csv(args.output_dir / f"{prefix}-details.csv", details)
    write_csv(args.output_dir / f"{prefix}-by-event.csv", by_event)
    write_csv(args.output_dir / f"{prefix}-by-type.csv", by_type)
    write_csv(args.output_dir / f"{prefix}-summary.csv", summary)
    (args.output_dir / f"{prefix}.json").write_text(
        json.dumps(
            {
                "method": (
                    args.method_name
                    if args.method_name
                    else "AUTO_POLICY_V4_4_CSS_SLOT_COMPLETION"
                    if args.robust_ir
                    else "AUTO_POLICY_V4_3_TEMPORAL_UNIQUE_TOP1"
                    if args.temporal_unique_top1
                    else "AUTO_POLICY_V4_2_CONSTRAINT_RERANK"
                    if args.reranker == "constraint"
                    else "AUTO_POLICY_V4_SEMANTIC_IR"
                ),
                "css_slot_completion": True,
                "reranker": args.reranker,
                "min_score": args.min_score,
                "gre_css_min_score": args.gre_css_min_score,
                "min_margin": args.min_margin,
                "temporal_unique_top1": args.temporal_unique_top1,
                "robust_ir": args.robust_ir,
                "candidate_blind_ir": True,
                "qwen_called": False,
                "candidate_seen_stage": "candidate_ranking_after_ir_fixed",
                "summary": summary,
                "ir_status_counts": dict(Counter(row["ir_status"] for row in details)),
                "decision_path_counts": dict(Counter(row["decision_path"] for row in details)),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("AUTO_POLICY_V4_SEMANTIC_IR candidate repair")
    print(f"details={args.output_dir / (prefix + '-details.csv')}")
    print(f"summary={args.output_dir / (prefix + '-summary.csv')}")
    for row in by_type:
        print(
            "[{semantic_type}] oracle={oracle_accuracy:.2%} closure={full_closure_accuracy:.2%} "
            "strict={strict_event_successes}/{events} abstains={abstains}/{attempts}".format(**row)
        )
    print("ir_status=" + json.dumps(dict(Counter(row["ir_status"] for row in details)), ensure_ascii=False, sort_keys=True))
    print("decision_path=" + json.dumps(dict(Counter(row["decision_path"] for row in details)), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
