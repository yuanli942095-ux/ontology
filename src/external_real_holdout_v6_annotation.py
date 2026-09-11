from __future__ import annotations

"""Dual-blind annotation workflow for external-real-holdout-v6-direct-ir-blind."""

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence


DATASET_ID = "external-real-holdout-v6-direct-ir-blind"
PROJECT_DIR = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / DATASET_ID
DEFAULT_EVENTS = BENCHMARK_DIR / "public" / "events" / "events.jsonl"
DEFAULT_PROPOSED_GOLD = (
    BENCHMARK_DIR / "private" / "construction" / "proposed-gold-repair-ir.jsonl"
)
DEFAULT_OUTPUT_DIR = BENCHMARK_DIR / "private" / "annotation"

TEMPLATE_FIELDS = (
    "event_id",
    "semantic_type",
    "gold_source_window",
    "target_subject",
    "target_predicate",
    "old_lexical",
    "old_datatype",
    "new_lexical",
    "new_datatype",
    "decision",
    "target_cq",
    "non_target_cq",
    "confidence",
    "supporting_span",
    "reason",
    "annotator_id",
)
ANSWER_FIELDS = tuple(
    field for field in TEMPLATE_FIELDS if field not in {"event_id", "annotator_id"}
)
DISAGREEMENT_FIELDS = (
    "event_id",
    "field",
    "annotator_a_id",
    "annotator_a_value",
    "annotator_b_id",
    "annotator_b_value",
)
ADJUDICATION_FIELDS = (
    *DISAGREEMENT_FIELDS,
    "adjudicated_value",
    "adjudicator_id",
    "adjudication_reason",
)


class AnnotationWorkflowError(ValueError):
    """Raised when the workflow cannot safely continue."""


def _normalized_identity(value: str) -> str:
    return " ".join(value.split()).casefold()


def _validate_distinct_annotators(annotator_a_id: str, annotator_b_id: str) -> tuple[str, str]:
    annotator_a_id = annotator_a_id.strip()
    annotator_b_id = annotator_b_id.strip()
    if not annotator_a_id or not annotator_b_id:
        raise AnnotationWorkflowError("annotator IDs must be explicitly provided and non-empty")
    if _normalized_identity(annotator_a_id) == _normalized_identity(annotator_b_id):
        raise AnnotationWorkflowError("Annotator A and Annotator B must be different people")
    return annotator_a_id, annotator_b_id


def _read_jsonl_event_ids(path: Path, label: str) -> list[str]:
    if not path.is_file():
        raise AnnotationWorkflowError(f"{label} file is missing: {path}")
    event_ids: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise AnnotationWorkflowError(
                    f"{label} has invalid JSON on line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise AnnotationWorkflowError(
                    f"{label} line {line_number} must contain a JSON object"
                )
            event_id = str(record.get("event_id", "")).strip()
            if not event_id:
                raise AnnotationWorkflowError(
                    f"{label} line {line_number} has no non-empty event_id"
                )
            if event_id in seen:
                raise AnnotationWorkflowError(f"{label} contains duplicate event_id {event_id!r}")
            seen.add(event_id)
            event_ids.append(event_id)
    if not event_ids:
        raise AnnotationWorkflowError(f"{label} contains no events")
    return event_ids


def _write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _blank_template_rows(event_ids: Sequence[str], annotator_id: str) -> list[dict[str, str]]:
    return [
        {
            **{field: "" for field in TEMPLATE_FIELDS},
            "event_id": event_id,
            "annotator_id": annotator_id,
        }
        for event_id in event_ids
    ]


def generate_templates(
    events_path: Path,
    proposed_gold_path: Path,
    output_dir: Path,
    annotator_a_id: str,
    annotator_b_id: str,
    *,
    seed: int = 20260909,
) -> tuple[Path, Path]:
    """Generate two differently ordered templates without copying private judgments."""

    annotator_a_id, annotator_b_id = _validate_distinct_annotators(
        annotator_a_id, annotator_b_id
    )
    public_ids = _read_jsonl_event_ids(events_path, "public events")
    proposed_ids = _read_jsonl_event_ids(proposed_gold_path, "proposed gold")
    if set(public_ids) != set(proposed_ids):
        missing_private = sorted(set(public_ids) - set(proposed_ids))
        missing_public = sorted(set(proposed_ids) - set(public_ids))
        raise AnnotationWorkflowError(
            "public/proposed-gold event sets differ "
            f"(missing from proposed gold: {missing_private}; missing from public: {missing_public})"
        )
    if len(public_ids) < 2:
        raise AnnotationWorkflowError(
            "at least two events are required to produce different random orders"
        )

    canonical_ids = sorted(public_ids)
    order_a = canonical_ids.copy()
    order_b = canonical_ids.copy()
    random.Random(seed).shuffle(order_a)
    random.Random(f"{seed}:{DATASET_ID}:B").shuffle(order_b)
    if order_a == order_b:
        order_b = order_b[1:] + order_b[:1]

    path_a = output_dir / "annotator-a-template.csv"
    path_b = output_dir / "annotator-b-template.csv"
    _write_csv(path_a, TEMPLATE_FIELDS, _blank_template_rows(order_a, annotator_a_id))
    _write_csv(path_b, TEMPLATE_FIELDS, _blank_template_rows(order_b, annotator_b_id))
    return path_a, path_b


def _read_completed_sheet(path: Path, sheet_label: str) -> tuple[dict[str, dict[str, str]], str]:
    if not path.is_file():
        raise AnnotationWorkflowError(f"{sheet_label} is missing: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise AnnotationWorkflowError(f"{sheet_label} has no CSV header")
        missing_columns = [field for field in TEMPLATE_FIELDS if field not in reader.fieldnames]
        if missing_columns:
            raise AnnotationWorkflowError(
                f"{sheet_label} is missing required columns: {missing_columns}"
            )
        extra_columns = [field for field in reader.fieldnames if field not in TEMPLATE_FIELDS]
        if extra_columns:
            raise AnnotationWorkflowError(
                f"{sheet_label} has unexpected columns: {extra_columns}"
            )
        rows = list(reader)

    if not rows:
        raise AnnotationWorkflowError(f"{sheet_label} contains no annotation rows")
    by_event: dict[str, dict[str, str]] = {}
    annotator_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        cleaned = {field: str(row.get(field, "") or "").strip() for field in TEMPLATE_FIELDS}
        empty_fields = [field for field, value in cleaned.items() if not value]
        if empty_fields:
            raise AnnotationWorkflowError(
                f"{sheet_label} row {row_number} is incomplete; empty fields: {empty_fields}"
            )
        event_id = cleaned["event_id"]
        if event_id in by_event:
            raise AnnotationWorkflowError(
                f"{sheet_label} contains duplicate event_id {event_id!r}"
            )
        by_event[event_id] = cleaned
        annotator_ids.add(cleaned["annotator_id"])

    normalized_ids = {_normalized_identity(value) for value in annotator_ids}
    if len(normalized_ids) != 1:
        raise AnnotationWorkflowError(
            f"{sheet_label} mixes annotator IDs: {sorted(annotator_ids)}"
        )
    return by_event, next(iter(annotator_ids))


def cohen_kappa(labels_a: Sequence[str], labels_b: Sequence[str]) -> float:
    """Return unweighted Cohen's kappa, including 1.0 for constant perfect agreement."""

    if len(labels_a) != len(labels_b) or not labels_a:
        raise AnnotationWorkflowError("Cohen kappa requires equal non-empty label sequences")
    total = len(labels_a)
    observed = sum(a == b for a, b in zip(labels_a, labels_b)) / total
    counts_a = Counter(labels_a)
    counts_b = Counter(labels_b)
    expected = sum(
        (counts_a[label] / total) * (counts_b[label] / total)
        for label in set(counts_a) | set(counts_b)
    )
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else 0.0
    return (observed - expected) / (1.0 - expected)


def analyze_annotations(
    annotator_a_path: Path,
    annotator_b_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Validate completed sheets and emit agreement and adjudication artifacts."""

    rows_a, annotator_a_id = _read_completed_sheet(annotator_a_path, "Annotator A sheet")
    rows_b, annotator_b_id = _read_completed_sheet(annotator_b_path, "Annotator B sheet")
    annotator_a_id, annotator_b_id = _validate_distinct_annotators(
        annotator_a_id, annotator_b_id
    )
    if set(rows_a) != set(rows_b):
        missing_b = sorted(set(rows_a) - set(rows_b))
        missing_a = sorted(set(rows_b) - set(rows_a))
        raise AnnotationWorkflowError(
            "annotator event sets differ "
            f"(missing from B: {missing_b}; missing from A: {missing_a})"
        )

    event_ids = sorted(rows_a)
    field_agreement: dict[str, dict[str, Any]] = {}
    disagreements: list[dict[str, str]] = []
    for field in ANSWER_FIELDS:
        agreed = 0
        for event_id in event_ids:
            value_a = rows_a[event_id][field]
            value_b = rows_b[event_id][field]
            if value_a == value_b:
                agreed += 1
            else:
                disagreements.append(
                    {
                        "event_id": event_id,
                        "field": field,
                        "annotator_a_id": annotator_a_id,
                        "annotator_a_value": value_a,
                        "annotator_b_id": annotator_b_id,
                        "annotator_b_value": value_b,
                    }
                )
        field_agreement[field] = {
            "agreed": agreed,
            "total": len(event_ids),
            "raw_agreement": agreed / len(event_ids),
        }

    decision_a = [rows_a[event_id]["decision"] for event_id in event_ids]
    decision_b = [rows_b[event_id]["decision"] for event_id in event_ids]
    semantic_a = [rows_a[event_id]["semantic_type"] for event_id in event_ids]
    semantic_b = [rows_b[event_id]["semantic_type"] for event_id in event_ids]
    per_type_kappa: dict[str, float] = {}
    by_type: dict[str, list[str]] = {}
    for event_id in event_ids:
        by_type.setdefault(rows_a[event_id]["semantic_type"], []).append(event_id)
    for semantic_type, typed_ids in sorted(by_type.items()):
        if len(typed_ids) < 2:
            continue
        per_type_kappa[semantic_type] = cohen_kappa(
            [rows_a[event_id]["decision"] for event_id in typed_ids],
            [rows_b[event_id]["decision"] for event_id in typed_ids],
        )
    summary: dict[str, Any] = {
        "dataset_id": DATASET_ID,
        "status": "COMPLETE",
        "annotator_a_id": annotator_a_id,
        "annotator_b_id": annotator_b_id,
        "event_count": len(event_ids),
        "field_agreement": field_agreement,
        "decision_cohen_kappa": cohen_kappa(decision_a, decision_b),
        "semantic_type_cohen_kappa": cohen_kappa(semantic_a, semantic_b),
        "per_semantic_type_kappa": per_type_kappa,
        "disagreement_event_count": len({row["event_id"] for row in disagreements}),
        "disagreement_field_count": len(disagreements),
        "automatic_human_approval": False,
    }
    adjudication_rows = [
        {
            **row,
            "adjudicated_value": "",
            "adjudicator_id": "",
            "adjudication_reason": "",
        }
        for row in disagreements
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "disagreements.csv", DISAGREEMENT_FIELDS, disagreements)
    _write_csv(
        output_dir / "adjudication-template.csv",
        ADJUDICATION_FIELDS,
        adjudication_rows,
    )
    with (output_dir / "agreement-summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="generate two blind CSV templates")
    generate.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    generate.add_argument("--proposed-gold", type=Path, default=DEFAULT_PROPOSED_GOLD)
    generate.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    generate.add_argument("--annotator-a-id", required=True)
    generate.add_argument("--annotator-b-id", required=True)
    generate.add_argument("--seed", type=int, default=20260909)

    analyze = subparsers.add_parser("analyze", help="validate and analyze completed sheets")
    analyze.add_argument("--annotator-a", "--sheet-a", dest="annotator_a", type=Path, required=True)
    analyze.add_argument("--annotator-b", "--sheet-b", dest="annotator_b", type=Path, required=True)
    analyze.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            path_a, path_b = generate_templates(
                args.events,
                args.proposed_gold,
                args.output_dir,
                args.annotator_a_id,
                args.annotator_b_id,
                seed=args.seed,
            )
            print(json.dumps({"annotator_a": str(path_a), "annotator_b": str(path_b)}))
        else:
            summary = analyze_annotations(
                args.annotator_a,
                args.annotator_b,
                args.output_dir,
            )
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    except (AnnotationWorkflowError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
