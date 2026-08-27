from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import build_external_real_v8_grounded as v8  # noqa: E402
import gate_external_real_v8_grounded as gate  # noqa: E402
from adjudicate_external_real_v8_semantic_support import adjudicate_semantic_support  # noqa: E402
from audit_external_real_v8_metadata_leakage import audit_event  # noqa: E402
from external_real_v8_layout import (  # noqa: E402
    BenchmarkLayout,
    StageAccessError,
    candidate_selection_paths,
    construction_paths,
)
from run_auto_formal_policy_batch_v3 import find_forbidden_input_markers  # noqa: E402
from run_auto_policy_v3_natural_evidence_robustness import (  # noqa: E402
    RetrievalResult,
    assert_raw_isolation,
)


class QueryBoundaryTests(unittest.TestCase):
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
            "notes": "SECRET_NOTE_ANSWER candidate oracle gold",
        }

    def test_full_query_uses_title_and_excludes_notes(self) -> None:
        text = v8.metadata_query_text(self.event, "full")
        self.assertIn("Alpha target", text)
        self.assertIn("Gamma subject", text)
        self.assertNotIn("SECRET_NOTE_ANSWER", text)
        self.assertNotIn("oracle", text)

    def test_query_tokens_include_cjk_character_grams(self) -> None:
        event = {
            "title": "叶类根茎类蔬菜轮作保险金额拆分",
            "case_context": "",
            "subject_label": "叶类、根茎类蔬菜",
            "predicate_label": "每亩保险金额分项",
            "domain": "insurance",
            "semantic_type": "TEMPORAL_VERSION",
        }
        tokens = v8.query_tokens(event, "full")
        self.assertIn("叶类", tokens)
        self.assertIn("轮作", tokens)
        self.assertIn("保险", tokens)
        text = v8.metadata_query_text(self.event, "light")
        self.assertNotIn("Alpha target", text)
        self.assertIn("Gamma subject", text)
        self.assertNotIn("SECRET_NOTE_ANSWER", text)


class ExcerptIsolationTests(unittest.TestCase):
    def test_rendered_excerpt_has_no_forbidden_markers(self) -> None:
        evidence = v8.render_evidence_excerpt(
            "EXT_E001",
            {
                "source_title": "Official terms",
                "source_url": "https://example.test/terms.pdf",
                "old_source_title": "Prior terms",
                "old_source_url": "https://example.test/old.pdf",
            },
            ["The insured amount per mu is 2000 yuan after the 2026 terms take effect."],
            ["The 2025 terms stated a combined amount without the 2026 split."],
        )
        document = v8.render_document_excerpt(
            "Official terms",
            "https://example.test/terms.pdf",
            ["The insured amount per mu is 2000 yuan after the 2026 terms take effect."],
        )
        for text in (evidence, document):
            self.assertEqual(find_forbidden_input_markers(text), [])
            self.assertNotIn("Event:", text)
            self.assertNotIn("Target:", text)
            self.assertNotIn("Benchmark boundary", text)
            self.assertNotIn("Oracle", text)
            self.assertNotIn("candidate", text.lower())

    def test_split_units_keeps_table_rows_and_drops_toc(self) -> None:
        text = "\n".join(
            [
                "一、北京市 2026 年政策性农业保险费率明细表 ....................",
                "叶类、根茎类 春播1000元",
                "夏播及秋播800元 合计2000元",
                "The English prose window must stay long enough for latin retrieval units.",
            ]
        )
        units = v8.split_units(text)
        joined = " ".join(units)
        self.assertIn("春播1000元", joined)
        self.assertNotIn("........", joined)
        windows = v8.sanitize_windows(
            [
                "Status: READY",
                "Public candidate values must not appear.",
                "Safe window describing a 2026 insurance amount of 2000 yuan per mu.",
            ]
        )
        self.assertEqual(
            windows,
            ["Safe window describing a 2026 insurance amount of 2000 yuan per mu."],
        )


class GatePolicyTests(unittest.TestCase):
    def test_expand_conclusion_values_adds_source_language_surface(self) -> None:
        expanded = " ".join(gate.expand_conclusion_values(["spring=1100;summer_autumn=900;total=2000"]))
        self.assertIn("春播", expanded)
        self.assertIn("1100", expanded)
        self.assertIn("夏播", expanded)
        self.assertTrue(gate.keepable("RETRIEVAL_READY", "PASS"))
        self.assertTrue(gate.keepable("RETRIEVAL_READY", "SEMANTIC_PASS"))
        self.assertFalse(gate.keepable("RETRIEVAL_READY", "WARN"))
        self.assertFalse(gate.keepable("RETRIEVAL_READY", "FAIL"))
        self.assertFalse(gate.keepable("RETRIEVAL_FAILED", "PASS"))

    def test_scaffold_does_not_copy_candidate_private_or_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dst = root / "benchmark" / "external-real-v8-grounded"
            src = root / "benchmark" / "external-real-v4-evidence-aligned"
            (src / "input").mkdir(parents=True)
            (src / "source-intake").mkdir(parents=True)
            (src / "private").mkdir()
            (src / "rules").mkdir()
            (src / "input" / "external-real-event-template.csv").write_text(
                "event_id,split,semantic_type,domain,title,case_context,subject_label,predicate_label,value_kind,allowed_min,allowed_max,document_ids,source_owl,source_url,retrieved_at,status,notes\n"
                "EXT_E001,external,TEMPORAL_VERSION,insurance,t,c,s,p,literal_string,,,EXT_DOC_001_2026,mutants/EXT_E001.owl,https://example.test,2026-08-26,READY,construction note\n",
                encoding="utf-8",
            )
            (src / "input" / "external-real-document-template.csv").write_text(
                "document_id,event_id,file_name,source_title,source_url,publisher,publication_date,effective_from,effective_to,document_type,source_type,license_note,sha256,status,notes\n"
                "EXT_DOC_001_2026,EXT_E001,doc.txt,title,https://example.test,pub,2026,2026-01-01,2026-12-31,policy,EXTERNAL_PUBLIC_PDF,note,abc,READY,note\n",
                encoding="utf-8",
            )
            (src / "input" / "external-real-candidate-template.csv").write_text(
                "event_id,candidate_id,display_value,operation_json,status,notes\nEXT_E001,CAND_001,x,{},READY,note\n",
                encoding="utf-8",
            )
            (src / "source-intake" / "external-real-v4-event-source-alignment.csv").write_text(
                "event_id,domain,source_id,source_card_title,alignment_score,alignment_status\n"
                "EXT_E001,insurance,NAT_INS_BEIJING_2026_TERMS,,1.0,ALIGNED\n",
                encoding="utf-8",
            )
            (src / "source-intake" / "external-real-v4-evidence-aligned-source-families.csv").write_text(
                "source_id,domain,publisher,source_title,source_url,old_source_title,old_source_url,public_basis,raw_window_count\n"
                "NAT_INS_BEIJING_2026_TERMS,insurance,pub,title,https://example.test,old,https://example.test/old,basis,1\n",
                encoding="utf-8",
            )
            (src / "private" / "oracle.csv").write_text("secret\n", encoding="utf-8")
            (src / "rules" / "EXT_E001-formal-policy.json").write_text("{}", encoding="utf-8")
            with patch.object(v8, "SRC", src), patch.object(v8, "DST", dst), patch.object(
                v8, "REGISTRY_CSV", root / "missing.csv"
            ):
                v8.write_scaffold()
            self.assertFalse((dst / "public" / "events" / "external-real-candidate-template.csv").exists())
            self.assertFalse((dst / "public" / "candidates").exists())
            self.assertFalse((dst / "private").exists())
            self.assertFalse((dst / "rules").exists())
            events = v8.read_csv(dst / "public" / "events" / "external-real-event-template.csv")
            self.assertEqual(events[0]["notes"], "")
            self.assertEqual(events[0]["source_owl"], "")
            self.assertIn("semantic_support", events[0])


class StageAccessTests(unittest.TestCase):
    def test_construction_cannot_read_repair_or_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "benchmark" / "external-real-v8-grounded"
            (root / "public" / "events").mkdir(parents=True)
            (root / "repair-stage" / "candidates").mkdir(parents=True)
            (root / "private" / "oracle").mkdir(parents=True)
            (root / "rules").mkdir()
            candidate = root / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
            oracle = root / "private" / "oracle" / "external-real-oracle-template.csv"
            candidate.write_text("event_id\n", encoding="utf-8")
            oracle.write_text("event_id\n", encoding="utf-8")
            layout = BenchmarkLayout(root)
            with self.assertRaises(StageAccessError):
                layout.assert_readable("construction", candidate)
            with self.assertRaises(StageAccessError):
                layout.assert_readable("construction", oracle)
            with self.assertRaises(StageAccessError):
                layout.assert_readable("candidate_selection", oracle)
            layout.assert_readable("candidate_selection", candidate)
            layout.assert_readable("evaluation", oracle)
            paths = construction_paths(root)
            self.assertNotIn("candidate_csv", paths)
            self.assertTrue(str(paths["event_csv"]).replace("\\", "/").endswith("public/events/external-real-event-template.csv"))
            selection = candidate_selection_paths(root)
            self.assertEqual(selection["candidate_csv"], candidate)


class SemanticAdjudicationTests(unittest.TestCase):
    def test_paraphrase_can_pass_when_lexical_overlap_is_weak(self) -> None:
        event = {
            "event_id": "EXT_E999",
            "semantic_type": "TEMPORAL_VERSION",
            "title": "FDA 2025 guidance",
            "case_context": "",
            "subject_label": "FDA guidance",
        }
        evidence = "The 2025 guidance supersedes the 2023 guidance and is no longer current for prior submissions."
        row = adjudicate_semantic_support(
            event,
            evidence,
            {"status": "WARN", "missing_numeric_tokens": "", "distinctive_token_count": 1, "reason": "partial"},
        )
        self.assertEqual(row["lexical_status"], "LEXICAL_AMBIGUOUS")
        self.assertEqual(row["support_status"], "SEMANTIC_PASS")
        self.assertEqual(row["semantic_support"], "PASS")

    def test_lexical_pass_confirms_semantic_pass(self) -> None:
        row = adjudicate_semantic_support(
            {"event_id": "EXT_E001", "semantic_type": "TEMPORAL_VERSION"},
            "window",
            {"status": "PASS", "missing_numeric_tokens": "", "distinctive_token_count": 2},
        )
        self.assertEqual(row["support_status"], "LEXICAL_PASS")
        self.assertEqual(row["semantic_support"], "PASS")


class MetadataLeakageTests(unittest.TestCase):
    def test_title_supersedes_is_full_only_leak(self) -> None:
        event = {
            "event_id": "EXT_E999",
            "title": "FDA 2025 guidance supersedes 2023 guidance",
            "case_context": "",
            "subject_label": "FDA guidance",
            "predicate_label": "status",
            "domain": "medical_device_cybersecurity",
            "semantic_type": "TEMPORAL_VERSION",
        }
        row = audit_event(event, ["canonical-secret-result-value"])
        self.assertEqual(row["severity"], "LEAK_FULL_ONLY")

    def test_light_metadata_relation_is_light_leak(self) -> None:
        event = {
            "event_id": "EXT_E998",
            "title": "Guidance update",
            "case_context": "",
            "subject_label": "FDA 2025 guidance supersedes 2023 guidance",
            "predicate_label": "status",
            "domain": "medical_device_cybersecurity",
            "semantic_type": "TEMPORAL_VERSION",
        }
        row = audit_event(event, [])
        self.assertEqual(row["severity"], "LEAK_LIGHT")


class RawIsolationTests(unittest.TestCase):
    def test_failed_raw_retrieval_cannot_carry_note_or_candidate(self) -> None:
        failed = RetrievalResult(
            evidence="",
            retrieval_status="RETRIEVAL_FAILED",
            source_label="raw_short_window",
            raw_source_used=True,
            fallback_used=False,
            retrieved_doc_count=0,
            retrieved_window_count=0,
        )
        assert_raw_isolation(failed, "RAW_SHORT_WINDOW")
        with self.assertRaises(AssertionError):
            assert_raw_isolation(
                RetrievalResult(
                    evidence="event summary",
                    retrieval_status="RETRIEVAL_FAILED",
                    source_label="raw_short_window",
                    raw_source_used=True,
                    fallback_used=False,
                    retrieved_doc_count=0,
                    retrieved_window_count=0,
                    note_used=True,
                ),
                "RAW_SHORT_WINDOW",
            )


if __name__ == "__main__":
    unittest.main()
