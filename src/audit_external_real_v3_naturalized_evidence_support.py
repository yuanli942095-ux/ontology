from __future__ import annotations

"""Audit whether each public evidence window supports its formal-policy conclusion.

This is an offline benchmark-quality audit. It reads public event, candidate,
excerpt, and formal-policy files only. It never reads the private Oracle and it
does not call an LLM.
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(r"G:\LearnAI\ontology-evolution")
DEFAULT_BENCHMARK = ROOT / "benchmark" / "external-real-v3-naturalized"
DEFAULT_OUTPUT = ROOT / "output" / "external-real-v3-naturalized"

STOP_TOKENS = {
    "added",
    "after",
    "all",
    "and",
    "application",
    "available",
    "before",
    "case",
    "condition",
    "context",
    "current",
    "date",
    "document",
    "effective",
    "event",
    "general",
    "included",
    "information",
    "minimum",
    "new",
    "not",
    "objective",
    "old",
    "policy",
    "previous",
    "process",
    "provided",
    "public",
    "regulation",
    "requirement",
    "rule",
    "scope",
    "specified",
    "status",
    "supported",
    "target",
    "the",
    "updated",
    "version",
    "with",
}

MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="audit event-level public evidence support")
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--prefix", default="external-real-v3-naturalized-evidence-support-audit")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def as_date(value: Any) -> date:
    return date.fromisoformat(str(value))


def clause_matches(facts: dict[str, Any], clause: dict[str, Any]) -> bool:
    actual = facts[str(clause["fact"])]
    expected = clause["value"]
    operator = str(clause["operator"])
    if operator == "equals":
        return type(actual) is type(expected) and actual == expected
    if operator == "not_equals":
        return not (type(actual) is type(expected) and actual == expected)
    if operator == "on_or_after":
        return as_date(actual) >= as_date(expected)
    if operator == "on_or_before":
        return as_date(actual) <= as_date(expected)
    if operator == "greater_than":
        return Decimal(str(actual)) > Decimal(str(expected))
    if operator == "greater_or_equal":
        return Decimal(str(actual)) >= Decimal(str(expected))
    if operator == "less_than":
        return Decimal(str(actual)) < Decimal(str(expected))
    if operator == "less_or_equal":
        return Decimal(str(actual)) <= Decimal(str(expected))
    raise ValueError(f"unsupported operator: {operator}")


def selected_policy_values(path: Path) -> tuple[list[str], str]:
    policy = json.loads(path.read_text(encoding="utf-8-sig"))
    facts = policy.get("facts")
    rules = policy.get("rules")
    if not isinstance(facts, dict) or not isinstance(rules, list):
        return [], "invalid policy shape"
    applicable = [
        rule
        for rule in rules
        if all(clause_matches(facts, clause) for clause in rule.get("conditions", []))
    ]
    if not applicable:
        return [], "no applicable rule"
    priority = max(int(rule.get("priority", 0)) for rule in applicable)
    winners = [rule for rule in applicable if int(rule.get("priority", 0)) == priority]
    if len(winners) != 1:
        return [], "non-unique highest-priority rule"
    values = [str(value).strip() for value in winners[0].get("allowed_values", [])]
    return values, ""


def evidence_body(text: str) -> str:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().lower() in {"evidence summary:", "raw source excerpt windows:"}:
            start = index + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        lower = line.strip().lower()
        if "public candidate values" in lower or lower in {"status:", "status"}:
            break
        body.append(line)
    return "\n".join(body).strip()


def normalize_dates(text: str) -> str:
    normalized = text.lower()
    for month, number in MONTHS.items():
        normalized = re.sub(
            rf"\b(\d{{1,2}})\s+{month}\s+(\d{{4}})\b",
            lambda match: f"{match.group(2)} {number} {int(match.group(1)):02d}",
            normalized,
        )
        normalized = re.sub(
            rf"\b{month}\s+(\d{{1,2}}),?\s+(\d{{4}})\b",
            lambda match: f"{match.group(2)} {number} {int(match.group(1)):02d}",
            normalized,
        )
    return normalized


def semantic_surface(text: str) -> str:
    """Expand common canonical codes before lexical evidence comparison.

    The benchmark's machine values use compact tokens such as ``wcag22`` and
    ``gt60``. Requiring those exact codes in natural source prose creates false
    failures even when the source says ``WCAG 2.2`` or ``above 60 degrees``.
    This expansion is generic and does not inspect event-specific answers.
    """
    expanded = text.lower().replace("_", " ").replace("=", " ").replace(";", " ")
    expanded = re.sub(r"\bwcag(\d)(\d)\b", r"wcag \1.\2", expanded)
    expanded = re.sub(r"\blt\s*(\d+(?:\.\d+)?)\b", r"below \1", expanded)
    expanded = re.sub(r"\bgt\s*(\d+(?:\.\d+)?)\b", r"above \1", expanded)
    expanded = re.sub(r"\bge\s*(\d+(?:\.\d+)?)\b", r"at least \1", expanded)
    expanded = re.sub(r"\ble\s*(\d+(?:\.\d+)?)\b", r"at most \1", expanded)
    # level1/level2 are serialization positions, not source-side measurements.
    expanded = re.sub(r"\blevel\s*\d+\b", "level", expanded)
    return expanded


def tokens(text: str) -> set[str]:
    normalized = normalize_dates(semantic_surface(text)).replace("wcag2ict", "wcag 2 ict")
    return {
        token
        for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", normalized.lower())
        if len(token) >= 2 and token not in STOP_TOKENS
    }


def numeric_tokens(items: set[str]) -> set[str]:
    return {item for item in items if re.fullmatch(r"\d+(?:\.\d+)?", item)}


def score_event(
    event: dict[str, str],
    evidence_text: str,
    allowed_values: list[str],
    candidate_values: list[str],
) -> dict[str, Any]:
    body = evidence_body(evidence_text)
    source_title_match = re.search(
        r"^- (?:Current )?source title:\s*(.+)$",
        evidence_text,
        flags=re.M | re.I,
    )
    source_title = source_title_match.group(1).strip() if source_title_match else ""
    source_tokens = tokens(f"{source_title}\n{body}")
    expected_tokens = tokens(" ".join(allowed_values))
    distractor_tokens = tokens(" ".join(value for value in candidate_values if value not in allowed_values))
    distinctive = expected_tokens - distractor_tokens
    matched = expected_tokens & source_tokens
    missing = expected_tokens - source_tokens
    critical_numeric = numeric_tokens(expected_tokens)
    missing_numeric = critical_numeric - source_tokens
    ratio = len(matched) / len(expected_tokens) if expected_tokens else 0.0

    if not allowed_values:
        status = "FAIL"
        reason = "no unique executable policy conclusion"
    elif not body:
        status = "FAIL"
        reason = "no raw evidence body"
    elif missing_numeric:
        status = "FAIL"
        reason = "critical numeric/date/entity tokens missing"
    elif distinctive and not (distinctive & source_tokens):
        status = "FAIL"
        reason = "no distinctive conclusion token in source window"
    elif ratio >= 0.60:
        status = "PASS"
        reason = "strong lexical support"
    elif ratio >= 0.30:
        status = "WARN"
        reason = "partial lexical support; manual review required"
    else:
        status = "FAIL"
        reason = "weak event-level lexical support"

    return {
        "event_id": event["event_id"],
        "domain": event["domain"],
        "semantic_type": event["semantic_type"],
        "source_title": source_title,
        "status": status,
        "reason": reason,
        "support_ratio": round(ratio, 4),
        "expected_token_count": len(expected_tokens),
        "matched_token_count": len(matched),
        "distinctive_token_count": len(distinctive),
        "missing_tokens": "|".join(sorted(missing)),
        "missing_numeric_tokens": "|".join(sorted(missing_numeric)),
        "evidence_body_chars": len(body),
    }


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    benchmark = args.benchmark_dir.resolve()
    input_dir = benchmark / "input"
    excerpts = benchmark / "documents" / "excerpts"
    rules = benchmark / "rules"
    events = [
        row
        for row in read_csv(input_dir / "external-real-event-template.csv")
        if str(row.get("status", "")).upper() == "READY"
    ]
    candidates_by_event: dict[str, list[str]] = defaultdict(list)
    for row in read_csv(input_dir / "external-real-candidate-template.csv"):
        if str(row.get("status", "")).upper() == "READY":
            candidates_by_event[row["event_id"]].append(row["display_value"])

    details: list[dict[str, Any]] = []
    for event in events:
        event_id = event["event_id"]
        allowed, error = selected_policy_values(rules / f"{event_id}-formal-policy.json")
        evidence_text = (excerpts / f"{event_id}-evidence.md").read_text(encoding="utf-8-sig")
        row = score_event(event, evidence_text, allowed, candidates_by_event[event_id])
        if error:
            row["reason"] = error
        details.append(row)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        groups[row["domain"]].append(row)
    summary: list[dict[str, Any]] = []
    for domain, items in [("ALL", details), *sorted(groups.items())]:
        counts = Counter(row["status"] for row in items)
        summary.append(
            {
                "domain": domain,
                "events": len(items),
                "pass": counts["PASS"],
                "warn": counts["WARN"],
                "fail": counts["FAIL"],
                "pass_rate": counts["PASS"] / len(items) if items else 0,
                "mean_support_ratio": round(
                    sum(float(row["support_ratio"]) for row in items) / len(items), 4
                ) if items else 0,
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    details_path = args.output_dir / f"{args.prefix}-details.csv"
    summary_path = args.output_dir / f"{args.prefix}-summary.csv"
    json_path = args.output_dir / f"{args.prefix}.json"
    report_path = args.output_dir / f"{args.prefix}.md"
    write_csv(details_path, details)
    write_csv(summary_path, summary)
    payload = {
        "schema_version": "1.0",
        "audit_version": "2.0-canonical-surface",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": benchmark.name,
        "private_oracle_read": False,
        "llm_used": False,
        "method": "deterministic lexical support screen against executable public policy conclusion",
        "summary": summary,
        "details_csv": str(details_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report = f"""# Event-level Evidence Support Audit

This audit checks whether the event-specific public source title and raw excerpt
window lexically support the unique conclusion of the executable public formal
policy. It does not read the private Oracle and does not call an LLM.

{markdown_table(summary, ['domain', 'events', 'pass', 'warn', 'fail', 'pass_rate', 'mean_support_ratio'])}

Interpretation:

- `PASS` means the window contains strong conclusion-specific lexical support.
- `WARN` means partial support and requires manual evidence review.
- `FAIL` means the current window is not adequate for a natural-document experiment.
- This screen is conservative and cannot prove semantic entailment; it is a triage tool.
"""
    report_path.write_text(report, encoding="utf-8")
    print(f"details={details_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for row in summary:
        print(
            f"[{row['domain']}] events={row['events']} pass={row['pass']} "
            f"warn={row['warn']} fail={row['fail']} mean_support={row['mean_support_ratio']:.2%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
