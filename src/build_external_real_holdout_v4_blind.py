from __future__ import annotations

"""Build external-real-holdout-v4-blind for post-calibration blind evaluation.

The builder uses public normative RFC text, excludes top evidence windows that
already appeared in v3-large, and marks the result as requiring independent
oracle review before freeze/evaluation.
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import build_external_real_holdout_v1 as base
import build_external_real_holdout_v3_large as v3
from build_external_real_holdout_v1 import EventSpec, SourceSpec
from build_external_real_holdout_v1_expanded import assign_plausible_distractors, normative_units
from holdout_metadata_enrichment import case_context_for, predicate_label_for
from semantic_v2_common import PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v4-blind"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v4-blind"
CACHE = PROJECT_DIR / "data" / "external-real-holdout-v1-cache"
V3_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v3-large"
TYPE_CYCLE = ("TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE")


V4_EXTRA_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("HO_HTTP_RFC9205", "http_semantics", "IETF RFC 9205", "Building Protocols with HTTP", "https://www.rfc-editor.org/rfc/rfc9205.txt"),
    SourceSpec("HO_HTTP_RFC7540", "http_messaging", "IETF RFC 7540", "HTTP/2", "https://www.rfc-editor.org/rfc/rfc7540.txt"),
    SourceSpec("HO_TLS_RFC9147", "tls_security", "IETF RFC 9147", "DTLS 1.3", "https://www.rfc-editor.org/rfc/rfc9147.txt"),
    SourceSpec("HO_TLS_RFC8879", "tls_security", "IETF RFC 8879", "TLS Certificate Compression", "https://www.rfc-editor.org/rfc/rfc8879.txt"),
    SourceSpec("HO_OAUTH_RFC7009", "oauth_authorization", "IETF RFC 7009", "OAuth 2.0 Token Revocation", "https://www.rfc-editor.org/rfc/rfc7009.txt"),
    SourceSpec("HO_OAUTH_RFC7662", "oauth_authorization", "IETF RFC 7662", "OAuth 2.0 Token Introspection", "https://www.rfc-editor.org/rfc/rfc7662.txt"),
    SourceSpec("HO_EMAIL_RFC6376", "email_authentication", "IETF RFC 6376", "DKIM Signatures", "https://www.rfc-editor.org/rfc/rfc6376.txt"),
    SourceSpec("HO_EMAIL_RFC8601", "email_authentication", "IETF RFC 8601", "Authentication-Results", "https://www.rfc-editor.org/rfc/rfc8601.txt"),
    SourceSpec("HO_DNS_RFC4034", "dns_security", "IETF RFC 4034", "DNSSEC Resource Records", "https://www.rfc-editor.org/rfc/rfc4034.txt"),
    SourceSpec("HO_DNS_RFC5155", "dns_security", "IETF RFC 5155", "DNSSEC NSEC3", "https://www.rfc-editor.org/rfc/rfc5155.txt"),
    SourceSpec("HO_JSON_RFC7396", "json_formats", "IETF RFC 7396", "JSON Merge Patch", "https://www.rfc-editor.org/rfc/rfc7396.txt"),
    SourceSpec("HO_JSON_RFC7464", "json_formats", "IETF RFC 7464", "JSON Text Sequences", "https://www.rfc-editor.org/rfc/rfc7464.txt"),
    SourceSpec("HO_URI_RFC3987", "uri_templates", "IETF RFC 3987", "Internationalized Resource Identifiers", "https://www.rfc-editor.org/rfc/rfc3987.txt"),
    SourceSpec("HO_URI_RFC7320", "uri_templates", "IETF RFC 7320", "URI Design and Ownership", "https://www.rfc-editor.org/rfc/rfc7320.txt"),
    SourceSpec("HO_IDNA_RFC5892", "internationalized_identifiers", "IETF RFC 5892", "IDNA Code Points", "https://www.rfc-editor.org/rfc/rfc5892.txt"),
    SourceSpec("HO_IDNA_RFC5893", "internationalized_identifiers", "IETF RFC 5893", "IDNA Right-to-Left Scripts", "https://www.rfc-editor.org/rfc/rfc5893.txt"),
    SourceSpec("HO_PRIV_RFC7624", "privacy_considerations", "IETF RFC 7624", "Confidentiality in the Face of Pervasive Surveillance", "https://www.rfc-editor.org/rfc/rfc7624.txt"),
    SourceSpec("HO_PRIV_RFC8280", "privacy_considerations", "IETF RFC 8280", "Research into Human Rights Protocol Considerations", "https://www.rfc-editor.org/rfc/rfc8280.txt"),
    SourceSpec("HO_SEC_RFC4101", "security_considerations", "IETF RFC 4101", "Writing Protocol Models", "https://www.rfc-editor.org/rfc/rfc4101.txt"),
    SourceSpec("HO_SEC_RFC7457", "security_considerations", "IETF RFC 7457", "Known Attacks on TLS", "https://www.rfc-editor.org/rfc/rfc7457.txt"),
)


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def unit_signature(text: str) -> str:
    return compact(text)[:480]


def first_window(text: str) -> str:
    match = re.search(r"(?s)\[SOURCE_WINDOW_1\]\s*(.*?)(?:\n\n\[SOURCE_WINDOW_2\]|\Z)", text)
    return match.group(1).strip() if match else text.strip()


def excluded_window_signatures() -> set[str]:
    signatures: set[str] = set()
    excerpt_dir = V3_BENCHMARK / "public" / "excerpts"
    if not excerpt_dir.is_dir():
        return signatures
    for path in excerpt_dir.glob("*-evidence.md"):
        signatures.add(unit_signature(first_window(path.read_text(encoding="utf-8", errors="replace"))))
    return signatures


def all_sources() -> tuple[SourceSpec, ...]:
    seen: set[str] = set()
    out: list[SourceSpec] = []
    for source in (*v3.all_sources(), *V4_EXTRA_SOURCES):
        if source.source_id in seen:
            continue
        seen.add(source.source_id)
        out.append(source)
    return tuple(out)


def ordered_keywords(text: str, limit: int = 5) -> tuple[str, ...]:
    return v3.ordered_keywords(text, limit=limit)


def slug(text: str, limit: int = 9) -> str:
    return v3.slug(text, limit=limit)


def source_skip(source: SourceSpec, old_source_skip: int, v3_extra_skip: int, v4_extra_skip: int) -> int:
    if source.source_id in {item.source_id for item in V4_EXTRA_SOURCES}:
        return v4_extra_skip
    if source.source_id in {item.source_id for item in v3.V3_EXTRA_SOURCES}:
        return v3_extra_skip
    return old_source_skip


def build_events_balanced(
    source_by_id: dict[str, dict[str, str]],
    *,
    min_per_domain: int,
    max_events: int,
    old_source_skip: int,
    v3_extra_skip: int,
    v4_extra_skip: int,
) -> tuple[EventSpec, ...]:
    by_domain: dict[str, list[SourceSpec]] = defaultdict(list)
    for source in all_sources():
        by_domain[source.domain].append(source)

    excluded = excluded_window_signatures()
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
            skip = source_skip(source, old_source_skip, v3_extra_skip, v4_extra_skip)
            for local_index, unit in enumerate(units[skip:], start=skip + 1):
                if len(domain_events) >= min_per_domain:
                    break
                if unit_signature(unit) in excluded:
                    continue
                semantic_type = TYPE_CYCLE[global_type_index % len(TYPE_CYCLE)]
                global_type_index += 1
                subject_label = v3.subject_for(source, unit)
                predicate_label = predicate_label_for(
                    semantic_type,
                    subject_label=subject_label,
                    source_title=source.title,
                    evidence_window=unit,
                )
                domain_events.append(
                    EventSpec(
                        event_id=f"H4_E{event_no:03d}",
                        semantic_type=semantic_type,
                        domain=source.domain,
                        source_id=source.source_id,
                        title=f"{source.title} v4 blind normative claim {local_index}",
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
                        dimension=v3.dimension_for(source, unit),
                        value=slug(unit),
                        distractor="",
                    )
                )
                event_no += 1
                if len(events) + len(domain_events) >= max_events:
                    break
            if len(domain_events) >= min_per_domain or len(events) + len(domain_events) >= max_events:
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
    parser.add_argument("--old-source-skip", type=int, default=45)
    parser.add_argument("--v3-extra-skip", type=int, default=24)
    parser.add_argument("--v4-extra-skip", type=int, default=0)
    parser.add_argument("--min-per-domain", type=int, default=20)
    parser.add_argument("--max-events", type=int, default=260)
    parser.add_argument("--min-ready", type=int, default=220)
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
        old_source_skip=args.old_source_skip,
        v3_extra_skip=args.v3_extra_skip,
        v4_extra_skip=args.v4_extra_skip,
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
    ready_events = [event for event in events]
    summary = {
        "benchmark": BENCHMARK.name,
        "events": len(ready_events),
        "domains": dict(sorted(Counter(event.domain for event in ready_events).items())),
        "semantic_types": dict(sorted(Counter(event.semantic_type for event in ready_events).items())),
        "source_families": len(all_sources()),
        "old_source_skip": args.old_source_skip,
        "v3_extra_skip": args.v3_extra_skip,
        "v4_extra_sources": len(V4_EXTRA_SOURCES),
        "excluded_v3_window_signatures": len(excluded_window_signatures()),
        "manual_review_required_before_freeze": True,
    }
    (OUTPUT / "v4-blind-build-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
