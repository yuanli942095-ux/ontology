"""Adjudicate legal v2 A/B drift-type disagreements without re-selecting gold.

Independent reviewers already agreed on every gold candidate. This step only
resolves `annotator_drift_type` from the distinctive old/new change, using the
annotation protocol rather than mined event types or either annotator's
heuristic fallback.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from dosd_multidomain_common import SEMANTIC_TYPES, read_csv, write_csv


ROOT = Path(__file__).resolve().parents[1]
ADJUDICATOR = "ADJ-LAW-V2"
COMPLETED_AT = "2026-09-08"

# Explicit per-event decisions. Gold is never changed.
# Rationale codes:
#   NUMERIC_DURATION_DATE      date, duration, or numeric threshold of the same proposition
#   VERSIONED_RESTATEMENT      same object restated after a named reform (e.g. retained → assimilated)
#   AUTHORISATION_CLASS_SCOPE  UKMA/THR class the duty attaches to
#   INSTRUMENT_LIST_SCOPE      which Act, section, or authorisation instrument is covered
#   PURPOSE_SCOPE              purpose/object of the duty (e.g. proliferation financing)
#   PERSON_BODY_SCOPE          who the rule addresses or which body is listed
#   ACT_RANGE_SCOPE            which acts, objects, or scheme types the rule covers
#   EXCEPTION_OPERATOR         add/remove unless/except/subject-to or a conditional limb
DECISIONS: dict[str, tuple[str, str, str]] = {
    "LAW_E004": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "ELTIF infringement ground removed from an existing list; exception grammar unchanged."),
    "LAW_E006": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Northern Ireland Pension Schemes Act added to the interpretation list."),
    "LAW_E007": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) replaced by UKMA(UK)(Category 1); class of authorisation, not a date."),
    "LAW_E012": ("TEMPORAL_VERSION", "NUMERIC_DURATION_DATE", "Retention period three years → two years."),
    "LAW_E013": ("CROSS_SENTENCE_SCOPE", "PURPOSE_SCOPE", "AML/CTF purpose expanded to proliferation financing."),
    "LAW_E019": ("GENERAL_RULE_EXCEPTION", "EXCEPTION_OPERATOR", "Except-in-the-case-of enhanced disclosure carve-out deleted."),
    "LAW_E023": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "New designated-activity-rules limb added to an existing does-not-apply list."),
    "LAW_E026": ("CROSS_SENTENCE_SCOPE", "PERSON_BODY_SCOPE", "Advanced Research and Invention Agency added to the listed bodies."),
    "LAW_E035": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E039": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Stabilisation power relocated from Banking Act 2009 to FSMA 2023 Sch 11; 2-week period unchanged."),
    "LAW_E042": ("CROSS_SENTENCE_SCOPE", "ACT_RANGE_SCOPE", "Mobility assistance now covers loading mobility aids, not only luggage."),
    "LAW_E045": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "Marketing authorisation class restated as UKMA(UK)(Category 2)/UKMA(UK)(NI)."),
    "LAW_E046": ("CROSS_SENTENCE_SCOPE", "PERSON_BODY_SCOPE", "Trusts and overseas entities added to the CDD customer list."),
    "LAW_E050": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "EU marketing authorisation removed from the NI authorisation list."),
    "LAW_E057": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E064": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "EU marketing authorisation removed; exception operator unchanged."),
    "LAW_E066": ("CROSS_SENTENCE_SCOPE", "PURPOSE_SCOPE", "AML/CTF purpose expanded to proliferation financing."),
    "LAW_E070": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "Holder of UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E073": ("CROSS_SENTENCE_SCOPE", "ACT_RANGE_SCOPE", "Prohibition narrowed from use-or-disclose to disclose, and a new permitted-disclosure section added to the existing except-list."),
    "LAW_E074": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) list expanded by UKMA(UK)(Category 1)."),
    "LAW_E076": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) → UKMA(GB) or UKMA(UK)(Category 1)."),
    "LAW_E086": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Additional Act added to the listed statutory sources."),
    "LAW_E088": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E091": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Section 167C added to the listed functions."),
    "LAW_E093": ("TEMPORAL_VERSION", "VERSIONED_RESTATEMENT", "retained EU law renamed assimilated law; same legal object after REUL reform."),
    "LAW_E094": ("TEMPORAL_VERSION", "VERSIONED_RESTATEMENT", "retained EU law renamed assimilated law."),
    "LAW_E095": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) → UKMA(UK)(Category 2) on the unfettered-access grant."),
    "LAW_E096": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) → UKMA(GB) or UKMA(UK)(Category 1)."),
    "LAW_E098": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "Holder list expanded by UKMA(UK)(Category 1)."),
    "LAW_E101": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Registrar strike-off power s.1002A added."),
    "LAW_E104": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Chapter 2A of Part 18 added to the covered functions."),
    "LAW_E108": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E110": ("CROSS_SENTENCE_SCOPE", "PERSON_BODY_SCOPE", "Recognised-scheme operator added to the definition of operator."),
    "LAW_E121": ("TEMPORAL_VERSION", "VERSIONED_RESTATEMENT", "retained EU law renamed assimilated law."),
    "LAW_E122": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) → UKMA(UK)(Category 1)."),
    "LAW_E125": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E127": ("TEMPORAL_VERSION", "VERSIONED_RESTATEMENT", "retained EU law renamed assimilated law."),
    "LAW_E129": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) → UKMA(UK)(Category 2)."),
    "LAW_E130": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E139": ("CROSS_SENTENCE_SCOPE", "PERSON_BODY_SCOPE", "UK-registered partnership given an explicit Limited Partnerships Act meaning."),
    "LAW_E143": ("CROSS_SENTENCE_SCOPE", "PURPOSE_SCOPE", "AML/CTF purpose expanded to proliferation financing."),
    "LAW_E145": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E147": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Commission Regulation 2016/161 Art 9 removed from applicable requirements."),
    "LAW_E148": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "Holder of UKMA(GB) → UKMA(UK)(Category 1)."),
    "LAW_E150": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E154": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Section 1002A added to the strike-off powers."),
    "LAW_E156": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) list expanded by UKMA(UK)(Category 1)."),
    "LAW_E158": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Capital Requirements Directive annex reference removed."),
    "LAW_E163": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Section 2F added to the review list."),
    "LAW_E164": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "EU marketing authorisation removed from the NI authorisation list."),
    "LAW_E168": ("TEMPORAL_VERSION", "VERSIONED_RESTATEMENT", "retained EU law renamed assimilated law."),
    "LAW_E170": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E177": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) → UKMA(GB) or UKMA(UK)(Category 1)."),
    "LAW_E180": ("GENERAL_RULE_EXCEPTION", "EXCEPTION_OPERATOR", "Alternative paediatric-plan limb for the 6-month extension repealed; duration unchanged."),
    "LAW_E190": ("GENERAL_RULE_EXCEPTION", "EXCEPTION_OPERATOR", "Establishment rule collapsed to a general UK/EEA duty subject to a Category 2 carve-out."),
    "LAW_E192": ("TEMPORAL_VERSION", "NUMERIC_DURATION_DATE", "Summary-conviction maximum 12 months restated as the magistrates’ court general limit."),
    "LAW_E202": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Northern Ireland Pension Schemes Act added to the interpretation list."),
    "LAW_E204": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) → UKMA(UK)(Category 1)."),
    "LAW_E208": ("CROSS_SENTENCE_SCOPE", "PURPOSE_SCOPE", "AML/CTF purpose expanded to proliferation financing."),
    "LAW_E215": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Commission Regulation 2016/161 Art 9 removed."),
    "LAW_E219": ("TEMPORAL_VERSION", "VERSIONED_RESTATEMENT", "retained direct EU legislation renamed assimilated direct legislation."),
    "LAW_E221": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(GB) → UKMA(UK)(Category 2)."),
    "LAW_E222": ("CROSS_SENTENCE_SCOPE", "PERSON_BODY_SCOPE", "Clinical commissioning group replaced by integrated care board / NHS England."),
    "LAW_E227": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "EU marketing authorisation removed from the NI limb."),
    "LAW_E228": ("CROSS_SENTENCE_SCOPE", "PURPOSE_SCOPE", "AML/CTF purpose expanded to proliferation financing."),
    "LAW_E229": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "EU marketing authorisation removed from the NI authorisation list."),
    "LAW_E232": ("TEMPORAL_VERSION", "NUMERIC_DURATION_DATE", "Filing dates and 30-day window updated to 1 Sep 2022 / 4 Jun 2022 / 90 days."),
    "LAW_E235": ("CROSS_SENTENCE_SCOPE", "ACT_RANGE_SCOPE", "Participants in a contractual scheme → participants in the scheme."),
    "LAW_E237": ("CROSS_SENTENCE_SCOPE", "PERSON_BODY_SCOPE", "Anaesthesia/physician associates added to the listed persons."),
    "LAW_E239": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "EU marketing authorisation limb repealed."),
    "LAW_E243": ("TEMPORAL_VERSION", "VERSIONED_RESTATEMENT", "retained EU law renamed assimilated law."),
    "LAW_E245": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E252": ("CROSS_SENTENCE_SCOPE", "INSTRUMENT_LIST_SCOPE", "Regulation (EC) 726/2004 authorisation limb repealed."),
    "LAW_E253": ("CROSS_SENTENCE_SCOPE", "AUTHORISATION_CLASS_SCOPE", "UKMA(UK) → UKMA(UK)(Category 2)."),
    "LAW_E257": ("TEMPORAL_VERSION", "VERSIONED_RESTATEMENT", "retained EU law renamed assimilated law."),
    "LAW_E258": ("GENERAL_RULE_EXCEPTION", "EXCEPTION_OPERATOR", "Establishment rule collapsed to a general UK/EEA duty subject to a Category 2 carve-out."),
    "LAW_E260": ("CROSS_SENTENCE_SCOPE", "PERSON_BODY_SCOPE", "Receptacle duty split between Welsh and English waste collection authorities."),
}


def change_snippet(old: str, new: str, width: int = 180) -> tuple[str, str]:
    matcher = SequenceMatcher(None, old, new)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            old_snip = old[max(0, i1 - 40) : min(len(old), i2 + 40)].replace("\n", " ")
            new_snip = new[max(0, j1 - 40) : min(len(new), j2 + 40)].replace("\n", " ")
            return old_snip[:width], new_snip[:width]
    return old[:width], new[:width]


def export_disagreements(rows_a: dict[str, dict[str, str]], rows_b: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    exported: list[dict[str, str]] = []
    for event_id, row_a in rows_a.items():
        row_b = rows_b[event_id]
        if row_a["annotator_drift_type"] == row_b["annotator_drift_type"]:
            continue
        old_snip, new_snip = change_snippet(row_a["old_span"], row_a["new_span"])
        exported.append(
            {
                "event_id": event_id,
                "source_pair": row_a["source_pair"],
                "annotator_a": row_a["annotator_drift_type"],
                "annotator_b": row_b["annotator_drift_type"],
                "gold_candidate": row_a["annotator_gold_candidate"],
                "gold_agrees": "YES" if row_a["annotator_gold_candidate"] == row_b["annotator_gold_candidate"] else "NO",
                "old_change_snippet": old_snip,
                "new_change_snippet": new_snip,
            }
        )
    return exported


def build_adjudication_rows(disagreements: list[dict[str, str]]) -> list[dict[str, str]]:
    missing = [row["event_id"] for row in disagreements if row["event_id"] not in DECISIONS]
    extra = sorted(set(DECISIONS) - {row["event_id"] for row in disagreements})
    if missing or extra:
        raise SystemExit(f"adjudication table mismatch: missing={missing} extra={extra}")
    rows: list[dict[str, str]] = []
    for row in disagreements:
        final_type, code, note = DECISIONS[row["event_id"]]
        if final_type not in SEMANTIC_TYPES:
            raise SystemExit(f"{row['event_id']}: invalid type {final_type}")
        chosen = "A" if final_type == row["annotator_a"] else "B" if final_type == row["annotator_b"] else "NEITHER"
        rows.append(
            {
                "event_id": row["event_id"],
                "field": "annotator_drift_type",
                "annotator_a": row["annotator_a"],
                "annotator_b": row["annotator_b"],
                "final_value": final_type,
                "chose": chosen,
                "rationale_code": code,
                "adjudicator": ADJUDICATOR,
                "notes": note,
                "gold_candidate": row["gold_candidate"],
                "gold_unchanged": "YES",
                "completed_at": COMPLETED_AT,
                "old_change_snippet": row["old_change_snippet"],
                "new_change_snippet": row["new_change_snippet"],
                "source_pair": row["source_pair"],
            }
        )
    return rows


def apply_adjudicated_sheet(
    rows_a: list[dict[str, str]],
    adjudication: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in rows_a:
        copy = dict(row)
        decision = adjudication.get(row["event_id"])
        if decision:
            copy["annotator_drift_type"] = decision["final_value"]
            copy["notes"] = (
                f"ADJUDICATED_TYPE={decision['final_value']}; "
                f"A={decision['annotator_a']}; B={decision['annotator_b']}; "
                f"{decision['rationale_code']}; gold unchanged {copy['annotator_gold_candidate']}. "
                f"{row.get('notes', '')}"
            )
        output.append(copy)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=ROOT / "benchmark" / "dosd-legal-v2-draft")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    benchmark = args.benchmark_dir
    annotation_dir = benchmark / "private" / "annotation"
    output_dir = args.output_dir or ROOT / "output" / "dosd-legal-v2-draft"
    output_dir.mkdir(parents=True, exist_ok=True)

    rows_a = read_csv(annotation_dir / "annotation-sheet-A.csv")
    rows_b = read_csv(annotation_dir / "annotation-sheet-B.csv")
    by_a = {row["event_id"]: row for row in rows_a}
    by_b = {row["event_id"]: row for row in rows_b}
    if by_a.keys() != by_b.keys():
        raise SystemExit("A/B event_id sets differ")

    gold_mismatch = [
        event_id
        for event_id, row in by_a.items()
        if row["annotator_gold_candidate"] != by_b[event_id]["annotator_gold_candidate"]
    ]
    if gold_mismatch:
        raise SystemExit(f"refusing type adjudication while gold disagrees: {gold_mismatch}")
    disagreements = export_disagreements(by_a, by_b)
    adjudication_rows = build_adjudication_rows(disagreements)
    adjudicated_sheet = apply_adjudicated_sheet(rows_a, {row["event_id"]: row for row in adjudication_rows})

    write_csv(annotation_dir / "drift-type-disagreements.csv", disagreements)
    write_csv(annotation_dir / "drift-type-adjudication.csv", adjudication_rows)
    write_csv(annotation_dir / "annotation-sheet-adjudicated.csv", adjudicated_sheet)
    write_csv(output_dir / "drift-type-disagreements.csv", disagreements)
    write_csv(output_dir / "drift-type-adjudication.csv", adjudication_rows)

    summary = {
        "benchmark": benchmark.name,
        "adjudicated_at_utc": datetime.now(timezone.utc).isoformat(),
        "adjudicator": ADJUDICATOR,
        "events": len(rows_a),
        "type_disagreements": len(disagreements),
        "gold_disagreements": 0,
        "chose": dict(Counter(row["chose"] for row in adjudication_rows)),
        "final_types_on_disagreements": dict(Counter(row["final_value"] for row in adjudication_rows)),
        "rationale_codes": dict(Counter(row["rationale_code"] for row in adjudication_rows)),
        "resolved_type_distribution": dict(Counter(row["annotator_drift_type"] for row in adjudicated_sheet)),
        "gold_unchanged": True,
    }
    (annotation_dir / "drift-type-adjudication-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "drift-type-adjudication-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
