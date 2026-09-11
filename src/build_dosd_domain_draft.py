from __future__ import annotations

import argparse
import difflib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dosd_multidomain_common import (
    SourcePair,
    compact_token,
    extract_text,
    fetch,
    load_source_pairs,
    operation_json,
    read_csv,
    owl_text,
    paragraphs,
    sha256_bytes,
    sha256_text,
    write_csv,
)


ROOT = Path(__file__).resolve().parents[1]
TYPE_TARGETS = {
    "medical": {"TEMPORAL_VERSION": 90, "GENERAL_RULE_EXCEPTION": 90, "CROSS_SENTENCE_SCOPE": 84},
    "legal": {"TEMPORAL_VERSION": 80, "GENERAL_RULE_EXCEPTION": 94, "CROSS_SENTENCE_SCOPE": 90},
}
PREFIX = {"medical": "MED", "legal": "LAW"}

NORMATIVE = re.compile(r"(?i)\b(shall|must|should|recommend|offer|consider|advise|ensure|refer|do not|may|required|prohibited|eligible|except|unless)\b")
EXCEPTION = re.compile(r"(?i)\b(except|unless|however|provided that|subject to|does not apply|contraindicat)\b")
SCOPE = re.compile(r"(?i)\b(adult|child|patient|person|organisation|undertaking|provider|aged|scope|appl(?:y|ies|icable)|only|including|excluding)\b")
TEMPORAL = re.compile(r"(?i)\b(effective|from|until|version|update|replace|supersed|amend|date|year|month|day)\b")


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower(), autojunk=False).ratio()


def changed_units(pair: SourcePair, old_text: str, new_text: str) -> list[dict[str, str | float]]:
    old_blocks = paragraphs(old_text)
    new_blocks = paragraphs(new_text)
    old_signatures = {re.sub(r"\W+", "", value.lower()) for value in old_blocks}
    token_index: dict[str, set[int]] = defaultdict(set)
    for old_index, old in enumerate(old_blocks):
        for token in set(re.findall(r"[a-z][a-z0-9-]{4,}", old.lower())):
            token_index[token].add(old_index)
    candidates: list[dict[str, str | float]] = []
    for new in new_blocks:
        if not NORMATIVE.search(new):
            continue
        signature = re.sub(r"\W+", "", new.lower())
        if signature in old_signatures:
            continue
        overlap: Counter[int] = Counter()
        for token in set(re.findall(r"[a-z][a-z0-9-]{4,}", new.lower())):
            overlap.update(token_index.get(token, set()))
        shortlist = [index for index, _ in overlap.most_common(20)]
        pool = [old_blocks[i] for i in shortlist if abs(len(old_blocks[i]) - len(new)) <= max(900, len(new))]
        if not pool:
            continue
        old = max(pool, key=lambda value: similarity(value, new))
        score = similarity(old, new)
        if not 0.18 <= score <= 0.96:
            continue
        candidates.append({"pair_id": pair.pair_id, "old_span": old, "new_span": new, "alignment_score": score})
    return candidates


def classify(item: dict[str, str | float]) -> str:
    text = f"{item['old_span']} {item['new_span']}"
    scores = {
        "TEMPORAL_VERSION": len(TEMPORAL.findall(text)),
        "GENERAL_RULE_EXCEPTION": len(EXCEPTION.findall(text)) * 3,
        "CROSS_SENTENCE_SCOPE": len(SCOPE.findall(text)) * 2,
    }
    return max(scores, key=lambda key: (scores[key], key))


def fetch_pair(pair: SourcePair, cache: Path, timeout: int) -> tuple[dict[str, str], dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for role, url in (("old", pair.old_url), ("new", pair.new_url)):
        text_path = cache / f"{pair.pair_id}-{role}.txt"
        cached_raw = next((path for path in sorted(cache.glob(f"{pair.pair_id}-{role}.*")) if path != text_path), None)
        if text_path.is_file() and cached_raw:
            raw_path = cached_raw
            raw = raw_path.read_bytes()
            content_type = "application/xml" if raw.lstrip().startswith(b"<?xml") else "application/octet-stream"
            text = text_path.read_text(encoding="utf-8", errors="replace")
        else:
            raw, content_type = fetch(url, timeout)
            suffix = ".pdf" if raw.startswith(b"%PDF") or "pdf" in content_type else (".xml" if "xml" in content_type else ".html")
            raw_path = cache / f"{pair.pair_id}-{role}{suffix}"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(raw)
            text = extract_text(raw_path, content_type)
            text_path.write_text(text, encoding="utf-8")
        result[role] = {
            "url": url,
            "raw_path": str(raw_path.relative_to(ROOT)),
            "text_path": str(text_path.relative_to(ROOT)),
            "raw_sha256": sha256_bytes(raw),
            "text_sha256": sha256_text(text),
            "content_type": content_type,
            "text": text,
        }
    return result["old"], result["new"]


def choose_events(items: list[dict[str, str | float]], targets: dict[str, int]) -> list[dict[str, str | float]]:
    by_type: dict[str, list[dict[str, str | float]]] = defaultdict(list)
    for item in items:
        by_type[classify(item)].append(item)
    selected: list[dict[str, str | float]] = []
    used: set[tuple[str, str]] = set()
    used_evidence: set[tuple[str, str]] = set()
    for semantic_type, target in sorted(targets.items(), key=lambda item: len(by_type[item[0]])):
        ranked = sorted(by_type[semantic_type], key=lambda row: (abs(float(row["alignment_score"]) - 0.62), str(row["pair_id"])))
        available = [row for row in ranked if (str(row["pair_id"]), str(row["new_span"])) not in used]
        if len(available) < target:
            fallback = [row for row in items if (str(row["pair_id"]), str(row["new_span"])) not in used]
            seen_available = {(str(row["pair_id"]), str(row["new_span"])) for row in available}
            available.extend(
                row
                for row in sorted(fallback, key=lambda row: abs(float(row["alignment_score"]) - 0.62))
                if (str(row["pair_id"]), str(row["new_span"])) not in seen_available
            )
        if len(available) < target:
            raise RuntimeError(f"insufficient unique changes for {semantic_type}: found={len(available)} target={target}; add source pairs")
        chosen = available[:target]
        selected.extend({**row, "assigned_semantic_type": semantic_type} for row in chosen)
        used.update((str(row["pair_id"]), str(row["new_span"])) for row in chosen)
    random.Random(20260907).shuffle(selected)
    return selected


def choose_verified_pool_events(
    items: list[dict[str, str | float]], targets: dict[str, int], total: int
) -> list[dict[str, str | float]]:
    """Select only pre-verified alignments; never relabel a row to fill a quota."""
    by_type: dict[str, list[dict[str, str | float]]] = defaultdict(list)
    for item in items:
        by_type[str(item["assigned_semantic_type"])].append(item)
    selected: list[dict[str, str | float]] = []
    used: set[tuple[str, str]] = set()
    used_evidence: set[tuple[str, str]] = set()
    for semantic_type, target in targets.items():
        ranked = sorted(by_type[semantic_type], key=lambda row: float(row["alignment_score"]), reverse=True)
        for row in ranked[:target]:
            key = (str(row["pair_id"]), str(row["provision_id"]))
            evidence_key = (str(row["old_span"]), str(row["new_span"]))
            if key not in used and evidence_key not in used_evidence:
                selected.append(row)
                used.add(key)
                used_evidence.add(evidence_key)
    if len(selected) < total:
        remaining = sorted(
            (
                row
                for rows in by_type.values()
                for row in rows
                if (str(row["pair_id"]), str(row["provision_id"])) not in used
                and (str(row["old_span"]), str(row["new_span"])) not in used_evidence
            ),
            key=lambda row: float(row["alignment_score"]),
            reverse=True,
        )
        for row in remaining:
            if len(selected) >= total:
                break
            key = (str(row["pair_id"]), str(row["provision_id"]))
            evidence_key = (str(row["old_span"]), str(row["new_span"]))
            if key in used or evidence_key in used_evidence:
                continue
            selected.append(row)
            used.add(key)
            used_evidence.add(evidence_key)
    if len(selected) < total:
        raise RuntimeError(f"verified pool has only {len(selected)} unique aligned provisions; requested={total}")
    random.Random(20260907).shuffle(selected)
    return selected


def build(args: argparse.Namespace) -> int:
    corpus = args.corpus
    benchmark = (args.benchmark_dir or ROOT / "benchmark" / f"dosd-{corpus}-v1-draft").resolve()
    output = (args.output_dir or ROOT / "output" / f"dosd-{corpus}-v1-draft").resolve()
    cache = ROOT / "data" / "dosd-source-cache" / corpus
    pairs = load_source_pairs(args.source_pairs)
    fetched: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
    fetch_rows: list[dict[str, str]] = []
    all_changes: list[dict[str, str | float]] = []
    for pair in pairs:
        try:
            old, new = fetch_pair(pair, cache, args.timeout)
            fetched[pair.pair_id] = (old, new)
            changes = [] if args.change_pool else changed_units(pair, old["text"], new["text"])
            all_changes.extend(changes)
            fetch_rows.append({"pair_id": pair.pair_id, "status": "SUCCESS", "old_sha256": old["text_sha256"], "new_sha256": new["text_sha256"], "change_candidates": str(len(changes)), "error": ""})
        except Exception as exc:
            fetch_rows.append({"pair_id": pair.pair_id, "status": "FAILED", "old_sha256": "", "new_sha256": "", "change_candidates": "0", "error": f"{type(exc).__name__}: {exc}"})
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "source-fetch-audit.csv", fetch_rows)
    if args.change_pool:
        if corpus != "legal":
            raise RuntimeError("--change-pool currently supports the anchored legal source format only")
        qualified = [
            {
                "pair_id": row["source_pair"],
                "provision_id": row["provision_id"],
                "old_span": row["old_span"],
                "new_span": row["new_span"],
                "alignment_score": float(row["sequence_ratio"]),
                "assigned_semantic_type": row["provisional_semantic_type"],
            }
            for row in read_csv(args.change_pool)
            if row.get("quality_gate") == "SAME_OFFICIAL_PROVISION_ID"
        ]
        selected = choose_verified_pool_events(qualified, TYPE_TARGETS[corpus], sum(TYPE_TARGETS[corpus].values()))
    else:
        qualified_by_pair: dict[str, list[dict[str, str | float]]] = defaultdict(list)
        for change in all_changes:
            if float(change["alignment_score"]) >= args.min_alignment:
                qualified_by_pair[str(change["pair_id"])].append(change)
        qualified: list[dict[str, str | float]] = []
        for rows in qualified_by_pair.values():
            qualified.extend(sorted(rows, key=lambda row: float(row["alignment_score"]), reverse=True)[: args.max_per_source_pair])
        selected = choose_events(qualified, TYPE_TARGETS[corpus])
    pair_by_id = {pair.pair_id: pair for pair in pairs}
    events: list[dict[str, str]] = []
    documents: list[dict[str, str]] = []
    retrieval: list[dict[str, str]] = []
    candidates: list[dict[str, str]] = []
    construction: list[dict[str, str]] = []
    annotations: list[dict[str, str]] = []
    source_families: list[dict[str, str]] = []
    now = datetime.now(timezone.utc).isoformat()
    selected_by_pair: dict[str, list[dict[str, str | float]]] = defaultdict(list)
    for selected_item in selected:
        selected_by_pair[str(selected_item["pair_id"])].append(selected_item)
    for pair in pairs:
        source_families.append({**pair.__dict__, "status": "SUCCESS" if pair.pair_id in fetched else "FAILED"})
    for index, item in enumerate(selected, 1):
        event_id = f"{PREFIX[corpus]}_E{index:03d}"
        pair = pair_by_id[str(item["pair_id"])]
        old_meta, new_meta = fetched[pair.pair_id]
        old_span = str(item["old_span"])
        new_span = str(item["new_span"])
        semantic_type = str(item.get("assigned_semantic_type") or classify(item))
        dimension = compact_token(f"{pair.title} {semantic_type}", 8)
        old_value = f"{dimension}={compact_token(old_span)}_{sha256_text(old_span)[:8]}"
        new_value = f"{dimension}={compact_token(new_span)}_{sha256_text(new_span)[:8]}"
        same_pair = selected_by_pair[pair.pair_id]
        local_position = next(i for i, row in enumerate(same_pair) if row is item)
        distractor_source = same_pair[(local_position + 1) % len(same_pair)] if len(same_pair) > 1 else selected[index % len(selected)]
        distractor_span = str(distractor_source["new_span"])
        distractor = f"{dimension}={compact_token(distractor_span)}_{sha256_text(distractor_span)[:8]}"
        if distractor in {old_value, new_value}:
            distractor = f"{distractor}_{sha256_text(str(distractor_source['new_span']))[:8]}"
        values = [("OLD", old_value), ("NEW", new_value), ("DISTRACTOR", distractor)]
        random.Random(int(sha256_text(event_id)[:12], 16)).shuffle(values)
        role_to_id: dict[str, str] = {}
        predicate = re.sub(r"[^a-z0-9_]+", "_", dimension.lower())[:80]
        for cand_index, (role, value) in enumerate(values, 1):
            candidate_id = f"CAND_{cand_index:03d}"
            role_to_id[role] = candidate_id
            candidates.append({"event_id": event_id, "candidate_id": candidate_id, "display_value": value, "operation_json": operation_json(event_id, predicate, old_value, value), "status": "READY", "notes": "Candidate generated before annotation; role and oracle are not exposed to the repair method."})
        old_doc = f"DOC_{event_id}_OLD"
        new_doc = f"DOC_{event_id}_NEW"
        excerpt = f"[OLD_VERSION]\n{old_span}\n\n[NEW_VERSION]\n{new_span}\n"
        excerpt_path = benchmark / "public" / "excerpts" / f"{event_id}-evidence.md"
        excerpt_path.parent.mkdir(parents=True, exist_ok=True)
        excerpt_path.write_text(excerpt, encoding="utf-8")
        for role, doc_id, version, meta in (("OLD", old_doc, pair.old_version, old_meta), ("NEW", new_doc, pair.new_version, new_meta)):
            file_name = f"{doc_id}.txt"
            target = benchmark / "public" / "documents" / file_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(meta["text"], encoding="utf-8")
            documents.append({"document_id": doc_id, "event_id": event_id, "file_name": file_name, "authority": "100", "effective_from": version, "effective_to": "", "issuer": pair.issuer, "document_type": "public_versioned_normative_document", "source_type": "EXTERNAL_PUBLIC_FULL_TEXT", "source_url": meta["url"], "retrieved_at": now, "raw_sha256": meta["raw_sha256"], "text_sha256": meta["text_sha256"], "extraction_mode": "full_text_fetch_plus_version_diff", "cache_path": meta["text_path"], "window_sha256": sha256_text(old_span if role == "OLD" else new_span), "sha256": meta["text_sha256"], "status": "READY"})
        mutant_path = benchmark / "repair-stage" / "mutants" / f"{event_id}.owl"
        mutant_path.parent.mkdir(parents=True, exist_ok=True)
        mutant_path.write_text(owl_text(event_id, predicate, old_value), encoding="utf-8")
        events.append({"event_id": event_id, "split": "annotation", "semantic_type": semantic_type, "domain": corpus, "title": f"{pair.title}: candidate drift {index}", "case_context": f"Compare official versions {pair.old_version} and {pair.new_version}; determine the evidence-grounded ontology repair.", "subject_label": pair.title, "predicate_label": dimension.replace("_", " "), "value_kind": "literal_string", "allowed_min": "", "allowed_max": "", "document_ids": f"{old_doc}|{new_doc}", "source_owl": str(mutant_path.relative_to(ROOT)), "source_url": pair.new_url, "retrieved_at": now, "status": "PENDING_DUAL_REVIEW", "notes": "Automatically mined version difference; not gold until dual review and adjudication.", "lexical_status": "VERSION_PAIR_ALIGNED", "support_status": "PENDING_DUAL_REVIEW", "semantic_support": "PENDING", "support_adjudication_method": "dual_independent_annotation", "support_checked_before_model_run": "false", "support_gate_version": "dosd-version-pair-v1"})
        retrieval.append({"event_id": event_id, "domain": corpus, "source_id": pair.pair_id, "query_terms": f"{pair.title}|{semantic_type}", "retrieval_status": "RETRIEVAL_READY", "current_score": str(item["alignment_score"]), "previous_score": "", "current_windows": "1", "previous_windows": "1", "window_sha256": sha256_text(excerpt), "fallback_used": "false", "candidate_used": "false", "oracle_used": "false", "note_used": "false", "errors": ""})
        construction.append({"event_id": event_id, "pair_id": pair.pair_id, "provision_id": str(item.get("provision_id", "")), "automated_alignment_score": str(item["alignment_score"]), "construction_new_candidate_id": role_to_id["NEW"], "status": "PRIVATE_CONSTRUCTION_HINT_NOT_GOLD"})
        annotations.append({"event_id": event_id, "source_pair": pair.pair_id, "old_span": old_span, "new_span": new_span, "candidate_1": values[0][1], "candidate_2": values[1][1], "candidate_3": values[2][1], "annotator_name": "", "annotator_drift_type": "", "annotator_ontology_change": "", "annotator_gold_candidate": "", "evidence_sufficient": "", "version_relation_valid": "", "notes": "", "completed_at": ""})
    write_csv(benchmark / "public" / "events" / "external-real-event-template.csv", events)
    write_csv(benchmark / "public" / "documents" / "external-real-document-template.csv", documents)
    write_csv(benchmark / "public" / "retrieval" / "external-real-v8-event-retrieval.csv", retrieval)
    write_csv(benchmark / "public" / "retrieval" / "external-real-v8-grounded-source-families.csv", source_families)
    write_csv(benchmark / "repair-stage" / "candidates" / "external-real-candidate-template.csv", candidates)
    write_csv(benchmark / "private" / "construction" / "construction-provenance.csv", construction)
    write_csv(benchmark / "private" / "annotation" / "annotation-sheet-A.csv", annotations)
    write_csv(benchmark / "private" / "annotation" / "annotation-sheet-B.csv", annotations)
    summary = {"benchmark": benchmark.name, "status": "DRAFT_REQUIRES_DUAL_REVIEW", "events": len(events), "type_counts": dict(Counter(row["semantic_type"] for row in events)), "source_pairs_registered": len(pairs), "source_pairs_fetched": len(fetched), "qualified_changes": len(qualified), "min_alignment": args.min_alignment, "max_events_per_source_pair": args.max_per_source_pair, "candidate_rows": len(candidates), "oracle_rows": 0, "freeze_allowed": False}
    (output / "build-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", choices=("medical", "legal"), required=True)
    parser.add_argument("--source-pairs", type=Path, required=True)
    parser.add_argument("--benchmark-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--min-alignment", type=float, default=0.45)
    parser.add_argument("--max-per-source-pair", type=int, default=80)
    parser.add_argument("--change-pool", type=Path, help="Pre-validated alignment pool. Rows are never relabelled to satisfy type quotas.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(build(parse_args()))
