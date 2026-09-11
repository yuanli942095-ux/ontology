from __future__ import annotations

"""Replace the rejected auto-mined round with source-grounded event designs."""

import json
import re
from collections import Counter
from typing import Any

from build_ecr_repair_post_freeze_blind_v1 import CATALOG, SOURCE_POOL
from ecr_repair_post_freeze_blind_v1_common import (
    FULL_DOMAIN_COUNTS,
    FULL_PARTITION_COUNTS,
    FULL_REPAIR_SEMANTIC_COUNTS,
    read_csv,
    read_json,
    write_csv,
    write_json,
    write_jsonl,
    BENCHMARK_DIR,
)

PRIOR_REVIEW = BENCHMARK_DIR / "private/construction/round2-64-quality-review.csv"
CURATION_AUDIT = BENCHMARK_DIR / "private/construction/round2-64-curation-audit.csv"
V2_REVIEW = BENCHMARK_DIR / "private/construction/round2-64-quality-review-v2.csv"
V3_AUDIT = BENCHMARK_DIR / "private/construction/round2-64-v3-revision-audit.csv"
V3_REVIEW = BENCHMARK_DIR / "private/construction/round2-64-quality-review-v3.csv"


def W(document_id: str, *anchors: str) -> list[tuple[str, str]]:
    roles = ("scope_context", "support", "exception_or_control", "additional_context")
    return [(document_id, anchor, roles[index]) for index, anchor in enumerate(anchors)]


def E(
    number: int,
    partition: str,
    semantic_type: str,
    domain: str,
    title: str,
    subject: str,
    predicate: str,
    old: str,
    new: str | None,
    windows: list[tuple[str, str]] | list[list[tuple[str, str]]],
    *,
    context: str = "",
    supporting_window_ids: list[str] | None = None,
) -> dict[str, Any]:
    flattened: list[tuple[str, str]] = []
    for item in windows:
        if isinstance(item, list):
            flattened.extend(item)
        else:
            flattened.append(item)
    if len(flattened) < 3:
        raise ValueError(f"E{number}: at least three windows required")
    event_id = f"BLIND_E{number:03d}"
    predicate_local = re.sub(r"[^A-Za-z0-9]", "", predicate.title())
    case = context or (
        "Using only the supplied frozen official source windows, determine the supported value "
        f"for {predicate} of {subject}."
    )
    return {
        "event_id": event_id,
        "partition": partition,
        "semantic_type": semantic_type,
        "safety_feature": "NONE" if partition == "REPAIR" else partition,
        "domain": domain,
        "source_family": "",
        "title": title,
        "case_context": "Assessment date: 2026-09-01T00:00:00Z. " + case,
        "subject_label": subject,
        "predicate_label": predicate,
        "predicate_local": predicate_local[:70] + f"{number:03d}",
        "old_value": old,
        "new_value": new,
        "supporting_window_ids": (
            supporting_window_ids
            if supporting_window_ids is not None
            else (["SOURCE_WINDOW_2"] if new is not None else [])
        ),
        "structures": [semantic_type, "NATURAL_FULL_TEXT", "CURATED_SOURCE_CLAIM"],
        "windows": [
            {"document_id": doc, "anchor": anchor, "role": role}
            for doc, anchor, role in flattened
        ],
        "distractors": (
            [old, "not specified", "implementation-defined"] if partition == "REPAIR" else []
        ),
        "construction_status": "HUMAN_REVIEW_REQUIRED",
        "construction_mode": "CURATED_REAL_SOURCE_CONTROLLED_MUTANT",
    }


SPECS = [
    # WebAuthn / FIDO: 6 repair, 2 no-change.
    E(17,"REPAIR","GENERAL_RULE_EXCEPTION","webauthn_fido","CTAP minimum PIN length","CTAP 2.1 authenticatorClientPIN","default minimum PIN length","2 Unicode code points","at least 4 Unicode code points",W("DOC_FIDO_CTAP21_PS","This specifies the current minimum PIN length","The default pre-configured minimum PIN length is at least 4 Unicode code points","minPINLength member MUST be absent")),
    E(18,"REPAIR","CROSS_SENTENCE_SCOPE","webauthn_fido","WebAuthn RP ID scope","WebAuthn relying party identifier","allowed relationship to caller origin","any unrelated domain","origin effective domain or its registrable domain suffix",W("DOC_WEBAUTHN_L2_REC","Let effectiveDomain be the callerOrigin","The RP ID must be equal to the origin's effective domain","RP ID defaults to being the caller")),
    E(19,"REPAIR","CROSS_SENTENCE_SCOPE","webauthn_fido","Credential origin isolation","WebAuthn public-key credential","origins allowed to access credential","any web origin","origins belonging to the credential's relying party only",W("DOC_WEBAUTHN_L1_REC","credentials, each scoped to a given WebAuthn Relying Party","public key credential can only be accessed by origins belonging","Each public key credential is strictly scoped")),
    E(20,"NO_CHANGE","GENERAL_RULE_EXCEPTION","webauthn_fido","CTAP superseded getPinToken compatibility","CTAP 2.1 authenticator supporting clientPIN and CTAP 2.0","getPinToken support","required for backward compatibility","required for backward compatibility",W("DOC_FIDO_CTAP21_PS","Whole documents or specific features may be superseded","MUST still support authenticatorClientPIN's getPinToken","getPinToken (superseded by")),
    E(21,"REPAIR","TEMPORAL_VERSION","webauthn_fido","Authenticator response helper-method evolution","WebAuthn AuthenticatorAttestationResponse","direct credential-data access methods","attestationObject only","getPublicKey and getAuthenticatorData helper methods",W("DOC_WEBAUTHN_L2_REC","getPublicKey() and getAuthenticatorData() were only added in level two","getAuthenticatorData()","getPublicKey()"),supporting_window_ids=["SOURCE_WINDOW_1","SOURCE_WINDOW_2","SOURCE_WINDOW_3"]),
    E(22,"REPAIR","TEMPORAL_VERSION","webauthn_fido","Attestation preference enumeration evolution","WebAuthn AttestationConveyancePreference","available enumeration values","none, indirect, and direct","none, indirect, direct, and enterprise",[W("DOC_WEBAUTHN_L1_REC","enum AttestationConveyancePreference","\"none\"","\"direct\""),W("DOC_WEBAUTHN_L2_REC","enum AttestationConveyancePreference","\"enterprise\"","\"direct\" or \"enterprise\"")],supporting_window_ids=["SOURCE_WINDOW_4","SOURCE_WINDOW_5"]),
    E(23,"REPAIR","GENERAL_RULE_EXCEPTION","webauthn_fido","CTAP data-element encapsulation","CTAP credentialID, up, and uv data elements","external specifications relying on internal structure","permitted","prohibited because internal structure may change",W("DOC_FIDO_CTAP21_PS","Some of the data elements might have an internal structure","Other specifications shall not rely on such internal structure","credentialID, data type byte string")),
    E(24,"INSUFFICIENT_EVIDENCE","GENERAL_RULE_EXCEPTION","webauthn_fido","WebAuthn built-in UV modality","WebAuthn authenticator with built-in user verification","required biometric modality","fingerprint",None,W("DOC_WEBAUTHN_L1_REC","User Verification","touch plus pin code, password entry, or biometric recognition","various authorization gesture modalities"),context="Determine whether WebAuthn requires one specific biometric modality for every built-in user-verification authenticator."),

    # W3C WebAppSec: 6 repair, 1 no-change, 1 insufficient.
    E(25,"REPAIR","GENERAL_RULE_EXCEPTION","w3c_webappsec","Mixed script handling","user agent fetching active mixed script","mixed-content category","optionally blockable","blockable",W("DOC_W3C_MIXED_CONTENT_REC","script requests are blockable","Any resource or request that isn’t optionally-blockable is blockable","NOT provide users with a mechanism for forcing blockable")),
    E(26,"REPAIR","GENERAL_RULE_EXCEPTION","w3c_webappsec","Mixed image handling","user agent fetching mixed image","mixed-content category","always blockable","optionally blockable",W("DOC_W3C_MIXED_CONTENT_REC","image requests are optionally-blockable","Optionally-blockable Content","does not mean that they are safe")),
    E(27,"REPAIR","GENERAL_RULE_EXCEPTION","w3c_webappsec","SRI multiple-hash selection","user agent validating multiple SRI hashes","hash-strength selection","weakest listed hash","strongest supported hash",W("DOC_W3C_SRI_REC","Multiple sets of integrity metadata may be associated","user agent will choose the strongest hash function","get the strongest metadata from set")),
    E(28,"NO_CHANGE","GENERAL_RULE_EXCEPTION","w3c_webappsec","CSP frame-ancestors fallback","CSP frame-ancestors directive","fallback to default-src","no fallback","no fallback",W("DOC_W3C_CSP2_REC","The frame-ancestors directive MUST be ignored","frame-ancestors does not fall back to the","default-src 'none' but not")),
    E(29,"REPAIR","TEMPORAL_VERSION","w3c_webappsec","Mixed-content strict-mode reporting evolution","block-all-mixed-content directive","violation reporting behavior","block without a violation report","trigger violation reports",W("DOC_W3C_MIXED_CONTENT_REC","Normative changes since the prior CR publication","block-all-mixed-content` reports","This directive will trigger violation reports")),
    E(30,"REPAIR","CROSS_SENTENCE_SCOPE","w3c_webappsec","SRI cross-origin eligibility","cross-origin response with SRI metadata","integrity-check eligibility","eligible without CORS","eligible only with explicit CORS access",W("DOC_W3C_SRI_REC","responses are only eligible for such checks","same-origin or are the result of explicit access","Subresource Integrity requires CORS")),
    E(31,"REPAIR","TEMPORAL_VERSION","w3c_webappsec","Mixed-content prefetch categorization","W3C Mixed Content 2016 snapshot","prefetch category","optionally blockable","removed from optionally-blockable because prior listing was incorrect",W("DOC_W3C_MIXED_CONTENT_REC","Normative changes since the prior CR publication","prefetch` was incorrectly listed as optionally-blockable","split mixed content into two categories")),
    E(32,"INSUFFICIENT_EVIDENCE","CROSS_SENTENCE_SCOPE","w3c_webappsec","SRI cross-origin response eligibility","integrity-protected cross-origin response with response type unrecorded","eligibility for integrity validation","eligible",None,W("DOC_W3C_SRI_REC","responses are only eligible for such checks if they are same-origin","If the response type is basic, cors or default, return true","Return false"),context="The response is cross-origin, but its CORS authorization and response type are not recorded. Determine one unique integrity-validation eligibility value."),

    # NIST: 6 repair, 1 no-change, 1 insufficient.
    E(33,"REPAIR","TEMPORAL_VERSION","nist_cybersecurity","CSF 1.1 compatibility","NIST CSF 1.1","compatibility with CSF 1.0 implementations","breaking migration required","implementable with minimal disruption to CSF 1.0 users",W("DOC_NIST_CSF_1_1","refines, clarifies, and enhances Version 1.0","implement Version 1.1 with minimal or no disruption","compatibility with Version 1.0 has been an explicit objective")),
    E(34,"CONFLICTING_EVIDENCE","TEMPORAL_VERSION","nist_cybersecurity","CSF supply-chain category placement with version missing","NIST CSF supply-chain risk management category with framework version unrecorded","parent Function and category identifier","ID.SC under IDENTIFY",None,[W("DOC_NIST_CSF_1_1","ID.SC Supply Chain Risk Management","ID Identify","Supply Chain Risk Management (ID.SC)"),W("DOC_NIST_CSF_2_0","Cybersecurity Supply Chain Risk Management GV.SC","GOVERN (GV)","Subcategories within the CSF C-SCRM Category [GV.SC]")],context="The ontology records a CSF supply-chain category but omits whether it follows CSF 1.1 or CSF 2.0. Determine one unique parent Function and category identifier."),
    E(35,"REPAIR","TEMPORAL_VERSION","nist_cybersecurity","Governance placement from CSF 1.1 to 2.0","NIST CSF governance outcomes","Core placement","Governance category under Identify","standalone GOVERN Function",[W("DOC_NIST_CSF_1_1","ID.GV Governance","five concurrent and continuous Functions","Governance (ID.GV)"),W("DOC_NIST_CSF_2_0","GOVERN (GV)","GOVERN is in the center of the wheel","six CSF Functions")],supporting_window_ids=["SOURCE_WINDOW_4","SOURCE_WINDOW_5","SOURCE_WINDOW_6"]),
    E(36,"REPAIR","CROSS_SENTENCE_SCOPE","nist_cybersecurity","Zero-trust session authorization","zero trust architecture resource access","authorization granularity","permanent network-wide authorization","per-session access to individual resources",W("DOC_NIST_SP_800_207","Access to individual enterprise resources is granted on a per-session basis","least privileges needed to complete the task","All communication is secured regardless of network location")),
    E(37,"REPAIR","CROSS_SENTENCE_SCOPE","nist_cybersecurity","Zero-trust dynamic access policy","zero trust architecture resource access","policy basis","static network location only","dynamic policy using identity, asset state, and context",W("DOC_NIST_SP_800_207","Access to resources is determined by dynamic policy","network location, time/date of request","policy decision point (PDP)")),
    E(38,"REPAIR","TEMPORAL_VERSION","nist_cybersecurity","CSF Profile taxonomy evolution","NIST CSF Profile taxonomy","community-oriented profile term","generic Target Profile","Community Profile",W("DOC_NIST_CSF2_COMMUNITY_NEWS","CSF 2.0 introduced the term","Community Profiles","use case-specific cybersecurity risk management guidance for multiple organizations"),supporting_window_ids=["SOURCE_WINDOW_1","SOURCE_WINDOW_2","SOURCE_WINDOW_3"]),
    E(39,"NO_CHANGE","CROSS_SENTENCE_SCOPE","nist_cybersecurity","CSF Tier count","NIST CSF Implementation Tiers","number of tiers","four","four",W("DOC_NIST_CSF_TIERS_FAQ","Partial (Tier 1)","Adaptive (Tier 4)","Framework Implementation Tiers")),
    E(40,"REPAIR","GENERAL_RULE_EXCEPTION","nist_cybersecurity","CSF Function activation timing","NIST CSF 2.0 Functions","activation timing","fixed sequential execution","GOVERN, IDENTIFY, PROTECT, and DETECT continuously; RESPOND and RECOVER ready and triggered by incidents",W("DOC_NIST_CSF_2_0","The Functions should be addressed concurrently","should all happen continuously","RESPOND and RECOVER should be ready at all times and happen when cybersecurity incidents occur")),

    # OAuth/OIDC: 6 repair, 1 insufficient, 1 conflict.
    E(41,"REPAIR","CROSS_SENTENCE_SCOPE","oauth_oidc","OIDC ID Token protection","OpenID Connect ID Token","required JOSE protection","unsigned token allowed","must be JWS signed; encryption is optional after signing",W("DOC_OIDC_CORE_1_0","ID Tokens MUST be signed using JWS","optionally both signed and then","If the ID Token is encrypted, it MUST be signed then encrypted")),
    E(42,"REPAIR","CROSS_SENTENCE_SCOPE","oauth_oidc","Discovery issuer consistency","OpenID Provider discovery metadata","issuer equality across discovery","prefix match is sufficient","exact match with WebFinger issuer",W("DOC_OIDC_DISCOVERY_1_0","issuer value returned MUST be identical","returned Issuer location MUST be a URI","RPs MUST ensure that the Issuer URL")),
    E(43,"REPAIR","TEMPORAL_VERSION","oauth_oidc","FAPI authorization request protection evolution","FAPI authorization request","primary request protection mechanism","signed request object required by FAPI 1 Advanced","pushed authorization request profile in FAPI 2",[W("DOC_FAPI1_PART2_FINAL","shall send all parameters inside the authorization request's signed request object","shall only use the parameters included in the signed request object","does not support public clients"),W("DOC_FAPI20_SECURITY_FINAL","Pushed Authorization Requests","authorization request","public clients to be secured")],supporting_window_ids=["SOURCE_WINDOW_4"]),
    E(44,"REPAIR","CROSS_SENTENCE_SCOPE","oauth_oidc","FAPI public-client applicability","FAPI security profile","public-client applicability","supported as conforming clients","outside the profile scope",[W("DOC_FAPI1_PART2_FINAL","shall not support public clients","This profile does not support public clients","OAuth Public Clients"),W("DOC_FAPI20_SECURITY_FINAL","public clients to be secured to the same degree","their use is not within the scope","Security Profile")],supporting_window_ids=["SOURCE_WINDOW_2","SOURCE_WINDOW_5"]),
    E(45,"REPAIR","GENERAL_RULE_EXCEPTION","oauth_oidc","DPoP transport requirement","OAuth DPoP","transport security","may replace HTTPS","must always be used with HTTPS",W("DOC_IETF_RFC9449","not, however, a substitute for a secure transport","MUST always be","underlying secure transport layer")),
    E(46,"REPAIR","GENERAL_RULE_EXCEPTION","oauth_oidc","OIDC max_age response requirement","ID Token returned for a max_age request","auth_time claim presence","optional","required",W("DOC_OIDC_CORE_1_0","When max_age is used, the ID Token returned","MUST include an auth_time Claim Value","max_age=0 is equivalent")),
    E(47,"INSUFFICIENT_EVIDENCE","GENERAL_RULE_EXCEPTION","oauth_oidc","OIDC nonce requirement with flow missing","OpenID Connect authentication request with flow type unrecorded","nonce requirement","required",None,W("DOC_OIDC_CORE_1_0","Use of the nonce Claim is REQUIRED","If a nonce parameter is present in the Authentication Request","MUST include a nonce Claim in the ID Token"),context="The authentication flow and whether a nonce parameter was sent are both unrecorded. Determine one unique nonce requirement."),
    E(48,"CONFLICTING_EVIDENCE","TEMPORAL_VERSION","oauth_oidc","FAPI request-object requirement across profiles","deployment identified only as FAPI compliant","authorization request object requirement","signed request object required",None,[W("DOC_FAPI1_PART2_FINAL","shall send all parameters inside the authorization request's signed request object","shall only use the parameters included in the signed request object","request_uri parameter"),W("DOC_FAPI20_SECURITY_FINAL","Pushed Authorization Requests","authorization request","Security Profile")],context="The deployment metadata says only 'FAPI compliant' and does not identify FAPI 1 Advanced or FAPI 2. Determine a unique authorization request-object requirement."),

    # WHO: 5 repair, 1 no-change, 2 genuine subgroup conflicts.
    E(49,"REPAIR","GENERAL_RULE_EXCEPTION","who_clinical","WHO household crowding measurement","Member State household-crowding policy","measurement threshold","more than three people per room universally","an appropriate context-specific measure and threshold chosen by each Member State",W("DOC_WHO_HOUSING_FULL_2018","Strategies should be developed and implemented to prevent and reduce","Each Member State should choose an appropriate way to measure","including a threshold that can be used to define a household as")),
    E(50,"REPAIR","GENERAL_RULE_EXCEPTION","who_clinical","WHO minimum indoor temperature","general-population housing in cold seasons","safe indoor temperature","16 degrees Celsius","18 degrees Celsius",W("DOC_WHO_HOUSING_FULL_2018","18 ˚C has been proposed","safe and well-balanced indoor temperature","higher minimum indoor temperature than 18 °C may be necessary")),
    E(51,"REPAIR","TEMPORAL_VERSION","who_clinical","WHO inactivity-reduction target transition","WHO global physical-inactivity reduction target as of 2026","active target milestone","10 percent relative reduction by 2025","15 percent relative reduction by 2030",W("DOC_WHO_PA_2020","10% relative reduction by 2025","15% by 2030","from the 2010 baseline")),
    E(52,"REPAIR","CROSS_SENTENCE_SCOPE","who_clinical","Self-care enabling environment","self-care enabling environment","responsible sectors","health sector only","health, education, justice, and social services sectors",W("DOC_WHO_SELFCARE_2022","Ensuring an enabling environment requires action not only from the health sector","education, justice and social services sectors","used outside formal health facilities")),
    E(53,"REPAIR","TEMPORAL_VERSION","who_clinical","WHO self-care guideline revision","WHO self-care intervention guideline","current edition","2019 guideline only","2022 revision incorporating new and existing recommendations",W("DOC_WHO_SELFCARE_FULL_2022","well-being, 2022 revision","Building on the 2019 guideline","brings together new and existing WHO recommendations")),
    E(54,"NO_CHANGE","GENERAL_RULE_EXCEPTION","who_clinical","WHO HIV self-testing recommendation","HIV testing services","self-testing role","additional approach","additional approach",W("DOC_WHO_SELFCARE_FULL_2022","HIV self-testing should be offered as an additional approach","Recommendation 30","Strong recommendation; moderate certainty evidence")),
    E(55,"CONFLICTING_EVIDENCE","GENERAL_RULE_EXCEPTION","who_clinical","STI self-collection recommendation strength","STI self-collection without pathogen recorded","recommendation strength","strong recommendation",None,W("DOC_WHO_SELFCARE_FULL_2022","Self-collection of samples for Neisseria gonorrhoeae","Strong recommendation; moderate certainty evidence","Self-collection of samples for Treponema pallidum","Conditional recommendation; low certainty evidence"),context="The ontology records only STI self-collection and omits the pathogen. Determine one unique recommendation strength."),
    E(56,"CONFLICTING_EVIDENCE","GENERAL_RULE_EXCEPTION","who_clinical","Diabetes glucose self-monitoring recommendation","person with diabetes whose insulin status is missing","self-monitoring recommendation","recommended",None,W("DOC_WHO_SELFCARE_FULL_2022","type 2 diabetes not on insulin is not recommended","insufficient evidence to support such a recommendation","type 1 and type 2 diabetes on insulin should be offered","Conditional recommendation; low certainty evidence"),context="The patient record omits whether insulin is used. Determine one unique WHO recommendation for blood-glucose self-monitoring."),

    # EU: 6 repair, 2 conflicts.
    E(57,"REPAIR","GENERAL_RULE_EXCEPTION","eu_regulation","DSA maximum substantive fine","intermediary service provider","maximum fine for DSA obligation breach","4 percent of annual worldwide turnover","6 percent of preceding-year annual worldwide turnover",W("DOC_EU_DSA","maximum amount of fines that may be imposed","6 % of the annual worldwide turnover","1 % of the annual income")),
    E(58,"REPAIR","CROSS_SENTENCE_SCOPE","eu_regulation","DSA service scope","Digital Services Act","regulated service layer","all underlying products and services","intermediary services, excluding unrelated underlying services",W("DOC_EU_DSA","This Regulation should apply to providers of intermediary services","apply only to intermediary services","integral part of another service which is not an intermediary service")),
    E(59,"REPAIR","TEMPORAL_VERSION","eu_regulation","NIS predecessor replacement","Directive (EU) 2016/1148","status under NIS2","still governing instrument","repealed and replaced by Directive (EU) 2022/2555",W("DOC_EU_NIS2","Directive (EU) 2016/1148 should be repealed and replaced","updating the list of sectors and activities","This Directive aims to remove")),
    E(60,"REPAIR","TEMPORAL_VERSION","eu_regulation","DSA general application date","Digital Services Act","general application date","16 November 2022","17 February 2024",W("DOC_EU_DSA","This Regulation shall apply from 17 February 2024","shall apply from 16 November 2022","very large online platforms")),
    E(61,"REPAIR","GENERAL_RULE_EXCEPTION","eu_regulation","eIDAS qualified-signature legal effect","qualified electronic signature","legal equivalence","lower legal effect than handwritten signature","equivalent legal effect to a handwritten signature",W("DOC_EU_EIDAS","qualified electronic signature shall have the equivalent legal effect","legal effect and admissibility as evidence","Electronic signatures")),
    E(62,"REPAIR","CROSS_SENTENCE_SCOPE","eu_regulation","NIS2 small-enterprise scope","small enterprise or microenterprise with a key societal role","NIS2 scope","always excluded by size cap","included when specified key-role criteria are met",W("DOC_EU_NIS2","application of a size-cap rule","certain small enterprises and microenterprises","key role for society, the economy")),
    E(63,"CONFLICTING_EVIDENCE","GENERAL_RULE_EXCEPTION","eu_regulation","eIDAS liability burden with qualification missing","trust service provider whose qualified status is unrecorded","burden of proving intention or negligence","claimant bears the burden",None,W("DOC_EU_EIDAS","burden of proving intention or negligence of a non-qualified trust service provider","intention or negligence of a qualified trust service provider shall be presumed","unless that qualified trust service provider proves"),context="The provider's qualified or non-qualified status is missing. Determine one unique allocation of the burden of proving intention or negligence."),
    E(64,"CONFLICTING_EVIDENCE","GENERAL_RULE_EXCEPTION","eu_regulation","Overlapping platform maximum-fine rate","online platform subject to DSA and GDPR with violated obligation unspecified","maximum turnover-based fine rate","6 percent",None,[W("DOC_EU_DSA","6 % of the annual worldwide turnover","maximum amount of fines","failure to comply with an obligation"),W("DOC_EU_GDPR","4 % of the total worldwide annual turnover","administrative fines","preceding financial year")],context="The platform is subject to both DSA and GDPR, but the violated obligation is not recorded. Determine one unique turnover-based maximum fine rate."),

    # Cloud: 6 repair, 1 no-change, 1 conflict caused by missing bucket state.
    E(65,"REPAIR","TEMPORAL_VERSION","cloud_provider","S3 new-bucket ACL default evolution","Amazon S3 bucket created after the April 2023 security-default rollout","access control list default","ACLs enabled","ACLs disabled with Block Public Access enabled",W("DOC_AWS_S3_SECURITY_DEFAULTS_2023","Starting in April 2023","automatically enabling S3 Block Public Access","disabling S3 access control lists (ACLs) for all new S3 buckets")),
    E(66,"REPAIR","GENERAL_RULE_EXCEPTION","cloud_provider","GCP storage-level encryption algorithm","Google Cloud storage-level data encryption","default data-encryption-key algorithm","AES-128 for all stored data","AES-256 by default, except a small number of Persistent Disks created before 2015 use AES-128",W("DOC_GCP_DEFAULT_ENCRYPTION","AES-256 by default","with the exception of a small number of","Persistent Disks","created before 2015 that use AES-128"),supporting_window_ids=["SOURCE_WINDOW_1","SOURCE_WINDOW_2","SOURCE_WINDOW_3","SOURCE_WINDOW_4"]),
    E(67,"REPAIR","TEMPORAL_VERSION","cloud_provider","S3 configured-bucket preservation","existing S3 bucket already configured with SSE-KMS","effect of 2023 default update","changed to SSE-S3","existing SSE-KMS configuration remains unchanged",W("DOC_AWS_S3_DEFAULT_ENCRYPTION","Existing buckets currently using S3 Default Encryption configuration will not change","all new buckets and for existing buckets without","continue to update the Default Encryption configuration")),
    E(68,"REPAIR","CROSS_SENTENCE_SCOPE","cloud_provider","Azure Storage service cipher","Azure Storage service-side encryption","cipher","AES-CBC 128","AES-GCM 256",W("DOC_AZURE_SSE","uses 256-bit AES Galois/Counter Mode","enabled for all storage accounts","similar to BitLocker encryption")),
    E(69,"NO_CHANGE","GENERAL_RULE_EXCEPTION","cloud_provider","Azure Storage new-account key default","new Azure Storage account","default encryption-key management","Microsoft-managed keys","Microsoft-managed keys",W("DOC_AZURE_SSE","Data in a new storage account is encrypted with Microsoft-managed keys by default","you can manage encryption with your own keys","customer-managed key")),
    E(70,"REPAIR","GENERAL_RULE_EXCEPTION","cloud_provider","Google data-chunk decryptability","Google Cloud encrypted data chunk","authorized decryptors","any storage-device holder","authorized Google services with granted roles only",W("DOC_GCP_DEFAULT_ENCRYPTION","each chunk can be decrypted only by Google services","authorized roles","Only authorized Google services and users")),
    E(71,"REPAIR","CROSS_SENTENCE_SCOPE","cloud_provider","S3 replication destination encryption","previously unencrypted object replicated to an encrypted destination bucket","encryption setting used for replica","source bucket setting","destination bucket default encryption setting",W("DOC_AWS_S3_DEFAULT_ENCRYPTION_UG","Using default encryption with replication","source bucket are not encrypted","default encryption settings of the destination bucket")),
    E(72,"INSUFFICIENT_EVIDENCE","GENERAL_RULE_EXCEPTION","cloud_provider","S3 bucket encryption key type with missing configuration","existing S3 bucket whose configured encryption setting is unavailable","encryption key type","SSE-S3",None,[W("DOC_AWS_S3_DEFAULT_ENCRYPTION","automatically apply SSE-S3 as the base level","existing buckets without any customer configured encryption setting","Existing buckets currently using S3 Default Encryption configuration will not change"),W("DOC_AWS_S3_DEFAULT_ENCRYPTION_UG","If you want to encrypt your objects with SSE-KMS","must change the encryption type","no changes to the default encryption configuration")],context="The evidence contains the SSE-S3 fallback and preservation of an existing configured encryption type, but the bucket's prior configuration is missing. Determine one unique key type."),

    # IETF: 5 repair, 1 no-change, 2 insufficient/alternative outcomes.
    E(73,"REPAIR","GENERAL_RULE_EXCEPTION","ietf_unseen","JWT duplicate Claim Names","JWT Claims Set","duplicate member handling","accept every duplicate","reject duplicates or retain only the lexically last duplicate",W("DOC_IETF_RFC7519","MUST be unique; JWT parsers MUST either reject","returns only the lexically last duplicate member name","Claim Names")),
    E(74,"REPAIR","CROSS_SENTENCE_SCOPE","ietf_unseen","JWT expiration processing","JWT with current time after exp","acceptance","accepted with no time check","must not be accepted",W("DOC_IETF_RFC7519","or after which the JWT MUST NOT be accepted","The \"exp\" (expiration time) claim","small leeway")),
    E(75,"REPAIR","TEMPORAL_VERSION","ietf_unseen","YANG Library NMDA transition","NMDA-supporting YANG server","library tree","modules-state only","populate deprecated modules-state for legacy clients and yang-library for NMDA clients",W("DOC_IETF_RFC8525","SHOULD populate the deprecated \"/modules-state\" tree","new \"/yang-library\" tree will be ignored by","NMDA-aware clients")),
    E(76,"REPAIR","GENERAL_RULE_EXCEPTION","ietf_unseen","ACME JWS signature constraints","ACME request JWS","signature structure","multiple signatures allowed","single signature; unprotected header and detached payload prohibited",W("DOC_IETF_RFC8555","The JWS MUST NOT have multiple signatures","JWS Unprotected Header","JWS Payload MUST NOT be detached")),
    E(77,"REPAIR","CROSS_SENTENCE_SCOPE","ietf_unseen","TLS-ALPN ACME identifier extension","tls-alpn-01 validation certificate","acmeIdentifier extension","optional non-critical extension","critical extension containing SHA-256 key-authorization digest",W("DOC_IETF_RFC8737","certificate that MUST contain an acmeIdentifier extension","MUST contain the SHA-256 digest","extension MUST be critical")),
    E(78,"INSUFFICIENT_EVIDENCE","GENERAL_RULE_EXCEPTION","ietf_unseen","JWT duplicate parser strategy","JWT parser confronted with duplicate Claim Names","single mandatory strategy","reject the JWT",None,W("DOC_IETF_RFC7519","MUST either reject JWTs with duplicate","or use a JSON parser that returns only the lexically last","duplicate member name"),context="Determine whether RFC 7519 mandates exactly one of its two permitted duplicate-Claim handling strategies."),
    E(79,"INSUFFICIENT_EVIDENCE","GENERAL_RULE_EXCEPTION","ietf_unseen","YANG library client-independent tree","YANG client with NMDA capability not recorded","tree the client must consume","yang-library",None,W("DOC_IETF_RFC8525","new \"/yang-library\" tree will be ignored by","NMDA-aware clients","deprecated \"/modules-state\" tree"),context="The client capability is absent. Determine one unique YANG Library tree that every such client must consume."),
    E(80,"NO_CHANGE","CROSS_SENTENCE_SCOPE","ietf_unseen","ACME challenge token entropy","ACME challenge token","minimum entropy","at least 128 bits","at least 128 bits",[W("DOC_IETF_RFC8555","This value MUST have at least 128 bits of entropy","MUST NOT contain any characters outside the base64url alphabet","MUST NOT include base64 padding"),W("DOC_IETF_RFC8737","This value MUST have at least 128 bits of entropy","MUST NOT contain any characters outside the base64url alphabet","token (required, string)")],supporting_window_ids=["SOURCE_WINDOW_1","SOURCE_WINDOW_4"]),
]


def main() -> int:
    catalog = read_json(CATALOG)
    original = catalog["events"][:16]
    pool = read_json(SOURCE_POOL)
    docs = {row["document_id"]: row for row in pool["documents"]}
    for event in SPECS:
        first_doc = event["windows"][0]["document_id"]
        event["source_family"] = docs[first_doc]["source_family"]
    events = original + SPECS
    partitions = Counter(row["partition"] for row in events)
    domains = Counter(row["domain"] for row in events)
    semantics = Counter(row["semantic_type"] for row in events if row["partition"] == "REPAIR")
    doc_usage = Counter(
        doc for row in events for doc in {window["document_id"] for window in row["windows"]}
    )
    errors = []
    if partitions != Counter(FULL_PARTITION_COUNTS): errors.append(f"partitions={partitions}")
    if domains != Counter(FULL_DOMAIN_COUNTS): errors.append(f"domains={domains}")
    if semantics != Counter(FULL_REPAIR_SEMANTIC_COUNTS): errors.append(f"semantics={semantics}")
    overused = {key: value for key, value in doc_usage.items() if value > 4}
    if overused: errors.append(f"documents_over_4={overused}")
    if errors:
        raise RuntimeError("; ".join(errors))
    prior_rows = read_csv(PRIOR_REVIEW)
    prior_by_id = {row["event_id"]: row for row in prior_rows}
    prior_counts = Counter(row.get("reviewer_decision", "") for row in prior_rows)
    if prior_counts != Counter({"REVISE": 22, "REJECT": 42}):
        raise RuntimeError(f"unexpected prior quality-review decisions: {prior_counts}")
    missing_prior = sorted(row["event_id"] for row in SPECS if row["event_id"] not in prior_by_id)
    if missing_prior:
        raise RuntimeError(f"events missing from prior quality review: {missing_prior}")
    v2_rows = read_csv(V2_REVIEW)
    v2_by_id = {row["event_id"]: row for row in v2_rows}
    v2_counts = Counter(row.get("reviewer_decision", "") for row in v2_rows)
    if v2_counts != Counter({"ACCEPT": 46, "REVISE": 11, "REJECT": 7}):
        raise RuntimeError(f"unexpected v2 quality-review decisions: {v2_counts}")
    changed_ids = {
        row["event_id"] for row in v2_rows
        if row.get("reviewer_decision") in {"REVISE", "REJECT"}
    }
    if len(changed_ids) != 18:
        raise RuntimeError(f"expected 18 v3 changes, found {len(changed_ids)}")
    for row in SPECS:
        if row["event_id"] in changed_ids:
            continue
        prior = v2_by_id[row["event_id"]]
        stable_fields = {
            "domain": row["domain"],
            "proposed_partition": row["partition"],
            "proposed_semantic_type": row["semantic_type"],
            "subject": row["subject_label"],
            "predicate": row["predicate_label"],
            "old_value": row["old_value"],
            "new_value": row.get("new_value") or "",
        }
        drift = [key for key, value in stable_fields.items() if prior.get(key, "") != value]
        if drift:
            raise RuntimeError(f"v2 ACCEPT event changed: {row['event_id']} fields={drift}")
    write_json(CATALOG, {**catalog, "status": "CURATED_AWAITING_QUALITY_REVIEW", "event_count": 80, "events": events})
    write_jsonl(BENCHMARK_DIR / "private/construction/round2-64-event-ids.jsonl", [{"event_id": row["event_id"]} for row in SPECS])
    write_csv(V3_AUDIT, [{
        "event_id": row["event_id"],
        "v2_decision": v2_by_id[row["event_id"]]["reviewer_decision"],
        "v3_action": "REVISE_CLAIM" if v2_by_id[row["event_id"]]["reviewer_decision"] == "REVISE" else "REPLACE_DUPLICATE_CLAIM",
        "prior_document_ids": v2_by_id[row["event_id"]].get("document_ids", ""),
        "new_document_ids": ";".join(dict.fromkeys(w["document_id"] for w in row["windows"])),
        "prior_partition": v2_by_id[row["event_id"]].get("proposed_partition", ""),
        "new_partition": row["partition"],
        "prior_semantic_type": v2_by_id[row["event_id"]].get("proposed_semantic_type", ""),
        "new_semantic_type": row["semantic_type"],
        "prior_subject": v2_by_id[row["event_id"]].get("subject", ""),
        "prior_predicate": v2_by_id[row["event_id"]].get("predicate", ""),
        "new_subject": row["subject_label"],
        "new_predicate": row["predicate_label"],
        "new_old_value": row["old_value"],
        "new_proposed_value": row.get("new_value") or "",
        "source_anchor_count": len(row["windows"]),
        "construction_status": row["construction_status"],
        "v2_reviewer_notes": v2_by_id[row["event_id"]].get("reviewer_notes", ""),
    } for row in SPECS if row["event_id"] in changed_ids])
    write_csv(V3_REVIEW, [{
        "event_id": row["event_id"], "domain": row["domain"],
        "curation_action": (
            "UNCHANGED_V2_ACCEPT"
            if row["event_id"] not in changed_ids
            else ("V3_REVISION" if v2_by_id[row["event_id"]]["reviewer_decision"] == "REVISE" else "V3_REPLACEMENT")
        ),
        "document_ids": ";".join(dict.fromkeys(w["document_id"] for w in row["windows"])),
        "proposed_partition": row["partition"], "proposed_semantic_type": row["semantic_type"],
        "subject": row["subject_label"], "predicate": row["predicate_label"],
        "old_value": row["old_value"], "new_value": row.get("new_value") or "",
        "normative_not_metadata": v2_by_id[row["event_id"]].get("normative_not_metadata", "") if row["event_id"] not in changed_ids else "",
        "target_specific": v2_by_id[row["event_id"]].get("target_specific", "") if row["event_id"] not in changed_ids else "",
        "partition_supported": v2_by_id[row["event_id"]].get("partition_supported", "") if row["event_id"] not in changed_ids else "",
        "semantic_type_supported": v2_by_id[row["event_id"]].get("semantic_type_supported", "") if row["event_id"] not in changed_ids else "",
        "mutant_plausible": v2_by_id[row["event_id"]].get("mutant_plausible", "") if row["event_id"] not in changed_ids else "",
        "reviewer_decision": v2_by_id[row["event_id"]].get("reviewer_decision", "") if row["event_id"] not in changed_ids else "",
        "reviewer_notes": v2_by_id[row["event_id"]].get("reviewer_notes", "") if row["event_id"] not in changed_ids else "",
    } for row in SPECS])
    print(json.dumps({
        "events": len(events),
        "partitions": dict(partitions),
        "repair_semantics": dict(semantics),
        "curation_actions": {
            "UNCHANGED_V2_ACCEPT": v2_counts["ACCEPT"],
            "V3_REVISION": v2_counts["REVISE"],
            "V3_REPLACEMENT": v2_counts["REJECT"],
        },
        "max_doc_usage": max(doc_usage.values()),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
