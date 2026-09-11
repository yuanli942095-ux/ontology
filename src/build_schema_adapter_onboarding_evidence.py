from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVENTS_CSV = ROOT / "benchmark" / "external-real-v1" / "input" / "external-real-event-template.csv"
OUT_CSV = ROOT / "output" / "auto-policy-v3-schema-adapter-onboarding-evidence.csv"
OUT_BY_DOMAIN = ROOT / "output" / "auto-policy-v3-schema-adapter-onboarding-by-domain.csv"
OUT_MD = ROOT / "output" / "auto-policy-v3-schema-adapter-onboarding-evidence.md"
OUT_JSON = ROOT / "output" / "auto-policy-v3-schema-adapter-onboarding-evidence.json"


NIST_CHANGE_KEYS = {
    "EXT_E025": "risk_management_text",
    "EXT_E026": "continuous_evaluation_metrics",
    "EXT_E027": "fraud_requirements",
    "EXT_E028": "identity_proofing_controls",
    "EXT_E029": "controls_added",
    "EXT_E030": "syncable_authenticators",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def family_for_event(row: dict[str, str]) -> str:
    domain = row["domain"]
    title = row["title"].lower()
    predicate = row["predicate_label"].lower()
    semantic_type = row["semantic_type"]
    event_id = row["event_id"]
    if domain == "web_accessibility":
        if "input modality" in title or "input" in predicate:
            return "wcag21_input_rule"
        if semantic_type == "CROSS_SENTENCE_SCOPE":
            return "wcag21_cross_scope"
        return "wcag22_added"
    if domain == "insurance":
        text = row["title"] + " " + row["subject_label"] + " " + row["predicate_label"]
        if "保险金额" in text:
            return "insurance_amount_split"
        if "倒伏" in text:
            return "insurance_lodging_levels"
        if "公式" in text or "赔偿" in text:
            return "insurance_formula"
        return "insurance_other"
    if domain == "digital_identity":
        return f"nist_revision_change:{NIST_CHANGE_KEYS.get(event_id, 'unknown_change_key')}"
    return "unknown"


def build_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events = [row for row in read_csv(EVENTS_CSV) if row.get("status", "").upper() == "READY"]
    family_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in events:
        family_groups[(row["domain"], family_for_event(row))].append(row)

    rows: list[dict[str, Any]] = []
    for (domain, family), items in sorted(family_groups.items()):
        semantic_counts = Counter(row["semantic_type"] for row in items)
        rows.append(
            {
                "domain": domain,
                "canonical_family": family,
                "event_count": len(items),
                "event_ids": "; ".join(row["event_id"] for row in items),
                "semantic_type_counts": json.dumps(dict(sorted(semantic_counts.items())), ensure_ascii=False, sort_keys=True),
                "minimum_family_exemplars": 1,
                "reuse_ratio_events_per_family_exemplar": len(items),
                "adapter_claim_strength": "family_reuse" if len(items) > 1 else "single_event_family",
            }
        )

    domain_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        domain_groups[str(row["domain"])].append(row)
    by_domain: list[dict[str, Any]] = []
    for domain, items in sorted(domain_groups.items()):
        event_count = sum(int(row["event_count"]) for row in items)
        family_count = len(items)
        reused_families = sum(int(row["event_count"]) > 1 for row in items)
        singletons = family_count - reused_families
        by_domain.append(
            {
                "domain": domain,
                "event_count": event_count,
                "canonical_family_count": family_count,
                "minimum_family_exemplars": family_count,
                "event_per_family_ratio": event_count / family_count if family_count else 0,
                "reused_family_count": reused_families,
                "single_event_family_count": singletons,
                "schema_adapter_assessment": (
                    "strong_reuse"
                    if event_count / family_count >= 3
                    else "moderate_or_small_sample_reuse"
                    if event_count / family_count > 1
                    else "weak_reuse_single_event_keys"
                ),
            }
        )
    return rows, by_domain


def pct_or_float(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    fields = list(rows[0].keys())
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(pct_or_float(row[field]).replace("|", "/") for field in fields) + " |")
    return "\n".join(lines)


def write_md(rows: list[dict[str, Any]], by_domain: list[dict[str, Any]]) -> None:
    text = f"""# Auto Policy V3 Schema Adapter Onboarding Evidence

Generated at {datetime.now(timezone.utc).isoformat()} UTC.

This table evaluates whether the canonical schema layer behaves like event-level answer coding or reusable domain-family adapters.

## By Domain

{markdown_table(by_domain)}

## By Canonical Family

{markdown_table(rows)}

Interpretation:

- Web accessibility shows strong family reuse: 21 events are covered by 3 canonical families.
- Insurance is too small for a strong reuse claim, but it uses 3 families for 3 semantic patterns rather than event-id branches.
- Digital identity is the weakest transfer case: 6 events currently map to 6 distinct NIST change keys, so it should be described as a domain adapter with enumerated change-key vocabulary.
- This supports a cautious claim: schema adapters are reusable at the domain/family level, but new domains still require onboarding a canonical vocabulary.

Outputs:

- `{OUT_CSV.relative_to(ROOT)}`
- `{OUT_BY_DOMAIN.relative_to(ROOT)}`
- `{OUT_JSON.relative_to(ROOT)}`
"""
    OUT_MD.write_text(text, encoding="utf-8")


def main() -> int:
    rows, by_domain = build_rows()
    write_csv(OUT_CSV, rows)
    write_csv(OUT_BY_DOMAIN, by_domain)
    write_md(rows, by_domain)
    OUT_JSON.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "rows": rows,
                "by_domain": by_domain,
                "paper_safe_claim": "Schema adapters are reusable at domain/family level, but not domain-free.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("AUTO_POLICY_V3 schema adapter onboarding evidence")
    print(f"csv={OUT_CSV}")
    print(f"by_domain={OUT_BY_DOMAIN}")
    print(f"md={OUT_MD}")
    for row in by_domain:
        print(
            f"[{row['domain']}] events={row['event_count']} families={row['canonical_family_count']} "
            f"event_per_family={row['event_per_family_ratio']:.2f} assessment={row['schema_adapter_assessment']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
