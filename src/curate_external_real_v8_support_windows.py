from __future__ import annotations

"""Curation-only: relocate raw windows inside the same cached official source.

Gold conclusion tokens are used to find the supporting paragraph. They are not
written into public excerpts. This is dataset QC, not model inference.
"""

import argparse
from collections import defaultdict
from pathlib import Path

import audit_external_real_v3_naturalized_evidence_support as support
import build_external_real_v8_grounded as v8
import gate_external_real_v8_grounded as gate
from external_real_v8_layout import BenchmarkLayout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="curate v8 support windows from cached full text")
    parser.add_argument("--only", default="")
    parser.add_argument("--query-mode", choices=("full", "light"), default="full")
    return parser.parse_args()


def locate_windows(
    event: dict[str, str],
    family: dict[str, str],
    cache: dict[str, dict[str, str]],
    allowed_values: list[str],
    mode: str,
) -> dict:
    current_units, current_record, current_error = v8.cached_units(family["source_url"], cache)
    previous_units, previous_record, previous_error = v8.cached_units(family.get("old_source_url", ""), cache)
    gold_text = " ".join(gate.expand_conclusion_values(allowed_values))
    gold_tokens = gate.tokens_with_cjk_grams(gold_text) | v8.query_tokens(event, mode)
    current_windows, current_score, current_err = rank_units(gold_tokens, current_units)
    previous_windows, previous_score, previous_err = rank_units(gold_tokens, previous_units)
    current_windows = v8.sanitize_windows(current_windows)
    previous_windows = v8.sanitize_windows(previous_windows)
    errors = [item for item in (current_error, previous_error, current_err, previous_err) if item]
    status = "RETRIEVAL_READY" if current_windows else "RETRIEVAL_FAILED"
    return {
        "current_windows": current_windows,
        "previous_windows": previous_windows,
        "current_score": current_score,
        "previous_score": previous_score,
        "current_record": current_record,
        "previous_record": previous_record,
        "status": status,
        "errors": " | ".join(errors),
        "fallback_used": False,
        "candidate_used": False,
        "oracle_used": False,
        "note_used": False,
    }


def rank_units(
    query: set[str],
    units: list[tuple[str, set[str]]],
    limit: int = v8.WINDOW_LIMIT,
) -> tuple[list[str], float, str]:
    if not query or not units:
        return [], 0.0, "empty query or units"
    ranked: list[tuple[float, str, set[str]]] = []
    total = sum(v8.v5.token_weight(token) for token in query) or 1.0
    for unit, unit_tokens in units:
        overlap = query & unit_tokens
        if not overlap:
            continue
        score = sum(v8.v5.token_weight(token) for token in overlap) / total
        ranked.append((score, unit, unit_tokens))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected: list[str] = []
    selected_token_sets: list[set[str]] = []
    for _score, unit, unit_tokens in ranked:
        if any(
            len(unit_tokens & existing) / max(1, len(unit_tokens | existing)) > 0.75
            for existing in selected_token_sets
        ):
            continue
        selected.append(unit)
        selected_token_sets.append(unit_tokens)
        if len(selected) == limit:
            break
    if not selected:
        return [], 0.0, "no gold-located unit"
    unit_order = [unit for unit, _tokens in units]
    index = {unit: position for position, unit in enumerate(unit_order)}
    with_neighbors: list[str] = []
    seen: set[str] = set()
    for unit in selected:
        position = index.get(unit)
        if position is None:
            continue
        for neighbor in unit_order[max(0, position - 1) : min(len(unit_order), position + 2)]:
            if neighbor in seen:
                continue
            seen.add(neighbor)
            with_neighbors.append(neighbor)
    return with_neighbors, round(ranked[0][0], 4), ""


def main() -> int:
    args = parse_args()
    only = v8.parse_only(args.only)
    layout = BenchmarkLayout(v8.DST)
    events = v8.read_csv(layout.event_csv)
    docs = v8.read_csv(layout.document_csv)
    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for doc in docs:
        docs_by_event[doc["event_id"]].append(doc)
    registry = v8.load_registry(layout.registry_csv)
    alignments = {row["event_id"]: row for row in v8.read_csv(layout.alignment_csv)}
    cache = v8.load_cache(v8.reuse_v6_cache())
    policy_dir = layout.rules if layout.rules.is_dir() else gate.DEFAULT_POLICY_DIR
    selected = [
        event
        for event in events
        if (not only or event["event_id"] in only)
        and str(event.get("status", "")).upper() != "READY"
    ]
    retrievals = []
    for event in selected:
        event_id = event["event_id"]
        source_id = v8.locked_source_id(event, alignments.get(event_id, {}), registry)
        family = registry[source_id]
        policy = policy_dir / f"{event_id}-formal-policy.json"
        allowed, _error = support.selected_policy_values(policy) if policy.is_file() else ([], "")
        retrieved = locate_windows(event, family, cache, allowed, args.query_mode)
        v8.write_event_files(event, docs_by_event[event_id], family, retrieved)
        row = v8.retrieval_row(event, source_id, family, retrieved, args.query_mode)
        row["curation_window_repair"] = True
        retrievals.append(row)
        event["status"] = "DRAFT"
    existing = [row for row in v8.read_csv(layout.retrieval_csv) if row["event_id"] not in {event["event_id"] for event in selected}]
    merged = existing + retrievals
    merged.sort(key=lambda row: row["event_id"])
    v8.write_csv(layout.event_csv, events)
    v8.write_csv(layout.document_csv, docs)
    v8.write_csv(layout.retrieval_csv, merged)
    print(
        {
            "curated": len(retrievals),
            "retrieval_ready": sum(row["retrieval_status"] == "RETRIEVAL_READY" for row in retrievals),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
