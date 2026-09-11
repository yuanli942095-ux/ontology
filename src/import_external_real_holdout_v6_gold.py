from __future__ import annotations

"""Import adjudicated dual annotations into private human gold. Never auto-approves."""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from build_external_real_holdout_v6 import BENCHMARK_DIR, write_jsonl
from external_real_holdout_v6_annotation import (
    ANSWER_FIELDS,
    AnnotationWorkflowError,
    _read_completed_sheet,
    _validate_distinct_annotators,
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def merge_annotations(
    rows_a: dict[str, dict[str, str]],
    rows_b: dict[str, dict[str, str]],
    adjudication: list[dict[str, str]],
) -> dict[str, dict[str, str]]:
    resolved: dict[tuple[str, str], str] = {}
    for row in adjudication:
        value = row.get("adjudicated_value", "").strip()
        adjudicator = row.get("adjudicator_id", "").strip()
        if not value or not adjudicator:
            raise AnnotationWorkflowError(
                f"unadjudicated disagreement {row.get('event_id')} {row.get('field')}"
            )
        if adjudicator.casefold() in {
            rows_a[next(iter(rows_a))]["annotator_id"].casefold(),
            rows_b[next(iter(rows_b))]["annotator_id"].casefold(),
        }:
            raise AnnotationWorkflowError("adjudicator must be a third person")
        resolved[(row["event_id"], row["field"])] = value
    merged: dict[str, dict[str, str]] = {}
    for event_id, row_a in rows_a.items():
        row_b = rows_b[event_id]
        item = {"event_id": event_id}
        for field in ANSWER_FIELDS:
            if row_a[field] == row_b[field]:
                item[field] = row_a[field]
            elif (event_id, field) in resolved:
                item[field] = resolved[(event_id, field)]
            else:
                raise AnnotationWorkflowError(f"missing adjudication for {event_id} {field}")
        merged[event_id] = item
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotator-a", type=Path, required=True)
    parser.add_argument("--annotator-b", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument(
        "--proposed-gold",
        type=Path,
        default=BENCHMARK_DIR / "private/construction/proposed-gold-repair-ir.jsonl",
    )
    args = parser.parse_args()
    rows_a, annotator_a = _read_completed_sheet(args.annotator_a, "Annotator A")
    rows_b, annotator_b = _read_completed_sheet(args.annotator_b, "Annotator B")
    _validate_distinct_annotators(annotator_a, annotator_b)
    adjudication = read_csv(args.adjudication) if args.adjudication.is_file() else []
    merged = merge_annotations(rows_a, rows_b, adjudication)
    proposed = {row["event_id"]: row for row in read_jsonl(args.proposed_gold)}
    if set(merged) != set(proposed):
        raise AnnotationWorkflowError("imported events do not match proposed gold event set")
    gold_rows = []
    for event_id, human in merged.items():
        machine = proposed[event_id]
        gold_rows.append(
            {
                "event_id": event_id,
                "decision": human["decision"],
                "semantic_type": human["semantic_type"],
                "gold_source_window": human["gold_source_window"],
                "operation": "UPDATE_LITERAL" if human["decision"] == "REPAIR" else "",
                "target": {
                    "subject_iri": machine["target"]["subject_iri"],
                    "predicate_iri": machine["target"]["predicate_iri"],
                    "old_value": {
                        "kind": "literal",
                        "lexical": human["old_lexical"],
                        "datatype": human["old_datatype"],
                    },
                },
                "replacement": {
                    "new_value": {
                        "kind": "literal",
                        "lexical": human["new_lexical"],
                        "datatype": human["new_datatype"],
                    }
                }
                if human["decision"] == "REPAIR"
                else {},
                "target_cq": human["target_cq"],
                "non_target_cq": human["non_target_cq"],
                "annotator_a_id": annotator_a,
                "annotator_b_id": annotator_b,
                "status": "HUMAN_ADJUDICATED_GOLD",
            }
        )
    output = BENCHMARK_DIR / "private/oracle/gold-repair-ir.jsonl"
    write_jsonl(output, gold_rows)
    print(json.dumps({"imported": len(gold_rows), "path": str(output), "automatic_approval": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
