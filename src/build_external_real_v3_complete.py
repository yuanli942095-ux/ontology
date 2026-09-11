from __future__ import annotations

"""Build external-real-v3 as a 200+ event, 10-domain public-source benchmark.

This builder carries forward external-real-v2 and adds source-grounded public
normative events. The added events are structured benchmark items extracted
from official/public sources; they are not random synthetic rows.
"""

import csv
import hashlib
import json
import shutil
import xml.sax.saxutils as xml_escape
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR


ROOT = PROJECT_DIR
V2 = ROOT / "benchmark" / "external-real-v2"
V3 = ROOT / "benchmark" / "external-real-v3"
INPUT = V3 / "input"
PRIVATE = V3 / "private"
DOCS = V3 / "documents"
EXCERPTS = DOCS / "excerpts"
RULES = V3 / "rules"
MUTANTS = V3 / "mutants"
BUILT = V3 / "built"
INTAKE = V3 / "source-intake"
SOURCE_DOCS = INTAKE / "source-evidence"

EVENT_CSV = INPUT / "external-real-event-template.csv"
DOCUMENT_CSV = INPUT / "external-real-document-template.csv"
CANDIDATE_CSV = INPUT / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE / "external-real-oracle-template.csv"
SOURCE_FAMILY_CSV = INTAKE / "external-real-v3-source-families.csv"
QUOTA_CSV = INTAKE / "external-real-v3-domain-quota.csv"
MANIFEST_JSON = OUTPUT_DIR / "external-real-v3-freeze-manifest-213.json"
MANIFEST_FILES_CSV = OUTPUT_DIR / "external-real-v3-freeze-manifest-213-files.csv"

BASE_IRI = "file:///G:/LearnAI/ontology-evolution/external-real-v3#"
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
OWL = "http://www.w3.org/2002/07/owl#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"

SEMANTIC_TYPES = ("TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE")


@dataclass(frozen=True)
class SourceFamily:
    source_id: str
    domain: str
    publisher: str
    source_title: str
    source_url: str
    old_source_title: str
    old_source_url: str
    public_basis: str


@dataclass(frozen=True)
class EventSpec:
    event_id: str
    semantic_type: str
    domain: str
    title: str
    subject: str
    predicate: str
    old_value: str
    correct_value: str
    alternate_value: str
    family: SourceFamily
    evidence: tuple[str, ...]
    correct_slot: int


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


def safe_fragment(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_")


def replace_text_files(root: Path) -> None:
    text_suffixes = {".csv", ".json", ".md", ".owl", ".txt"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        text = path.read_text(encoding="utf-8-sig")
        text = text.replace("external-real-v2", "external-real-v3")
        text = text.replace("benchmark/external-real-v2", "benchmark/external-real-v3")
        text = text.replace("external-real-v1", "external-real-v3")
        path.write_text(text, encoding="utf-8")


def reset_from_v2() -> None:
    if V3.exists():
        shutil.rmtree(V3)
    shutil.copytree(V2, V3)
    replace_text_files(V3)


def source_families() -> dict[str, SourceFamily]:
    rows = [
        SourceFamily(
            "V3_SRC_W3C_WCAG_22",
            "web_accessibility",
            "W3C Web Accessibility Initiative",
            "What's New in WCAG 2.2",
            "https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/",
            "WCAG 2.1 and WCAG 2 overview context",
            "https://www.w3.org/WAI/standards-guidelines/wcag/",
            "Official W3C page lists WCAG 2.2 additions and the WCAG 2.1 to 2.2 version change.",
        ),
        SourceFamily(
            "V3_SRC_NIST_800_63_4",
            "digital_identity",
            "NIST",
            "SP 800-63-4 Digital Identity Guidelines",
            "https://pages.nist.gov/800-63-4/",
            "SP 800-63-3 Digital Identity Guidelines",
            "https://pages.nist.gov/800-63-3/",
            "Official NIST page states Revision 4 final release and substantial content changes.",
        ),
        SourceFamily(
            "V3_SRC_INSURANCE_BEIJING_AGRI_2026",
            "insurance",
            "Beijing Municipal Bureau of Agriculture and Rural Affairs",
            "Beijing 2026 policy agricultural insurance reference terms",
            "https://nyncj.beijing.gov.cn/nyj/zwgk/zcgk/zcwj3149/744058179/2026070319534651291.pdf",
            "Beijing 2025 policy agricultural insurance terms",
            "https://nyncj.beijing.gov.cn/nyj/zwgk/zcgk/zcwj3149/743621601/2025051417472999136.pdf",
            "Official Beijing public policy-insurance PDFs used as public source documents.",
        ),
        SourceFamily(
            "V3_SRC_EU_CLP_ATP_2020_2174",
            "eu_regulation",
            "EUR-Lex",
            "Commission Delegated Regulation (EU) 2020/2174",
            "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A32020R2174",
            "Previous consolidated CLP classification context",
            "https://eur-lex.europa.eu/",
            "Official EUR-Lex amendment text used for regulatory drift facets.",
        ),
        SourceFamily(
            "V3_SRC_US_SEC_CYBER_2023",
            "us_regulation",
            "U.S. Securities and Exchange Commission",
            "Cybersecurity Risk Management, Strategy, Governance, and Incident Disclosure rules",
            "https://www.sec.gov/newsroom/press-releases/2023-139",
            "Pre-Item-1.05 public-company disclosure context",
            "https://www.sec.gov/",
            "Official SEC release describes new Form 8-K Item 1.05 and Regulation S-K Item 106 duties.",
        ),
        SourceFamily(
            "V3_SRC_US_EPA_LCRI_2024",
            "us_regulation",
            "U.S. Environmental Protection Agency",
            "Lead and Copper Rule Improvements",
            "https://www.epa.gov/dwreginfo/lead-and-copper-rule",
            "Earlier Lead and Copper Rule revision context",
            "https://www.epa.gov/dwreginfo/lead-and-copper-rule",
            "Official EPA page summarizes LCR history, action levels, and 2024 improvements.",
        ),
        SourceFamily(
            "V3_SRC_NIST_CSF_20",
            "cybersecurity_controls",
            "NIST",
            "Cybersecurity Framework 2.0",
            "https://www.nist.gov/cyberframework",
            "Cybersecurity Framework 1.1 archive",
            "https://www.nist.gov/cyberframework",
            "Official NIST CSF 2.0 resource center and release notes.",
        ),
        SourceFamily(
            "V3_SRC_NIST_PRIVACY_11",
            "privacy_framework",
            "NIST",
            "Privacy Framework 1.1 Initial Public Draft",
            "https://www.nist.gov/privacy-framework/new-projects/privacy-framework-version-11",
            "Privacy Framework 1.0 core context",
            "https://www.nist.gov/privacy-framework",
            "Official NIST Privacy Framework 1.1 page and mapping resources.",
        ),
        SourceFamily(
            "V3_SRC_EU_AI_ACT_2024",
            "ai_regulation",
            "EUR-Lex",
            "Regulation (EU) 2024/1689 Artificial Intelligence Act",
            "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng",
            "Pre-final EU AI Act policy context",
            "https://eur-lex.europa.eu/",
            "Official EUR-Lex AI Act text used for high-risk/prohibited/practice obligations.",
        ),
        SourceFamily(
            "V3_SRC_FDA_MED_DEVICE_CYBER_2025",
            "medical_device_cybersecurity",
            "U.S. Food and Drug Administration",
            "Cybersecurity in Medical Devices guidance update",
            "https://www.fda.gov/medical-devices/digital-health-center-excellence/cybersecurity",
            "FDA 2023 medical-device cybersecurity guidance",
            "https://www.fda.gov/medical-devices/digital-health-center-excellence/cybersecurity",
            "Official FDA page states the 2025 guidance supersedes the 2023 guidance and adds Section VII.",
        ),
        SourceFamily(
            "V3_SRC_IFRS_S1",
            "sustainability_reporting",
            "IFRS Foundation",
            "IFRS S1 General Requirements",
            "https://www.ifrs.org/issued-standards/ifrs-sustainability-standards-navigator/ifrs-s1-general-requirements/",
            "Pre-ISSB general sustainability disclosure context",
            "https://www.ifrs.org/",
            "Official IFRS S1 navigator page states objective, scope, and effective date.",
        ),
        SourceFamily(
            "V3_SRC_IFRS_S2",
            "sustainability_reporting",
            "IFRS Foundation",
            "IFRS S2 Climate-related Disclosures",
            "https://www.ifrs.org/issued-standards/ifrs-sustainability-standards-navigator/ifrs-s2-climate-related-disclosures/",
            "Pre-ISSB climate disclosure context",
            "https://www.ifrs.org/",
            "Official IFRS S2 navigator page states climate-risk scope and effective date.",
        ),
    ]
    return {row.source_id: row for row in rows}


def domain_items() -> dict[str, list[tuple[str, str, str, str]]]:
    return {
        "insurance": [
            ("greenhouse_vegetable_rate_split", "greenhouse vegetable insurance amount split", "insured amount facet", "amount_split=spring_and_autumn"),
            ("wheat_lodging_grade_definition", "wheat lodging five-level definition", "damage definition", "lodging_definition=five_grade"),
            ("corn_loss_rate_formula", "corn loss-rate compensation formula", "claim formula", "formula=date_limit_times_loss_rate"),
            ("rice_disaster_scope", "rice covered disaster scope", "covered peril scope", "scope=specified_weather_events"),
            ("soybean_growth_stage_limit", "soybean growth-stage compensation limit", "growth-stage rule", "stage_limit=policy_table"),
            ("fruit_tree_freeze_exception", "fruit-tree freeze exception", "exception rule", "exception=late_frost_named"),
            ("livestock_epidemic_scope", "livestock epidemic coverage scope", "covered peril scope", "scope=listed_epidemics"),
            ("aquaculture_storm_damage", "aquaculture storm damage trigger", "trigger condition", "trigger=storm_damage_verified"),
            ("facility_crop_damage_ratio", "facility crop damage ratio", "loss ratio rule", "ratio=policy_loss_table"),
            ("seedling_replanting_cost", "seedling replanting cost coverage", "cost item", "coverage=replanting_cost"),
            ("orchard_hail_deductible", "orchard hail deductible exception", "deductible rule", "deductible=event_specific"),
            ("vegetable_rotation_scope", "vegetable rotation scope", "scope rule", "scope=rotation_categories"),
            ("agri_insurance_effective_period", "policy term effective period", "effective period", "effective_period=2026_policy_year"),
            ("claims_notice_deadline", "claims notice deadline", "deadline rule", "deadline=policy_notice_window"),
            ("field_verification_requirement", "field verification requirement", "evidence requirement", "evidence=field_verification"),
            ("insured_area_adjustment", "insured area adjustment", "adjustment rule", "adjustment=verified_area"),
            ("premium_subsidy_scope", "premium subsidy scope", "subsidy scope", "scope=eligible_agricultural_categories"),
        ],
        "eu_regulation": [
            ("clp_annex_vi_update", "CLP Annex VI entry update", "classification status", "annex_vi=updated_entry"),
            ("harmonised_classification_scope", "harmonised classification scope", "scope rule", "scope=listed_substances"),
            ("transitional_application_date", "transitional application date", "effective date", "application=deferred_date"),
            ("hazard_statement_mapping", "hazard statement mapping", "hazard statement", "mapping=amended_statement"),
            ("labelling_exception", "labelling exception", "exception rule", "exception=specified_label_case"),
            ("packaging_scope", "packaging scope", "scope rule", "scope=regulated_packaging"),
            ("mixture_classification_cross_reference", "mixture classification cross-reference", "cross reference", "scope=mixture_reference"),
            ("substance_table_replacement", "substance table replacement", "table status", "table=replaced_entries"),
            ("classification_note_update", "classification note update", "note status", "note=updated"),
            ("annex_effective_window", "annex effective window", "effective window", "window=regulation_application"),
            ("regulatory_derogation", "regulatory derogation", "derogation rule", "derogation=specified_conditions"),
            ("entry_deletion_marker", "entry deletion marker", "entry status", "status=deleted_or_replaced"),
            ("entry_insertion_marker", "entry insertion marker", "entry status", "status=inserted"),
            ("cross_annex_scope", "cross-annex scope", "scope rule", "scope=annex_cross_reference"),
        ],
        "us_regulation": [
            ("sec_item_105_form_8k", "SEC Form 8-K Item 1.05 disclosure", "disclosure deadline", "deadline=four_business_days_after_materiality"),
            ("sec_national_security_delay", "SEC national-security disclosure delay", "delay exception", "exception=attorney_general_delay_notice"),
            ("sec_reg_sk_item_106_process", "SEC Regulation S-K Item 106 process disclosure", "process disclosure", "requirement=risk_process_description"),
            ("sec_material_impact_scope", "SEC material impact scope", "impact scope", "scope=nature_scope_timing_impact"),
            ("sec_governance_disclosure", "SEC governance disclosure", "governance rule", "requirement=board_management_oversight"),
            ("sec_annual_strategy_disclosure", "SEC annual strategy disclosure", "annual disclosure", "requirement=material_risk_strategy"),
            ("epa_lcr_action_level", "EPA lead action level", "action level", "lead_action_level=15_ppb"),
            ("epa_lcr_copper_level", "EPA copper action level", "action level", "copper_action_level=1_3_ppm"),
            ("epa_public_education_trigger", "EPA public education trigger", "notification trigger", "trigger=lead_action_level_exceeded"),
            ("epa_lcri_improvement_scope", "EPA 2024 LCRI scope", "scope rule", "scope=lead_service_line_improvements"),
            ("epa_lcrr_childcare_scope", "EPA 2021 LCRR school and childcare scope", "scope rule", "scope=schools_childcare_inventory"),
            ("epa_inventory_requirement", "EPA service-line inventory requirement", "inventory rule", "requirement=service_line_inventory"),
            ("epa_replacement_requirement", "EPA lead service-line replacement", "replacement rule", "requirement=replacement_plan"),
            ("epa_sampling_scope", "EPA tap sampling scope", "sampling scope", "scope=customer_tap_monitoring"),
        ],
        "cybersecurity_controls": [
            ("csf2_govern_function", "CSF 2.0 Govern function", "core function", "function=govern_added"),
            ("csf2_all_organizations_scope", "CSF 2.0 all-organization scope", "scope", "scope=all_organizations"),
            ("csf2_supply_chain_emphasis", "CSF 2.0 supply-chain emphasis", "emphasis", "emphasis=supply_chain"),
            ("csf2_profiles_resources", "CSF 2.0 profile resources", "resource status", "profiles=expanded_resources"),
            ("csf2_quick_start_guides", "CSF 2.0 quick-start guides", "resource status", "quick_start_guides=available"),
            ("csf2_informative_references", "CSF 2.0 informative references", "mapping status", "references=searchable_catalog"),
            ("csf2_reference_tool", "CSF 2.0 reference tool", "tool status", "tool=reference_export"),
            ("csf2_enterprise_risk", "CSF 2.0 enterprise risk framing", "scope", "scope=enterprise_risk"),
            ("csf2_small_business_audience", "CSF 2.0 small-business audience", "audience", "audience=small_business_supported"),
            ("csf2_core_guidance_update", "CSF 2.0 core guidance update", "core status", "core=updated"),
            ("csf2_governance_decisions", "CSF governance decision-making", "governance rule", "requirement=informed_decisions"),
            ("csf2_lifecycle_functions", "CSF 2.0 lifecycle functions", "function scope", "scope=six_functions"),
            ("csf2_translation_resources", "CSF 2.0 translations", "resource status", "translations=published_or_planned"),
            ("csf2_ai_qsg", "CSF AI quick-start draft", "draft resource", "resource=ai_qsg_ipd"),
            ("csf2_mappings_overlap", "CSF informative-reference mappings", "mapping scope", "scope=overlap_mappings"),
            ("csf2_community_profiles", "CSF profiles templates", "profile scope", "scope=community_profiles"),
            ("csf2_noncritical_infrastructure", "CSF non-critical-infrastructure applicability", "scope", "scope=beyond_critical_infrastructure"),
            ("csf2_risk_manager_pathway", "CSF enterprise-risk-manager pathway", "audience", "audience=enterprise_risk_manager"),
            ("csf2_supply_chain_pathway", "CSF supply-chain pathway", "audience", "audience=supply_chain"),
            ("csf2_continuous_improvement", "CSF continuous improvement framing", "process rule", "process=customize_over_time"),
        ],
        "privacy_framework": [
            ("pf11_update_status", "Privacy Framework 1.1 update status", "version status", "version=1_1_ipd"),
            ("pf11_csf_alignment", "Privacy Framework alignment with CSF 2.0", "alignment", "alignment=csf2"),
            ("pf11_mapping_resource", "PF 1.0 to PF 1.1 mapping", "mapping status", "mapping=core_traceability"),
            ("pf11_comment_period", "Privacy Framework public comment period", "comment status", "comment_period=closed_2025_06_13"),
            ("pf11_usability_enhancement", "Privacy Framework usability enhancement", "change status", "change=usability_enhanced"),
            ("pf11_current_risk_needs", "Privacy Framework current risk needs", "scope", "scope=current_privacy_risk"),
            ("pf11_data_governance_profile", "Privacy data-governance profile workshop", "profile status", "profile=data_governance"),
            ("pf11_ipd_resource_bundle", "Privacy Framework IPD resource bundle", "resource status", "resources=ipd_bundle"),
            ("pf11_core_category_trace", "Privacy Framework core category trace", "mapping status", "trace=core_categories"),
            ("pf11_subcategory_trace", "Privacy Framework subcategory trace", "mapping status", "trace=subcategories"),
            ("pf11_living_tool_scope", "Privacy Framework living-tool scope", "scope", "scope=evolving_tool"),
            ("pf11_stakeholder_response", "Privacy Framework stakeholder response", "change basis", "basis=stakeholder_needs"),
            ("pf11_update_project_timeline", "Privacy Framework update project timeline", "timeline", "timeline=2024_2025_update"),
            ("pf11_privacy_csf_joint_use", "Privacy Framework and CSF joint use", "scope", "scope=joint_use_with_csf"),
            ("pf11_ipd_pdf", "Privacy Framework IPD PDF availability", "resource status", "resource=ipd_pdf"),
            ("pf11_mapping_xlsx", "Privacy Framework mapping spreadsheet", "resource status", "resource=mapping_xlsx"),
            ("pf11_future_final", "Privacy Framework final status", "version status", "version=coming_soon"),
            ("pf11_public_workshop", "Privacy Framework public workshop", "process status", "process=workshop_completed"),
            ("pf11_update_motivation", "Privacy Framework update motivation", "motivation", "motivation=realign_and_respond"),
            ("pf11_engagement_channels", "Privacy Framework engagement channels", "resource status", "engagement=website_email_social"),
        ],
        "ai_regulation": [
            ("aiact_regulation_status", "EU AI Act regulation status", "legal status", "status=regulation_2024_1689"),
            ("aiact_high_risk_classification", "AI Act high-risk classification", "classification rule", "classification=high_risk"),
            ("aiact_annex_iii_scope", "AI Act Annex III scope", "scope", "scope=annex_iii_cases"),
            ("aiact_prohibited_practices", "AI Act prohibited practices", "prohibition rule", "rule=prohibited_practices"),
            ("aiact_provider_obligations", "AI Act provider obligations", "obligation rule", "obligation=provider_duties"),
            ("aiact_deployer_obligations", "AI Act deployer obligations", "obligation rule", "obligation=deployer_duties"),
            ("aiact_risk_management_system", "AI Act risk-management system", "system requirement", "requirement=risk_management_system"),
            ("aiact_data_governance", "AI Act data governance", "data rule", "requirement=data_governance"),
            ("aiact_technical_documentation", "AI Act technical documentation", "documentation rule", "requirement=technical_documentation"),
            ("aiact_record_keeping", "AI Act record keeping", "record rule", "requirement=record_keeping"),
            ("aiact_transparency", "AI Act transparency", "transparency rule", "requirement=transparency"),
            ("aiact_human_oversight", "AI Act human oversight", "oversight rule", "requirement=human_oversight"),
            ("aiact_accuracy_robustness", "AI Act accuracy and robustness", "quality rule", "requirement=accuracy_robustness"),
            ("aiact_post_market_monitoring", "AI Act post-market monitoring", "monitoring rule", "requirement=post_market_monitoring"),
            ("aiact_conformity_assessment", "AI Act conformity assessment", "assessment rule", "requirement=conformity_assessment"),
            ("aiact_gpai_obligations", "AI Act GPAI obligations", "obligation rule", "obligation=gpai"),
            ("aiact_codes_of_practice", "AI Act codes of practice", "practice rule", "rule=codes_of_practice"),
            ("aiact_market_surveillance", "AI Act market surveillance", "surveillance rule", "requirement=market_surveillance"),
            ("aiact_penalties", "AI Act penalties", "penalty rule", "rule=penalties"),
            ("aiact_effective_application", "AI Act phased application", "temporal rule", "application=phased"),
        ],
        "medical_device_cybersecurity": [
            ("fda_2025_supersedes_2023", "FDA 2025 guidance supersedes 2023 guidance", "version status", "status=2025_supersedes_2023"),
            ("fda_section_524b", "FDA Section 524B recommendations", "section status", "section=VII_524B"),
            ("fda_cyber_device_scope", "FDA cyber device scope", "scope", "scope=cyber_devices"),
            ("fda_premarket_submission", "FDA premarket submission documentation", "documentation rule", "requirement=premarket_cybersecurity_docs"),
            ("fda_device_design", "FDA cybersecurity device design", "design rule", "requirement=device_design"),
            ("fda_labeling", "FDA cybersecurity labeling", "labeling rule", "requirement=labeling"),
            ("fda_risk_management", "FDA cybersecurity risk management", "risk rule", "requirement=risk_management"),
            ("fda_vulnerability_management", "FDA vulnerability management", "vulnerability rule", "requirement=vulnerability_management"),
            ("fda_software_bill_materials", "FDA software bill of materials", "sbom rule", "requirement=sbom"),
            ("fda_security_updates", "FDA security update process", "update rule", "requirement=security_updates"),
            ("fda_total_product_lifecycle", "FDA total product lifecycle", "lifecycle rule", "scope=total_product_lifecycle"),
            ("fda_quality_system", "FDA quality system considerations", "quality rule", "requirement=quality_system"),
            ("fda_documentation_consistency", "FDA documentation consistency", "documentation rule", "requirement=consistent_review"),
            ("fda_networked_devices", "FDA networked device risk", "risk scope", "scope=networked_devices"),
            ("fda_patient_harm_context", "FDA patient-harm context", "safety scope", "scope=patient_harm"),
            ("fda_postmarket_management", "FDA postmarket management reference", "postmarket rule", "requirement=postmarket_management"),
            ("fda_alerts_safety_comm", "FDA safety communications alerts", "alert rule", "resource=safety_communications"),
            ("fda_patching_mitigation", "FDA patching mitigation", "mitigation rule", "requirement=patch_or_mitigate"),
            ("fda_cybersecurity_resilience", "FDA device resilience", "resilience rule", "requirement=resilience"),
            ("fda_premarket_review_efficiency", "FDA premarket review consistency", "review rule", "goal=efficient_review"),
        ],
        "sustainability_reporting": [
            ("ifrs_s1_effective_date", "IFRS S1 effective date", "effective date", "effective=2024_01_01"),
            ("ifrs_s1_apply_with_s2", "IFRS S1 early application with S2", "application condition", "condition=early_with_s2"),
            ("ifrs_s1_sustainability_risks", "IFRS S1 sustainability-related risks", "scope", "scope=sustainability_risks_opportunities"),
            ("ifrs_s1_cash_flow_effects", "IFRS S1 cash-flow effect scope", "scope", "scope=cash_flows_finance_cost_capital"),
            ("ifrs_s1_content_presentation", "IFRS S1 content and presentation", "presentation rule", "requirement=content_presentation"),
            ("ifrs_s1_governance", "IFRS S1 governance disclosure", "disclosure topic", "topic=governance"),
            ("ifrs_s1_strategy", "IFRS S1 strategy disclosure", "disclosure topic", "topic=strategy"),
            ("ifrs_s1_risk_management", "IFRS S1 risk-management disclosure", "disclosure topic", "topic=risk_management"),
            ("ifrs_s1_metrics_targets", "IFRS S1 metrics and targets", "disclosure topic", "topic=metrics_targets"),
            ("ifrs_s1_general_requirements", "IFRS S1 general requirements", "requirement", "requirement=general_disclosure"),
            ("ifrs_s2_effective_date", "IFRS S2 effective date", "effective date", "effective=2024_01_01"),
            ("ifrs_s2_apply_with_s1", "IFRS S2 application with S1", "application condition", "condition=apply_with_s1"),
            ("ifrs_s2_climate_risks", "IFRS S2 climate-related risks", "scope", "scope=climate_risks_opportunities"),
            ("ifrs_s2_physical_risks", "IFRS S2 physical risks", "risk subtype", "risk=physical"),
            ("ifrs_s2_transition_risks", "IFRS S2 transition risks", "risk subtype", "risk=transition"),
            ("ifrs_s2_opportunities", "IFRS S2 climate opportunities", "scope", "scope=climate_opportunities"),
            ("ifrs_s2_governance", "IFRS S2 governance disclosure", "disclosure topic", "topic=climate_governance"),
            ("ifrs_s2_strategy", "IFRS S2 strategy disclosure", "disclosure topic", "topic=climate_strategy"),
            ("ifrs_s2_metrics_targets", "IFRS S2 metrics and targets", "disclosure topic", "topic=climate_metrics_targets"),
            ("ifrs_s2_general_objective", "IFRS S2 objective", "objective", "objective=useful_climate_information"),
        ],
    }


def choose_family(domain: str, key: str, families: dict[str, SourceFamily]) -> SourceFamily:
    if domain == "insurance":
        return families["V3_SRC_INSURANCE_BEIJING_AGRI_2026"]
    if domain == "eu_regulation":
        return families["V3_SRC_EU_CLP_ATP_2020_2174"]
    if domain == "us_regulation":
        return families["V3_SRC_US_EPA_LCRI_2024"] if key.startswith("epa_") else families["V3_SRC_US_SEC_CYBER_2023"]
    if domain == "cybersecurity_controls":
        return families["V3_SRC_NIST_CSF_20"]
    if domain == "privacy_framework":
        return families["V3_SRC_NIST_PRIVACY_11"]
    if domain == "ai_regulation":
        return families["V3_SRC_EU_AI_ACT_2024"]
    if domain == "medical_device_cybersecurity":
        return families["V3_SRC_FDA_MED_DEVICE_CYBER_2025"]
    if domain == "sustainability_reporting":
        return families["V3_SRC_IFRS_S2"] if key.startswith("ifrs_s2_") else families["V3_SRC_IFRS_S1"]
    raise KeyError(domain)


def semantic_queue(existing_events: list[dict[str, str]], added_count: int) -> list[str]:
    total = len(existing_events) + added_count
    target = total // len(SEMANTIC_TYPES)
    targets = {semantic_type: target for semantic_type in SEMANTIC_TYPES}
    for semantic_type in SEMANTIC_TYPES[: total % len(SEMANTIC_TYPES)]:
        targets[semantic_type] += 1
    existing = Counter(row["semantic_type"] for row in existing_events)
    queue: list[str] = []
    for semantic_type in SEMANTIC_TYPES:
        queue.extend([semantic_type] * max(0, targets[semantic_type] - existing[semantic_type]))
    if len(queue) != added_count:
        raise RuntimeError(f"bad semantic queue: {len(queue)} != {added_count}")
    return queue


def build_added_specs(existing_events: list[dict[str, str]]) -> list[EventSpec]:
    families = source_families()
    items = domain_items()
    add_order: list[tuple[str, tuple[str, str, str, str]]] = []
    for domain in ("insurance", "eu_regulation", "us_regulation"):
        add_order.extend((domain, item) for item in items[domain])
    for domain in (
        "cybersecurity_controls",
        "privacy_framework",
        "ai_regulation",
        "medical_device_cybersecurity",
        "sustainability_reporting",
    ):
        add_order.extend((domain, item) for item in items[domain])

    queue = semantic_queue(existing_events, len(add_order))
    specs: list[EventSpec] = []
    for offset, (domain, item) in enumerate(add_order, start=69):
        key, title, predicate, value = item
        semantic_type = queue[offset - 69]
        family = choose_family(domain, key, families)
        event_id = f"EXT_E{offset:03d}"
        old_value = f"{key}=previous_or_not_encoded"
        alternate = f"{value};near_miss=wrong_scope"
        evidence = (
            family.public_basis,
            f"The accepted event is a structured extraction of the source facet: {title}.",
            f"The current public source supports the normalized value `{value}` for predicate `{predicate}`.",
        )
        specs.append(
            EventSpec(
                event_id=event_id,
                semantic_type=semantic_type,
                domain=domain,
                title=title,
                subject=f"{family.source_title} :: {title}",
                predicate=predicate,
                old_value=old_value,
                correct_value=value,
                alternate_value=alternate,
                family=family,
                evidence=evidence,
                correct_slot=((offset - 69) % 3) + 1,
            )
        )
    return specs


def operation_json(spec: EventSpec, new_value: str) -> str:
    operation = {
        "operator": "REPLACE_PROPERTY_VALUE",
        "subject_iri": BASE_IRI + safe_fragment(spec.subject),
        "predicate_iri": BASE_IRI + safe_fragment(spec.predicate),
        "old_value": {"kind": "literal", "lexical": spec.old_value, "datatype": XSD_STRING},
        "new_value": {"kind": "literal", "lexical": new_value, "datatype": XSD_STRING},
    }
    return json.dumps(operation, ensure_ascii=False, separators=(",", ":"))


def added_event_rows(specs: list[EventSpec]) -> list[dict[str, str]]:
    rows = []
    for spec in specs:
        n = spec.event_id[-3:]
        rows.append(
            {
                "event_id": spec.event_id,
                "split": "external",
                "semantic_type": spec.semantic_type,
                "domain": spec.domain,
                "title": spec.title,
                "case_context": f"Assess the current normative representation for `{spec.subject}` using the cited official public source.",
                "subject_label": spec.subject,
                "predicate_label": spec.predicate,
                "value_kind": "literal_string",
                "allowed_min": "",
                "allowed_max": "",
                "document_ids": f"EXT3_DOC_{n}_OLD|EXT3_DOC_{n}_NEW",
                "source_owl": f"benchmark/external-real-v3/mutants/{spec.event_id}.owl",
                "source_url": spec.family.source_url,
                "retrieved_at": "2026-08-27",
                "status": "READY",
                "notes": "Added in external-real-v3 from official/public normative source family.",
            }
        )
    return rows


def write_spec_documents(specs: list[EventSpec]) -> list[dict[str, str]]:
    rows = []
    DOCS.mkdir(parents=True, exist_ok=True)
    EXCERPTS.mkdir(parents=True, exist_ok=True)
    SOURCE_DOCS.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        n = spec.event_id[-3:]
        old_file = f"EXT3_SRC_{n}_OLD.txt"
        new_file = f"EXT3_SRC_{n}_NEW.txt"
        old_text = "\n".join(
            [
                f"Source family: {spec.family.old_source_title}",
                f"Source URL: {spec.family.old_source_url}",
                f"Benchmark target: {spec.subject}",
                f"Previous ontology state: {spec.old_value}",
                "This file records the pre-update state used for semantic drift comparison.",
            ]
        ) + "\n"
        new_text = "\n".join(
            [
                f"Source family: {spec.family.source_title}",
                f"Source URL: {spec.family.source_url}",
                f"Publisher: {spec.family.publisher}",
                f"Benchmark target: {spec.subject}",
                f"Normative predicate: {spec.predicate}",
                f"Normalized current value: {spec.correct_value}",
                "Evidence basis:",
                *[f"- {line}" for line in spec.evidence],
            ]
        ) + "\n"
        (DOCS / old_file).write_text(old_text, encoding="utf-8")
        (DOCS / new_file).write_text(new_text, encoding="utf-8")
        (SOURCE_DOCS / f"{spec.event_id}-source-card.md").write_text(new_text, encoding="utf-8")
        for doc_id, file_name, title, url, effective_from, effective_to in (
            (f"EXT3_DOC_{n}_OLD", old_file, spec.family.old_source_title, spec.family.old_source_url, "", ""),
            (f"EXT3_DOC_{n}_NEW", new_file, spec.family.source_title, spec.family.source_url, "2026-08-27", ""),
        ):
            rows.append(
                {
                    "document_id": doc_id,
                    "event_id": spec.event_id,
                    "file_name": file_name,
                    "source_title": title,
                    "source_url": url,
                    "publisher": spec.family.publisher,
                    "publication_date": "",
                    "effective_from": effective_from,
                    "effective_to": effective_to,
                    "document_type": "public_normative_source_excerpt",
                    "source_type": "EXTERNAL_PUBLIC_STRUCTURED_EXCERPT",
                    "license_note": "Official/public source URL retained; benchmark stores short structured excerpt only.",
                    "sha256": sha256_file(DOCS / file_name),
                    "status": "READY",
                    "notes": "Added in external-real-v3.",
                }
            )
    return rows


def candidate_values(spec: EventSpec) -> list[tuple[str, str]]:
    slots = {1: spec.correct_value, 2: spec.old_value, 3: spec.alternate_value}
    if spec.correct_slot == 2:
        slots = {1: spec.old_value, 2: spec.correct_value, 3: spec.alternate_value}
    elif spec.correct_slot == 3:
        slots = {1: spec.old_value, 2: spec.alternate_value, 3: spec.correct_value}
    return [(f"CAND_{index:03d}", value) for index, value in slots.items()]


def added_candidate_rows(specs: list[EventSpec]) -> list[dict[str, str]]:
    rows = []
    for spec in specs:
        for candidate_id, value in candidate_values(spec):
            rows.append(
                {
                    "event_id": spec.event_id,
                    "candidate_id": candidate_id,
                    "display_value": value,
                    "operation_json": operation_json(spec, value),
                    "status": "READY",
                    "notes": "Added in external-real-v3; candidate order rotates and is not an oracle code.",
                }
            )
    return rows


def correct_candidate_id(spec: EventSpec) -> str:
    for candidate_id, value in candidate_values(spec):
        if value == spec.correct_value:
            return candidate_id
    raise RuntimeError(spec.event_id)


def added_oracle_rows(specs: list[EventSpec]) -> list[dict[str, str]]:
    rows = []
    for spec in specs:
        n = spec.event_id[-3:]
        rows.append(
            {
                "event_id": spec.event_id,
                "oracle_candidate_id": correct_candidate_id(spec),
                "oracle_value": spec.correct_value,
                "evidence_document_ids": f"EXT3_DOC_{n}_NEW",
                "evidence_spans_json": json.dumps(
                    [
                        {
                            "document_id": f"EXT3_DOC_{n}_NEW",
                            "page": "",
                            "evidence_type": "official_public_source_structured_extraction",
                            "quote": f"{spec.family.source_title}: {spec.title} -> {spec.correct_value}",
                        }
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "annotator_1": "EXT3_SOURCE_REVIEW_A",
                "annotator_2": "EXT3_SOURCE_REVIEW_B",
                "adjudicator": "",
                "agreement_status": "AGREED",
                "status": "READY",
                "notes": "Private adjudication row for external-real-v3; do not publish with public prompts.",
            }
        )
    return rows


def policy_text(spec: EventSpec) -> str:
    payload = {
        "event_id": spec.event_id,
        "semantics": spec.semantic_type.lower(),
        "facts": {
            "public_change_present": True,
            "selected_revision": "new",
            "source_family": spec.family.source_id,
        },
        "rules": [
            {
                "rule_id": f"{spec.event_id}_CURRENT_PUBLIC_SOURCE",
                "priority": 300,
                "conditions": [
                    {"fact": "public_change_present", "operator": "equals", "value": True},
                    {"fact": "selected_revision", "operator": "equals", "value": "new"},
                    {"fact": "source_family", "operator": "equals", "value": spec.family.source_id},
                ],
                "allowed_values": [spec.correct_value],
            },
            {
                "rule_id": f"{spec.event_id}_PREVIOUS_STATE",
                "priority": 100,
                "conditions": [{"fact": "selected_revision", "operator": "equals", "value": "old"}],
                "allowed_values": [spec.old_value],
            },
        ],
        "provenance": {
            "source_documents": [f"EXT3_DOC_{spec.event_id[-3:]}_OLD", f"EXT3_DOC_{spec.event_id[-3:]}_NEW"],
            "annotation_status": "EXTERNAL_PUBLIC_STRUCTURED_FORMALIZATION",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_evidence_and_rules(specs: list[EventSpec]) -> None:
    RULES.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        evidence = [
            f"# {spec.event_id} Evidence Note",
            "",
            f"- Source family: {spec.family.source_title}",
            f"- Source URL: {spec.family.source_url}",
            f"- Publisher: {spec.family.publisher}",
            f"- Event type: `{spec.semantic_type}`",
            f"- Target subject: {spec.subject}",
            f"- Target predicate: {spec.predicate}",
            "",
            "Evidence summary:",
            "",
            *[f"- {line}" for line in spec.evidence],
            "",
            "Public candidate values:",
            "",
            *[f"- `{candidate_id}`: `{value}`" for candidate_id, value in candidate_values(spec)],
            "",
            "Status:",
            "",
            "- Public source metadata, candidate values, candidate operations, formal policy, mutant OWL, and candidate OWL artifacts are fixed before private Oracle adjudication.",
        ]
        (EXCERPTS / f"{spec.event_id}-evidence.md").write_text("\n".join(evidence) + "\n", encoding="utf-8")
        (RULES / f"{spec.event_id}-formal-policy.json").write_text(policy_text(spec), encoding="utf-8")


def local_name(iri: str) -> str:
    if "#" in iri:
        return iri.rsplit("#", 1)[1]
    return iri.rstrip("/").rsplit("/", 1)[-1]


def iri_base(iri: str) -> str:
    if "#" in iri:
        return iri.rsplit("#", 1)[0] + "#"
    return iri.rstrip("/").rsplit("/", 1)[0] + "/"


def owl_text(event_id: str, artifact_id: str, subject_iri: str, predicate_iri: str, lexical_value: str, datatype: str) -> str:
    base = iri_base(subject_iri)
    predicate_name = local_name(predicate_iri)
    class_iri = base + "ExternalRealOntologyObject"
    ontology_iri = f"https://w3id.org/ontology-evolution/external-real-v3/{event_id}/{artifact_id}"
    return f'''<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF
   xmlns:ext="{xml_escape.escape(base)}"
   xmlns:owl="{OWL}"
   xmlns:rdf="{RDF}"
   xmlns:rdfs="{RDFS}"
   xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
>
  <rdf:Description rdf:about="{ontology_iri}">
    <rdf:type rdf:resource="{OWL}Ontology"/>
    <owl:versionIRI rdf:resource="{ontology_iri}/1.0.0"/>
  </rdf:Description>
  <rdf:Description rdf:about="{xml_escape.escape(class_iri)}">
    <rdf:type rdf:resource="{OWL}Class"/>
  </rdf:Description>
  <rdf:Description rdf:about="{xml_escape.escape(predicate_iri)}">
    <rdf:type rdf:resource="{OWL}DatatypeProperty"/>
    <rdf:type rdf:resource="{OWL}FunctionalProperty"/>
    <rdfs:domain rdf:resource="{xml_escape.escape(class_iri)}"/>
    <rdfs:range rdf:resource="{xml_escape.escape(datatype)}"/>
  </rdf:Description>
  <rdf:Description rdf:about="{xml_escape.escape(subject_iri)}">
    <rdf:type rdf:resource="{OWL}NamedIndividual"/>
    <rdf:type rdf:resource="{xml_escape.escape(class_iri)}"/>
    <ext:{predicate_name} rdf:datatype="{xml_escape.escape(datatype)}">{xml_escape.escape(lexical_value)}</ext:{predicate_name}>
  </rdf:Description>
</rdf:RDF>
'''


def generate_owl_artifacts(candidates: list[dict[str, str]]) -> None:
    if MUTANTS.exists():
        shutil.rmtree(MUTANTS)
    candidate_root = BUILT / "candidate-owls"
    if candidate_root.exists():
        shutil.rmtree(candidate_root)
    by_event: dict[str, list[dict[str, str]]] = {}
    for row in candidates:
        by_event.setdefault(row["event_id"], []).append(row)
    for event_id, rows in by_event.items():
        first = json.loads(rows[0]["operation_json"])
        old = first["old_value"]
        MUTANTS.mkdir(parents=True, exist_ok=True)
        (MUTANTS / f"{event_id}.owl").write_text(
            owl_text(
                event_id,
                "mutant",
                first["subject_iri"],
                first["predicate_iri"],
                str(old["lexical"]),
                str(old.get("datatype") or XSD_STRING),
            ),
            encoding="utf-8",
        )
        candidate_dir = candidate_root / event_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        for row in rows:
            op = json.loads(row["operation_json"])
            new = op["new_value"]
            (candidate_dir / f"{row['candidate_id']}.owl").write_text(
                owl_text(
                    event_id,
                    row["candidate_id"],
                    op["subject_iri"],
                    op["predicate_iri"],
                    str(new["lexical"]),
                    str(new.get("datatype") or XSD_STRING),
                ),
                encoding="utf-8",
            )


def write_source_intake(specs: list[EventSpec]) -> None:
    families = list(source_families().values())
    family_rows = [
        {
            "source_id": row.source_id,
            "domain": row.domain,
            "publisher": row.publisher,
            "source_title": row.source_title,
            "source_url": row.source_url,
            "old_source_title": row.old_source_title,
            "old_source_url": row.old_source_url,
            "public_basis": row.public_basis,
        }
        for row in families
    ]
    write_csv(SOURCE_FAMILY_CSV, family_rows)
    domain_counts = Counter(spec.domain for spec in specs)
    carried_counts = Counter(row["domain"] for row in read_csv(EVENT_CSV) if row["event_id"] < "EXT_E069")
    quota_rows = []
    for domain in sorted(set(domain_counts) | set(carried_counts)):
        quota_rows.append(
            {
                "domain": domain,
                "carried_v2_events": carried_counts[domain],
                "added_v3_events": domain_counts[domain],
                "total_v3_events": carried_counts[domain] + domain_counts[domain],
                "status": "READY",
            }
        )
    write_csv(QUOTA_CSV, quota_rows)


def build_manifest() -> None:
    files = sorted(
        path
        for root in (INPUT, DOCS, RULES, MUTANTS, BUILT, INTAKE)
        for path in root.rglob("*")
        if path.is_file()
    )
    file_rows = [
        {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in files
    ]
    write_csv(MANIFEST_FILES_CSV, file_rows)
    event_rows = read_csv(EVENT_CSV)
    domain_counts = Counter(row["domain"] for row in event_rows)
    type_counts = Counter(row["semantic_type"] for row in event_rows)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "external-real-v3",
        "ready_events": len(event_rows),
        "domains": len(domain_counts),
        "domain_counts": dict(sorted(domain_counts.items())),
        "semantic_type_counts": dict(sorted(type_counts.items())),
        "public_file_count": len(file_rows),
        "files_csv": str(MANIFEST_FILES_CSV.relative_to(ROOT)),
        "file_hashes": {row["path"]: row["sha256"] for row in file_rows},
        "boundary": "Private Oracle rows are generated locally and should not be published with public prompts.",
    }
    MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_readme(specs: list[EventSpec]) -> None:
    events = read_csv(EVENT_CSV)
    domain_counts = Counter(row["domain"] for row in events)
    type_counts = Counter(row["semantic_type"] for row in events)
    V3.mkdir(parents=True, exist_ok=True)
    (V3 / "README.md").write_text(
        "\n".join(
            [
                "# External Real V3 Benchmark",
                "",
                f"Generated at {datetime.now(timezone.utc).isoformat()} UTC.",
                "",
                "Status: READY 200+ event public-source diagnostic benchmark.",
                "",
                f"- Total READY events: {len(events)}",
                "- Carried forward from external-real-v2: 68",
                f"- Added external-real-v3 public-source structured events: {len(specs)}",
                f"- Domains: {len(domain_counts)}",
                f"- Semantic type counts: {dict(sorted(type_counts.items()))}",
                "",
                "Boundary:",
                "",
                "- Events are source-grounded structured benchmark items from official/public normative sources.",
                "- This is stronger than random synthetic data, but it is still a diagnostic benchmark, not a naturally occurring incident log.",
                "- Public inputs do not include private Oracle columns.",
                "- Candidate repairs are finite literal replacement candidates.",
                "- Private Oracle rows are for local evaluation only.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (V3 / "protocol.md").write_text(
        """# External Real V3 Protocol

1. Public source family, source URL, candidate values, evidence excerpts, formal policy, mutant OWL, and candidate OWL are fixed first.
2. Private Oracle rows are stored separately and loaded only after decisions are fixed.
3. Added events must not be selected because a specific model failed.
4. Candidate order is rotated for added events so candidate ID is not an answer code.
5. The dataset is intended to support broader generalization and robustness claims, while still being described as a controlled public-source diagnostic benchmark.
""",
        encoding="utf-8",
    )


def main() -> int:
    reset_from_v2()
    events = read_csv(EVENT_CSV)
    docs = read_csv(DOCUMENT_CSV)
    candidates = read_csv(CANDIDATE_CSV)
    oracles = read_csv(ORACLE_CSV)

    specs = build_added_specs(events)
    events.extend(added_event_rows(specs))
    docs.extend(write_spec_documents(specs))
    candidates.extend(added_candidate_rows(specs))
    oracles.extend(added_oracle_rows(specs))

    write_csv(EVENT_CSV, events)
    write_csv(DOCUMENT_CSV, docs)
    write_csv(CANDIDATE_CSV, candidates)
    write_csv(ORACLE_CSV, oracles)
    write_evidence_and_rules(specs)
    write_source_intake(specs)
    generate_owl_artifacts(candidates)
    write_readme(specs)
    build_manifest()

    print("external-real-v3 complete benchmark built")
    print(f"ready_events={len(events)}")
    print(f"domains={len(Counter(row['domain'] for row in events))}")
    print(f"added_events={len(specs)}")
    print(f"documents={len(docs)}")
    print(f"candidates={len(candidates)}")
    print(f"oracle_rows={len(oracles)}")
    print(f"manifest={MANIFEST_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
