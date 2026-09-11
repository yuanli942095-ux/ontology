from __future__ import annotations

"""Diagnose 75 RETRIEVAL_FAILED Auto Policy LIGHT attempts.

Does not call Qwen, does not change retrieval, does not read Oracle/candidates.
"""

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from external_real_v8_layout import BenchmarkLayout
from run_auto_policy_v3_natural_evidence_robustness import (
    configure_benchmark,
    document_rows_by_event,
    provenance_source_context,
    query_terms,
    read_csv,
)
from semantic_v2_common import PROJECT_DIR, write_csv


DETAILS = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.4-css-ir"
    / "auto-policy-v4.4-css-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
RAW_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-v8-grounded"
    / "preformal-r3"
    / "raw_window_metadata_light"
)
OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v4.4-retrieval-failed"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def nonempty_text(path: Path) -> bool:
    if not path.is_file():
        return False
    return bool(path.read_text(encoding="utf-8", errors="replace").strip())


def source_status_for(docs: list[dict[str, str]], document_dir: Path) -> str:
    if not docs:
        return "MISSING"
    existing = 0
    cache_ok = 0
    for doc in docs:
        if (document_dir / doc.get("file_name", "")).is_file():
            existing += 1
        cache = str(doc.get("cache_path") or "").strip()
        if cache and (PROJECT_DIR / cache).is_file():
            cache_ok += 1
    if existing == 0:
        return "MISSING"
    if existing < len(docs):
        return "PARTIAL"
    if cache_ok == 0 and any(str(doc.get("cache_path") or "").strip() for doc in docs):
        return "DOCS_OK_CACHE_MISSING"
    return "EXISTS"


def classify(
    *,
    public_ready: bool,
    public_windows: int,
    public_errors: str,
    source_status: str,
    mapping_ok: bool,
    excerpt_ok: bool,
    model_window_ok: bool,
    light_selected: int,
    light_terms: int,
) -> tuple[str, str]:
    if source_status == "MISSING":
        return "R-A", "official source/cache files missing"
    if not mapping_ok:
        return "R-B", "event has no READY document mapping"
    if public_ready and public_windows > 0 and not model_window_ok:
        return "R-F", "public retrieval READY with windows, Auto Policy input empty"
    if not public_ready and public_errors:
        return "R-C", "metadata query reported errors and public retrieval not READY"
    if not public_ready and public_windows <= 0:
        if light_terms == 0:
            return "R-G", "LIGHT metadata produced no query terms"
        return "R-C", "metadata query returned no public windows"
    if public_windows > 0 and not excerpt_ok and not model_window_ok:
        return "R-E", "public windows counted but excerpt/window text empty"
    if light_selected == 0 and public_windows > 0:
        return "R-D", "paragraphs existed but Auto Policy score filter dropped them"
    if model_window_ok:
        return "R-F", "retrieval marked failed after a nonempty window was built"
    return "R-G", "LIGHT metadata did not uniquely locate source content"


def main() -> int:
    configure_benchmark(
        "external-real-v8-grounded",
        output_dir=Path("output/external-real-v8-grounded/preformal-r3"),
    )
    layout = BenchmarkLayout()
    events = {row["event_id"]: row for row in read_csv(layout.event_csv)}
    retrieval = {row["event_id"]: row for row in read_csv(layout.retrieval_csv)}
    alignment = {row["event_id"]: row for row in read_csv(layout.alignment_csv)}
    docs_by_event = document_rows_by_event()
    details = [
        row
        for row in csv.DictReader(DETAILS.open(encoding="utf-8-sig"))
        if row["generation_status"] == "RETRIEVAL_FAILED" or row["ir_status"] == "RETRIEVAL_FAILED"
    ]

    rows_out: list[dict[str, Any]] = []
    replay_cache: dict[str, dict[str, Any]] = {}
    for row in details:
        event_id = row["event_id"]
        event = events[event_id]
        public = retrieval.get(event_id, {})
        docs = docs_by_event.get(event_id, [])
        raw = load_json(PROJECT_DIR / row["raw_output_file"])
        evidence_name = Path(str(raw.get("candidate_blind_file") or "")).name
        evidence_path = RAW_DIR / "candidate-blind-evidence" / evidence_name
        excerpt_path = layout.public_excerpts / f"{event_id}-evidence.md"
        public_status = str(public.get("retrieval_status") or "")
        public_windows = int(public.get("current_windows") or 0)
        public_errors = str(public.get("errors") or "").strip()
        public_ready = public_status == "RETRIEVAL_READY" and public_windows > 0
        model_windows = int(raw.get("retrieved_window_count") or 0)
        model_window_ok = model_windows > 0 and nonempty_text(evidence_path)
        excerpt_ok = nonempty_text(excerpt_path)
        source_status = source_status_for(docs, layout.public_documents)
        mapping_ok = bool(docs)
        if event_id not in replay_cache:
            light = provenance_source_context(event, docs, metadata_mode="light")
            full = provenance_source_context(event, docs, metadata_mode="full")
            replay_cache[event_id] = {
                "light_status": light.retrieval_status,
                "light_windows": light.retrieved_window_count,
                "full_status": full.retrieval_status,
                "full_windows": full.retrieved_window_count,
                "light_terms": len(query_terms(event, "light")),
                "full_terms": len(query_terms(event, "full")),
            }
        replay = replay_cache[event_id]
        subtype, reason = classify(
            public_ready=public_ready,
            public_windows=public_windows,
            public_errors=public_errors,
            source_status=source_status,
            mapping_ok=mapping_ok,
            excerpt_ok=excerpt_ok,
            model_window_ok=model_window_ok,
            light_selected=int(replay["light_windows"]),
            light_terms=int(replay["light_terms"]),
        )
        rows_out.append(
            {
                "event_id": event_id,
                "run": row["run"],
                "semantic_type": row["semantic_type"],
                "domain": row["domain"],
                "source_status": source_status,
                "retrieval_status": public_status or "MISSING",
                "query_status": "PUBLIC_OK" if public_ready else ("PUBLIC_ERROR" if public_errors else "PUBLIC_MISS"),
                "window_status": "NONEMPTY" if model_window_ok else "EMPTY",
                "failure_subtype": subtype,
                "reason": reason,
                "public_ready": public_ready,
                "public_current_windows": public_windows,
                "public_previous_windows": public.get("previous_windows", ""),
                "public_errors": public_errors,
                "public_query_mode": public.get("query_mode", ""),
                "public_query_fields": public.get("query_fields", ""),
                "excerpt_nonempty": excerpt_ok,
                "document_rows": len(docs),
                "alignment_status": alignment.get(event_id, {}).get("alignment_status", ""),
                "alignment_source_id": alignment.get(event_id, {}).get("source_id", ""),
                "model_retrieval_status": raw.get("retrieval_status", ""),
                "model_retrieved_doc_count": raw.get("retrieved_doc_count", 0),
                "model_retrieved_window_count": model_windows,
                "model_source_urls": "|".join(raw.get("source_urls") or []),
                "qwen_ran": bool(raw.get("eval_count") or raw.get("prompt_eval_count") or raw.get("response")),
                "light_replay_status": replay["light_status"],
                "light_replay_windows": replay["light_windows"],
                "full_replay_status": replay["full_status"],
                "full_replay_windows": replay["full_windows"],
                "light_term_count": replay["light_terms"],
                "full_term_count": replay["full_terms"],
            }
        )

    counts = Counter(item["failure_subtype"] for item in rows_out)
    four = {
        "public_retrieval_ready": sum(bool(item["public_ready"]) for item in rows_out),
        "source_exists": sum(item["source_status"] in {"EXISTS", "DOCS_OK_CACHE_MISSING"} for item in rows_out),
        "query_executed_ok": sum(item["query_status"] == "PUBLIC_OK" for item in rows_out),
        "qwen_window_nonempty": sum(item["window_status"] == "NONEMPTY" for item in rows_out),
        "previous_source_query_note": sum(bool(item["public_errors"]) for item in rows_out),
    }
    event_rows = []
    by_event: dict[str, list[dict[str, Any]]] = {}
    for item in rows_out:
        by_event.setdefault(item["event_id"], []).append(item)
    for event_id, items in sorted(by_event.items()):
        first = items[0]
        event_rows.append(
            {
                "event_id": event_id,
                "n_runs": len(items),
                "domain": first["domain"],
                "semantic_type": first["semantic_type"],
                "source_status": first["source_status"],
                "retrieval_status": first["retrieval_status"],
                "query_status": first["query_status"],
                "window_status": first["window_status"],
                "failure_subtype": first["failure_subtype"],
                "public_current_windows": first["public_current_windows"],
                "excerpt_nonempty": first["excerpt_nonempty"],
                "light_replay_status": first["light_replay_status"],
                "light_replay_windows": first["light_replay_windows"],
                "full_replay_status": first["full_replay_status"],
                "full_replay_windows": first["full_replay_windows"],
                "qwen_ran_any": any(item["qwen_ran"] for item in items),
            }
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT_DIR / "retrieval-failed-details.csv", rows_out)
    write_csv(OUTPUT_DIR / "retrieval-failed-by-event.csv", event_rows)
    summary = {
        "n_attempts": len(rows_out),
        "n_events": len(event_rows),
        "subtype_attempts": dict(counts),
        "subtype_events": dict(Counter(item["failure_subtype"] for item in event_rows)),
        "four_stats_attempts": four,
        "qwen_ran": sum(item["qwen_ran"] for item in rows_out),
        "public_ready_but_model_empty": sum(
            bool(item["public_ready"]) and item["window_status"] == "EMPTY" for item in rows_out
        ),
        "full_replay_would_retrieve": sum(item["full_replay_status"] == "RETRIEVED" for item in event_rows),
        "light_replay_retrieves": sum(item["light_replay_status"] == "RETRIEVED" for item in event_rows),
        "by_domain": dict(Counter(item["domain"] for item in event_rows)),
        "by_semantic_type": dict(Counter(item["semantic_type"] for item in event_rows)),
        "full_replay_event_ids": [item["event_id"] for item in event_rows if item["full_replay_status"] == "RETRIEVED"],
        "next_action": "Fix Auto Policy input wiring to consume public READY windows (R-F). Do not change retrieval ranking yet.",
    }
    (OUTPUT_DIR / "retrieval-failed-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
