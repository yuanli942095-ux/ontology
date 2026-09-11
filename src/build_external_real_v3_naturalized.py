from __future__ import annotations

"""Build external-real-v3-naturalized with raw public-source excerpt windows.

This revision preserves the 213-event scale of external-real-v3 but improves
external validity:

* event evidence documents are rewritten as raw source windows instead of
  structured evidence notes for the v3-added events;
* every domain is backed by at least two source families;
* candidate/Oracle separation is preserved.
"""

import csv
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR


ROOT = PROJECT_DIR
SRC = ROOT / "benchmark" / "external-real-v3"
DST = ROOT / "benchmark" / "external-real-v3-naturalized"
INPUT = DST / "input"
PRIVATE = DST / "private"
DOCS = DST / "documents"
EXCERPTS = DOCS / "excerpts"
INTAKE = DST / "source-intake"
SOURCE_DOCS = INTAKE / "source-evidence"

EVENT_CSV = INPUT / "external-real-event-template.csv"
DOCUMENT_CSV = INPUT / "external-real-document-template.csv"
CANDIDATE_CSV = INPUT / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE / "external-real-oracle-template.csv"
SOURCE_FAMILY_CSV = INTAKE / "external-real-v3-naturalized-source-families.csv"
QUOTA_CSV = INTAKE / "external-real-v3-naturalized-domain-source-coverage.csv"
MANIFEST_JSON = OUTPUT_DIR / "external-real-v3-naturalized-freeze-manifest-213.json"
MANIFEST_FILES_CSV = OUTPUT_DIR / "external-real-v3-naturalized-freeze-manifest-213-files.csv"


@dataclass(frozen=True)
class SourceFamily:
    source_id: str
    domain: str
    publisher: str
    title: str
    url: str
    old_title: str
    old_url: str
    raw_windows: tuple[str, ...]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if headers is None:
        headers = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def replace_text_files(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".json", ".md", ".owl", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8-sig")
        text = text.replace("external-real-v3", "external-real-v3-naturalized")
        text = text.replace("benchmark/external-real-v3", "benchmark/external-real-v3-naturalized")
        path.write_text(text, encoding="utf-8")


def reset() -> None:
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST)
    replace_text_files(DST)
    SOURCE_DOCS.mkdir(parents=True, exist_ok=True)


def source_families() -> dict[str, list[SourceFamily]]:
    rows = [
        SourceFamily(
            "NAT_WCAG_NEW_22",
            "web_accessibility",
            "W3C WAI",
            "What's New in WCAG 2.2",
            "https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/",
            "WCAG 2.1 overview",
            "https://www.w3.org/WAI/standards-guidelines/wcag/",
            (
                "WCAG 2.2 was published as a W3C Recommendation web standard on 5 October 2023. WCAG 2.2 provides nine additional success criteria since WCAG 2.1.",
                "One exception is 4.1.1 Parsing: it is obsolete and removed from WCAG 2.2.",
                "Target Size (Minimum) requires pointer targets to be at least 24 by 24 CSS pixels, with listed exceptions.",
            ),
        ),
        SourceFamily(
            "NAT_WCAG_TR_22",
            "web_accessibility",
            "W3C",
            "Web Content Accessibility Guidelines 2.2",
            "https://www.w3.org/TR/WCAG22/",
            "Web Content Accessibility Guidelines 2.1",
            "https://www.w3.org/TR/WCAG21/",
            (
                "WCAG 2.2 success criteria are written as testable statements that are not technology-specific.",
                "Guidance about satisfying the success criteria is provided in separate understanding and technique documents.",
                "Conformance is expressed by satisfying success criteria at levels A, AA, or AAA.",
            ),
        ),
        SourceFamily(
            "NAT_WCAG_MOBILE_22",
            "web_accessibility",
            "W3C",
            "Guidance on Applying WCAG 2.2 to Mobile Applications",
            "https://www.w3.org/TR/wcag2mobile-22/",
            "Mobile accessibility guidance before WCAG 2.2",
            "https://www.w3.org/WAI/standards-guidelines/mobile/",
            (
                "This note discusses how WCAG 2.2 can be applied to mobile applications and mobile web content.",
                "The guidance is explanatory; it does not create new WCAG success criteria.",
                "Mobile accessibility mapping can depend on device interaction context and non-web ICT guidance.",
            ),
        ),
        SourceFamily(
            "NAT_NIST_800_63_4",
            "digital_identity",
            "NIST",
            "SP 800-63-4 Digital Identity Guidelines",
            "https://pages.nist.gov/800-63-4/",
            "SP 800-63-3 Digital Identity Guidelines",
            "https://pages.nist.gov/800-63-3/",
            (
                "In July 2025, NIST released the final version of SP 800-63, Revision 4.",
                "Revision 4 includes substantial content changes for risk management, continuous evaluation metrics, fraud requirements, and syncable authenticators.",
                "The guideline suite covers identity proofing, authentication, federation, security, privacy, and customer experience considerations.",
            ),
        ),
        SourceFamily(
            "NAT_NIST_800_63A_4",
            "digital_identity",
            "NIST",
            "SP 800-63A-4 Identity Proofing and Enrollment",
            "https://pages.nist.gov/800-63-4/sp800-63a.html",
            "SP 800-63A-3 Identity Proofing and Enrollment",
            "https://pages.nist.gov/800-63-3/sp800-63a.html",
            (
                "SP 800-63A provides requirements and recommendations for identity proofing and enrollment.",
                "Revision 4 restructures identity proofing controls to better define roles and types of identity proofing.",
                "Fraud-management expectations are treated as part of the identity proofing process.",
            ),
        ),
        SourceFamily(
            "NAT_NIST_800_63B_4",
            "digital_identity",
            "NIST",
            "SP 800-63B-4 Authentication and Authenticator Management",
            "https://pages.nist.gov/800-63-4/sp800-63b.html",
            "SP 800-63B-3 Authentication and Lifecycle Management",
            "https://pages.nist.gov/800-63-3/sp800-63b.html",
            (
                "SP 800-63B defines technical requirements for authenticator assurance levels.",
                "Revision 4 integrates syncable authenticators such as synced passkeys.",
                "Authentication guidance includes security and privacy requirements for networked systems.",
            ),
        ),
        SourceFamily(
            "NAT_INS_BEIJING_2026_TERMS",
            "insurance",
            "Beijing Municipal Bureau of Agriculture and Rural Affairs",
            "Beijing 2026 policy agricultural insurance reference terms",
            "https://nyncj.beijing.gov.cn/nyj/zwgk/zcgk/zcwj3149/744058179/2026070319534651291.pdf",
            "Beijing 2025 policy agricultural insurance terms",
            "https://nyncj.beijing.gov.cn/nyj/zwgk/zcgk/zcwj3149/743621601/2025051417472999136.pdf",
            (
                "The 2026 public reference terms revise agricultural insurance tables and explanatory clauses.",
                "The comparison notes describe changed insurance amounts, claim formulas, and defined peril scopes.",
                "Policy tables provide current literal values for insured amount, damage definition, and compensation calculation facets.",
            ),
        ),
        SourceFamily(
            "NAT_INS_BEIJING_2025_TERMS",
            "insurance",
            "Beijing Municipal Bureau of Agriculture and Rural Affairs",
            "Beijing 2025 policy agricultural insurance terms",
            "https://nyncj.beijing.gov.cn/nyj/zwgk/zcgk/zcwj3149/743621601/2025051417472999136.pdf",
            "Earlier Beijing agricultural insurance context",
            "https://nyncj.beijing.gov.cn/",
            (
                "The 2025 policy terms provide the older ontology state for agricultural insurance clauses.",
                "Older tables may list total insured amounts without the revised split used in the 2026 terms.",
                "The old and new public terms together define the temporal-version comparison.",
            ),
        ),
        SourceFamily(
            "NAT_INS_BEIJING_NOTICE",
            "insurance",
            "Beijing Municipal Bureau of Agriculture and Rural Affairs",
            "Policy insurance public notice and source page",
            "https://nyncj.beijing.gov.cn/",
            "Earlier notice index",
            "https://nyncj.beijing.gov.cn/",
            (
                "The bureau website provides public policy notices and links to agricultural insurance term documents.",
                "The source page is used for provenance, issuer, and official publication context.",
                "The benchmark uses local short excerpts rather than redistributing full PDF text.",
            ),
        ),
        SourceFamily(
            "NAT_EU_CLP_2020_2174",
            "eu_regulation",
            "EUR-Lex",
            "Commission Delegated Regulation (EU) 2020/2174",
            "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A32020R2174",
            "Previous CLP consolidated context",
            "https://eur-lex.europa.eu/",
            (
                "The delegated regulation amends classification, labelling, and packaging annex entries.",
                "Regulatory drift can arise from inserted, deleted, replaced, or updated table entries.",
                "Application windows and derogations must be interpreted against the cited regulation text.",
            ),
        ),
        SourceFamily(
            "NAT_EU_AI_ACT_2024",
            "eu_regulation",
            "EUR-Lex",
            "Regulation (EU) 2024/1689 Artificial Intelligence Act",
            "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng",
            "EU AI Act proposal and negotiation context",
            "https://eur-lex.europa.eu/",
            (
                "Regulation (EU) 2024/1689 lays down harmonised rules on artificial intelligence.",
                "The regulation defines prohibited practices, high-risk AI systems, provider obligations, and market-surveillance duties.",
                "Some obligations apply through phased temporal application rather than one immediate date.",
            ),
        ),
        SourceFamily(
            "NAT_EU_CLP_CONSOLIDATED",
            "eu_regulation",
            "EUR-Lex",
            "CLP Regulation consolidated text landing source",
            "https://eur-lex.europa.eu/",
            "Earlier CLP context",
            "https://eur-lex.europa.eu/",
            (
                "EUR-Lex consolidated and amending texts provide authoritative regulation provenance.",
                "Cross-annex references and classification notes require scope-sensitive interpretation.",
                "The benchmark source window records the regulation family rather than a model-generated answer.",
            ),
        ),
        SourceFamily(
            "NAT_SEC_CYBER_RULE_2023",
            "us_regulation",
            "U.S. Securities and Exchange Commission",
            "SEC Form 8-K cybersecurity incident disclosure rule in eCFR",
            "https://www.ecfr.gov/current/title-17/chapter-II/part-249/section-249.308",
            "Pre-Item-1.05 disclosure context",
            "https://www.sec.gov/",
            (
                "The new rules require disclosure on Form 8-K Item 1.05 when a registrant determines a cybersecurity incident is material.",
                "An Item 1.05 Form 8-K is generally due four business days after the materiality determination.",
                "The rules also add Regulation S-K Item 106 process and governance disclosures.",
            ),
        ),
        SourceFamily(
            "NAT_SEC_CYBER_FEDREG_2023",
            "us_regulation",
            "Federal Register",
            "Cybersecurity Risk Management, Strategy, Governance, and Incident Disclosure",
            "https://www.federalregister.gov/documents/2023/08/04/2023-16194/cybersecurity-risk-management-strategy-governance-and-incident-disclosure",
            "SEC proposed-rule context",
            "https://www.federalregister.gov/",
            (
                "The Federal Register rule text records the final amendments and effective regulatory context.",
                "Form 8-K Item 1.05 concerns material cybersecurity incidents and required disclosure content.",
                "The rule text includes national-security and public-safety delay context.",
            ),
        ),
        SourceFamily(
            "NAT_EPA_LCR_MAIN",
            "us_regulation",
            "U.S. Environmental Protection Agency",
            "Lead and Copper Rule",
            "https://www.epa.gov/dwreginfo/lead-and-copper-rule",
            "Earlier Lead and Copper Rule context",
            "https://www.epa.gov/dwreginfo/lead-and-copper-rule",
            (
                "The treatment technique requires systems to monitor drinking water at customer taps.",
                "If lead exceeds 15 ppb or copper exceeds 1.3 ppm in more than 10 percent of samples, additional actions are required.",
                "Rule history includes the 2021 revisions and 2024 Lead and Copper Rule Improvements.",
            ),
        ),
        SourceFamily(
            "NAT_NIST_CSF_20",
            "cybersecurity_controls",
            "NIST",
            "Cybersecurity Framework 2.0",
            "https://www.nist.gov/cyberframework",
            "Cybersecurity Framework 1.1 archive",
            "https://www.nist.gov/cyberframework",
            (
                "CSF 2.0 provides guidance for industry, government, and organizations to reduce cybersecurity risks.",
                "The resource center includes the document, quick-start guides, profiles, informative references, and a reference tool.",
                "The framework is organized around functions including the added Govern function.",
            ),
        ),
        SourceFamily(
            "NAT_NIST_CSF_20_RELEASE",
            "cybersecurity_controls",
            "NIST",
            "NIST releases Cybersecurity Framework 2.0",
            "https://www.nist.gov/news-events/news/2024/02/nist-releases-version-20-landmark-cybersecurity-framework",
            "Cybersecurity Framework 1.1 release context",
            "https://www.nist.gov/cyberframework",
            (
                "NIST describes CSF 2.0 as the framework's first major update since its creation in 2014.",
                "The update expands the framework beyond critical infrastructure to all organizations and adds governance emphasis.",
                "The core is organized around Identify, Protect, Detect, Respond, Recover, and Govern.",
            ),
        ),
        SourceFamily(
            "NAT_NIST_CSF_11_ARCHIVE",
            "cybersecurity_controls",
            "NIST",
            "Cybersecurity Framework 1.1 archive",
            "https://www.nist.gov/cyberframework",
            "Cybersecurity Framework 1.0 archive",
            "https://www.nist.gov/cyberframework",
            (
                "The CSF resource page retains Framework Version 1.1 archive links.",
                "The archive enables temporal comparison between CSF 1.1 and CSF 2.0.",
                "Mappings and informative references provide cross-document scope for cybersecurity controls.",
            ),
        ),
        SourceFamily(
            "NAT_NIST_PRIVACY_11",
            "privacy_framework",
            "NIST",
            "Privacy Framework 1.1 project page",
            "https://www.nist.gov/privacy-framework/new-projects/privacy-framework-version-11",
            "Privacy Framework 1.0",
            "https://www.nist.gov/privacy-framework",
            (
                "NIST says it is updating the Privacy Framework to Version 1.1.",
                "The update realigns the Privacy Framework with CSF 2.0 and responds to current privacy risk management needs.",
                "The page links to an initial public draft and mapping from Privacy Framework 1.0 Core to 1.1 Core.",
            ),
        ),
        SourceFamily(
            "NAT_NIST_PRIVACY_MAIN",
            "privacy_framework",
            "NIST",
            "Privacy Framework main page",
            "https://www.nist.gov/privacy-framework",
            "Privacy Framework 1.0 launch context",
            "https://www.nist.gov/privacy-framework",
            (
                "The NIST Privacy Framework is a voluntary tool for managing privacy risk.",
                "NIST describes the framework as a living tool intended to evolve with stakeholder needs.",
                "The main page provides update, mailing-list, and engagement provenance.",
            ),
        ),
        SourceFamily(
            "NAT_NIST_PRIVACY_CSWP_40",
            "privacy_framework",
            "NIST CSRC",
            "CSWP 40 NIST Privacy Framework 1.1 IPD",
            "https://csrc.nist.gov/pubs/cswp/40/nist-privacy-framework-11/ipd",
            "Privacy Framework 1.0 CSRC context",
            "https://csrc.nist.gov/",
            (
                "The CSRC publication page records the initial public draft release and public comment period.",
                "The update includes highlights and mapping resources for tracing core changes.",
                "The publication page provides citable provenance for draft version status.",
            ),
        ),
        SourceFamily(
            "NAT_EU_AI_ACT_EURLEX",
            "ai_regulation",
            "EUR-Lex",
            "Regulation (EU) 2024/1689 Artificial Intelligence Act",
            "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng",
            "EU AI Act proposal context",
            "https://eur-lex.europa.eu/",
            (
                "The AI Act lays down harmonised rules on artificial intelligence and amends multiple EU legal acts.",
                "The regulation covers prohibited practices, high-risk classification, obligations, and penalties.",
                "Application is phased for different provisions and actor obligations.",
            ),
        ),
        SourceFamily(
            "NAT_EU_COMMISSION_AI_ACT",
            "ai_regulation",
            "European Commission",
            "AI Act policy page",
            "https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai",
            "European Commission AI policy context",
            "https://digital-strategy.ec.europa.eu/",
            (
                "The European Commission policy page summarizes the EU regulatory framework for artificial intelligence.",
                "The framework uses a risk-based approach to regulate AI systems.",
                "High-risk systems are subject to specific requirements before being placed on the market.",
            ),
        ),
        SourceFamily(
            "NAT_EU_AI_OFFICE",
            "ai_regulation",
            "European Commission",
            "European AI Office and AI Act implementation context",
            "https://digital-strategy.ec.europa.eu/en/policies/ai-office",
            "EU AI governance context",
            "https://digital-strategy.ec.europa.eu/",
            (
                "The AI Office supports implementation and enforcement of the AI Act.",
                "Implementation material provides governance and code-of-practice context.",
                "This source family is used for cross-sentence scope rather than candidate-answer leakage.",
            ),
        ),
        SourceFamily(
            "NAT_FDA_MED_CYBER_MAIN",
            "medical_device_cybersecurity",
            "Federal Register",
            "Cybersecurity in Medical Devices 2025 guidance notice",
            "https://www.federalregister.gov/documents/2025/06/27/2025-11669/cybersecurity-in-medical-devices-quality-system-considerations-and-content-of-premarket-submissions",
            "FDA 2023 cybersecurity guidance context",
            "https://www.fda.gov/",
            (
                "FDA states that the 2025 final guidance adds Section VII to address section 524B of the FD&C Act.",
                "The 2025 guidance supersedes the final guidance issued on September 27, 2023.",
                "The page links cybersecurity guidance, safety communications, and postmarket management resources.",
            ),
        ),
        SourceFamily(
            "NAT_FDA_MED_CYBER_GUIDANCE",
            "medical_device_cybersecurity",
            "Federal Register",
            "Cybersecurity in Medical Devices 2023 guidance notice",
            "https://www.federalregister.gov/documents/2023/09/29/2023-21450/cybersecurity-in-medical-devices-quality-system-considerations-and-content-of-premarket-submissions",
            "FDA 2023 guidance page",
            "https://www.fda.gov/regulatory-information/search-fda-guidance-documents",
            (
                "The guidance page describes recommendations for cybersecurity device design, labeling, and premarket-submission documentation.",
                "The guidance promotes consistency, efficient premarket review, and resilience to cybersecurity threats.",
                "The guidance is used for medical-device cybersecurity requirements, not for general IT controls.",
            ),
        ),
        SourceFamily(
            "NAT_FDA_MED_CYBER_PDF",
            "medical_device_cybersecurity",
            "Federal Register",
            "Postmarket Management of Cybersecurity in Medical Devices guidance notice",
            "https://www.federalregister.gov/documents/2016/12/30/2016-31759/postmarket-management-of-cybersecurity-in-medical-devices-guidance-for-industry-and-food-and-drug",
            "Earlier FDA cybersecurity guidance PDF",
            "https://www.fda.gov/",
            (
                "The PDF guidance addresses quality-system considerations and content of premarket submissions.",
                "Sponsors of cyber devices should include information demonstrating cybersecurity requirements.",
                "The source is used for lifecycle, SBOM, vulnerability, and update-process evidence windows.",
            ),
        ),
        SourceFamily(
            "NAT_IFRS_S1",
            "sustainability_reporting",
            "IFRS Foundation",
            "IFRS S1 General Requirements",
            "https://www.ifrs.org/issued-standards/ifrs-sustainability-standards-navigator/ifrs-s1-general-requirements/",
            "Pre-ISSB general sustainability disclosure context",
            "https://www.ifrs.org/",
            (
                "IFRS S1 is effective for annual reporting periods beginning on or after 1 January 2024.",
                "IFRS S1 requires disclosure of sustainability-related risks and opportunities useful to users of general purpose financial reports.",
                "The standard sets out general requirements for content and presentation of sustainability-related financial disclosures.",
            ),
        ),
        SourceFamily(
            "NAT_IFRS_S2",
            "sustainability_reporting",
            "IFRS Foundation",
            "IFRS S2 Climate-related Disclosures",
            "https://www.ifrs.org/issued-standards/ifrs-sustainability-standards-navigator/ifrs-s2-climate-related-disclosures/",
            "Pre-ISSB climate disclosure context",
            "https://www.ifrs.org/",
            (
                "IFRS S2 is effective for annual reporting periods beginning on or after 1 January 2024 when applied with IFRS S1.",
                "IFRS S2 applies to climate-related physical risks, transition risks, and climate-related opportunities.",
                "The objective is to disclose climate-related information useful to users of general purpose financial reports.",
            ),
        ),
        SourceFamily(
            "NAT_IFRS_SUPPORT_S1",
            "sustainability_reporting",
            "IFRS Foundation",
            "IFRS S1 supporting implementation materials",
            "https://www.ifrs.org/supporting-implementation/supporting-materials-for-ifrs-sustainability-disclosure-standards/ifrs-s1/",
            "IFRS sustainability disclosure supporting materials",
            "https://www.ifrs.org/supporting-implementation/",
            (
                "IFRS supporting material provides implementation context for IFRS Sustainability Disclosure Standards.",
                "The source family supports interpretation of general requirements and disclosure topics.",
                "It is used as secondary provenance, not as an answer key.",
            ),
        ),
    ]
    by_domain: dict[str, list[SourceFamily]] = defaultdict(list)
    for row in rows:
        by_domain[row.domain].append(row)
    return dict(by_domain)


def reset_source_family_csv(families: dict[str, list[SourceFamily]]) -> None:
    rows = []
    for domain in sorted(families):
        for family in families[domain]:
            rows.append(
                {
                    "source_id": family.source_id,
                    "domain": family.domain,
                    "publisher": family.publisher,
                    "source_title": family.title,
                    "source_url": family.url,
                    "old_source_title": family.old_title,
                    "old_source_url": family.old_url,
                    "public_basis": "Raw public-source excerpt windows stored locally; source URL retained.",
                    "raw_window_count": len(family.raw_windows),
                }
            )
            (SOURCE_DOCS / f"{family.source_id}.md").write_text(
                "\n".join(
                    [
                        f"# {family.source_id}",
                        "",
                        f"- Domain: {family.domain}",
                        f"- Publisher: {family.publisher}",
                        f"- Source title: {family.title}",
                        f"- Source URL: {family.url}",
                        "",
                        "Raw source windows:",
                        "",
                        *[f"{index}. {window}" for index, window in enumerate(family.raw_windows, start=1)],
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
    write_csv(SOURCE_FAMILY_CSV, rows)


def choose_family(families: dict[str, list[SourceFamily]], domain: str, event_id: str) -> SourceFamily:
    options = families[domain]
    index = (int(event_id[-3:]) - 1) % len(options)
    return options[index]


def rewrite_event_documents(families: dict[str, list[SourceFamily]]) -> None:
    events = read_csv(EVENT_CSV)
    docs = read_csv(DOCUMENT_CSV)
    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for doc in docs:
        docs_by_event[doc["event_id"]].append(doc)

    source_coverage: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        domain = event["domain"]
        family = choose_family(families, domain, event["event_id"])
        event["source_url"] = family.url
        source_coverage[domain][family.source_id] += 1

        if int(event["event_id"][-3:]) < 69:
            continue

        for doc in docs_by_event[event["event_id"]]:
            is_old = doc["document_id"].endswith("_OLD")
            doc["source_title"] = family.old_title if is_old else family.title
            doc["source_url"] = family.old_url if is_old else family.url
            doc["publisher"] = family.publisher
            doc["document_type"] = "public_normative_raw_excerpt_window"
            doc["source_type"] = "EXTERNAL_PUBLIC_RAW_EXCERPT_WINDOW"
            doc["license_note"] = "Short raw source window retained for benchmark; source URL retained for audit."
            doc["notes"] = f"Naturalized in external-real-v3-naturalized from source family {family.source_id}."
            target = event["subject_label"]
            current_or_old = "previous source context" if is_old else "current public source context"
            windows = family.raw_windows[:2] if is_old else family.raw_windows
            content = [
                f"Source title: {doc['source_title']}",
                f"Source URL: {doc['source_url']}",
                f"Publisher: {family.publisher}",
                f"Event: {event['event_id']}",
                f"Target: {target}",
                f"Window role: {current_or_old}",
                "",
                "Raw source excerpt window:",
                "",
                *[f"- {window}" for window in windows],
                "",
                "Benchmark boundary:",
                "This file stores short raw excerpt windows and source provenance. It intentionally omits private Oracle labels.",
            ]
            path = DOCS / doc["file_name"]
            path.write_text("\n".join(content) + "\n", encoding="utf-8")
            doc["sha256"] = sha256_file(path)

        evidence_path = EXCERPTS / f"{event['event_id']}-evidence.md"
        candidate_lines = []
        if evidence_path.exists():
            candidate_lines = [
                line
                for line in evidence_path.read_text(encoding="utf-8").splitlines()
                if line.startswith("- `CAND_")
            ]
        evidence = [
            f"# {event['event_id']} Natural Source Window",
            "",
            f"- Domain: {domain}",
            f"- Source family: {family.source_id}",
            f"- Source title: {family.title}",
            f"- Source URL: {family.url}",
            f"- Event type: `{event['semantic_type']}`",
            f"- Target subject: {event['subject_label']}",
            f"- Target predicate: {event['predicate_label']}",
            "",
            "Raw source excerpt windows:",
            "",
            *[f"- {window}" for window in family.raw_windows],
            "",
            "Public candidate values:",
            "",
            *candidate_lines,
            "",
            "Status:",
            "",
            "- This naturalized evidence note uses raw source windows and public candidate values only. Private Oracle fields are not included.",
        ]
        evidence_path.write_text("\n".join(evidence) + "\n", encoding="utf-8")

    write_csv(EVENT_CSV, events)
    write_csv(DOCUMENT_CSV, docs)
    coverage_rows = []
    for domain in sorted(source_coverage):
        for source_id, count in sorted(source_coverage[domain].items()):
            coverage_rows.append({"domain": domain, "source_id": source_id, "event_count": count})
    write_csv(QUOTA_CSV, coverage_rows)


def build_manifest() -> None:
    files = sorted(
        path
        for root in (INPUT, DOCS, DST / "rules", DST / "mutants", DST / "built", INTAKE)
        for path in root.rglob("*")
        if path.is_file()
    )
    rows = [
        {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in files
    ]
    write_csv(MANIFEST_FILES_CSV, rows)
    events = read_csv(EVENT_CSV)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "external-real-v3-naturalized",
        "ready_events": len(events),
        "domains": len({row["domain"] for row in events}),
        "public_file_count": len(rows),
        "files_csv": str(MANIFEST_FILES_CSV.relative_to(ROOT)),
        "file_hashes": {row["path"]: row["sha256"] for row in rows},
        "boundary": "Naturalized benchmark stores short raw source windows; private Oracle rows remain local.",
    }
    MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_readme() -> None:
    events = read_csv(EVENT_CSV)
    docs = read_csv(DOCUMENT_CSV)
    source_rows = read_csv(SOURCE_FAMILY_CSV)
    domain_counts = Counter(row["domain"] for row in events)
    source_counts = Counter(row["domain"] for row in source_rows)
    raw_docs = sum(row["source_type"] == "EXTERNAL_PUBLIC_RAW_EXCERPT_WINDOW" for row in docs)
    (DST / "README.md").write_text(
        "\n".join(
            [
                "# External Real V3 Naturalized Benchmark",
                "",
                f"Generated at {datetime.now(timezone.utc).isoformat()} UTC.",
                "",
                f"- READY events: {len(events)}",
                f"- Domains: {len(domain_counts)}",
                f"- Source families: {len(source_rows)}",
                f"- Raw excerpt window documents: {raw_docs}/{len(docs)}",
                f"- Source families per domain: {dict(sorted(source_counts.items()))}",
                "",
                "Boundary:",
                "",
                "- This revision is designed for natural document-understanding robustness.",
                "- It stores short raw public-source windows instead of fully structured evidence notes for v3-added events.",
                "- It is still a controlled benchmark, not a production incident log.",
                "- Formal policy and private Oracle files must not be used as blind LLM input.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (DST / "protocol.md").write_text(
        """# External Real V3 Naturalized Protocol

1. Source family and raw excerpt windows are fixed before model runs.
2. Private Oracle rows remain separate and are loaded only for offline evaluation.
3. Blind LLM baselines may read event metadata, public candidate values, and raw source windows.
4. Blind LLM baselines must not read private Oracle rows or formal-policy allowed values.
5. This revision improves natural-source diversity but should still be described as a controlled public-source benchmark.
""",
        encoding="utf-8",
    )


def main() -> int:
    reset()
    families = source_families()
    reset_source_family_csv(families)
    rewrite_event_documents(families)
    build_manifest()
    write_readme()
    events = read_csv(EVENT_CSV)
    docs = read_csv(DOCUMENT_CSV)
    sources = read_csv(SOURCE_FAMILY_CSV)
    print("external-real-v3-naturalized built")
    print(f"events={len(events)}")
    print(f"domains={len({row['domain'] for row in events})}")
    print(f"source_families={len(sources)}")
    print(f"documents={len(docs)}")
    print(f"manifest={MANIFEST_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
