from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from external_real_holdout_v6_annotation import (  # noqa: E402
    ANSWER_FIELDS,
    TEMPLATE_FIELDS,
    AnnotationWorkflowError,
    analyze_annotations,
    generate_templates,
    main,
)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEMPLATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def completed_rows(
    event_ids: list[str],
    annotator_id: str,
    *,
    overrides: dict[tuple[str, str], str] | None = None,
) -> list[dict[str, str]]:
    overrides = overrides or {}
    rows: list[dict[str, str]] = []
    for event_id in event_ids:
        row = {
            "event_id": event_id,
            "semantic_type": "TEMPORAL_VERSION",
            "gold_source_window": "window text",
            "target_subject": "subject",
            "target_predicate": "predicate",
            "old_lexical": "old",
            "old_datatype": "xsd:string",
            "new_lexical": "new",
            "new_datatype": "xsd:string",
            "decision": "REPAIR",
            "target_cq": "PASS",
            "non_target_cq": "PRESERVED",
            "confidence": "high",
            "supporting_span": "support",
            "reason": "because",
            "annotator_id": annotator_id,
        }
        for field in TEMPLATE_FIELDS:
            if (event_id, field) in overrides:
                row[field] = overrides[(event_id, field)]
        rows.append(row)
    return rows


class ExternalRealHoldoutV6AnnotationTests(unittest.TestCase):
    def test_generate_is_blind_blank_and_differently_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "public" / "events.jsonl"
            proposed = root / "private" / "proposed-gold-repair-ir.jsonl"
            event_rows = [
                {
                    "event_id": event_id,
                    "semantic_type": "PRIVATE_TYPE",
                    "candidate_id": f"CAND_SECRET_{event_id}",
                    "model_output": f"MODEL_SECRET_{event_id}",
                }
                for event_id in ("E1", "E2", "E3", "E4")
            ]
            proposed_rows = [
                {
                    "event_id": event_id,
                    "proposed_gold": f"GOLD_SECRET_{event_id}",
                    "candidate_id": f"PRIVATE_CAND_{event_id}",
                }
                for event_id in ("E1", "E2", "E3", "E4")
            ]
            write_jsonl(events, event_rows)
            write_jsonl(proposed, proposed_rows)

            path_a, path_b = generate_templates(
                events, proposed, root / "annotation", "human-a", "human-b", seed=7
            )
            rows_a = read_csv(path_a)
            rows_b = read_csv(path_b)

            self.assertEqual(tuple(rows_a[0]), TEMPLATE_FIELDS)
            self.assertEqual(
                {row["event_id"] for row in rows_a},
                {row["event_id"] for row in rows_b},
            )
            self.assertNotEqual(
                [row["event_id"] for row in rows_a],
                [row["event_id"] for row in rows_b],
            )
            for row in rows_a + rows_b:
                self.assertTrue(all(row[field] == "" for field in ANSWER_FIELDS))
            self.assertTrue(all(row["annotator_id"] == "human-a" for row in rows_a))
            self.assertTrue(all(row["annotator_id"] == "human-b" for row in rows_b))
            combined = path_a.read_text(encoding="utf-8-sig") + path_b.read_text(
                encoding="utf-8-sig"
            )
            self.assertNotIn("GOLD_SECRET", combined)
            self.assertNotIn("CAND_SECRET", combined)
            self.assertNotIn("MODEL_SECRET", combined)

    def test_analyze_writes_agreement_and_only_disagreements_for_adjudication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_ids = ["E1", "E2", "E3"]
            sheet_a = root / "a.csv"
            sheet_b = root / "b.csv"
            write_csv(sheet_a, completed_rows(event_ids, "human-a"))
            write_csv(
                sheet_b,
                completed_rows(
                    list(reversed(event_ids)),
                    "human-b",
                    overrides={
                        ("E2", "decision"): "ABSTAIN",
                        ("E3", "semantic_type"): "GENERAL_RULE_EXCEPTION",
                    },
                ),
            )

            summary = analyze_annotations(sheet_a, sheet_b, root / "results")

            self.assertEqual(summary["event_count"], 3)
            self.assertEqual(summary["disagreement_event_count"], 2)
            self.assertEqual(summary["disagreement_field_count"], 2)
            self.assertAlmostEqual(
                summary["field_agreement"]["decision"]["raw_agreement"], 2 / 3
            )
            self.assertAlmostEqual(summary["decision_cohen_kappa"], 0.0)
            self.assertAlmostEqual(summary["semantic_type_cohen_kappa"], 0.0)
            disagreements = read_csv(root / "results" / "disagreements.csv")
            adjudication = read_csv(root / "results" / "adjudication-template.csv")
            self.assertEqual(
                {(row["event_id"], row["field"]) for row in disagreements},
                {("E2", "decision"), ("E3", "semantic_type")},
            )
            self.assertEqual(len(adjudication), 2)
            self.assertTrue(
                all(
                    row["adjudicated_value"] == ""
                    and row["adjudicator_id"] == ""
                    and row["adjudication_reason"] == ""
                    for row in adjudication
                )
            )
            saved_summary = json.loads(
                (root / "results" / "agreement-summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_summary, summary)

    def test_generate_rejects_same_person_and_event_set_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            proposed = root / "proposed.jsonl"
            write_jsonl(events, [{"event_id": "E1"}, {"event_id": "E2"}])
            write_jsonl(proposed, [{"event_id": "E1"}, {"event_id": "E3"}])

            with self.assertRaises(AnnotationWorkflowError):
                generate_templates(events, proposed, root / "out", "same", " SAME ")
            with self.assertRaises(AnnotationWorkflowError):
                generate_templates(events, proposed, root / "out", "a", "b")

    def test_analyze_fails_closed_for_incomplete_or_same_annotator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sheet_a = root / "a.csv"
            sheet_b = root / "b.csv"
            incomplete = completed_rows(["E1", "E2"], "human-a")
            incomplete[0]["reason"] = ""
            write_csv(sheet_a, incomplete)
            write_csv(sheet_b, completed_rows(["E1", "E2"], "human-b"))
            output = root / "results"

            self.assertEqual(
                main(
                    [
                        "analyze",
                        "--annotator-a",
                        str(sheet_a),
                        "--annotator-b",
                        str(sheet_b),
                        "--output-dir",
                        str(output),
                    ]
                ),
                2,
            )
            self.assertFalse(output.exists())

            write_csv(sheet_a, completed_rows(["E1", "E2"], "human-a"))
            write_csv(sheet_b, completed_rows(["E1", "E2"], " HUMAN-A "))
            with self.assertRaises(AnnotationWorkflowError):
                analyze_annotations(sheet_a, sheet_b, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
