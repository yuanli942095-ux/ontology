from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVENTS_CSV = ROOT / "benchmark" / "external-real-v1" / "input" / "external-real-event-template.csv"
SCHEMA_TRANSFER_SUMMARY = ROOT / "output" / "auto-policy-v3-schema-transfer-r5-seed20260820-semantic-only-summary.csv"
SCHEMA_TRANSFER_BY_DOMAIN = ROOT / "output" / "auto-policy-v3-schema-transfer-r5-seed20260820-semantic-only-by-domain.csv"
OUT_CSV = ROOT / "output" / "auto-policy-v3-schema-adapter-inventory.csv"
OUT_MD = ROOT / "output" / "auto-policy-v3-schema-adapter-inventory.md"
OUT_JSON = ROOT / "output" / "auto-policy-v3-schema-adapter-inventory.json"


DOMAIN_ADAPTERS = {
    "insurance": {
        "normalizer": "normalize_insurance",
        "canonical_families": [
            "insurance_amount_split",
            "insurance_lodging_levels",
            "insurance_formula",
        ],
        "family_basis": "domain terms plus value/formula cues in evidence and target labels",
        "event_id_branching": "No",
        "adapter_scope": "domain adapter over Beijing agricultural-insurance policy terms",
    },
    "web_accessibility": {
        "normalizer": "normalize_wcag",
        "canonical_families": [
            "wcag22_added",
            "wcag21_cross_scope",
            "wcag21_input_rule",
        ],
        "family_basis": "WCAG criterion id, level, semantic type, and input-modality target cues",
        "event_id_branching": "No",
        "adapter_scope": "domain adapter over W3C WCAG 2.1/2.2 change summaries",
    },
    "digital_identity": {
        "normalizer": "normalize_nist",
        "canonical_families": [
            "nist_revision_change:risk_management_text",
            "nist_revision_change:continuous_evaluation_metrics",
            "nist_revision_change:fraud_requirements",
            "nist_revision_change:identity_proofing_controls",
            "nist_revision_change:controls_added",
            "nist_revision_change:syncable_authenticators",
        ],
        "family_basis": "NIST SP 800-63 revision keywords and predefined canonical change keys",
        "event_id_branching": "No",
        "adapter_scope": "domain adapter over NIST SP 800-63 revision evidence",
    },
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.2f}%"


def event_counts(events: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    domains: dict[str, dict[str, object]] = {}
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in events:
        grouped[row["domain"]].append(row)

    for domain, rows in grouped.items():
        semantic_counts = Counter(row["semantic_type"] for row in rows)
        source_urls = sorted({row.get("source_url", "") for row in rows if row.get("source_url")})
        document_ids = sorted(
            {
                doc_id
                for row in rows
                for doc_id in row.get("document_ids", "").split("|")
                if doc_id
            }
        )
        domains[domain] = {
            "event_count": len(rows),
            "semantic_types": dict(sorted(semantic_counts.items())),
            "source_url_count": len(source_urls),
            "document_reference_count": len(document_ids),
            "event_ids": [row["event_id"] for row in rows],
        }
    return domains


def schema_transfer() -> tuple[dict[str, dict[str, str]], dict[tuple[str, str], dict[str, str]]]:
    summary = {row["variant"]: row for row in read_csv(SCHEMA_TRANSFER_SUMMARY)}
    by_domain = {(row["variant"], row["domain"]): row for row in read_csv(SCHEMA_TRANSFER_BY_DOMAIN)}
    return summary, by_domain


def build_inventory() -> list[dict[str, object]]:
    events = read_csv(EVENTS_CSV)
    counts = event_counts(events)
    transfer_summary, transfer_by_domain = schema_transfer()
    full_schema = transfer_summary.get("FULL_SCHEMA", {})

    rows: list[dict[str, object]] = []
    for domain in sorted(counts):
        adapter = DOMAIN_ADAPTERS[domain]
        held_out_variant = f"LEAVE_OUT_{domain.upper()}"
        full_domain = transfer_by_domain.get(("FULL_SCHEMA", domain), {})
        held_out_domain = transfer_by_domain.get((held_out_variant, domain), {})
        held_out_summary = transfer_summary.get(held_out_variant, {})
        domain_counts = counts[domain]

        rows.append(
            {
                "domain": domain,
                "event_count": domain_counts["event_count"],
                "semantic_type_counts": json.dumps(domain_counts["semantic_types"], ensure_ascii=False, sort_keys=True),
                "source_url_count": domain_counts["source_url_count"],
                "document_reference_count": domain_counts["document_reference_count"],
                "normalizer": adapter["normalizer"],
                "canonical_families": "; ".join(adapter["canonical_families"]),
                "family_basis": adapter["family_basis"],
                "event_id_branching": adapter["event_id_branching"],
                "adapter_scope": adapter["adapter_scope"],
                "full_schema_canonical_ok": f"{full_domain.get('canonical_ok', '')}/{full_domain.get('attempts', '')}",
                "full_schema_domain_rate": pct(float(full_domain["canonical_ok_rate"])) if full_domain else "",
                "held_out_variant": held_out_variant,
                "held_out_domain_canonical_ok": f"{held_out_domain.get('canonical_ok', '')}/{held_out_domain.get('attempts', '')}",
                "held_out_domain_canonical_rate": pct(float(held_out_domain["canonical_ok_rate"])) if held_out_domain else "",
                "held_out_overall_semantic_accuracy": pct(float(held_out_summary["semantic_accuracy"]))
                if held_out_summary
                else "",
                "full_schema_overall_semantic_accuracy": pct(float(full_schema["semantic_accuracy"]))
                if full_schema
                else "",
                "event_ids": "; ".join(domain_counts["event_ids"]),
            }
        )
    return rows


def write_csv(rows: list[dict[str, object]]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def md_table(rows: list[dict[str, object]]) -> str:
    columns = [
        "domain",
        "event_count",
        "semantic_type_counts",
        "normalizer",
        "canonical_families",
        "event_id_branching",
        "full_schema_domain_rate",
        "held_out_domain_canonical_rate",
        "held_out_overall_semantic_accuracy",
    ]
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(row[col]).replace("|", "/") for col in columns) + " |")
    return "\n".join([header, separator, *body])


def write_md(rows: list[dict[str, object]]) -> None:
    text = f"""# Auto Policy V3 Schema Adapter Inventory

Generated at {datetime.now(timezone.utc).isoformat()} UTC.

This inventory separates the domain-independent executor from the domain-specific canonical schema adapters used by Auto Policy V3. It is intended to prevent overclaiming: V3 does not learn a universal symbolic rule language from scratch.

{md_table(rows)}

## Interpretation

- The adapter layer is domain-level, not event-id-level: the normalizers do not branch on `event_id`.
- Leave-one-domain-out disables the held-out domain's canonical normalization completely, with 0 canonical matches on the held-out domain attempts.
- Full-schema performance therefore depends on predefined domain canonical vocabularies and deterministic normalization.
- The current evidence supports the claim "auditable domain adapter plus candidate-blind policy generation", not "domain-free automatic rule learning".

Outputs:

- `{OUT_CSV.relative_to(ROOT)}`
- `{OUT_JSON.relative_to(ROOT)}`
"""
    OUT_MD.write_text(text, encoding="utf-8")


def write_json(rows: list[dict[str, object]]) -> None:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "events_csv": str(EVENTS_CSV.relative_to(ROOT)),
            "schema_transfer_summary": str(SCHEMA_TRANSFER_SUMMARY.relative_to(ROOT)),
            "schema_transfer_by_domain": str(SCHEMA_TRANSFER_BY_DOMAIN.relative_to(ROOT)),
        },
        "rows": rows,
        "paper_safe_claim": "Auto Policy V3 uses auditable domain-level canonical schema adapters; it is not domain-free automatic rule learning.",
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    rows = build_inventory()
    write_csv(rows)
    write_md(rows)
    write_json(rows)
    print("AUTO_POLICY_V3 schema adapter inventory")
    print(f"csv={OUT_CSV}")
    print(f"md={OUT_MD}")
    print(f"json={OUT_JSON}")
    for row in rows:
        print(
            f"[{row['domain']}] events={row['event_count']} normalizer={row['normalizer']} "
            f"full={row['full_schema_domain_rate']} held_out_canonical={row['held_out_domain_canonical_rate']} "
            f"held_out_overall={row['held_out_overall_semantic_accuracy']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
