from __future__ import annotations

"""Fine-grained semantic-missing analysis for C5 failure cases (V4.5 frozen 600)."""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready, result_from_canonical
from semantic_v2_common import PROJECT_DIR, write_csv

OUTPUT_ROOT = PROJECT_DIR / "output" / "schema-contract-repair-ablation"
FAILURE_CSV = OUTPUT_ROOT / "failure-classification.csv"
IR_DETAILS = (
    OUTPUT_ROOT
    / "arm-a-adaptive"
    / "ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)

REQUIRED_BY_TYPE = {
    "TEMPORAL_VERSION": ("subject", "relation", "result"),
    "GENERAL_RULE_EXCEPTION": ("subject", "result"),
    "CROSS_SENTENCE_SCOPE": ("subject", "statement", "scope_relation", "result"),
}

TEMPORAL_ROLE_FIELDS = ("relation", "old_value", "new_value", "effective_time")
EXCEPTION_FIELDS = ("exception_condition", "exception_rule", "general_rule", "priority")
SCOPE_FIELDS = ("scope_relation", "scope_target", "qualifier", "statement")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {flatten_text(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(flatten_text(item) for item in value)
    return str(value or "")


def nonempty(value: Any) -> bool:
    return value not in (None, "", [], {}) and not (isinstance(value, str) and not value.strip())


def model_abstained(response: dict[str, Any] | None) -> bool:
    if not isinstance(response, dict):
        return False
    if response.get("abstain") is True:
        return True
    canonical = response.get("canonical_result")
    if isinstance(canonical, dict):
        reason = str(canonical.get("reason", "")).strip().lower()
        if reason in {"abstain", "abstained", "insufficient_evidence"}:
            return True
        if canonical.get("family") in (None, "", "null") and reason:
            return True
    return False


def semantic_result_present(response: dict[str, Any] | None) -> bool:
    if not isinstance(response, dict):
        return False
    rules = response.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict) and nonempty(rule.get("semantic_result")):
                return True
    if nonempty(result_from_canonical(response.get("canonical_result"))):
        return True
    return nonempty(response.get("result")) or nonempty(response.get("semantic_result"))


def facts_richness(response: dict[str, Any] | None) -> int:
    if not isinstance(response, dict):
        return 0
    facts = response.get("facts")
    if not isinstance(facts, dict):
        return 0
    return sum(1 for value in facts.values() if nonempty(value))


def parse_missing_fields(ir_reason: str) -> list[str]:
    text = str(ir_reason or "")
    if not text.startswith("missing:"):
        return []
    return [part.strip() for part in text.split("missing:", 1)[1].split("|") if part.strip()]


def classify_missing_subtype(
    row: dict[str, str],
    response: dict[str, Any] | None,
    missing_fields: list[str],
) -> str:
    generation_status = row.get("generation_status", "")
    if generation_status == "INVALID_JSON":
        return "M7_INVALID_JSON"
    if model_abstained(response):
        return "M1_ABSTAIN_OR_EMPTY"
    if facts_richness(response) >= 4 and not semantic_result_present(response):
        return "M6_MALFORMED_RICH_PAYLOAD"
    if "scope_relation" in missing_fields:
        return "M4_MISSING_SCOPE_RELATION"
    if "relation" in missing_fields:
        return "M3_MISSING_RELATION"
    if "result" in missing_fields:
        return "M2_MISSING_RESULT"
    if row.get("semantic_type") == "GENERAL_RULE_EXCEPTION" and not semantic_result_present(response):
        return "M5_MISSING_EXCEPTION_RULE"
    if generation_status == "INVALID_SCHEMA" and not semantic_result_present(response):
        return "M2_MISSING_RESULT"
    return "M0_OTHER_SEMANTIC_GAP"


def evidence_supports_missing(
    evidence: str,
    response: dict[str, Any] | None,
    missing_fields: list[str],
    subtype: str,
) -> str:
    if not evidence.strip():
        return "no_evidence_file"
    lower = evidence.lower()
    if subtype == "M7_INVALID_JSON":
        return "not_applicable"
    if subtype == "M1_ABSTAIN_OR_EMPTY":
        return "unknown_model_withheld"

    hits: list[str] = []
    if isinstance(response, dict):
        facts = response.get("facts")
        if isinstance(facts, dict):
            for key, value in facts.items():
                if not nonempty(value):
                    continue
                token = str(value).strip().lower()
                if len(token) >= 6 and token[:40] in lower:
                    hits.append(f"fact_echo:{key}")

    keyword_map = {
        "M2_MISSING_RESULT": ("effective", "revision", "supersed", "replace", "amend", "exception", "shall", "must"),
        "M3_MISSING_RELATION": ("effective", "supersed", "replace", "amend", "revision", "updated"),
        "M4_MISSING_SCOPE_RELATION": ("applies to", "scope", "except", "unless", "remains valid"),
        "M5_MISSING_EXCEPTION_RULE": ("except", "unless", "override", "exception", "provided that"),
        "M6_MALFORMED_RICH_PAYLOAD": ("except", "unless", "effective", "scope", "replace"),
    }
    for keyword in keyword_map.get(subtype, ()):
        if keyword in lower:
            hits.append(f"kw:{keyword}")

    if hits:
        return "evidence_present_model_gap"
    if facts_richness(response) >= 2:
        return "partial_extraction_evidence_unclear"
    return "weak_or_missing_evidence_signal"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failure-csv", type=Path, default=FAILURE_CSV)
    parser.add_argument("--ir-details", type=Path, default=IR_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()

    failures = [row for row in read_csv(args.failure_csv) if row.get("failure_class") == "C5"]
    ir_map = {(row["event_id"], row["run"]): row for row in read_csv(args.ir_details)}

    rows: list[dict[str, Any]] = []
    for row in failures:
        key = (row["event_id"], row["run"])
        ir = ir_map.get(key, {})
        raw_path = PROJECT_DIR / row["raw_output_file"]
        record = load_json(raw_path) if raw_path.is_file() else {}
        response = record.get("response") if isinstance(record.get("response"), dict) else None
        missing_fields = parse_missing_fields(ir.get("ir_reason", row.get("ir_reason", "")))
        subtype = classify_missing_subtype(row, response, missing_fields)

        evidence = ""
        evidence_path = None
        if record.get("candidate_blind_file"):
            evidence_path = PROJECT_DIR / str(record["candidate_blind_file"])
            if evidence_path.is_file():
                evidence = evidence_path.read_text(encoding="utf-8")

        rows.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "seed": row["seed"],
                "semantic_type": row["semantic_type"],
                "domain": row.get("domain", ""),
                "generation_status": row.get("generation_status", ""),
                "validation_reason": row.get("validation_reason", ""),
                "ir_status": ir.get("ir_status", row.get("ir_status", "")),
                "ir_reason": ir.get("ir_reason", row.get("ir_reason", "")),
                "missing_fields": "|".join(missing_fields),
                "missing_subtype": subtype,
                "facts_field_count": facts_richness(response),
                "semantic_result_present": semantic_result_present(response),
                "model_abstained": model_abstained(response),
                "evidence_signal": evidence_supports_missing(evidence, response, missing_fields, subtype),
                "evidence_chars": len(evidence),
                "raw_output_file": row.get("raw_output_file", ""),
            }
        )

    subtype_counts = Counter(row["missing_subtype"] for row in rows)
    bucket_counts = Counter(
        "generation_tail_invalid_json"
        if row["missing_subtype"] == "M7_INVALID_JSON"
        else "true_semantic_missing"
        for row in rows
    )
    by_type = defaultdict(lambda: Counter())
    for row in rows:
        by_type[row["semantic_type"]][row["missing_subtype"]] += 1

    summary = {
        "c5_attempts": len(rows),
        "bucket_counts": dict(bucket_counts),
        "subtype_counts": dict(subtype_counts),
        "by_semantic_type": {key: dict(value) for key, value in sorted(by_type.items())},
        "missing_field_counts": dict(Counter(field for row in rows for field in row["missing_fields"].split("|") if field)),
        "evidence_signal_counts": dict(Counter(row["evidence_signal"] for row in rows)),
        "true_semantic_missing": sum(1 for row in rows if row["missing_subtype"] != "M7_INVALID_JSON"),
        "invalid_json_in_c5": sum(1 for row in rows if row["missing_subtype"] == "M7_INVALID_JSON"),
        "evidence_present_model_gap": sum(1 for row in rows if row["evidence_signal"] == "evidence_present_model_gap"),
        "next_step": "Type-specific Structured Extraction on true_semantic_missing subset",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "c5-semantic-missing-analysis.csv", rows)
    (args.output_dir / "c5-semantic-missing-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
