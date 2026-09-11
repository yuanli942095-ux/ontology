"""Create a clean medical v2 seed from the subset that passed both v1 reviews.

The v1 corpus is retained unchanged as an audit trail.  A seed event is copied
only when both reviewers marked the evidence and version relation sufficient,
and independently agreed on semantic type and gold candidate.  The new v2
annotation sheets are deliberately blank: the seed still requires fresh dual
review before it can become a benchmark.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from dosd_multidomain_common import read_csv, write_csv


ROOT = Path(__file__).resolve().parents[1]


def copy_event_files(source: Path, target: Path, event_id: str) -> None:
    relative_paths = (
        Path("public/excerpts") / f"{event_id}-evidence.md",
        Path("public/documents") / f"DOC_{event_id}_OLD.txt",
        Path("public/documents") / f"DOC_{event_id}_NEW.txt",
        Path("repair-stage/mutants") / f"{event_id}.owl",
    )
    for relative in relative_paths:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "benchmark" / "dosd-medical-v1-draft")
    parser.add_argument("--target", type=Path, default=ROOT / "benchmark" / "dosd-medical-v2-screened-seed")
    args = parser.parse_args()
    source, target = args.source.resolve(), args.target.resolve()
    events = read_csv(source / "public/events/external-real-event-template.csv")
    documents = read_csv(source / "public/documents/external-real-document-template.csv")
    candidates = read_csv(source / "repair-stage/candidates/external-real-candidate-template.csv")
    retrieval = read_csv(source / "public/retrieval/external-real-v8-event-retrieval.csv")
    construction = read_csv(source / "private/construction/construction-provenance.csv")
    sheet_a = {row["event_id"]: row for row in read_csv(source / "private/annotation/annotation-sheet-A.csv")}
    sheet_b = {row["event_id"]: row for row in read_csv(source / "private/annotation/annotation-sheet-B.csv")}

    accepted: list[str] = []
    for event in events:
        event_id = event["event_id"]
        a, b = sheet_a[event_id], sheet_b[event_id]
        if not all((
            a["evidence_sufficient"] == "YES",
            b["evidence_sufficient"] == "YES",
            a["version_relation_valid"] == "YES",
            b["version_relation_valid"] == "YES",
            a["annotator_drift_type"] == b["annotator_drift_type"],
            a["annotator_gold_candidate"] == b["annotator_gold_candidate"],
        )):
            continue
        accepted.append(event_id)
        copy_event_files(source, target, event_id)

    selected = set(accepted)
    selected_events = [row for row in events if row["event_id"] in selected]
    for row in selected_events:
        row["status"] = "PENDING_FRESH_DUAL_REVIEW"
        row["support_status"] = "SCREENED_SEED_REQUIRES_FRESH_REVIEW"
        row["semantic_support"] = "PENDING"
        row["notes"] = "v2 screened seed selected from a prior dual-review agreement; no Oracle is exposed or written."
    blank_annotations = []
    for row in selected_events:
        event_id = row["event_id"]
        prior = sheet_a[event_id]
        blank_annotations.append({
            "event_id": event_id,
            "source_pair": prior["source_pair"],
            "old_span": prior["old_span"],
            "new_span": prior["new_span"],
            "candidate_1": prior["candidate_1"],
            "candidate_2": prior["candidate_2"],
            "candidate_3": prior["candidate_3"],
            "annotator_name": "", "annotator_drift_type": "", "annotator_ontology_change": "",
            "annotator_gold_candidate": "", "evidence_sufficient": "", "version_relation_valid": "",
            "notes": "", "completed_at": "",
        })
    target.mkdir(parents=True, exist_ok=True)
    write_csv(target / "public/events/external-real-event-template.csv", selected_events)
    write_csv(target / "public/documents/external-real-document-template.csv", [row for row in documents if row["event_id"] in selected])
    write_csv(target / "public/retrieval/external-real-v8-event-retrieval.csv", [row for row in retrieval if row["event_id"] in selected])
    shutil.copy2(source / "public/retrieval/external-real-v8-grounded-source-families.csv", target / "public/retrieval/external-real-v8-grounded-source-families.csv")
    write_csv(target / "repair-stage/candidates/external-real-candidate-template.csv", [row for row in candidates if row["event_id"] in selected])
    write_csv(target / "private/construction/construction-provenance.csv", [row for row in construction if row["event_id"] in selected])
    write_csv(target / "private/annotation/annotation-sheet-A.csv", blank_annotations)
    write_csv(target / "private/annotation/annotation-sheet-B.csv", blank_annotations)
    print({"benchmark": target.name, "screened_seed_events": len(selected_events), "status": "REQUIRES_FRESH_DUAL_REVIEW"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
