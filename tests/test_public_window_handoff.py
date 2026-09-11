from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import run_auto_policy_v3_natural_evidence_robustness as natural  # noqa: E402
from auto_policy_public_window_handoff import (  # noqa: E402
    INPUT_CONSTRUCTION_ERROR,
    PUBLIC_FROZEN_WINDOW,
    parse_frozen_windows,
    public_ready,
)
from run_auto_policy_v4_ir_candidate_repair import parse_semantic_ir  # noqa: E402


class FrozenWindowParseTests(unittest.TestCase):
    def test_parse_keeps_stored_order(self) -> None:
        text = (
            "Source title: Example\n"
            "Raw source excerpt window:\n"
            "\n"
            "- first frozen window text that is long enough\n"
            "- second frozen window text that is long enough\n"
        )
        self.assertEqual(
            parse_frozen_windows(text),
            [
                "first frozen window text that is long enough",
                "second frozen window text that is long enough",
            ],
        )

    def test_public_ready_requires_windows(self) -> None:
        self.assertTrue(public_ready({"retrieval_status": "RETRIEVAL_READY", "current_windows": "14"}))
        self.assertFalse(public_ready({"retrieval_status": "RETRIEVAL_READY", "current_windows": "0"}))
        self.assertFalse(public_ready({"retrieval_status": "RETRIEVAL_FAILED", "current_windows": "3"}))


class PublicWindowHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.docs = [
            {
                "document_id": "DOC_NEW",
                "file_name": "DOC_NEW.txt",
                "source_title": "Current source",
                "source_url": "https://example.test/current",
            },
            {
                "document_id": "DOC_2025",
                "file_name": "DOC_2025.txt",
                "source_title": "Previous source",
                "source_url": "https://example.test/previous",
            },
        ]

    def test_ready_windows_are_consumed_without_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "DOC_NEW.txt").write_text(
                "Source title: Current source\n"
                "Source URL: https://example.test/current\n\n"
                "Raw source excerpt window:\n\n"
                "- current frozen lodging window with five level definition\n",
                encoding="utf-8",
            )
            (root / "DOC_2025.txt").write_text(
                "Source title: Previous source\n"
                "Raw source excerpt window:\n\n"
                "- previous only window must not be selected for current handoff\n",
                encoding="utf-8",
            )
            with (
                patch.object(natural, "DOCUMENT_DIR", root),
                patch.object(
                    natural,
                    "public_retrieval_index",
                    return_value={"EXT_E002": {"retrieval_status": "RETRIEVAL_READY", "current_windows": "1"}},
                ),
                patch.object(natural, "provenance_source_context") as replay,
            ):
                result = natural.public_frozen_window_context("EXT_E002", self.docs)
                replay.assert_not_called()
        self.assertEqual(result.retrieval_status, "RETRIEVED")
        self.assertEqual(result.retrieval_source, PUBLIC_FROZEN_WINDOW)
        self.assertFalse(result.retrieval_replayed)
        self.assertFalse(result.candidate_used)
        self.assertFalse(result.oracle_used)
        self.assertFalse(result.manual_policy_used)
        self.assertIn("five level definition", result.evidence)
        self.assertNotIn("previous only window", result.evidence)
        natural.assert_raw_isolation(result, "RAW_WINDOW_METADATA_LIGHT")

    def test_ready_but_empty_windows_is_construction_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(natural, "DOCUMENT_DIR", Path(directory)),
                patch.object(
                    natural,
                    "public_retrieval_index",
                    return_value={"EXT_E002": {"retrieval_status": "RETRIEVAL_READY", "current_windows": "12"}},
                ),
                patch.object(natural, "provenance_source_context") as replay,
            ):
                result = natural.public_frozen_window_context("EXT_E002", self.docs)
                replay.assert_not_called()
        self.assertEqual(result.retrieval_status, INPUT_CONSTRUCTION_ERROR)
        self.assertEqual(result.evidence, "")
        self.assertFalse(result.retrieval_replayed)
        natural.assert_raw_isolation(result, "RAW_WINDOW_METADATA_LIGHT")

    def test_not_ready_is_retrieval_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(natural, "DOCUMENT_DIR", Path(directory)),
                patch.object(
                    natural,
                    "public_retrieval_index",
                    return_value={"EXT_E002": {"retrieval_status": "RETRIEVAL_FAILED", "current_windows": "0"}},
                ),
            ):
                result = natural.public_frozen_window_context("EXT_E002", self.docs)
        self.assertEqual(result.retrieval_status, "RETRIEVAL_FAILED")
        self.assertEqual(result.evidence, "")

    def test_light_variant_uses_frozen_handoff_when_public_csv_present(self) -> None:
        with patch.object(natural, "RETRIEVAL_CSV", Path("dummy.csv")), patch.object(
            natural, "public_frozen_window_context"
        ) as handoff:
            handoff.return_value = natural.RetrievalResult(
                evidence="frozen",
                retrieval_status="RETRIEVED",
                source_label=PUBLIC_FROZEN_WINDOW,
                raw_source_used=True,
                fallback_used=False,
                retrieved_doc_count=1,
                retrieved_window_count=1,
                retrieval_source=PUBLIC_FROZEN_WINDOW,
                retrieval_replayed=False,
            )
            result = natural.evidence_for_variant(
                "RAW_WINDOW_METADATA_LIGHT",
                "EXT_E002",
                {"event_id": "EXT_E002", "semantic_type": "TEMPORAL_VERSION"},
                self.docs,
                {},
                {},
                20260827,
            )
        handoff.assert_called_once()
        self.assertEqual(result.retrieval_source, PUBLIC_FROZEN_WINDOW)

    def test_v8_ext_e002_frozen_windows_are_nonempty(self) -> None:
        natural.configure_benchmark("external-real-v8-grounded")
        docs = natural.document_rows_by_event()["EXT_E002"]
        with patch.object(natural, "provenance_source_context") as replay:
            result = natural.public_frozen_window_context("EXT_E002", docs)
            replay.assert_not_called()
        self.assertEqual(result.retrieval_status, "RETRIEVED")
        self.assertGreater(result.retrieved_window_count, 0)
        self.assertIn("倒伏", result.evidence)


class InputConstructionIrTests(unittest.TestCase):
    def test_parser_labels_construction_error(self) -> None:
        record = {"status": "INPUT_CONSTRUCTION_ERROR", "response": None}
        event = {
            "event_id": "EXT_E002",
            "semantic_type": "CROSS_SENTENCE_SCOPE",
            "domain": "insurance",
            "subject_label": "x",
            "predicate_label": "y",
            "title": "",
            "case_context": "",
        }
        ir = parse_semantic_ir(record, event, robust_ir=True)
        self.assertEqual(ir.ir_status, "INPUT_CONSTRUCTION_ERROR")


if __name__ == "__main__":
    unittest.main()
