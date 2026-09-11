from __future__ import annotations

"""Build an independent v2 smoke hold-out from later public RFC units.

The builder reuses the v1 public-source machinery but skips the early normative
units used by v1-expanded. It creates a separate benchmark directory and keeps
the same staged layout expected by the repair/evaluation scripts.
"""

import argparse
import re
from dataclasses import replace
from pathlib import Path

import build_external_real_holdout_v1 as base
from build_external_real_holdout_v1 import EventSpec, SourceSpec
from build_external_real_holdout_v1_expanded import assign_plausible_distractors, normative_units
from holdout_metadata_enrichment import case_context_for, predicate_label_for
from semantic_v2_common import PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v2-smoke"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v2-smoke"
CACHE = PROJECT_DIR / "data" / "external-real-holdout-v1-cache"
TYPE_CYCLE = ("TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE")


def keywords(text: str, limit: int = 5) -> tuple[str, ...]:
    return tuple(base.tokens(text) - {"the", "and", "for", "with", "from", "that", "this"})[:limit] or ("requirement",)


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


def build_events(source_by_id: dict[str, dict[str, str]], *, target_per_source: int, skip_per_source: int, max_events: int) -> tuple[EventSpec, ...]:
    events: list[EventSpec] = []
    event_no = 1
    for source in base.SOURCES:
        cache = source_by_id.get(source.source_id, {})
        text_path = PROJECT_DIR / cache.get("text_path", "")
        if not text_path.is_file():
            continue
        units = normative_units(text_path.read_text(encoding="utf-8", errors="replace"))
        for local_index, unit in enumerate(units[skip_per_source : skip_per_source + target_per_source], start=skip_per_source + 1):
            semantic_type = TYPE_CYCLE[(event_no - 1) % len(TYPE_CYCLE)]
            event_id = f"H2_E{event_no:03d}"
            subject_label = subject_for(source, unit)
            predicate_label = predicate_label_for(
                semantic_type,
                subject_label=subject_label,
                source_title=source.title,
                evidence_window=unit,
            )
            events.append(
                EventSpec(
                    event_id=event_id,
                    semantic_type=semantic_type,
                    domain=source.domain,
                    source_id=source.source_id,
                    title=f"{source.title} v2 normative claim {local_index}",
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
            if len(events) >= max_events:
                return assign_plausible_distractors(events)
    return assign_plausible_distractors(events)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument("--target-per-source", type=int, default=3)
    parser.add_argument("--skip-per-source", type=int, default=12)
    parser.add_argument("--max-events", type=int, default=60)
    parser.add_argument("--min-ready", type=int, default=30)
    parser.add_argument("--min-score", type=float, default=2.0)
    args = parser.parse_args()

    base.BENCHMARK = BENCHMARK
    base.OUTPUT = OUTPUT
    base.CACHE = CACHE
    base.ensure_dirs()
    source_by_id, _cache_rows = base.cache_sources(args.timeout, args.force_fetch)
    events = build_events(
        source_by_id,
        target_per_source=args.target_per_source,
        skip_per_source=args.skip_per_source,
        max_events=args.max_events,
    )
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
