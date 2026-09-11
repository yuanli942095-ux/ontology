"""Write Oracle rows after dual review, applying drift-type adjudication where needed."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from dosd_multidomain_common import SEMANTIC_TYPES, read_csv, write_csv, yes_no


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "annotator_name",
    "annotator_drift_type",
    "annotator_ontology_change",
    "annotator_gold_candidate",
    "evidence_sufficient",
    "version_relation_valid",
    "completed_at",
)


def load_adjudication(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    return {row["event_id"]: row for row in read_csv(path) if row.get("field", "annotator_drift_type") == "annotator_drift_type"}


def resolved_drift_type(event_id: str, a: dict[str, str], b: dict[str, str], adjudication: dict[str, dict[str, str]]) -> tuple[str, str, str]:
    if a.get("annotator_drift_type") == b.get("annotator_drift_type") and a.get("annotator_drift_type"):
        return a["annotator_drift_type"], "AGREED", ""
    row = adjudication.get(event_id, {})
    final = row.get("final_value", "")
    if not final:
        return "", "UNRESOLVED", f"{event_id}: disagreement in annotator_drift_type; adjudication required"
    return final, "ADJUDICATED", row.get("adjudicator", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", choices=("medical", "legal"), required=True)
    parser.add_argument("--benchmark-dir", type=Path)
    parser.add_argument("--adjudication", type=Path)
    args = parser.parse_args()
    benchmark = args.benchmark_dir or ROOT / "benchmark" / f"dosd-{args.corpus}-v1-draft"
    annotation_dir = benchmark / "private" / "annotation"
    rows_a = {row["event_id"]: row for row in read_csv(annotation_dir / "annotation-sheet-A.csv")}
    rows_b = {row["event_id"]: row for row in read_csv(annotation_dir / "annotation-sheet-B.csv")}
    adjudication_path = args.adjudication or annotation_dir / "drift-type-adjudication.csv"
    adjudication = load_adjudication(adjudication_path)
    events_path = benchmark / "public" / "events" / "external-real-event-template.csv"
    events = read_csv(events_path)
    candidates = read_csv(benchmark / "repair-stage" / "candidates" / "external-real-candidate-template.csv")
    valid_candidates: dict[str, set[str]] = {}
    for row in candidates:
        valid_candidates.setdefault(row["event_id"], set()).add(row["candidate_id"])
    errors: list[str] = []
    oracle_rows: list[dict[str, str]] = []
    now = datetime.now(timezone.utc).isoformat()
    resolved_types: dict[str, str] = {}
    adjudicated_count = 0
    for event in events:
        event_id = event["event_id"]
        a, b = rows_a.get(event_id, {}), rows_b.get(event_id, {})
        for label, row in (("A", a), ("B", b)):
            missing = [field for field in REQUIRED if not row.get(field)]
            if missing:
                errors.append(f"{event_id}/{label}: missing {','.join(missing)}")
        if a.get("annotator_name") and a.get("annotator_name") == b.get("annotator_name"):
            errors.append(f"{event_id}: annotators must be different people")
        if a.get("annotator_gold_candidate") != b.get("annotator_gold_candidate"):
            errors.append(f"{event_id}: disagreement in annotator_gold_candidate; adjudication required")
        if yes_no(a.get("evidence_sufficient", "")) != yes_no(b.get("evidence_sufficient", "")):
            errors.append(f"{event_id}: disagreement in evidence_sufficient; adjudication required")
        if yes_no(a.get("version_relation_valid", "")) != yes_no(b.get("version_relation_valid", "")):
            errors.append(f"{event_id}: disagreement in version_relation_valid; adjudication required")
        drift_type, agreement, adjudicator = resolved_drift_type(event_id, a, b, adjudication)
        if agreement == "UNRESOLVED":
            errors.append(adjudicator)
        if agreement == "ADJUDICATED":
            adjudicated_count += 1
        if drift_type not in SEMANTIC_TYPES:
            errors.append(f"{event_id}: invalid semantic_type {drift_type!r}")
        resolved_types[event_id] = drift_type
        candidate_id = a.get("annotator_gold_candidate", "")
        if candidate_id not in valid_candidates.get(event_id, set()):
            errors.append(f"{event_id}: invalid gold candidate {candidate_id}")
        if yes_no(a.get("evidence_sufficient", "")) != "YES" or yes_no(a.get("version_relation_valid", "")) != "YES":
            errors.append(f"{event_id}: evidence/version relation not accepted")
        candidate = next((row for row in candidates if row["event_id"] == event_id and row["candidate_id"] == candidate_id), {})
        note = f"Independent dual review completed at {now}."
        if agreement == "ADJUDICATED":
            note = (
                f"Gold agreed independently; drift type adjudicated as {drift_type} "
                f"by {adjudicator} at {now}."
            )
        oracle_rows.append(
            {
                "event_id": event_id,
                "oracle_candidate_id": candidate_id,
                "oracle_value": candidate.get("display_value", ""),
                "evidence_document_ids": event["document_ids"],
                "evidence_spans_json": "",
                "annotator_1": a.get("annotator_name", ""),
                "annotator_2": b.get("annotator_name", ""),
                "adjudicator": adjudicator,
                "agreement_status": agreement,
                "status": "READY",
                "notes": note,
            }
        )
    if errors:
        report = annotation_dir / "finalization-errors.txt"
        report.write_text("\n".join(errors) + "\n", encoding="utf-8")
        print(f"finalization blocked: {len(errors)} errors; see {report}")
        return 1
    oracle_by_event = {row["event_id"]: row for row in oracle_rows}
    for event in events:
        event_id = event["event_id"]
        event["semantic_type"] = resolved_types[event_id]
        event["status"] = "READY"
        event["support_status"] = "SEMANTIC_REVIEW_PASSED"
        event["semantic_support"] = "PASS"
        event["support_checked_before_model_run"] = "true"
        if oracle_by_event[event_id]["agreement_status"] == "ADJUDICATED":
            event["notes"] = "Independent dual annotation agreed on gold; drift type adjudicated; ready for freeze."
        else:
            event["notes"] = "Independent dual annotation agreed; ready for freeze."
    write_csv(events_path, events)
    write_csv(benchmark / "private" / "oracle" / "external-real-oracle-template.csv", oracle_rows)
    summary = {
        "benchmark": benchmark.name,
        "finalized_at_utc": now,
        "events": len(events),
        "oracle_rows": len(oracle_rows),
        "drift_types_adjudicated": adjudicated_count,
        "status": "READY_FOR_FREEZE",
    }
    (annotation_dir / "dual-review-finalization-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
