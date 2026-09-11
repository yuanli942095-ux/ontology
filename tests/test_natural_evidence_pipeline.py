from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import run_auto_formal_policy_batch_v3 as v3  # noqa: E402
import run_auto_policy_v3_natural_evidence_robustness as natural  # noqa: E402


class CandidateIsolationTests(unittest.TestCase):
    def test_candidate_section_and_status_are_removed(self) -> None:
        evidence = """# Evidence
Raw public source excerpt.

Public candidate values:
- CAND_001: leaked answer
- CAND_002: distractor

Status:
READY
"""

        cleaned = v3.remove_candidate_sections(evidence)

        self.assertEqual(cleaned, "# Evidence\nRaw public source excerpt.")
        self.assertEqual(v3.find_forbidden_input_markers(cleaned), [])

    def test_forbidden_input_markers_cover_all_disallowed_labels(self) -> None:
        text = (
            "CAND_001 candidate values oracle allowed_values "
            "formal-policy gold Status:"
        )

        self.assertEqual(
            set(v3.find_forbidden_input_markers(text)),
            {
                "candidate_id",
                "candidate_values",
                "oracle",
                "allowed_values",
                "formal_policy",
                "gold",
                "status",
            },
        )


class RetrievalBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.event = {
            "event_id": "EXT_E999",
            "status": "READY",
            "title": "Alpha target",
            "case_context": "Assess beta requirement",
            "subject_label": "Gamma subject",
            "predicate_label": "Delta predicate",
            "domain": "test_domain",
            "semantic_type": "TEMPORAL_VERSION",
        }

    def test_query_terms_use_only_frozen_full_metadata_fields(self) -> None:
        event = dict(self.event)
        event["notes"] = "SECRET_NOTE_ANSWER"

        terms = natural.query_terms(event, metadata_mode="full")

        self.assertIn("Alpha", terms)
        self.assertNotIn("SECRET_NOTE_ANSWER", terms)

    def test_metadata_light_query_excludes_title_and_case_context(self) -> None:
        terms = natural.query_terms(self.event, metadata_mode="light")

        self.assertNotIn("Alpha", terms)
        self.assertNotIn("Assess", terms)
        self.assertIn("Gamma", terms)
        self.assertIn("TEMPORAL_VERSION", terms)

    def test_metadata_light_target_code_cannot_read_full_metadata(self) -> None:
        event = dict(self.event)
        event["title"] = "Criterion 2.4.11"
        event["case_context"] = "Assess 2.4.11"

        self.assertEqual(natural.target_code(event, metadata_mode="light"), "")
        self.assertEqual(
            natural.target_code(event, metadata_mode="full"),
            "2.4.11",
        )

    def test_no_metadata_query_has_no_target_terms(self) -> None:
        self.assertEqual(
            natural.query_terms(self.event, metadata_mode="none"),
            [],
        )

    def test_prompt_metadata_context_drops_unknown_csv_columns(self) -> None:
        event = {
            **self.event,
            "notes": "hidden construction note",
            "source_owl": "hidden path",
            "source_url": "https://hidden.test",
        }

        light = natural.metadata_context(event, "light")
        none = natural.metadata_context(event, "none")

        self.assertNotIn("notes", light)
        self.assertNotIn("source_owl", light)
        self.assertNotIn("source_url", light)
        self.assertEqual(light["title"], "")
        self.assertEqual(none["domain"], "")
        self.assertEqual(none["event_id"], "")

    def test_stored_raw_window_wrapper_is_not_model_evidence(self) -> None:
        stored = """Source title: Example
Event: EXT_E001
Target: leaked retrieval hint

Raw source excerpt window:

The actual public source paragraph is retained verbatim.

Benchmark boundary:
This file omits private Oracle labels.
"""

        excerpt = natural.extract_raw_public_excerpt(stored)

        self.assertEqual(
            excerpt,
            "The actual public source paragraph is retained verbatim.",
        )
        self.assertNotIn("Target:", excerpt)
        self.assertNotIn("Oracle", excerpt)

    def test_raw_retrieval_failure_never_falls_back_to_note(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing_doc = {
                "document_id": "DOC_MISSING",
                "file_name": "missing.html",
                "source_title": "Missing source",
                "source_url": "https://example.test/missing",
            }
            with patch.object(natural, "DOCUMENT_DIR", Path(directory)):
                result = natural.source_context(
                    self.event,
                    [missing_doc],
                    limit=5,
                    metadata_mode="full",
                )

        self.assertEqual(result.retrieval_status, "RETRIEVAL_FAILED")
        self.assertEqual(result.evidence, "")
        self.assertTrue(result.raw_source_used)
        self.assertFalse(result.fallback_used)
        self.assertEqual(result.retrieved_doc_count, 0)
        self.assertEqual(result.retrieved_window_count, 0)

    def test_forbidden_raw_window_is_rejected_before_prompting(self) -> None:
        result = natural.retrieval_result(
            [
                (
                    "DOC_LEAK",
                    "https://example.test/leak",
                    "Source title: Public source\n"
                    "Source URL: https://example.test/leak\n"
                    "Public candidate values: CAND_001",
                )
            ],
            "test",
        )

        self.assertEqual(result.retrieval_status, "FORBIDDEN_INPUT")
        self.assertEqual(result.evidence, "")
        self.assertFalse(result.fallback_used)

    def test_forbidden_raw_window_is_dropped_when_safe_window_exists(self) -> None:
        result = natural.retrieval_result(
            [
                (
                    "DOC_LEAK",
                    "https://example.test/leak",
                    "Source title: Leaked\nSource URL: https://example.test/leak\n"
                    "Private Oracle construction note.",
                ),
                (
                    "DOC_SAFE",
                    "https://example.test/safe",
                    "Source title: Safe\nSource URL: https://example.test/safe\n"
                    "A sufficiently long raw public source paragraph.",
                ),
            ],
            "test",
        )

        self.assertEqual(result.retrieval_status, "RETRIEVED")
        self.assertNotIn("Oracle", result.evidence)
        self.assertIn("raw public source paragraph", result.evidence)
        self.assertIn("oracle", result.forbidden_input_markers)

    def test_no_metadata_uses_deterministic_raw_html_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "source.html").write_text(
                "<html><body><p>A sufficiently long first raw public paragraph.</p>"
                "<p>A sufficiently long second raw public paragraph.</p></body></html>",
                encoding="utf-8",
            )
            doc = {
                "document_id": "DOC_HTML",
                "file_name": "source.html",
                "source_title": "Public HTML source",
                "source_url": "https://example.test/source",
            }
            with patch.object(natural, "DOCUMENT_DIR", Path(directory)):
                result = natural.provenance_source_context(
                    self.event,
                    [doc],
                    metadata_mode="none",
                )

        self.assertEqual(result.retrieval_status, "RETRIEVED")
        self.assertIn("first raw public paragraph", result.evidence)
        self.assertFalse(result.fallback_used)

    def test_ready_event_ids_are_loaded_dynamically(self) -> None:
        events = {
            "EXT_E001": {"event_id": "EXT_E001", "status": "READY"},
            "EXT_E213": {"event_id": "EXT_E213", "status": "ready"},
            "EXT_E214": {"event_id": "EXT_E214", "status": "DRAFT"},
        }

        self.assertEqual(
            natural.event_ids(events),
            ["EXT_E001", "EXT_E213"],
        )

    def test_variants_are_the_six_strict_conditions(self) -> None:
        self.assertEqual(
            set(natural.VARIANTS),
            {
                "STRUCTURED_NOTE",
                "RAW_SHORT_WINDOW",
                "RAW_PROVENANCE_WINDOW",
                "RAW_WINDOW_WITH_DISTRACTOR",
                "RAW_WINDOW_METADATA_LIGHT",
                "RAW_WINDOW_NO_METADATA",
            },
        )

    def test_raw_distractor_is_selected_from_real_documents(self) -> None:
        events = {
            "EXT_E001": {
                **self.event,
                "event_id": "EXT_E001",
                "source_url": "https://primary.test/rule",
            },
            "EXT_E002": {
                **self.event,
                "event_id": "EXT_E002",
                "source_url": "https://other.test/rule",
            },
        }
        docs_by_event = {
            "EXT_E001": [],
            "EXT_E002": [
                {
                    "document_id": "DOC_OTHER",
                    "file_name": "other.txt",
                    "source_title": "Other public source",
                    "source_url": "https://other.test/rule",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "other.txt").write_text(
                "A sufficiently long raw public paragraph from another source family.",
                encoding="utf-8",
            )
            with patch.object(natural, "DOCUMENT_DIR", Path(directory)):
                result = natural.raw_distractor_context(
                    "EXT_E001",
                    events,
                    docs_by_event,
                    seed=20260827,
                )

        self.assertEqual(result.retrieval_status, "RETRIEVED")
        self.assertIn("raw public paragraph", result.evidence)
        self.assertNotIn("Evidence Summary", result.evidence)
        self.assertTrue(result.raw_source_used)
        self.assertFalse(result.fallback_used)


if __name__ == "__main__":
    unittest.main()
