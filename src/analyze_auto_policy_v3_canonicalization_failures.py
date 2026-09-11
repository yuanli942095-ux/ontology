from __future__ import annotations

"""Sub-classify AUTO_POLICY_V3 canonicalization failures.

This is a diagnostic triage over existing outputs. It reads private Oracle and
manual policies only for offline error analysis, not for model input.
"""

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
TRACE_DIR = PROJECT_DIR / "output" / "auto-policy-v3-trace-diagnostic-pack"
INPUT_CSV = TRACE_DIR / "all-attempt-layer-diagnosis.csv"
OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v3-canonicalization-failure-analysis"


LABELS = {
    "A": "Synonym / paraphrase not normalized",
    "B": "Field name or JSON structure mismatch",
    "C": "Type mismatch",
    "D": "Number / date / unit format mismatch",
    "E": "Relation direction reversal",
    "F": "Wrong schema adapter branch",
    "G": "Missing required semantic fields",
}

SYNONYM_MARKERS = {
    "replace",
    "replaces",
    "replaced",
    "replacement",
    "supersede",
    "supersedes",
    "superseded",
    "successor",
    "predecessor",
    "amend",
    "amended",
    "revision",
    "revised",
    "updated",
    "inherits",
    "extends",
    "exception",
    "except",
    "unless",
    "scope",
    "applies",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def abs_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


def norm(value: Any) -> str:
    text = str(value or "").lower()
    return re.sub(r"\s+", " ", text).strip()


def compact(value: Any) -> str:
    text = norm(value)
    for old, new in {
        "×": "*",
        "＝": "=",
        "：": ":",
        "；": ";",
        "，": ",",
        "（": "(",
        "）": ")",
        "-": "_",
        "/": "_",
    }.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", "", text)


def tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", norm(value))
        if len(token) > 1
    }


def parse_jsonish(value: str) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def top_manual_allowed(event_id: str) -> str:
    path = BENCHMARK_DIR / "rules" / f"{event_id}-formal-policy.json"
    policy = load_json(path)
    rules = policy.get("rules", [])
    if not isinstance(rules, list) or not rules:
        return ""
    top = max(rules, key=lambda item: int(item.get("priority", 0)) if isinstance(item, dict) else 0)
    values = top.get("allowed_values", []) if isinstance(top, dict) else []
    if isinstance(values, list) and values:
        return str(values[0])
    return ""


def expected_family(value: str) -> str:
    text = compact(value)
    if text.startswith("spring=1100") or "summer_autumn" in text:
        return "insurance_amount_split"
    if text.startswith("five_level_definition"):
        return "insurance_lodging_levels"
    if text.startswith("formula="):
        return "insurance_formula"
    if text.startswith("wcag22_added"):
        return "wcag22_added"
    if text.startswith("wcag21_cross_scope"):
        return "wcag21_cross_scope"
    if text.startswith("wcag21_input_rule"):
        return "wcag21_input_rule"
    if text.startswith("nist_revision_change"):
        return "nist_revision_change"
    if ":" in text:
        return text.split(":", 1)[0]
    if "=" in text:
        return text.split("=", 1)[0]
    return ""


def raw_family(raw_semantic: str, response: dict[str, Any]) -> str:
    text = compact(raw_semantic)
    if ":" in text:
        return text.split(":", 1)[0]
    if "=" in text:
        return text.split("=", 1)[0]
    canonical = response.get("canonical_result") if isinstance(response, dict) else None
    if isinstance(canonical, dict) and canonical.get("family"):
        return compact(canonical.get("family"))
    return ""


def response_payload(row: dict[str, str]) -> dict[str, Any]:
    raw_file = row.get("raw_output_file", "")
    if not raw_file:
        return {}
    path = abs_path(raw_file)
    if not path.is_file():
        return {}
    record = load_json(path)
    response = record.get("response", {})
    return response if isinstance(response, dict) else {}


def nested_values(value: Any) -> list[Any]:
    result: list[Any] = []
    if isinstance(value, dict):
        for item in value.values():
            result.extend(nested_values(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(nested_values(item))
    else:
        result.append(value)
    return result


def classify(row: dict[str, str], response: dict[str, Any], expected: str) -> tuple[str, str]:
    raw_semantic = row.get("raw_semantic_result", "")
    raw_text = norm(json.dumps(response, ensure_ascii=False))
    raw_compact = compact(raw_text)
    expected_compact = compact(expected)
    expected_family_value = expected_family(expected)
    raw_family_value = raw_family(raw_semantic, response)
    canonical = response.get("canonical_result") if isinstance(response, dict) else None
    facts = response.get("facts") if isinstance(response, dict) else None
    rules = response.get("rules") if isinstance(response, dict) else None

    if not raw_semantic and not canonical:
        return "G", "no top-rule semantic_result and no canonical_result"
    if not isinstance(rules, list) or not rules:
        return "G", "missing rules array"
    if not isinstance(canonical, dict):
        return "B", "canonical_result is not an object"
    keys = set(canonical.keys()) | (set(facts.keys()) if isinstance(facts, dict) else set())
    expected_tokens = tokens(expected)
    raw_tokens = tokens(raw_text)
    matched_expected = expected_tokens & raw_tokens
    digit_expected = re.findall(r"\d+(?:[.,]\d+)?", expected)
    digit_raw = re.findall(r"\d+(?:[.,]\d+)?", raw_text)

    if (
        any(marker in raw_compact for marker in ("supersededby", "replacedby", "predecessor", "successor"))
        and any(marker in expected_compact for marker in ("supersedes", "replace", "current", "new"))
    ):
        return "E", "directional replacement/supersession wording needs reversal handling"

    values = nested_values(canonical) + nested_values(facts)
    if any(isinstance(item, (int, float, bool)) for item in values):
        if any(marker in expected_compact for marker in ("version", "date", "year", "level", "true", "false")):
            return "C", "typed value emitted where canonical string vocabulary is expected"

    if digit_expected:
        normalized_expected_digits = {item.replace(",", "") for item in digit_expected}
        normalized_raw_digits = {item.replace(",", "") for item in digit_raw}
        missing_digits = normalized_expected_digits - normalized_raw_digits
        if missing_digits:
            return "D", "expected numeric/date tokens missing or serialized differently: " + "|".join(sorted(missing_digits))
        if digit_raw and digit_expected:
            return "D", "numeric/date/unit representation not reduced to canonical form"

    same_family = bool(expected_family_value and raw_family_value and expected_family_value == raw_family_value)
    web_expected = expected_family_value.startswith("wcag") or "mobile" in expected_family_value
    web_raw = raw_family_value.startswith("wcag") or "mobile" in raw_family_value
    nist_like_raw = raw_family_value in {"nist_revision_change", "nist63_revision"}
    if expected_family_value and raw_family_value and not same_family:
        if web_expected and web_raw:
            return "F", f"WCAG adapter branch mismatch: expected {expected_family_value}, got {raw_family_value}"
        if nist_like_raw:
            if matched_expected:
                return "B", f"broad NIST/source revision family not lowered to ontology family {expected_family_value}"
            return "G", f"broad NIST/source revision family lacks required target tokens for {expected_family_value}"
        if synonym_hits := sorted(SYNONYM_MARKERS & raw_tokens):
            return "A", "family alias/paraphrase needs canonical vocabulary mapping: " + "|".join(synonym_hits[:6])
        if matched_expected:
            return "B", f"family alias not mapped: expected {expected_family_value}, got {raw_family_value}"
        return "F", f"expected family {expected_family_value}, got {raw_family_value}"

    known_structural_keys = {
        "family",
        "value",
        "values",
        "current_value",
        "new_value",
        "old_value",
        "current_rule",
        "general_rule",
        "exception_rule",
        "exception_condition",
        "scope_relation",
        "applies_to",
        "target",
        "relation",
        "old_version",
        "new_version",
        "effective_date",
        "revision_year",
    }
    if keys and len(keys & known_structural_keys) < max(1, min(2, len(keys))):
        return "B", "field names do not match known Semantic IR / canonical adapter keys"

    synonym_hits = sorted(SYNONYM_MARKERS & raw_tokens)
    if synonym_hits and len(matched_expected) >= 1:
        return "A", "semantic paraphrase needs vocabulary normalization: " + "|".join(synonym_hits[:6])

    if len(matched_expected) < max(1, min(3, len(expected_tokens))):
        return "G", "required canonical facts are absent or underspecified"

    return "B", "structure is plausible but current adapter does not serialize it"


def main() -> int:
    rows = [
        row
        for row in read_csv(INPUT_CSV)
        if row.get("failure_layer_hypothesis") == "Canonicalization Failure"
    ]
    output_rows: list[dict[str, Any]] = []
    for row in rows:
        response = response_payload(row)
        expected = top_manual_allowed(row["event_id"])
        subcode, reason = classify(row, response, expected)
        output_rows.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "semantic_type": row["semantic_type"],
                "domain": row["domain"],
                "subtype_code": subcode,
                "subtype_label": LABELS[subcode],
                "reason": reason,
                "expected_family": expected_family(expected),
                "raw_family": raw_family(row.get("raw_semantic_result", ""), response),
                "raw_semantic_result": row.get("raw_semantic_result", ""),
                "auto_semantic_result_used_for_repair": row.get("auto_semantic_result_used_for_repair", ""),
                "semantic_correct": row.get("semantic_correct", ""),
                "selection_status": row.get("selection_status", ""),
                "survivor_count": row.get("survivor_count", ""),
                "raw_output_file": row.get("raw_output_file", ""),
                "trace_file": str((TRACE_DIR / "trace-markdown" / f"{row['event_id']}-run{row['run']}-canonicalization_failure.md").resolve()),
            }
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT_DIR / "canonicalization-failure-subtypes.csv", output_rows)

    subtype_counts = Counter(row["subtype_code"] for row in output_rows)
    type_subtype = Counter((row["semantic_type"], row["subtype_code"]) for row in output_rows)
    domain_subtype = Counter((row["domain"], row["subtype_code"]) for row in output_rows)
    summary_rows = [
        {
            "subtype_code": code,
            "subtype_label": LABELS[code],
            "count": subtype_counts.get(code, 0),
            "rate": subtype_counts.get(code, 0) / len(output_rows) if output_rows else 0,
        }
        for code in LABELS
    ]
    write_csv(OUTPUT_DIR / "canonicalization-failure-subtype-summary.csv", summary_rows)
    write_csv(
        OUTPUT_DIR / "canonicalization-failure-by-semantic-type.csv",
        [
            {"semantic_type": semantic_type, "subtype_code": code, "subtype_label": LABELS[code], "count": count}
            for (semantic_type, code), count in sorted(type_subtype.items())
        ],
    )
    write_csv(
        OUTPUT_DIR / "canonicalization-failure-by-domain.csv",
        [
            {"domain": domain, "subtype_code": code, "subtype_label": LABELS[code], "count": count}
            for (domain, code), count in sorted(domain_subtype.items())
        ],
    )

    lines = [
        "# Canonicalization failure subtype analysis",
        "",
        f"Input failures: {len(output_rows)}",
        "",
        "| subtype | label | count | rate |",
        "|---|---|---:|---:|",
    ]
    for item in summary_rows:
        lines.append(
            f"| {item['subtype_code']} | {item['subtype_label']} | {item['count']} | {float(item['rate']):.2%} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "- A/B/C/D/E/F/G are diagnostic labels, not benchmark scores.",
        "- The labels are meant to decide which normalizer/schema-adapter branch to implement first.",
        "- Private Oracle and manual formal policy are used only after automatic decisions are fixed, for offline diagnosis.",
    ]
    (OUTPUT_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"canonicalization_failures={len(output_rows)}")
    print(f"output={OUTPUT_DIR}")
    print("subtypes=" + json.dumps(dict(sorted(subtype_counts.items())), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
