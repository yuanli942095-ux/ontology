from __future__ import annotations

"""Compute agreement statistics for independent annotation sheets."""

import argparse
import math
from pathlib import Path

from paper_final_validation_common import (
    PAPER_VALIDATION_ROOT,
    read_csv,
    write_summary_json,
    utc_now_iso,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sheet-a",
        type=Path,
        default=PAPER_VALIDATION_ROOT / "03-independent-review" / "annotation-sheet-annotator-a.csv",
    )
    parser.add_argument(
        "--sheet-b",
        type=Path,
        default=PAPER_VALIDATION_ROOT / "03-independent-review" / "annotation-sheet-annotator-b.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=PAPER_VALIDATION_ROOT / "03-independent-review")
    return parser.parse_args()


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    labels = sorted({left for left, _ in pairs} | {right for _, right in pairs})
    if len(labels) < 2:
        return None
    n = len(pairs)
    if n == 0:
        return None
    matrix: dict[tuple[str, str], int] = {}
    for left, right in pairs:
        matrix[(left, right)] = matrix.get((left, right), 0) + 1
    po = sum(matrix.get((label, label), 0) for label in labels) / n
    row_marginals = {label: sum(matrix.get((label, other), 0) for other in labels) / n for label in labels}
    col_marginals = {label: sum(matrix.get((other, label), 0) for other in labels) / n for label in labels}
    pe = sum(row_marginals[label] * col_marginals[label] for label in labels)
    if math.isclose(1 - pe, 0.0):
        return None
    return (po - pe) / (1 - pe)


def raw_agreement(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    return sum(left == right for left, right in pairs) / len(pairs)


def main() -> int:
    args = parse_args()
    if not args.sheet_a.is_file() or not args.sheet_b.is_file():
        write_summary_json(
            args.output_dir / "agreement.json",
            {
                "generated_at_utc": utc_now_iso(),
                "status": "AWAITING_ANNOTATOR_SHEETS",
                "message": "Provide annotation-sheet-annotator-a.csv and annotation-sheet-annotator-b.csv",
            },
        )
        print("[independent-agreement] annotator sheets missing; agreement.json stub written")
        return 0

    rows_a = {row["event_id"]: row for row in read_csv(args.sheet_a)}
    rows_b = {row["event_id"]: row for row in read_csv(args.sheet_b)}
    shared = sorted(set(rows_a) & set(rows_b))
    oracle_pairs = [
        (rows_a[event_id]["annotator_oracle_candidate"], rows_b[event_id]["annotator_oracle_candidate"])
        for event_id in shared
        if rows_a[event_id].get("annotator_oracle_candidate") and rows_b[event_id].get("annotator_oracle_candidate")
    ]
    semantic_pairs = [
        (rows_a[event_id]["annotator_semantic_type"], rows_b[event_id]["annotator_semantic_type"])
        for event_id in shared
        if rows_a[event_id].get("annotator_semantic_type") and rows_b[event_id].get("annotator_semantic_type")
    ]
    evidence_pairs = [
        (rows_a[event_id]["annotator_evidence_sufficient"], rows_b[event_id]["annotator_evidence_sufficient"])
        for event_id in shared
        if rows_a[event_id].get("annotator_evidence_sufficient") and rows_b[event_id].get("annotator_evidence_sufficient")
    ]

    adjudication_rows = []
    for event_id in shared:
        a = rows_a[event_id]
        b = rows_b[event_id]
        disagreements = []
        for field in ("annotator_oracle_candidate", "annotator_semantic_type", "annotator_evidence_sufficient"):
            if a.get(field) and b.get(field) and a[field] != b[field]:
                disagreements.append(field)
        if disagreements:
            adjudication_rows.append(
                {
                    "event_id": event_id,
                    "annotator_A": a.get("annotator_name", "A"),
                    "annotator_B": b.get("annotator_name", "B"),
                    "disagreement_fields": "|".join(disagreements),
                    "disagreement_reason": "",
                    "final_adjudication": "",
                }
            )

    agreement = {
        "generated_at_utc": utc_now_iso(),
        "status": "COMPLETE",
        "shared_events": len(shared),
        "oracle_raw_agreement": raw_agreement(oracle_pairs),
        "oracle_cohen_kappa": cohen_kappa(oracle_pairs),
        "semantic_type_raw_agreement": raw_agreement(semantic_pairs),
        "semantic_type_cohen_kappa": cohen_kappa(semantic_pairs),
        "evidence_sufficiency_raw_agreement": raw_agreement(evidence_pairs),
        "disagreements": len(adjudication_rows),
    }
    write_summary_json(args.output_dir / "agreement.json", agreement)
    if adjudication_rows:
        from paper_final_validation_common import write_manifest_csv

        write_manifest_csv(args.output_dir / "adjudication.csv", adjudication_rows)
    print(f"[independent-agreement] shared={len(shared)} disagreements={len(adjudication_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
