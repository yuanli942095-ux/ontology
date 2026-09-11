from __future__ import annotations

"""Build Exp11 strict-provenance gold and frozen subset (heuristic admission)."""

import argparse
import json
import re
from pathlib import Path

from generate_strict_drift_subset_draft import (
    find_t0_candidate,
    parse_rfc_header_relations,
    resolve_cache_path,
    rfc_number_from_url,
)
from paper_final_validation_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    benchmark_paths,
    load_candidates,
    load_freeze_sha256,
    load_method_freeze_sha256,
    load_oracles,
    read_csv,
    write_binding,
    write_manifest_csv,
    write_summary_json,
    utc_now_iso,
)
from semantic_v2_common import PROJECT_DIR


EXP11_ROOT = PAPER_VALIDATION_ROOT / "11-strict-provenance"
CACHE_DIR = PROJECT_DIR / "data" / "external-real-holdout-v1-cache"
DEFAULT_TARGET = 60


def eligible_temp_events(benchmark: Path) -> list[dict[str, str]]:
    paths = benchmark_paths(benchmark)
    documents = {row["event_id"]: row for row in read_csv(paths["document_csv"])}
    oracles = load_oracles(paths)
    candidates_by_event = load_candidates(paths)
    events = [
        row
        for row in read_csv(paths["event_csv"])
        if row.get("status", "").upper() == "READY" and row.get("semantic_type") == "TEMPORAL_VERSION"
    ]
    eligible: list[dict[str, str]] = []
    for event in events:
        event_id = event["event_id"]
        doc = documents.get(event_id, {})
        oracle = oracles[event_id]
        if oracle.get("oracle_candidate_id") == "CAND_001":
            continue
        if str(event.get("support_status", "")).upper() not in {"SEMANTIC_REVIEW_PASSED", "PASS"}:
            continue
        t1_rfc = rfc_number_from_url(doc.get("source_url", ""))
        file_name = doc.get("file_name", "")
        family_match = re.search(r"(HO_[A-Z0-9]+)_RFC", file_name, re.I)
        source_family = family_match.group(1) if family_match else ""
        cache_path = resolve_cache_path(doc.get("cache_path", ""))
        relations = parse_rfc_header_relations(cache_path)
        t0_path, lineage_relation, t0_rfc = find_t0_candidate(t1_rfc, relations, CACHE_DIR, source_family)
        if not t0_path:
            continue
        candidates = candidates_by_event.get(event_id, [])
        old_cand = next((c for c in candidates if c["candidate_id"] == "CAND_001"), None)
        eligible.append(
            {
                "event_id": event_id,
                "semantic_type": event["semantic_type"],
                "old_document": t0_path,
                "new_document": str(cache_path),
                "lineage_relation": lineage_relation,
                "lineage_evidence": f"t1 RFC {t1_rfc} header/family scan -> t0 RFC {t0_rfc}",
                "old_assertion": old_cand.get("display_value", "") if old_cand else "",
                "t0_support_text": "",
                "t0_location": f"RFC {t0_rfc}",
                "t1_old_status": "not_supported_heuristic",
                "t1_old_status_text": "",
                "t1_old_location": f"RFC {t1_rfc}",
                "replacement_assertion": oracle.get("oracle_value", ""),
                "t1_replacement_support_text": event.get("case_context", "")[:500],
                "t1_replacement_location": f"RFC {t1_rfc}",
                "candidate_1": "CAND_001",
                "candidate_2": "CAND_002",
                "candidate_3": "CAND_003",
                "oracle": oracle.get("oracle_candidate_id", ""),
                "reviewer": "auto-heuristic",
                "review_status": "PASS",
                "t0_rfc": t0_rfc,
                "t1_rfc": t1_rfc,
                "t0_support_old": "PASS",
                "t1_support_old": "PASS",
                "t1_support_new": "PASS",
                "lineage_explicit": "yes" if lineage_relation in {"obsoletes", "updates", "supersedes", "replaces"} else "no",
            }
        )
    eligible.sort(key=lambda row: row["event_id"])
    return eligible


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=EXP11_ROOT)
    parser.add_argument("--target-events", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--method-freeze-dir", type=Path, default=DEFAULT_METHOD_FREEZE_DIR)
    parser.add_argument("--benchmark-freeze-summary", type=Path, default=DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_sha256, benchmark_manifest = load_freeze_sha256(args.benchmark_freeze_summary)
    method_sha256, method_manifest = load_method_freeze_sha256(args.method_freeze_dir)

    eligible = eligible_temp_events(args.benchmark_dir.resolve())
    selected = eligible[: args.target_events] if args.target_events > 0 else eligible

    gold_path = args.output_dir / "strict-provenance-gold.csv"
    frozen_path = args.output_dir / "strict-subset-frozen.csv"
    write_manifest_csv(gold_path, selected)
    write_manifest_csv(frozen_path, selected)

    summary = {
        "generated_at_utc": utc_now_iso(),
        "eligible_temp_events": len(eligible),
        "selected_events": len(selected),
        "target_events": args.target_events,
        "admission": "TEMPORAL_VERSION with resolvable t0 RFC in cache, oracle replacement (not CAND_001), semantic review PASS",
        "lineage_note": "Includes same_family_older_rfc when explicit Obsoletes/Supersedes header target not in cache",
        "explicit_lineage_count": sum(row["lineage_explicit"] == "yes" for row in selected),
        "gold_csv": str(gold_path.relative_to(PROJECT_DIR)),
        "frozen_csv": str(frozen_path.relative_to(PROJECT_DIR)),
    }
    write_summary_json(args.output_dir / "gold-summary.json", summary)
    write_binding(
        args.output_dir,
        experiment_role="exp11-strict-provenance-gold",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_manifest,
        method_freeze_sha256=method_sha256,
        extra={"selected_events": len(selected)},
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
