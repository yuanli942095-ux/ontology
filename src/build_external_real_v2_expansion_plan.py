from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = ROOT / "benchmark" / "external-real-v2"
INTAKE_DIR = BENCHMARK_DIR / "source-intake"
OUT = ROOT / "output"

INTAKE_CSV = INTAKE_DIR / "external-real-v2-source-intake-plan.csv"
QUOTA_CSV = INTAKE_DIR / "external-real-v2-quota-plan.csv"
README = BENCHMARK_DIR / "README.md"
PROTOCOL = BENCHMARK_DIR / "protocol.md"
OUT_MD = OUT / "external-real-v2-expansion-plan.md"
OUT_JSON = OUT / "external-real-v2-expansion-plan.json"


SOURCES = [
    {
        "source_id": "V2_SRC_WCAG_001",
        "domain": "web_accessibility",
        "publisher": "W3C Web Accessibility Initiative",
        "source_title": "What's New in WCAG 2.2",
        "source_url": "https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/",
        "paired_old_url": "https://www.w3.org/TR/2018/REC-WCAG21-20180605/",
        "paired_new_url": "https://www.w3.org/TR/WCAG22/",
        "candidate_event_quota": 8,
        "semantic_types": "TEMPORAL_VERSION|CROSS_SENTENCE_SCOPE",
        "screening_focus": "added success criteria, obsolete parsing exception, level/scope requirements",
        "source_status": "official_public_html_identified",
    },
    {
        "source_id": "V2_SRC_WCAG_002",
        "domain": "web_accessibility",
        "publisher": "W3C Web Accessibility Initiative",
        "source_title": "Guidance on Applying WCAG 2.2 to Mobile Applications",
        "source_url": "https://www.w3.org/TR/wcag2mobile-22/",
        "paired_old_url": "https://www.w3.org/TR/mobile-accessibility-mapping/",
        "paired_new_url": "https://www.w3.org/TR/wcag2mobile-22/",
        "candidate_event_quota": 4,
        "semantic_types": "CROSS_SENTENCE_SCOPE|GENERAL_RULE_EXCEPTION",
        "screening_focus": "scope transfer from WCAG criteria to mobile application guidance",
        "source_status": "official_public_html_identified",
    },
    {
        "source_id": "V2_SRC_NIST_001",
        "domain": "digital_identity",
        "publisher": "NIST",
        "source_title": "SP 800-63 Revision 3 to Revision 4",
        "source_url": "https://pages.nist.gov/800-63-4/",
        "paired_old_url": "https://pages.nist.gov/800-63-3/",
        "paired_new_url": "https://pages.nist.gov/800-63-4/",
        "candidate_event_quota": 8,
        "semantic_types": "TEMPORAL_VERSION|GENERAL_RULE_EXCEPTION",
        "screening_focus": "revision supersession, assurance-level text, risk management and fraud-control changes",
        "source_status": "official_public_html_identified",
    },
    {
        "source_id": "V2_SRC_NIST_002",
        "domain": "digital_identity",
        "publisher": "NIST",
        "source_title": "SP 800-63A and 800-63B Revision 3 to Revision 4",
        "source_url": "https://pages.nist.gov/800-63-4/sp800-63a.html",
        "paired_old_url": "https://pages.nist.gov/800-63-3/sp800-63a.html",
        "paired_new_url": "https://pages.nist.gov/800-63-4/sp800-63b.html",
        "candidate_event_quota": 6,
        "semantic_types": "CROSS_SENTENCE_SCOPE|GENERAL_RULE_EXCEPTION",
        "screening_focus": "identity proofing, authentication controls, syncable authenticator treatment",
        "source_status": "official_public_html_identified",
    },
    {
        "source_id": "V2_SRC_EURLEX_001",
        "domain": "eu_regulation",
        "publisher": "EUR-Lex",
        "source_title": "EU regulation amendment examples with effective dates and annex changes",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A32020R2174",
        "paired_old_url": "https://eur-lex.europa.eu/",
        "paired_new_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A32020R2174",
        "candidate_event_quota": 6,
        "semantic_types": "TEMPORAL_VERSION|GENERAL_RULE_EXCEPTION",
        "screening_focus": "annex amendment, effective date, exception/scope language",
        "source_status": "official_public_html_identified",
    },
    {
        "source_id": "V2_SRC_ECFR_001",
        "domain": "us_regulation",
        "publisher": "eCFR",
        "source_title": "eCFR current regulatory text with effective-date requirements",
        "source_url": "https://www.ecfr.gov/",
        "paired_old_url": "https://www.federalregister.gov/",
        "paired_new_url": "https://www.ecfr.gov/",
        "candidate_event_quota": 6,
        "semantic_types": "TEMPORAL_VERSION|CROSS_SENTENCE_SCOPE",
        "screening_focus": "effective date, current regulatory text, incorporated amendment scope",
        "source_status": "official_public_site_identified",
    },
]


QUOTAS = [
    {"dimension": "minimum_total_events", "target": 60, "current_external_real_v1": 30, "additional_needed": 30},
    {"dimension": "minimum_domains", "target": 5, "current_external_real_v1": 3, "additional_needed": 2},
    {"dimension": "TEMPORAL_VERSION", "target": 20, "current_external_real_v1": 10, "additional_needed": 10},
    {"dimension": "GENERAL_RULE_EXCEPTION", "target": 20, "current_external_real_v1": 10, "additional_needed": 10},
    {"dimension": "CROSS_SENTENCE_SCOPE", "target": 20, "current_external_real_v1": 10, "additional_needed": 10},
]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: list[dict[str, Any]], fields: list[str]) -> str:
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row[field]).replace("|", "/") for field in fields) + " |")
    return "\n".join(lines)


def write_docs() -> None:
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    README.write_text(
        f"""# External Real V2 Expansion Plan

Generated at {datetime.now(timezone.utc).isoformat()} UTC.

Status: intake plan only. This is not yet a completed benchmark.

Purpose:

- Expand external validation from 30 events to at least 60 events.
- Add at least two more public-source domains beyond external-real-v1.
- Freeze all accepted events before final reruns.
""",
        encoding="utf-8",
    )
    PROTOCOL.write_text(
        """# External Real V2 Protocol

1. Use official public sources only.
2. Record old/new URLs, retrieval date, source hash, and excerpt boundaries.
3. Create candidate repairs before private Oracle adjudication.
4. Keep private Oracle rows out of candidate-blind prompts and public evidence.
5. Freeze accepted events with a manifest before final reruns.
6. Label external-real-v2 as an expanded public-source diagnostic benchmark unless independently annotated by multiple reviewers.
""",
        encoding="utf-8",
    )
    md = f"""# External Real V2 Expansion Plan

Generated at {datetime.now(timezone.utc).isoformat()} UTC.

This plan addresses the residual sample-size limitation. It is a source-intake and quota plan, not a completed dataset.

## Quotas

{markdown_table(QUOTAS, list(QUOTAS[0].keys()))}

## Candidate Public Sources

{markdown_table(SOURCES, ["source_id", "domain", "publisher", "source_title", "candidate_event_quota", "semantic_types", "source_url"])}

## Acceptance Rules

- Each accepted event must have source evidence, candidate repairs, mutant OWL, candidate OWL, and private Oracle adjudication.
- Events must be accepted before model reruns.
- No event may be added because a specific method failed.
- New domains require explicit schema-adapter onboarding records.

Outputs:

- `{INTAKE_CSV.relative_to(ROOT)}`
- `{QUOTA_CSV.relative_to(ROOT)}`
- `{README.relative_to(ROOT)}`
- `{PROTOCOL.relative_to(ROOT)}`
"""
    OUT_MD.write_text(md, encoding="utf-8")


def main() -> int:
    write_csv(INTAKE_CSV, SOURCES)
    write_csv(QUOTA_CSV, QUOTAS)
    write_docs()
    OUT_JSON.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "intake_plan_only",
                "sources": SOURCES,
                "quotas": QUOTAS,
                "paper_safe_claim": "This is a concrete expansion protocol, not completed evidence.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("EXTERNAL_REAL_V2 expansion plan")
    print(f"intake={INTAKE_CSV}")
    print(f"quota={QUOTA_CSV}")
    print(f"md={OUT_MD}")
    print(f"planned_additional_quota={sum(int(row['candidate_event_quota']) for row in SOURCES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
