from __future__ import annotations

"""Build external-real-holdout-v5-blind-large.

This benchmark is intended for post-M16 blind evaluation. It writes to a new
directory, uses public RFC source text, excludes evidence windows already used
by v3-large and v4-blind, and targets more events than the 220-event v4 set.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import build_external_real_holdout_v1 as base
import build_external_real_holdout_v3_large as v3
import build_external_real_holdout_v4_blind as v4
from build_external_real_holdout_v1 import EventSpec, SourceSpec
from build_external_real_holdout_v1_expanded import assign_plausible_distractors, normative_units
from holdout_metadata_enrichment import case_context_for, predicate_label_for
from semantic_v2_common import PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v5-blind-large"
CACHE = PROJECT_DIR / "data" / "external-real-holdout-v1-cache"
TYPE_CYCLE = ("TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE")


V5_EXTRA_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("HO_HTTP_RFC9114", "http_messaging", "IETF RFC 9114", "HTTP/3", "https://www.rfc-editor.org/rfc/rfc9114.txt"),
    SourceSpec("HO_HTTP_RFC9204", "http_messaging", "IETF RFC 9204", "QPACK", "https://www.rfc-editor.org/rfc/rfc9204.txt"),
    SourceSpec("HO_HTTP_RFC6265", "http_semantics", "IETF RFC 6265", "HTTP State Management Mechanism", "https://www.rfc-editor.org/rfc/rfc6265.txt"),
    SourceSpec("HO_HTTP_RFC9457", "http_semantics", "IETF RFC 9457", "Problem Details for HTTP APIs", "https://www.rfc-editor.org/rfc/rfc9457.txt"),
    SourceSpec("HO_TLS_RFC8996", "tls_security", "IETF RFC 8996", "Deprecating TLS 1.0 and TLS 1.1", "https://www.rfc-editor.org/rfc/rfc8996.txt"),
    SourceSpec("HO_TLS_RFC9258", "tls_security", "IETF RFC 9258", "Importing External PSKs for TLS", "https://www.rfc-editor.org/rfc/rfc9258.txt"),
    SourceSpec("HO_OAUTH_RFC8628", "oauth_authorization", "IETF RFC 8628", "OAuth 2.0 Device Authorization Grant", "https://www.rfc-editor.org/rfc/rfc8628.txt"),
    SourceSpec("HO_OAUTH_RFC8705", "oauth_authorization", "IETF RFC 8705", "OAuth 2.0 Mutual-TLS Client Authentication", "https://www.rfc-editor.org/rfc/rfc8705.txt"),
    SourceSpec("HO_EMAIL_RFC8461", "email_authentication", "IETF RFC 8461", "SMTP MTA Strict Transport Security", "https://www.rfc-editor.org/rfc/rfc8461.txt"),
    SourceSpec("HO_EMAIL_RFC8617", "email_authentication", "IETF RFC 8617", "Authenticated Received Chain", "https://www.rfc-editor.org/rfc/rfc8617.txt"),
    SourceSpec("HO_DNS_RFC4509", "dns_security", "IETF RFC 4509", "Use of SHA-256 in DNSSEC DS Records", "https://www.rfc-editor.org/rfc/rfc4509.txt"),
    SourceSpec("HO_DNS_RFC6840", "dns_security", "IETF RFC 6840", "DNSSEC Clarifications", "https://www.rfc-editor.org/rfc/rfc6840.txt"),
    SourceSpec("HO_JSON_RFC7493", "json_formats", "IETF RFC 7493", "I-JSON Message Format", "https://www.rfc-editor.org/rfc/rfc7493.txt"),
    SourceSpec("HO_JSON_RFC8610", "json_formats", "IETF RFC 8610", "Concise Data Definition Language", "https://www.rfc-editor.org/rfc/rfc8610.txt"),
    SourceSpec("HO_URI_RFC6874", "uri_templates", "IETF RFC 6874", "IPv6 Zone Identifiers in URIs", "https://www.rfc-editor.org/rfc/rfc6874.txt"),
    SourceSpec("HO_URI_RFC8089", "uri_templates", "IETF RFC 8089", "The file URI Scheme", "https://www.rfc-editor.org/rfc/rfc8089.txt"),
    SourceSpec("HO_IDNA_RFC5894", "internationalized_identifiers", "IETF RFC 5894", "IDNA Background", "https://www.rfc-editor.org/rfc/rfc5894.txt"),
    SourceSpec("HO_IDNA_RFC5895", "internationalized_identifiers", "IETF RFC 5895", "IDNA Mapping Characters", "https://www.rfc-editor.org/rfc/rfc5895.txt"),
    SourceSpec("HO_PRIV_RFC6973B", "privacy_considerations", "IETF RFC 6973", "Privacy Considerations", "https://www.rfc-editor.org/rfc/rfc6973.txt"),
    SourceSpec("HO_PRIV_RFC9415", "privacy_considerations", "IETF RFC 9415", "Static Context Header Compression", "https://www.rfc-editor.org/rfc/rfc9415.txt"),
    SourceSpec("HO_SEC_RFC5280", "security_considerations", "IETF RFC 5280", "Internet X.509 Public Key Infrastructure Certificate and CRL Profile", "https://www.rfc-editor.org/rfc/rfc5280.txt"),
    SourceSpec("HO_SEC_RFC9142", "security_considerations", "IETF RFC 9142", "Key Exchange Token", "https://www.rfc-editor.org/rfc/rfc9142.txt"),
)


def evidence_signatures(benchmark: Path) -> set[str]:
    signatures: set[str] = set()
    excerpt_dir = benchmark / "public" / "excerpts"
    if not excerpt_dir.is_dir():
        return signatures
    for path in excerpt_dir.glob("*-evidence.md"):
        signatures.add(v4.unit_signature(v4.first_window(path.read_text(encoding="utf-8", errors="replace"))))
    return signatures


def excluded_window_signatures() -> set[str]:
    return evidence_signatures(v4.V3_BENCHMARK) | evidence_signatures(v4.BENCHMARK)


def all_sources() -> tuple[SourceSpec, ...]:
    seen: set[tuple[str, str]] = set()
    out: list[SourceSpec] = []
    for source in (*v4.all_sources(), *V5_EXTRA_SOURCES):
        key = (source.family, source.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(source)
    return tuple(out)


def source_skip(source: SourceSpec, old_source_skip: int, v3_extra_skip: int, v4_extra_skip: int, v5_extra_skip: int) -> int:
    if source.source_id in {item.source_id for item in V5_EXTRA_SOURCES}:
        return v5_extra_skip
    if source.source_id in {item.source_id for item in v4.V4_EXTRA_SOURCES}:
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
    v5_extra_skip: int,
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
            skip = source_skip(source, old_source_skip, v3_extra_skip, v4_extra_skip, v5_extra_skip)
            for local_index, unit in enumerate(units[skip:], start=skip + 1):
                if len(domain_events) >= min_per_domain:
                    break
                if v4.unit_signature(unit) in excluded:
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
                        event_id=f"H5_E{event_no:03d}",
                        semantic_type=semantic_type,
                        domain=source.domain,
                        source_id=source.source_id,
                        title=f"{source.title} v5 blind-large normative claim {local_index}",
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
                        query_terms=(unit[:180],) + v3.ordered_keywords(unit, limit=5),
                        dimension=v3.dimension_for(source, unit),
                        value=v3.slug(unit),
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
    parser.add_argument("--old-source-skip", type=int, default=70)
    parser.add_argument("--v3-extra-skip", type=int, default=45)
    parser.add_argument("--v4-extra-skip", type=int, default=24)
    parser.add_argument("--v5-extra-skip", type=int, default=0)
    parser.add_argument("--min-per-domain", type=int, default=24)
    parser.add_argument("--max-events", type=int, default=280)
    parser.add_argument("--min-ready", type=int, default=260)
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
        v5_extra_skip=args.v5_extra_skip,
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
        "status": "DRAFT_REQUIRES_ORACLE_REVIEW_AND_FREEZE",
        "events": len(events),
        "domains": dict(sorted(Counter(event.domain for event in events).items())),
        "semantic_types": dict(sorted(Counter(event.semantic_type for event in events).items())),
        "source_families": len(all_sources()),
        "excluded_prior_window_signatures": len(excluded_window_signatures()),
        "target_relation_to_previous": "larger_than_external-real-holdout-v4-blind_220_events",
        "manual_review_required_before_freeze": True,
    }
    (OUTPUT / "v5-blind-large-build-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
