from __future__ import annotations

"""Build external-real-holdout-v3-large: earlier large hold-out construction.

Uses normative units after v1-expanded (first 12/source) and v2-smoke (next 3/source).
Adds v3-only RFC sources for thin domains. Targets >=200 events with >=20/domain.
This builder is retained because later builders import its source utilities; it
is not the current final blind benchmark entrypoint.
"""

import argparse
import json
import re
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import build_external_real_holdout_v1 as base
from build_external_real_holdout_v1 import EventSpec, SourceSpec
from build_external_real_holdout_v1_expanded import assign_plausible_distractors, normative_units
from holdout_metadata_enrichment import case_context_for, predicate_label_for
from semantic_v2_common import PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v3-large"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v3-large"
CACHE = PROJECT_DIR / "data" / "external-real-holdout-v1-cache"
TYPE_CYCLE = ("TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE")

# v3-only sources (not used by v1/v2 builders) to fill thin domains / add families.
V3_EXTRA_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        "HO_JSON_RFC8785",
        "json_formats",
        "IETF RFC 8785",
        "JSON Canonicalization Scheme",
        "https://www.rfc-editor.org/rfc/rfc8785.txt",
    ),
    SourceSpec(
        "HO_JSON_RFC9535",
        "json_formats",
        "IETF RFC 9535",
        "JSONPath",
        "https://www.rfc-editor.org/rfc/rfc9535.txt",
    ),
    SourceSpec(
        "HO_PRIV_RFC8288",
        "privacy_considerations",
        "IETF RFC 8288",
        "Web Linking",
        "https://www.rfc-editor.org/rfc/rfc8288.txt",
    ),
    SourceSpec(
        "HO_PRIV_RFC8615",
        "privacy_considerations",
        "IETF RFC 8615",
        "Well-Known URIs",
        "https://www.rfc-editor.org/rfc/rfc8615.txt",
    ),
    SourceSpec(
        "HO_URI_RFC7595",
        "uri_templates",
        "IETF RFC 7595",
        "URI Scheme Guidelines",
        "https://www.rfc-editor.org/rfc/rfc7595.txt",
    ),
    SourceSpec(
        "HO_URI_RFC8820",
        "uri_templates",
        "IETF RFC 8820",
        "URI Design and Ownership",
        "https://www.rfc-editor.org/rfc/rfc8820.txt",
    ),
    SourceSpec(
        "HO_SEC_RFC3552",
        "security_considerations",
        "IETF RFC 3552",
        "Writing RFC Security Considerations",
        "https://www.rfc-editor.org/rfc/rfc3552.txt",
    ),
    SourceSpec(
        "HO_SEC_RFC4949",
        "security_considerations",
        "IETF RFC 4949",
        "Internet Security Glossary",
        "https://www.rfc-editor.org/rfc/rfc4949.txt",
    ),
)

BASE_SOURCE_IDS = {source.source_id for source in base.SOURCES}
V1_USED_PER_SOURCE = 12
V2_USED_PER_SOURCE = 3
DEFAULT_SKIP_PER_BASE_SOURCE = V1_USED_PER_SOURCE + V2_USED_PER_SOURCE  # 15


def ordered_keywords(text: str, limit: int = 5) -> tuple[str, ...]:
    stop = {
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
    raw = [token.lower().strip("-") for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text)]
    result: list[str] = []
    for token in raw:
        if token in stop or token in result:
            continue
        result.append(token)
        if len(result) >= limit:
            break
    return tuple(result or raw[:limit] or ("requirement",))


def slug(text: str, limit: int = 9) -> str:
    return "_".join(ordered_keywords(text, limit=limit))


def subject_for(source: SourceSpec, unit: str) -> str:
    return f"{source.title} {' '.join(ordered_keywords(unit, limit=3))}"


def dimension_for(source: SourceSpec, unit: str) -> str:
    prefix = re.sub(r"[^a-z0-9]+", "_", source.title.lower()).strip("_")[:28]
    return f"{prefix}_{'_'.join(ordered_keywords(unit, limit=3))}_claim".strip("_")


def all_sources() -> tuple[SourceSpec, ...]:
    return base.SOURCES + V3_EXTRA_SOURCES


def skip_for_source(source: SourceSpec, skip_per_base_source: int) -> int:
    if source.source_id in BASE_SOURCE_IDS:
        return skip_per_base_source
    return 0


def build_events_balanced(
    source_by_id: dict[str, dict[str, str]],
    *,
    min_per_domain: int,
    max_events: int,
    skip_per_base_source: int,
) -> tuple[EventSpec, ...]:
    by_domain: dict[str, list[SourceSpec]] = defaultdict(list)
    for source in all_sources():
        by_domain[source.domain].append(source)

    events: list[EventSpec] = []
    event_no = 1
    global_type_index = 0

    for domain in sorted(by_domain):
        domain_events: list[EventSpec] = []
        for source in by_domain[domain]:
            cache = source_by_id.get(source.source_id, {})
            text_path = PROJECT_DIR / cache.get("text_path", "")
            if not text_path.is_file():
                continue
            units = normative_units(text_path.read_text(encoding="utf-8", errors="replace"))
            skip = skip_for_source(source, skip_per_base_source)
            for local_index, unit in enumerate(units[skip:], start=skip + 1):
                if len(domain_events) >= min_per_domain:
                    break
                semantic_type = TYPE_CYCLE[global_type_index % len(TYPE_CYCLE)]
                global_type_index += 1
                subject_label = subject_for(source, unit)
                predicate_label = predicate_label_for(
                    semantic_type,
                    subject_label=subject_label,
                    source_title=source.title,
                    evidence_window=unit,
                )
                domain_events.append(
                    EventSpec(
                        event_id=f"H3_E{event_no:03d}",
                        semantic_type=semantic_type,
                        domain=source.domain,
                        source_id=source.source_id,
                        title=f"{source.title} v3 normative claim {local_index}",
                        subject_label=subject_label,
                        predicate_label=predicate_label,
                        case_context=case_context_for(
                            semantic_type=semantic_type,
                            domain=source.domain,
                            subject_label=subject_label,
                            predicate_label=predicate_label,
                            source_family=source.family,
                            source_title=source.title,
                            source_url=source.url,
                            evidence_window=unit,
                        ),
                        query_terms=(unit[:180],) + ordered_keywords(unit, limit=5),
                        dimension=dimension_for(source, unit),
                        value=slug(unit),
                        distractor="",
                    )
                )
                event_no += 1
                if len(events) + len(domain_events) >= max_events:
                    break
            if len(domain_events) >= min_per_domain:
                break
        events.extend(domain_events)
        if len(events) >= max_events:
            break

    return assign_plausible_distractors(events)


def cache_all_sources(timeout: int, force: bool) -> tuple[dict[str, dict[str, str]], list[dict[str, object]]]:
    original = base.SOURCES
    try:
        base.SOURCES = all_sources()
        return base.cache_sources(timeout, force)
    finally:
        base.SOURCES = original


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument("--skip-per-base-source", type=int, default=DEFAULT_SKIP_PER_BASE_SOURCE)
    parser.add_argument("--min-per-domain", type=int, default=20)
    parser.add_argument("--max-events", type=int, default=300)
    parser.add_argument("--min-ready", type=int, default=200)
    parser.add_argument("--min-score", type=float, default=2.0)
    args = parser.parse_args()

    base.BENCHMARK = BENCHMARK
    base.OUTPUT = OUTPUT
    base.CACHE = CACHE
    base.ensure_dirs()
    source_by_id, _cache_rows = cache_all_sources(args.timeout, args.force_fetch)
    events = build_events_balanced(
        source_by_id,
        min_per_domain=args.min_per_domain,
        max_events=args.max_events,
        skip_per_base_source=args.skip_per_base_source,
    )
    if len(events) < args.min_ready:
        print(json.dumps({"built": len(events), "required": args.min_ready, "status": "insufficient"}, indent=2))
        return 1

    base.SOURCES = all_sources()
    base.EVENTS = events
    build_args = argparse.Namespace(
        timeout=args.timeout,
        force_fetch=False,
        min_score=args.min_score,
        min_ready=args.min_ready,
    )
    result = base.build(build_args)
    summary = {
        "benchmark": BENCHMARK.name,
        "events": len(events),
        "domains": len({event.domain for event in events}),
        "skip_per_base_source": args.skip_per_base_source,
        "extra_sources": len(V3_EXTRA_SOURCES),
    }
    (OUTPUT / "v3-large-build-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
