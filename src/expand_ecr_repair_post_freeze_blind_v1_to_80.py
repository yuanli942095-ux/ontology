from __future__ import annotations

"""Expand the registered blind benchmark to 80 reviewable, non-frozen drafts.

The script mines anchors from cached official source text. It never creates an
official Oracle and marks every added event for manual source/claim review.
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from build_ecr_repair_post_freeze_blind_v1 import (
    SOURCE_POOL,
    CATALOG,
    download_document,
    document_is_excluded,
)
from collect_ecr_repair_post_freeze_blind_v1_exclusions import collect_exclusions
from ecr_repair_post_freeze_blind_v1_common import (
    AS_OF,
    BENCHMARK_DIR,
    FULL_PARTITION_COUNTS,
    read_json,
    write_json,
    write_jsonl,
    write_csv,
)


TARGET_DOMAINS = (
    "webauthn_fido",
    "w3c_webappsec",
    "nist_cybersecurity",
    "oauth_oidc",
    "who_clinical",
    "eu_regulation",
    "cloud_provider",
    "ietf_unseen",
)

ADDED_PARTITIONS = (
    ["REPAIR"] * 46
    + ["NO_CHANGE"] * 6
    + ["INSUFFICIENT_EVIDENCE"] * 6
    + ["CONFLICTING_EVIDENCE"] * 6
)
ADDED_REPAIR_TYPES = (
    ["TEMPORAL_VERSION"] * 15
    + ["GENERAL_RULE_EXCEPTION"] * 16
    + ["CROSS_SENTENCE_SCOPE"] * 15
)
NORMATIVE_RE = re.compile(
    r"\b(must|shall|should|required|recommended|may not|cannot|will|is enabled|"
    r"are required|at least|within|only|prohibited)\b",
    re.IGNORECASE,
)
NOISE_RE = re.compile(
    r"copyright|all rights reserved|table of contents|references|bibliography|"
    r"privacy policy|cookie|isbn|doi:|https?://|patent|working group|"
    r"implementation report|publication as|all audiences|two audiences|"
    r"key ?words|keywords|bcp\s*14|readers should|ought to begin|"
    r"status of this document|claim\(s\)",
    re.IGNORECASE,
)


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def candidate_sentences(text: str) -> list[str]:
    text = text.replace("\u00a0", " ")
    chunks = re.split(r"(?<=[.!?;])\s+|\n+", text)
    rows: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        value = compact(chunk)
        if not 45 <= len(value) <= 420:
            continue
        if NOISE_RE.search(value) or not NORMATIVE_RE.search(value):
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(value)
    if len(rows) < 12:
        for chunk in chunks:
            value = compact(chunk)
            if 60 <= len(value) <= 360 and not NOISE_RE.search(value):
                key = value.casefold()
                if key not in seen:
                    seen.add(key)
                    rows.append(value)
    return rows


def anchor_for(sentence: str) -> str:
    words = sentence.split()
    if not words:
        return sentence[:48]
    # Flexible-whitespace matching in find_anchor_span handles line wrapping.
    return " ".join(words[: min(6, len(words))])[:100]


def old_value_for(sentence: str, event_index: int) -> str:
    replacements = (
        (r"\bMUST NOT\b", "MAY"),
        (r"\bMUST\b", "MAY"),
        (r"\bSHALL NOT\b", "MAY"),
        (r"\bSHALL\b", "MAY"),
        (r"\bSHOULD NOT\b", "SHOULD"),
        (r"\bSHOULD\b", "MUST NOT"),
        (r"\brequired\b", "optional"),
        (r"\bat least\b", "at most"),
        (r"\bwithin\b", "after"),
        (r"\benabled\b", "disabled"),
    )
    for pattern, replacement in replacements[event_index % len(replacements) :] + replacements[: event_index % len(replacements)]:
        changed, count = re.subn(pattern, replacement, sentence, count=1, flags=re.IGNORECASE)
        if count:
            return changed[:360]
    return f"The registered ontology asserts the opposite of this source statement: {sentence[:260]}"


def load_documents(pool: dict[str, Any]) -> dict[str, dict[str, Any]]:
    exclusions = collect_exclusions()
    records: dict[str, dict[str, Any]] = {}
    for doc in pool["documents"]:
        if document_is_excluded(doc, exclusions):
            continue
        record = download_document(doc, refresh=False)
        records[doc["document_id"]] = record
    return records


def choose_primary_docs(
    domain: str,
    docs: list[dict[str, Any]],
    usage: Counter[str],
    needed: int,
) -> list[dict[str, Any]]:
    eligible = [doc for doc in docs if doc["domain"] == domain and candidate_sentences(doc["text"])]
    chosen: list[dict[str, Any]] = []
    for _ in range(needed):
        eligible.sort(key=lambda doc: (usage[doc["document_id"]], doc["document_id"]))
        doc = next((item for item in eligible if usage[item["document_id"]] < 4), None)
        if doc is None:
            raise RuntimeError(f"{domain}: no document remains below four-event limit")
        chosen.append(doc)
        usage[doc["document_id"]] += 1
    return chosen


def main() -> int:
    pool = read_json(SOURCE_POOL)
    catalog = read_json(CATALOG)
    original = list(catalog["events"])
    if len(original) not in {16, 80}:
        raise RuntimeError(f"expected 16 or 80 catalog events, found {len(original)}")
    if len(original) == 80:
        original = original[:16]

    records = load_documents(pool)
    usage: Counter[str] = Counter()
    for event in original:
        for doc_id in {window["document_id"] for window in event["windows"]}:
            usage[doc_id] += 1

    schedule: list[tuple[str, str]] = []
    partition_queue = list(ADDED_PARTITIONS)
    # Interleave safety cases instead of concentrating them in later domains.
    safety = partition_queue[46:]
    repair_left = 46
    safety_index = 0
    for slot in range(64):
        if slot % 4 == 3 and safety_index < len(safety):
            partition = safety[safety_index]
            safety_index += 1
        elif repair_left:
            partition = "REPAIR"
            repair_left -= 1
        else:
            partition = safety[safety_index]
            safety_index += 1
        schedule.append((TARGET_DOMAINS[slot // 8], partition))

    repair_types = iter(ADDED_REPAIR_TYPES)
    domain_docs: dict[str, list[dict[str, Any]]] = {}
    for domain in TARGET_DOMAINS:
        domain_docs[domain] = choose_primary_docs(domain, list(records.values()), usage, 8)

    sentence_offsets: defaultdict[str, int] = defaultdict(int)
    added: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    for offset, (domain, partition) in enumerate(schedule, start=17):
        doc = domain_docs[domain][(offset - 17) % 8]
        sentences = candidate_sentences(doc["text"])
        cursor = sentence_offsets[doc["document_id"]]
        support = sentences[cursor % len(sentences)]
        adjacent_a = sentences[(cursor + 1) % len(sentences)]
        adjacent_b = sentences[(cursor + 2) % len(sentences)]
        sentence_offsets[doc["document_id"]] += 3
        semantic_type = next(repair_types) if partition == "REPAIR" else (
            "GENERAL_RULE_EXCEPTION" if offset % 2 else "CROSS_SENTENCE_SCOPE"
        )
        decision_value = support[:360]
        old_value = old_value_for(decision_value, offset)
        new_value: str | None = decision_value if partition in {"REPAIR", "NO_CHANGE"} else None
        if partition == "NO_CHANGE":
            old_value = decision_value
        event_id = f"BLIND_E{offset:03d}"
        safety_feature = "NONE" if partition == "REPAIR" else partition
        windows = [
            {"document_id": doc["document_id"], "anchor": anchor_for(adjacent_a), "role": "adjacent_context"},
            {"document_id": doc["document_id"], "anchor": anchor_for(support), "role": "candidate_support"},
            {"document_id": doc["document_id"], "anchor": anchor_for(adjacent_b), "role": "adjacent_control"},
        ]
        event = {
            "event_id": event_id,
            "partition": partition,
            "semantic_type": semantic_type,
            "safety_feature": safety_feature,
            "domain": domain,
            "source_family": doc["source_family"],
            "title": f"Independent source claim review {event_id}",
            "case_context": (
                f"Assessment date: {AS_OF}. Using only the supplied frozen official source windows, "
                "determine whether the registered ontology assertion remains supported."
            ),
            "subject_label": doc["official_title"],
            "predicate_label": "registered normative assertion",
            "predicate_local": f"registeredAssertion{offset:03d}",
            "old_value": old_value,
            "new_value": new_value,
            "supporting_window_ids": ["SOURCE_WINDOW_2"] if new_value is not None else [],
            "structures": [semantic_type, "NATURAL_FULL_TEXT", "ADJACENT_MUST_SHOULD"],
            "windows": windows,
            "distractors": [old_value, adjacent_a[:240], adjacent_b[:240]] if partition == "REPAIR" else [],
            "construction_status": "MANUAL_REVIEW_REQUIRED",
            "construction_mode": "REAL_OFFICIAL_SOURCE_CONTROLLED_MUTANT_DRAFT",
        }
        added.append(event)
        audit.append(
            {
                "event_id": event_id,
                "document_id": doc["document_id"],
                "partition_proposal": partition,
                "semantic_type_proposal": semantic_type,
                "source_anchor": anchor_for(support),
                "review_required": True,
                "review_items": [
                    "claim_is_normative_and_not_front_matter_or_bibliography",
                    "target_subject_and_predicate_are_specific",
                    "partition_is_supported_by_natural_evidence",
                    "semantic_type_matches_the_reasoning_required",
                    "old_value_is_a_plausible_controlled_mutant",
                ],
            }
        )

    all_events = original + added
    partitions = Counter(item["partition"] for item in all_events)
    domains = Counter(item["domain"] for item in all_events)
    repair_semantics = Counter(
        item["semantic_type"] for item in all_events if item["partition"] == "REPAIR"
    )
    if partitions != Counter(FULL_PARTITION_COUNTS):
        raise RuntimeError(f"partition quota mismatch: {partitions}")
    if set(domains.values()) != {10}:
        raise RuntimeError(f"domain quota mismatch: {domains}")
    expected_semantics = Counter(
        {"TEMPORAL_VERSION": 19, "GENERAL_RULE_EXCEPTION": 19, "CROSS_SENTENCE_SCOPE": 18}
    )
    if repair_semantics != expected_semantics:
        raise RuntimeError(f"repair semantic quota mismatch: {repair_semantics}")

    write_json(
        CATALOG,
        {
            **{key: value for key, value in catalog.items() if key != "events"},
            "status": "PROPOSED_NOT_FROZEN",
            "event_count": 80,
            "events": all_events,
        },
    )
    write_json(
        BENCHMARK_DIR / "private/construction/expansion-to-80-audit.json",
        {
            "status": "AWAITING_HUMAN_EVENT_REVIEW",
            "original_adjudicated_events_preserved": 16,
            "added_draft_events": 64,
            "official_oracle_created": False,
            "v24_executed": False,
            "partition_counts": dict(partitions),
            "domain_counts": dict(domains),
            "repair_semantic_counts": dict(repair_semantics),
            "events": audit,
        },
    )
    write_jsonl(
        BENCHMARK_DIR / "private/construction/round2-64-event-ids.jsonl",
        [{"event_id": event["event_id"]} for event in added],
    )
    write_csv(
        BENCHMARK_DIR / "private/construction/round2-64-quality-review.csv",
        [
            {
                "event_id": event["event_id"],
                "domain": event["domain"],
                "document_id": event["windows"][1]["document_id"],
                "proposed_partition": event["partition"],
                "proposed_semantic_type": event["semantic_type"],
                "proposed_claim": event.get("new_value") or event["old_value"],
                "normative_not_metadata": "",
                "target_specific": "",
                "partition_supported": "",
                "semantic_type_supported": "",
                "mutant_plausible": "",
                "reviewer_decision": "",
                "reviewer_notes": "",
            }
            for event in added
        ],
    )
    print(json.dumps({"events": 80, "partitions": dict(partitions), "domains": dict(domains)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
