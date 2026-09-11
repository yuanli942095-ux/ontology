from __future__ import annotations

"""Build a non-destructive M7 revision of external-real-v8-grounded.

The original eCFR M7 rows used generic ``regulatory text status`` candidates
although the public evidence windows support narrower SEC amendment, deadline,
and disclosure-tagging claims. This script copies the benchmark and revises only
those four rows so reruns can separate benchmark construction error from method
failure.
"""

import csv
import json
import shutil
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


SOURCE = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
TARGET = PROJECT_DIR / "benchmark" / "external-real-v8-grounded-revised-m7"
MANIFEST = PROJECT_DIR / "output" / "m14-ecfr-m7-revised-manifest.csv"


REVISIONS: dict[str, dict[str, Any]] = {
    "EXT_E064": {
        "predicate_label": "list of subjects",
        "title": "SEC Federal Register list of subjects",
        "case_context": "Determine the listed subject scope for the cited Federal Register amendment section.",
        "values": {
            "CAND_001": "list=unmodeled",
            "CAND_002": "list=reporting_and_record_keeping_requirements_securities",
            "CAND_003": "list=reporting_and_record_keeping_requirements_securities_incorrect_scope",
        },
        "oracle_value": "list=reporting_and_record_keeping_requirements_securities",
        "quote": "List of Subjects in 17 CFR Parts 229, 232, 239, 240, and 249 - Reporting and record keeping requirements; Securities",
    },
    "EXT_E065": {
        "predicate_label": "nonbusiness day deadline exception",
        "title": "SEC four business day filing deadline nonbusiness-day exception",
        "case_context": "Determine the exception rule for the four business day filing period when the triggering event occurs on a nonbusiness day.",
        "values": {
            "CAND_001": "deadline_exception=unmodeled",
            "CAND_002": "deadline_exception=first_business_day_after_nonbusiness_day",
            "CAND_003": "deadline_exception=first_business_day_after_nonbusiness_day_incorrect_scope",
        },
        "oracle_value": "deadline_exception=first_business_day_after_nonbusiness_day",
        "quote": "If the event occurs on a Saturday, Sunday or holiday on which the Commission is not open for business, then the four business day period shall begin to run on, and include, the first business day thereafter.",
    },
    "EXT_E067": {
        "predicate_label": "disclosure tagging scope",
        "title": "Inline XBRL disclosure tagging scope",
        "case_context": "Determine the scope of the proposed Inline XBRL tagging requirement for the referenced disclosures.",
        "values": {
            "CAND_001": "tagging_scope=unmodeled",
            "CAND_002": "tagging_scope=narrative_and_quantitative_disclosures",
            "CAND_003": "tagging_scope=narrative_and_quantitative_disclosures_incorrect_scope",
        },
        "oracle_value": "tagging_scope=narrative_and_quantitative_disclosures",
        "quote": "The proposed requirements would include block text tagging of narrative disclosures, as well as detail tagging of quantitative amounts disclosed within the narrative disclosures.",
    },
    "EXT_E068": {
        "predicate_label": "regulation fd filing deadline exception",
        "title": "Regulation FD filing deadline nonbusiness-day exception",
        "case_context": "Determine the deadline exception for Regulation FD-related Form 8-K reports when the triggering event occurs on a nonbusiness day.",
        "values": {
            "CAND_001": "deadline_exception=unmodeled",
            "CAND_002": "deadline_exception=first_business_day_after_nonbusiness_day",
            "CAND_003": "deadline_exception=first_business_day_after_nonbusiness_day_incorrect_scope",
        },
        "oracle_value": "deadline_exception=first_business_day_after_nonbusiness_day",
        "quote": "If the event occurs on a Saturday, Sunday or holiday on which the Commission is not open for business, then the four business day period shall begin to run on, and include, the first business day thereafter.",
    },
}

RUNS = {
    "EXT_E064": [1, 2, 3],
    "EXT_E065": [1, 2],
    "EXT_E067": [1, 2, 3],
    "EXT_E068": [1, 2, 3],
}
SEED_BASE = 20260827


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        fieldnames = list(csv.DictReader(handle).fieldnames or [])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def revise_events() -> None:
    path = TARGET / "public" / "events" / "external-real-event-template.csv"
    rows = read_rows(path)
    for row in rows:
        revision = REVISIONS.get(row["event_id"])
        if not revision:
            continue
        row["predicate_label"] = revision["predicate_label"]
        row["title"] = revision["title"]
        row["case_context"] = revision["case_context"]
        row["notes"] = "revised_m7_predicate_candidate_alignment"
        row["support_adjudication_method"] = "m7_revision_evidence_alignment"
    write_rows(path, rows)


def revise_candidates() -> None:
    path = TARGET / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    rows = read_rows(path)
    for row in rows:
        revision = REVISIONS.get(row["event_id"])
        if not revision:
            continue
        new_value = revision["values"][row["candidate_id"]]
        row["display_value"] = new_value
        operation = json.loads(row["operation_json"])
        operation["new_value"]["lexical"] = new_value
        row["operation_json"] = json.dumps(operation, ensure_ascii=False, separators=(",", ":"))
        row["notes"] = "revised_m7_candidate_aligned_to_public_evidence"
    write_rows(path, rows)


def revise_oracle() -> None:
    path = TARGET / "private" / "oracle" / "external-real-oracle-template.csv"
    rows = read_rows(path)
    for row in rows:
        revision = REVISIONS.get(row["event_id"])
        if not revision:
            continue
        doc_id = f"EXT_DOC_{row['event_id'].split('_E')[-1]}_NEW"
        row["oracle_candidate_id"] = "CAND_002"
        row["oracle_value"] = revision["oracle_value"]
        row["evidence_document_ids"] = doc_id
        row["evidence_spans_json"] = json.dumps(
            [{"document_id": doc_id, "evidence_type": "public_source_span", "quote": revision["quote"]}],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        row["notes"] = "Revised M7 oracle aligned to public evidence span; original benchmark copy preserved."
    write_rows(path, rows)


def write_manifest() -> None:
    rows: list[dict[str, Any]] = []
    for event_id, runs in RUNS.items():
        revision = REVISIONS[event_id]
        semantic_type = "GENERAL_RULE_EXCEPTION" if event_id in {"EXT_E065", "EXT_E068"} else "CROSS_SENTENCE_SCOPE"
        for run in runs:
            seed = SEED_BASE + run - 1
            rows.append(
                {
                    "event_id": event_id,
                    "run": run,
                    "seed": seed,
                    "semantic_type": semantic_type,
                    "domain": "us_regulation",
                    "missing_subtype": "M7_INVALID_JSON",
                    "revision": "external-real-v8-grounded-revised-m7",
                    "predicate_label": revision["predicate_label"],
                }
            )
    write_csv(MANIFEST, rows)


def write_notes() -> None:
    lines = [
        "# external-real-v8-grounded-revised-m7",
        "",
        "This directory is a non-destructive revision copied from `benchmark/external-real-v8-grounded`.",
        "",
        "Scope:",
        "- Revised only EXT_E064, EXT_E065, EXT_E067, and EXT_E068.",
        "- Public predicate labels, candidate display values, candidate operation new lexical values, and private oracle rows were aligned to the public evidence spans.",
        "- Source OWL predicate IRIs remain the original `regulatory_text_status` diagnostic predicate so the rerun isolates candidate/evidence semantic alignment rather than ontology schema migration.",
        "- No Qwen output, candidate selection result, or model failure pattern was used to select the correct candidate; the revision follows public evidence spans already identified in the benchmark quality audit.",
        "",
        "Original benchmark remains unchanged.",
    ]
    (TARGET / "REVISION_NOTES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not SOURCE.is_dir():
        raise FileNotFoundError(SOURCE)
    if not TARGET.exists():
        shutil.copytree(SOURCE, TARGET)
    revise_events()
    revise_candidates()
    revise_oracle()
    write_manifest()
    write_notes()
    print(f"revised_benchmark={TARGET}")
    print(f"manifest={MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
