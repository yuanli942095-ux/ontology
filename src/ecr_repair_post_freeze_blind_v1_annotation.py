from __future__ import annotations

"""Dual-blind annotation workflow. Constructor proposed Gold is never copied into templates."""

import argparse
import csv
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

from ecr_repair_post_freeze_blind_v1_common import BENCHMARK_DIR, write_csv, write_json


TEMPLATE_FIELDS = (
    "event_id",
    "decision",
    "partition",
    "semantic_type",
    "supporting_window_ids",
    "old_lexical",
    "old_datatype",
    "new_lexical",
    "new_datatype",
    "target_cq_pass",
    "confidence",
    "reason",
    "annotator_id",
)
ANSWER_FIELDS = tuple(field for field in TEMPLATE_FIELDS if field not in {"event_id", "annotator_id"})
KAPPA_FIELDS = ("decision", "partition", "semantic_type", "new_lexical", "supporting_window_ids")


class AnnotationWorkflowError(ValueError):
    """Raised when the workflow cannot continue honestly."""


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _validate_distinct(annotator_a_id: str, annotator_b_id: str) -> tuple[str, str]:
    annotator_a_id = annotator_a_id.strip()
    annotator_b_id = annotator_b_id.strip()
    if not annotator_a_id or not annotator_b_id:
        raise AnnotationWorkflowError("annotator IDs must be non-empty")
    if _normalized(annotator_a_id) == _normalized(annotator_b_id):
        raise AnnotationWorkflowError("Annotator A and Annotator B must be different people")
    if _normalized(annotator_a_id) in {"constructor", "agent", "model"}:
        raise AnnotationWorkflowError("ANN_A must be a human identifier")
    return annotator_a_id, annotator_b_id


def _event_ids(path: Path) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not raw.strip():
            continue
        record = json.loads(raw)
        event_id = str(record.get("event_id", "")).strip()
        if not event_id or event_id in seen:
            raise AnnotationWorkflowError(f"invalid event_id on line {line_number}")
        seen.add(event_id)
        ids.append(event_id)
    if not ids:
        raise AnnotationWorkflowError("no events")
    return ids


def _blank_rows(event_ids: Sequence[str], annotator_id: str) -> list[dict[str, str]]:
    return [
        {**{field: "" for field in TEMPLATE_FIELDS}, "event_id": event_id, "annotator_id": annotator_id}
        for event_id in event_ids
    ]


def _enforce_round2_quality_gate(event_ids: Sequence[str]) -> None:
    round2_ids = {
        f"BLIND_E{index:03d}" for index in range(17, 81)
    }
    requested = set(event_ids) & round2_ids
    if not requested:
        return
    review_path = BENCHMARK_DIR / "private/construction/round2-64-quality-review-v3.csv"
    if not review_path.is_file():
        raise AnnotationWorkflowError("round-2 quality review is missing")
    with review_path.open(encoding="utf-8-sig", newline="") as handle:
        reviews = {row.get("event_id", "").strip(): row for row in csv.DictReader(handle)}
    missing = sorted(requested - set(reviews))
    rejected = sorted(
        event_id
        for event_id in requested
        if reviews.get(event_id, {}).get("reviewer_decision", "").strip().upper() != "ACCEPT"
    )
    if missing or rejected:
        raise AnnotationWorkflowError(
            "round-2 annotation is blocked until every requested event is ACCEPT; "
            f"missing={len(missing)} not_accepted={len(rejected)}"
        )


def generate_templates(
    events_path: Path,
    output_dir: Path,
    annotator_a_id: str,
    annotator_b_id: str,
    *,
    seed: int = 20260909,
) -> tuple[Path, Path]:
    annotator_a_id, annotator_b_id = _validate_distinct(annotator_a_id, annotator_b_id)
    event_ids = _event_ids(events_path)
    _enforce_round2_quality_gate(event_ids)
    rng_a = random.Random(seed)
    rng_b = random.Random(seed + 17)
    order_a = list(event_ids)
    order_b = list(event_ids)
    rng_a.shuffle(order_a)
    rng_b.shuffle(order_b)
    if order_a == order_b and len(order_a) > 1:
        order_b = list(reversed(order_b))
    path_a = output_dir / f"annotator-{annotator_a_id}-template.csv"
    path_b = output_dir / f"annotator-{annotator_b_id}-template.csv"
    write_csv(path_a, _blank_rows(order_a, annotator_a_id), TEMPLATE_FIELDS)
    write_csv(path_b, _blank_rows(order_b, annotator_b_id), TEMPLATE_FIELDS)
    return path_a, path_b


def _read_completed(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_id = {}
    for row in rows:
        event_id = row.get("event_id", "").strip()
        if not event_id:
            continue
        missing = [field for field in ANSWER_FIELDS if not str(row.get(field, "")).strip()]
        if missing:
            raise AnnotationWorkflowError(f"{path.name}:{event_id} missing {missing}")
        by_id[event_id] = row
    return by_id


def cohens_kappa(pairs: list[tuple[str, str]]) -> float:
    if not pairs:
        return 0.0
    n = len(pairs)
    agree = sum(1 for left, right in pairs if left == right)
    po = agree / n
    labels = sorted({item for pair in pairs for item in pair})
    count_a = Counter(left for left, _ in pairs)
    count_b = Counter(right for _, right in pairs)
    pe = sum((count_a[label] / n) * (count_b[label] / n) for label in labels)
    if math.isclose(1.0, pe):
        return 1.0 if math.isclose(po, 1.0) else 0.0
    return (po - pe) / (1.0 - pe)


def analyze_annotations(path_a: Path, path_b: Path, output_dir: Path) -> dict[str, Any]:
    rows_a = _read_completed(path_a)
    rows_b = _read_completed(path_b)
    if set(rows_a) != set(rows_b):
        raise AnnotationWorkflowError("annotator sheets do not cover the same events")
    disagreements = []
    kappas = {}
    for field in KAPPA_FIELDS:
        pairs = [
            (_normalized(rows_a[event_id][field]), _normalized(rows_b[event_id][field]))
            for event_id in sorted(rows_a)
        ]
        kappas[field] = round(cohens_kappa(pairs), 4)
        agreement = sum(left == right for left, right in pairs) / len(pairs)
        kappas[f"{field}_raw_agreement"] = round(agreement, 4)
        for event_id in sorted(rows_a):
            if _normalized(rows_a[event_id][field]) != _normalized(rows_b[event_id][field]):
                disagreements.append(
                    {
                        "event_id": event_id,
                        "field": field,
                        "annotator_a_id": rows_a[event_id]["annotator_id"],
                        "annotator_a_value": rows_a[event_id][field],
                        "annotator_b_id": rows_b[event_id]["annotator_id"],
                        "annotator_b_value": rows_b[event_id][field],
                    }
                )
    write_csv(
        output_dir / "disagreements.csv",
        disagreements,
        (
            "event_id",
            "field",
            "annotator_a_id",
            "annotator_a_value",
            "annotator_b_id",
            "annotator_b_value",
        ),
    )
    adjudication_rows = [
        {**row, "adjudicated_value": "", "adjudicator_id": "ADJ_C", "adjudication_reason": ""}
        for row in disagreements
    ]
    write_csv(
        BENCHMARK_DIR / "private/adjudication/adjudication-template.csv",
        adjudication_rows,
        (
            "event_id",
            "field",
            "annotator_a_id",
            "annotator_a_value",
            "annotator_b_id",
            "annotator_b_value",
            "adjudicated_value",
            "adjudicator_id",
            "adjudication_reason",
        ),
    )
    report = {
        "events": len(rows_a),
        "disagreement_rows": len(disagreements),
        "disagreement_events": len({row["event_id"] for row in disagreements}),
        "kappa": kappas,
        "status": "AWAITING_ADJUDICATION" if disagreements else "AGREED_PENDING_IMPORT",
    }
    write_json(output_dir / "agreement-report.json", report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate")
    generate.add_argument("--annotator-a-id", required=True)
    generate.add_argument("--annotator-b-id", required=True)
    generate.add_argument(
        "--events",
        type=Path,
        default=BENCHMARK_DIR / "public/events/events.jsonl",
    )
    generate.add_argument(
        "--output-dir",
        type=Path,
        default=BENCHMARK_DIR / "private/annotation",
    )
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--sheet-a", type=Path, required=True)
    analyze.add_argument("--sheet-b", type=Path, required=True)
    analyze.add_argument(
        "--output-dir",
        type=Path,
        default=BENCHMARK_DIR / "private/annotation",
    )
    args = parser.parse_args(argv)
    if args.command == "generate":
        path_a, path_b = generate_templates(
            args.events, args.output_dir, args.annotator_a_id, args.annotator_b_id
        )
        print(json.dumps({"annotator_a": str(path_a), "annotator_b": str(path_b)}, indent=2))
        return 0
    report = analyze_annotations(args.sheet_a, args.sheet_b, args.output_dir)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
