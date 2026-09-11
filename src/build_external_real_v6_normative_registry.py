from __future__ import annotations

"""Build the reviewed source-pair registry for full-document retrieval.

The registry is source-family level and was selected from official publisher
pages/APIs. It contains no event candidates, formal policies, or Oracle data.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(r"G:\LearnAI\ontology-evolution")
OUTPUT_DIR = ROOT / "data" / "external-real-v6-normative-cache"
REGISTRY = OUTPUT_DIR / "normative-source-registry.csv"


def eu_download(identifier: str) -> str:
    return (
        "https://op.europa.eu/o/opportal-service/download-handler?"
        f"identifier={identifier}&format=PDF&language=en&productionSystem=cellar&part="
    )


ROWS: list[dict[str, str]] = [
    {
        "source_id": "NAT_EU_AI_ACT_EURLEX",
        "domain": "ai_regulation",
        "publisher": "Publications Office of the European Union",
        "source_title": "Regulation (EU) 2024/1689 official English PDF",
        "source_url": eu_download("dc8116a1-3fe6-11ef-865a-01aa75ed71a1"),
        "old_source_title": "COM(2021) 206 AI Act proposal official English PDF",
        "old_source_url": eu_download("e0649735-a372-11eb-9585-01aa75ed71a1"),
        "selection_basis": "CELEX-resolved Publications Office Cellar manifestations",
    },
    {
        "source_id": "NAT_EU_CLP_2020_2174",
        "domain": "eu_regulation",
        "publisher": "Publications Office of the European Union",
        "source_title": "Commission Delegated Regulation (EU) 2020/2174 official English PDF",
        "source_url": eu_download("dec9f5e7-43f8-11eb-b59f-01aa75ed71a1"),
        "old_source_title": "Regulation (EC) No 1272/2008 official English PDF",
        "old_source_url": eu_download("6bf54b59-7673-461b-b8e1-f24c545cbd3c"),
        "selection_basis": "CELEX-resolved Publications Office Cellar manifestations",
    },
    {
        "source_id": "NAT_NIST_CSF_20",
        "domain": "cybersecurity_controls",
        "publisher": "NIST",
        "source_title": "NIST Cybersecurity Framework 2.0",
        "source_url": "https://doi.org/10.6028/NIST.CSWP.29",
        "old_source_title": "NIST Cybersecurity Framework 1.1",
        "old_source_url": "https://doi.org/10.6028/NIST.CSWP.04162018",
        "selection_basis": "official NIST DOI links from the CSF page",
    },
    {
        "source_id": "NAT_NIST_800_63_4",
        "domain": "digital_identity",
        "publisher": "NIST",
        "source_title": "SP 800-63-4 Digital Identity Guidelines",
        "source_url": "https://pages.nist.gov/800-63-4/",
        "old_source_title": "SP 800-63-3 Digital Identity Guidelines",
        "old_source_url": "https://pages.nist.gov/800-63-3/",
        "selection_basis": "official versioned NIST publication pages",
    },
    {
        "source_id": "NAT_NIST_800_63A_4",
        "domain": "digital_identity",
        "publisher": "NIST",
        "source_title": "SP 800-63A-4 Identity Proofing and Enrollment",
        "source_url": "https://pages.nist.gov/800-63-4/sp800-63a.html",
        "old_source_title": "SP 800-63A-3 Identity Proofing and Enrollment",
        "old_source_url": "https://pages.nist.gov/800-63-3/sp800-63a.html",
        "selection_basis": "official versioned NIST publication pages",
    },
    {
        "source_id": "NAT_NIST_800_63B_4",
        "domain": "digital_identity",
        "publisher": "NIST",
        "source_title": "SP 800-63B-4 Authentication and Authenticator Management",
        "source_url": "https://pages.nist.gov/800-63-4/sp800-63b.html",
        "old_source_title": "SP 800-63B-3 Authentication and Lifecycle Management",
        "old_source_url": "https://pages.nist.gov/800-63-3/sp800-63b.html",
        "selection_basis": "official versioned NIST publication pages",
    },
    {
        "source_id": "NAT_INS_BEIJING_2026_TERMS",
        "domain": "insurance",
        "publisher": "Beijing Municipal Bureau of Agriculture and Rural Affairs",
        "source_title": "Beijing 2026 policy agricultural insurance reference terms",
        "source_url": "https://nyncj.beijing.gov.cn/nyj/zwgk/zcgk/zcwj3149/744058179/2026070319534651291.pdf",
        "old_source_title": "Beijing 2025 policy agricultural insurance terms",
        "old_source_url": "https://nyncj.beijing.gov.cn/nyj/zwgk/zcgk/zcwj3149/743621601/2025051417472999136.pdf",
        "selection_basis": "official publisher PDF pair",
    },
    {
        "source_id": "NAT_FDA_MED_CYBER_MAIN",
        "domain": "medical_device_cybersecurity",
        "publisher": "Federal Register",
        "source_title": "FDA 2025 premarket cybersecurity guidance notice raw text",
        "source_url": "https://www.federalregister.gov/documents/full_text/html/2025/06/27/2025-11669.html",
        "old_source_title": "FDA 2023 premarket cybersecurity guidance notice raw text",
        "old_source_url": "https://www.federalregister.gov/documents/full_text/html/2023/09/29/2023-21450.html",
        "selection_basis": "FederalRegister.gov document API body_html_url fields",
    },
    {
        "source_id": "NAT_FDA_MED_CYBER_PDF",
        "domain": "medical_device_cybersecurity",
        "publisher": "Federal Register public inspection",
        "source_title": "FDA postmarket cybersecurity final-guidance notice 2016",
        "source_url": "https://public-inspection.federalregister.gov/2016-31406.pdf",
        "old_source_title": "FDA postmarket cybersecurity draft-guidance notice 2016",
        "old_source_url": "https://public-inspection.federalregister.gov/2016-01172.pdf",
        "selection_basis": "official Federal Register public-inspection PDFs for FDA-2015-D-5105",
    },
    {
        "source_id": "NAT_NIST_PRIVACY_11",
        "domain": "privacy_framework",
        "publisher": "NIST",
        "source_title": "NIST Privacy Framework 1.1 Initial Public Draft",
        "source_url": "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.40.ipd.pdf",
        "old_source_title": "NIST Privacy Framework 1.0",
        "old_source_url": "https://doi.org/10.6028/NIST.CSWP.01162020",
        "selection_basis": "official NIST publication download and DOI",
    },
    {
        "source_id": "NAT_IFRS_SUPPORT_S1",
        "domain": "sustainability_reporting",
        "publisher": "IFRS Foundation",
        "source_title": "Using the SASB Standards to meet IFRS S1 requirements",
        "source_url": "https://www.ifrs.org/content/dam/ifrs/supporting-implementation/ifrs-s1/using-sasb-standards-for-ifrs-s1.pdf",
        "old_source_title": "IFRS S1 project summary",
        "old_source_url": "https://www.ifrs.org/content/dam/ifrs/project/general-sustainability-related-disclosures/project-summary.pdf",
        "selection_basis": "official IFRS Foundation public implementation and project PDFs",
    },
    {
        "source_id": "NAT_IFRS_S1",
        "domain": "sustainability_reporting",
        "publisher": "IFRS Foundation",
        "source_title": "IFRS S1 effects analysis",
        "source_url": "https://www.ifrs.org/content/dam/ifrs/project/general-sustainability-related-disclosures/effects-analysis.pdf",
        "old_source_title": "IFRS S1 and S2 project summary",
        "old_source_url": "https://www.ifrs.org/content/dam/ifrs/project/general-sustainability-related-disclosures/project-summary.pdf",
        "selection_basis": "official IFRS Foundation public project PDFs",
    },
    {
        "source_id": "NAT_IFRS_S2",
        "domain": "sustainability_reporting",
        "publisher": "IFRS Foundation",
        "source_title": "Applying IFRS S1 when reporting only climate-related disclosures under IFRS S2",
        "source_url": "https://www.ifrs.org/content/dam/ifrs/supporting-implementation/issb-standards/applying-ifrs-s1-reporting-only-climate-related-disclosures-accordance-ifrs-s2.pdf",
        "old_source_title": "IFRS S1 and S2 project summary",
        "old_source_url": "https://www.ifrs.org/content/dam/ifrs/project/general-sustainability-related-disclosures/project-summary.pdf",
        "selection_basis": "official IFRS Foundation public implementation and project PDFs",
    },
    {
        "source_id": "NAT_SEC_CYBER_FEDREG_2023",
        "domain": "us_regulation",
        "publisher": "Federal Register",
        "source_title": "SEC 2023 final cybersecurity disclosure rule raw text",
        "source_url": "https://www.federalregister.gov/documents/full_text/html/2023/08/04/2023-16194.html",
        "old_source_title": "SEC 2022 proposed cybersecurity disclosure rule raw text",
        "old_source_url": "https://www.federalregister.gov/documents/full_text/html/2022/03/23/2022-05480.html",
        "selection_basis": "FederalRegister.gov document API body_html_url fields",
    },
    {
        "source_id": "NAT_SEC_CYBER_RULE_2023",
        "domain": "us_regulation",
        "publisher": "eCFR",
        "source_title": "17 CFR Part 249 current XML",
        "source_url": "https://www.ecfr.gov/api/versioner/v1/full/2026-08-24/title-17.xml?part=249",
        "old_source_title": "17 CFR Part 249 before the 2023 final rule XML",
        "old_source_url": "https://www.ecfr.gov/api/versioner/v1/full/2023-07-01/title-17.xml?part=249",
        "selection_basis": "official eCFR versioner API point-in-time XML",
    },
    {
        "source_id": "NAT_EPA_LCR_MAIN",
        "domain": "us_regulation",
        "publisher": "U.S. Government Publishing Office",
        "source_title": "Lead and Copper Rule final rule 2007",
        "source_url": "https://www.gpo.gov/fdsys/pkg/FR-2007-10-10/pdf/E7-19432.pdf",
        "old_source_title": "Lead and Copper Rule final rule 2000",
        "old_source_url": "https://www.gpo.gov/fdsys/pkg/FR-2000-01-12/pdf/00-3.pdf",
        "selection_basis": "official final-rule PDF links from the EPA rule page",
    },
    {
        "source_id": "NAT_WCAG_TR_22",
        "domain": "web_accessibility",
        "publisher": "W3C",
        "source_title": "Web Content Accessibility Guidelines 2.2",
        "source_url": "https://www.w3.org/TR/WCAG22/",
        "old_source_title": "Web Content Accessibility Guidelines 2.1",
        "old_source_url": "https://www.w3.org/TR/WCAG21/",
        "selection_basis": "official versioned W3C Recommendations",
    },
    {
        "source_id": "NAT_WCAG_MOBILE_22",
        "domain": "web_accessibility",
        "publisher": "W3C",
        "source_title": "Guidance on Applying WCAG 2.2 to Mobile Applications",
        "source_url": "https://www.w3.org/TR/wcag2mobile-22/",
        "old_source_title": "W3C mobile accessibility guidance",
        "old_source_url": "https://www.w3.org/WAI/standards-guidelines/mobile/",
        "selection_basis": "official W3C guidance pages",
    },
]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(REGISTRY, ROWS)
    summary = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_pairs": len(ROWS),
        "domains": sorted({row["domain"] for row in ROWS}),
        "domain_count": len({row["domain"] for row in ROWS}),
        "registry": str(REGISTRY),
        "candidate_used": False,
        "formal_policy_used": False,
        "private_oracle_used": False,
        "boundary": "Source-family registry only; no benchmark revision is modified or frozen.",
    }
    (OUTPUT_DIR / "normative-source-registry-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
