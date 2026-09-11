from __future__ import annotations

"""Expand external-real-holdout-v1 to 100+ real-source events.

The 30-event hold-out v1 is intentionally small. This script builds a larger
draft by mining normative sentences from the same public RFC source families.
It keeps the same staged benchmark layout and still marks the result as a draft
that requires independent semantic review before final freeze.
"""

import argparse
import re
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import build_external_real_holdout_v1 as base
from build_external_real_holdout_v1 import EventSpec, SourceSpec
from holdout_metadata_enrichment import case_context_for, predicate_label_for
from semantic_v2_common import PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded"
CACHE = PROJECT_DIR / "data" / "external-real-holdout-v1-cache"

TYPE_CYCLE = ("TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE")
TARGET_PER_SOURCE = 6
MAX_EVENTS = 240
STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "from",
    "are",
    "not",
    "must",
    "should",
    "shall",
    "may",
    "can",
    "when",
    "where",
    "which",
    "into",
    "than",
    "then",
    "been",
    "have",
    "has",
    "its",
    "their",
    "section",
    "rfc",
}

BOILERPLATE_PATTERNS = re.compile(
    r"(?i)("
    r"information about the current status|"
    r"copyright|"
    r"ietf trust|"
    r"ietf standards process|"
    r"ietf documents|"
    r"ietf contributions|"
    r"legal provisions|"
    r"code components|"
    r"bcp 14|"
    r"rfc 2119|"
    r"rfc 8174|"
    r"key words|"
    r"document carefully|"
    r"may contain material from ietf documents|"
    r"status of this memo|"
    r"abstract|"
    r"table of contents|"
    r"acknowledgements|"
    r"authors'? addresses"
    r")"
)


def clean_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" -\t")
    text = re.sub(r"^\d+(?:\.\d+)*\s+", "", text)
    return text.strip()


def normative_units(text: str) -> list[str]:
    units: list[str] = []
    seen: set[str] = set()
    for unit in base.split_units(text):
        unit = clean_sentence(unit)
        if not 80 <= len(unit) <= 700:
            continue
        if not re.search(r"[.!?:]$", unit):
            continue
        if not re.search(r"\b(MUST|MUST NOT|SHOULD|SHOULD NOT|REQUIRED|RECOMMENDED|NOT RECOMMENDED|MAY|need to|needs to|ought to)\b", unit, flags=re.I):
            continue
        if BOILERPLATE_PATTERNS.search(unit):
            continue
        numeric_tokens = re.findall(r"\b\d+(?:\.\d+)*\b", unit)
        alpha_tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", unit)
        if len(numeric_tokens) > max(8, len(alpha_tokens) // 3):
            continue
        if unit.count("|") >= 4 or unit.count("+---") >= 1:
            continue
        if len(re.findall(r"\b\d{3}\s+[A-Z][A-Za-z]+", unit)) >= 4:
            continue
        key = re.sub(r"\W+", " ", unit.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        units.append(unit)
    return units


def keywords(text: str, limit: int = 5) -> tuple[str, ...]:
    raw = [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text)]
    scored: dict[str, int] = {}
    for token in raw:
        if token in STOPWORDS or token.isdigit():
            continue
        scored[token] = scored.get(token, 0) + 1
    ranked = sorted(scored, key=lambda token: (-scored[token], raw.index(token)))
    return tuple(ranked[:limit] or raw[:limit] or ("requirement",))


def slug(text: str, limit: int = 9) -> str:
    words = [
        token.lower().strip("-")
        for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text)
        if token.lower() not in STOPWORDS
    ]
    if not words:
        words = ["normative", "claim"]
    return "_".join(words[:limit])


def predicate_for(semantic_type: str, source: SourceSpec, unit: str, subject_label: str) -> str:
    return predicate_label_for(
        semantic_type,
        subject_label=subject_label,
        source_title=source.title,
        evidence_window=unit,
    )


def case_context_for_event(
    semantic_type: str,
    source: SourceSpec,
    unit: str,
    subject_label: str,
    predicate_label: str,
) -> str:
    return case_context_for(
        semantic_type=semantic_type,
        domain=source.domain,
        subject_label=subject_label,
        predicate_label=predicate_label,
        source_family=source.family,
        source_title=source.title,
        source_url=source.url,
        evidence_window=unit,
    )


def subject_for(source: SourceSpec, unit: str) -> str:
    keys = keywords(unit, limit=3)
    return f"{source.title} {' '.join(keys)}"


def dimension_for(source: SourceSpec, unit: str) -> str:
    keys = keywords(unit, limit=3)
    prefix = re.sub(r"[^a-z0-9]+", "_", source.title.lower()).strip("_")[:28]
    suffix = "_".join(keys[:3])
    return f"{prefix}_{suffix}_claim".strip("_")


def build_events_from_sources(source_by_id: dict[str, dict[str, str]], target_per_source: int) -> tuple[EventSpec, ...]:
    events: list[EventSpec] = []
    event_no = 1
    for source in base.SOURCES:
        cache = source_by_id.get(source.source_id, {})
        text_path = PROJECT_DIR / cache.get("text_path", "")
        if not text_path.is_file():
            continue
        units = normative_units(text_path.read_text(encoding="utf-8", errors="replace"))
        for local_index, unit in enumerate(units[:target_per_source], start=1):
            semantic_type = TYPE_CYCLE[(event_no - 1) % len(TYPE_CYCLE)]
            value_slug = slug(unit)
            event_id = f"HOX_E{event_no:03d}"
            query = (unit[:180],) + keywords(unit, limit=5)
            subject_label = subject_for(source, unit)
            predicate_label = predicate_for(semantic_type, source, unit, subject_label)
            events.append(
                EventSpec(
                    event_id=event_id,
                    semantic_type=semantic_type,
                    domain=source.domain,
                    source_id=source.source_id,
                    title=f"{source.title} normative claim {local_index}",
                    subject_label=subject_label,
                    predicate_label=predicate_label,
                    case_context=case_context_for_event(
                        semantic_type,
                        source,
                        unit,
                        subject_label,
                        predicate_label,
                    ),
                    query_terms=query,
                    dimension=dimension_for(source, unit),
                    value=value_slug,
                    distractor="",
                )
            )
            event_no += 1
            if len(events) >= MAX_EVENTS:
                return assign_plausible_distractors(events)
    return assign_plausible_distractors(events)


def assign_plausible_distractors(events: list[EventSpec]) -> tuple[EventSpec, ...]:
    by_source: dict[str, list[EventSpec]] = defaultdict(list)
    by_domain: dict[str, list[EventSpec]] = defaultdict(list)
    for event in events:
        by_source[event.source_id].append(event)
        by_domain[event.domain].append(event)

    fixed: list[EventSpec] = []
    for index, event in enumerate(events):
        pool = [other for other in by_source[event.source_id] if other.event_id != event.event_id]
        if not pool:
            pool = [other for other in by_domain[event.domain] if other.event_id != event.event_id]
        if not pool:
            pool = [other for other in events if other.event_id != event.event_id]
        if not pool:
            distractor = "alternative_supported_normative_claim"
        else:
            choice = pool[(index + 1) % len(pool)]
            distractor = choice.value
            if distractor == event.value or distractor.startswith(f"not_{event.value}"):
                for candidate in pool:
                    if candidate.value != event.value and not candidate.value.startswith(f"not_{event.value}"):
                        distractor = candidate.value
                        break
        fixed.append(replace(event, distractor=distractor))
    return tuple(fixed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument("--target-per-source", type=int, default=TARGET_PER_SOURCE)
    parser.add_argument("--min-ready", type=int, default=100)
    parser.add_argument("--min-score", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base.BENCHMARK = BENCHMARK
    base.OUTPUT = OUTPUT
    base.CACHE = CACHE
    base.ensure_dirs()
    source_by_id, _cache_rows = base.cache_sources(args.timeout, args.force_fetch)
    events = build_events_from_sources(source_by_id, args.target_per_source)
    if len(events) < args.min_ready:
        print(f"only {len(events)} mined candidate events; need {args.min_ready}")
        return 1
    base.EVENTS = events
    build_args = argparse.Namespace(
        timeout=args.timeout,
        force_fetch=False,
        min_score=args.min_score,
        min_ready=args.min_ready,
    )
    return base.build(build_args)


if __name__ == "__main__":
    raise SystemExit(main())
