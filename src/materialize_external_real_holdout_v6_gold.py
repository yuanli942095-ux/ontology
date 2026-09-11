from __future__ import annotations

"""Materialize adjudicated human gold into OWL and baseline candidate IDs.

This does not relabel semantic types or decisions. It only compiles already
adjudicated Repair IR into OWL and restores CAND_001/002/003 balance for the
human REPAIR set.
"""

import json
from pathlib import Path
from typing import Any

from build_external_real_holdout_v6 import (
    BENCHMARK_DIR,
    ONTOLOGY_NS,
    candidate_mapping,
    operation,
    rdf_xml,
    write_csv,
    write_json,
    write_jsonl,
)
from rfc213_direct_repair_ir import benchmark_literal_from_span, source_windows


GOLD_PATH = BENCHMARK_DIR / "private/oracle/gold-repair-ir.jsonl"
CANDIDATE_PATH = BENCHMARK_DIR / "repair-stage/candidates/external-real-candidate-template.csv"
NOTES = "Uniform baseline candidate; not available to Direct-IR inference."


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def terms_from_gold(row: dict[str, Any]) -> dict[str, str]:
    predicate = row["target"]["predicate_iri"]
    dimension = predicate.rsplit("#", 1)[-1]
    return {
        "dimension": dimension,
        "subject_iri": row["target"]["subject_iri"],
        "predicate_iri": predicate,
        "class_iri": f"{ONTOLOGY_NS}HoldoutObject",
        "sentinel_iri": f"{ONTOLOGY_NS}regressionSentinel",
    }


def neighbor_lexical(event_id: str, old_lexical: str, gold_lexical: str) -> str:
    excerpt = (BENCHMARK_DIR / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
    windows = dict(source_windows(excerpt))
    for key in ("4", "5", "2", "3", "1"):
        text = windows.get(key, "")
        if not text:
            continue
        lexical = benchmark_literal_from_span(old_lexical, text)
        if lexical != gold_lexical:
            return lexical
    return f"{old_lexical.rsplit('=', 1)[0]}=distractor_normative_claim"


def materialize() -> dict[str, Any]:
    gold_rows = read_jsonl(GOLD_PATH)
    repair_ids = sorted(row["event_id"] for row in gold_rows if row["decision"] == "REPAIR")
    after_dir = BENCHMARK_DIR / "private/ontology-after"
    after_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[dict[str, Any]] = []
    written_owl = 0
    for index, event_id in enumerate(repair_ids):
        row = next(item for item in gold_rows if item["event_id"] == event_id)
        terms = terms_from_gold(row)
        old_lexical = row["target"]["old_value"]["lexical"]
        new_lexical = row["replacement"]["new_value"]["lexical"]
        owl = rdf_xml(
            event_id=event_id,
            terms=terms,
            lexical=new_lexical,
            ontology_suffix="gold",
        )
        (after_dir / f"{event_id}-gold.owl").write_text(owl, encoding="utf-8")
        (after_dir / f"{event_id}-proposed-gold.owl").write_text(owl, encoding="utf-8")
        written_owl += 1
        mapping = candidate_mapping(index)
        variants = {
            "gold": operation(terms, old_lexical, new_lexical),
            "old": operation(terms, old_lexical, old_lexical),
            "neighbor": operation(terms, old_lexical, neighbor_lexical(event_id, old_lexical, new_lexical)),
        }
        row["proposed_baseline_candidate_id"] = mapping["gold"]
        for role in ("gold", "old", "neighbor"):
            candidate_id = mapping[role]
            candidate_operation = variants[role]
            candidates.append(
                {
                    "event_id": event_id,
                    "candidate_id": candidate_id,
                    "display_value": candidate_operation["new_value"]["lexical"],
                    "operation_json": json.dumps(
                        candidate_operation, ensure_ascii=False, separators=(",", ":")
                    ),
                    "status": "DRAFT_BASELINE_ONLY",
                    "notes": NOTES,
                }
            )
            write_json(
                BENCHMARK_DIR / "repair-stage/operations" / f"{event_id}-{candidate_id}.json",
                candidate_operation,
            )
    write_jsonl(GOLD_PATH, gold_rows)
    write_csv(CANDIDATE_PATH, candidates)
    return {
        "repair_events": len(repair_ids),
        "gold_owl_written": written_owl,
        "candidates": len(candidates),
        "gold_id_counts": {
            "CAND_001": sum(row.get("proposed_baseline_candidate_id") == "CAND_001" for row in gold_rows),
            "CAND_002": sum(row.get("proposed_baseline_candidate_id") == "CAND_002" for row in gold_rows),
            "CAND_003": sum(row.get("proposed_baseline_candidate_id") == "CAND_003" for row in gold_rows),
        },
        "relabeled": False,
        "automatic_approval": False,
    }


def main() -> int:
    result = materialize()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
