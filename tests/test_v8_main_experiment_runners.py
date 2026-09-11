from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from run_auto_policy_v2_candidate_repair import (  # noqa: E402
    materialize_candidate_owl,
    resolve_candidate_owl_path,
    resolve_source_owl,
    select_ready_events,
)
from run_auto_policy_v3_natural_evidence_robustness import event_ids  # noqa: E402
from run_external_real_v1_candidate_ablation import utf8_safe  # noqa: E402
from run_external_real_v1_symbolic_closure import (  # noqa: E402
    load_graph,
    repair_checks,
)
from evaluate_auto_formal_policy_v2_semantic import select_event_rows  # noqa: E402


SOURCE_OWL = """<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF
   xmlns:ext="http://example.test/onto#"
   xmlns:owl="http://www.w3.org/2002/07/owl#"
   xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
   xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
>
  <rdf:Description rdf:about="http://example.test/onto#Item">
    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#NamedIndividual"/>
    <ext:amount rdf:datatype="http://www.w3.org/2001/XMLSchema#string">old=1</ext:amount>
  </rdf:Description>
</rdf:RDF>
"""

OPERATION = {
    "operator": "REPLACE_PROPERTY_VALUE",
    "subject_iri": "http://example.test/onto#Item",
    "predicate_iri": "http://example.test/onto#amount",
    "old_value": {
        "kind": "literal",
        "lexical": "old=1",
        "datatype": "http://www.w3.org/2001/XMLSchema#string",
    },
    "new_value": {
        "kind": "literal",
        "lexical": "new=2",
        "datatype": "http://www.w3.org/2001/XMLSchema#string",
    },
}


class ReadySubsetTests(unittest.TestCase):
    def test_select_ready_events_respects_only(self) -> None:
        rows = [
            {"event_id": "EXT_E001", "status": "READY"},
            {"event_id": "EXT_E002", "status": "READY"},
            {"event_id": "EXT_E003", "status": "DRAFT"},
        ]
        selected = select_ready_events(rows, {"EXT_E001"})
        self.assertEqual([row["event_id"] for row in selected], ["EXT_E001"])

    def test_select_event_rows_skips_non_ready(self) -> None:
        rows = [
            {"event_id": "EXT_E001", "status": "READY", "semantic_type": "TEMPORAL_VERSION"},
            {"event_id": "EXT_E002", "status": "READY", "semantic_type": "SCOPE"},
        ]
        selected = select_event_rows(rows, {"EXT_E002"})
        self.assertEqual([row["event_id"] for row in selected], ["EXT_E002"])

    def test_robustness_only_ids_keep_ready_events(self) -> None:
        events = {
            "EXT_E001": {"status": "READY"},
            "EXT_E002": {"status": "READY"},
        }
        self.assertEqual(event_ids(events, "EXT_E002"), ["EXT_E002"])
        self.assertEqual(event_ids(events, ""), ["EXT_E001", "EXT_E002"])


class OwlPathTests(unittest.TestCase):
    def test_empty_source_owl_uses_staged_mutant(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            mutants = root / "mutants"
            mutants.mkdir()
            mutant = mutants / "EXT_E001.owl"
            mutant.write_text(SOURCE_OWL, encoding="utf-8")
            path = resolve_source_owl(
                {"event_id": "EXT_E001", "source_owl": ""},
                mutants_dir=mutants,
                project_dir=root,
            )
            self.assertEqual(path, mutant)

    def test_explicit_source_owl_wins_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            explicit = root / "legacy" / "EXT_E001.owl"
            explicit.parent.mkdir()
            explicit.write_text(SOURCE_OWL, encoding="utf-8")
            path = resolve_source_owl(
                {"event_id": "EXT_E001", "source_owl": "legacy/EXT_E001.owl"},
                mutants_dir=root / "missing",
                project_dir=root,
            )
            self.assertEqual(path, explicit)

    def test_candidate_owl_prefers_prebuilt_then_output_cache(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prebuilt = root / "repair-stage" / "candidate-owls" / "EXT_E001" / "CAND_002.owl"
            prebuilt.parent.mkdir(parents=True)
            prebuilt.write_text(SOURCE_OWL, encoding="utf-8")
            path = resolve_candidate_owl_path(
                "EXT_E001",
                "CAND_002",
                benchmark_dir=root,
                built_dir=root / "built",
                output_dir=root / "output",
            )
            self.assertEqual(path, prebuilt)

            missing_event = resolve_candidate_owl_path(
                "EXT_E099",
                "CAND_001",
                benchmark_dir=root,
                built_dir=root / "built",
                output_dir=root / "output",
            )
            self.assertEqual(
                missing_event,
                root / "output" / "candidate-owls" / "EXT_E099" / "CAND_001.owl",
            )


class CandidateMaterializeTests(unittest.TestCase):
    def test_replace_property_value_writes_repaired_owl(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.owl"
            dest = root / "CAND_002.owl"
            source.write_text(SOURCE_OWL, encoding="utf-8")
            materialize_candidate_owl(source_path=source, operation=OPERATION, dest_path=dest)
            source_graph = load_graph(source)
            candidate_graph = load_graph(dest)
            triggers, repairs, message = repair_checks(source_graph, candidate_graph, OPERATION)
            self.assertTrue(triggers)
            self.assertTrue(repairs)
            self.assertEqual(message, "")
            self.assertIn("new=2", dest.read_text(encoding="utf-8"))


class Utf8SafeTests(unittest.TestCase):
    def test_unpaired_surrogate_can_be_json_utf8(self) -> None:
        cleaned = utf8_safe({"raw_content": "emoji\ud83d leftover"})
        encoded = json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
        self.assertIsInstance(encoded, bytes)
        self.assertNotIn("\ud83d", cleaned["raw_content"])


if __name__ == "__main__":
    unittest.main()
