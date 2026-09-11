from __future__ import annotations

"""Compute Exp12 Annotator A vs B agreement and Cohen's kappa."""

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from paper_final_validation_common import PAPER_VALIDATION_ROOT, read_csv, utc_now_iso, write_summary_json
from semantic_v2_common import PROJECT_DIR, write_csv


EXP12_ROOT = PAPER_VALIDATION_ROOT / "12-human-annotation"


def cohen_kappa(labels_a: list[str], labels_b: list[str]) -> float | None:
    if len(labels_a) != len(labels_b) or not labels_a:
        return None
    pairs = list(zip(labels_a, labels_b))
    n = len(pairs)
    agree = sum(1 for a, b in pairs if a == b)
    po = agree / n
    counts_a = Counter(labels_a)
    counts_b = Counter(labels_b)
    labels = set(counts_a) | set(counts_b)
    pe = sum((counts_a[l] / n) * (counts_b[l] / n) for l in labels)
    if math.isclose(1.0 - pe, 0.0):
        return 1.0 if math.isclose(po, 1.0) else None
    return (po - pe) / (1.0 - pe)


def agreement_block(
    field: str,
    rows_a: list[dict[str, str]],
    rows_b: list[dict[str, str]],
) -> dict[str, Any]:
    by_b = {r["event_id"]: r for r in rows_b}
    labels_a: list[str] = []
    labels_b: list[str] = []
    disagreements: list[dict[str, str]] = []
    for ra in rows_a:
        rb = by_b.get(ra["event_id"])
        if not rb:
            continue
        va = str(ra.get(field, "")).strip()
        vb = str(rb.get(field, "")).strip()
        labels_a.append(va)
        labels_b.append(vb)
        if va != vb:
            disagreements.append(
                {
                    "event_id": ra["event_id"],
                    "field": field,
                    "annotator_a": va,
                    "annotator_b": vb,
                    "adjudication_status": "pending",
                }
            )
    n = len(labels_a)
    agree = sum(1 for a, b in zip(labels_a, labels_b) if a == b)
    kappa = cohen_kappa(labels_a, labels_b)
    return {
        "field": field,
        "n": n,
        "raw_agreement": agree / n if n else 0.0,
        "raw_agreement_count": f"{agree}/{n}",
        "cohen_kappa": kappa if kappa is not None else "",
        "disagreements": disagreements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=EXP12_ROOT)
    args = parser.parse_args()

    rows_a = read_csv(args.input_dir / "annotator-a-heuristic.csv")
    rows_b = read_csv(args.input_dir / "annotator-b-responses.csv")
    if not rows_b:
        raise FileNotFoundError(args.input_dir / "annotator-b-responses.csv")

    fields = ("semantic_type", "evidence_sufficient", "oracle_candidate")
    blocks = [agreement_block(field, rows_a, rows_b) for field in fields]
    all_disagreements: list[dict[str, str]] = []
    summary_rows: list[dict[str, Any]] = []
    for block in blocks:
        all_disagreements.extend(block["disagreements"])
        summary_rows.append(
            {
                "field": block["field"],
                "n": block["n"],
                "raw_agreement": f"{block['raw_agreement']:.4f}",
                "raw_agreement_count": block["raw_agreement_count"],
                "cohen_kappa": block["cohen_kappa"],
            }
        )

    write_csv(args.input_dir / "human-annotation-agreement-summary.csv", summary_rows)
    if all_disagreements:
        write_csv(args.input_dir / "annotation-disagreements.csv", all_disagreements)
    else:
        (args.input_dir / "annotation-disagreements.csv").write_text(
            "event_id,field,annotator_a,annotator_b,adjudication_status\n",
            encoding="utf-8-sig",
        )

    oracle_a = [r.get("oracle_candidate", "") for r in rows_a]
    oracle_b = [r.get("oracle_candidate", "") for r in rows_b]
    dist = Counter(oracle_b)
    report_lines = [
        "# Exp12 Human Annotation Agreement Report",
        "",
        f"Completed: {utc_now_iso()}",
        "",
        "## Summary",
        "",
        "| Field | N | Raw agreement | Cohen's κ |",
        "|-------|---|---------------|-----------|",
    ]
    for row in summary_rows:
        k = row["cohen_kappa"]
        k_str = f"{k:.4f}" if isinstance(k, float) else str(k)
        report_lines.append(
            f"| {row['field']} | {row['n']} | {row['raw_agreement_count']} ({row['raw_agreement']}) | {k_str} |"
        )
    report_lines.extend(
        [
            "",
            "## Oracle candidate distribution (Annotator B)",
            "",
            f"- CAND_001: {dist.get('CAND_001', 0)}",
            f"- CAND_002: {dist.get('CAND_002', 0)}",
            f"- CAND_003: {dist.get('CAND_003', 0)}",
            f"- UNDECIDABLE: {dist.get('UNDECIDABLE', 0)}",
            "",
            "## Interpretation",
            "",
            "Annotator A is the frozen heuristic oracle route; Annotator B is independent blind review.",
            f"Disagreements requiring adjudication: **{len(all_disagreements)}**.",
        ]
    )
    if all(x == y for x, y in zip(oracle_a, oracle_b)):
        report_lines.append(
            "Label-level oracle agreement is perfect (κ=1); supporting_span/reason text still differs in prose."
        )

    report_path = args.input_dir / "human-annotation-report.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    payload = {
        "completed_at_utc": utc_now_iso(),
        "disagreement_count": len(all_disagreements),
        "summary_rows": summary_rows,
        "report": str(report_path.relative_to(PROJECT_DIR)),
    }
    write_summary_json(args.input_dir / "human-annotation-status.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
