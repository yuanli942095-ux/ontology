from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path

from rfc213_direct_repair_ir import (
    SCHEMA_VERSION,
    benchmark_literal_from_span,
    build_direct_ir_prompt,
    canonicalize_surface,
    ontology_literal_assertions,
    load_schema_contract,
    schema_sha256,
    source_windows,
    to_gamma_operation,
    validate_predicted_ir,
)
from semantic_v2_common import PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "rfc-213-confirmatory-core"
SOURCE = BENCHMARK / "repair-stage/mutants/H5_E001.owl"
EVIDENCE_PATH = BENCHMARK / "public/excerpts/H5_E001-evidence.md"


def gold_ir() -> dict:
    candidates = BENCHMARK / "repair-stage/candidates/external-real-candidate-template.csv"
    with candidates.open("r", encoding="utf-8-sig", newline="") as file:
        row = next(
            row for row in csv.DictReader(file)
            if row["event_id"] == "H5_E001" and row["candidate_id"] == "CAND_001"
        )
    operation = json.loads(row["operation_json"])
    evidence = EVIDENCE_PATH.read_text(encoding="utf-8")
    span = source_windows(evidence)[0][1]
    return {
        "schema_version": SCHEMA_VERSION,
        "decision": "REPAIR",
        "operation": "UPDATE_LITERAL",
        "target": {
            "subject_iri": operation["subject_iri"],
            "predicate_iri": operation["predicate_iri"],
            "old_value": operation["old_value"],
        },
        "replacement": {"new_value": operation["new_value"]},
        "evidence_spans": [span],
        "confidence": 1.0,
    }


class DirectRepairIRTests(unittest.TestCase):
    def test_method_owned_schema_contract_is_loaded(self) -> None:
        schema = load_schema_contract()
        self.assertEqual(schema["title"], "ECR-IR-Gamma Predicted Repair IR v1")
        self.assertEqual(len(schema_sha256()), 64)

    def test_ontology_context_contains_current_literal_without_candidates(self) -> None:
        assertions = ontology_literal_assertions(SOURCE)
        self.assertEqual(len(assertions), 1)
        self.assertIn("=unmodeled", assertions[0]["lexical"])
        self.assertNotIn("candidate", json.dumps(assertions).lower())

    def test_prompt_declares_candidate_blind_boundary(self) -> None:
        event_csv = BENCHMARK / "public/events/external-real-event-template.csv"
        with event_csv.open("r", encoding="utf-8-sig", newline="") as file:
            event = next(row for row in csv.DictReader(file) if row["event_id"] == "H5_E001")
        prompt = build_direct_ir_prompt(
            event, EVIDENCE_PATH.read_text(encoding="utf-8"), ontology_literal_assertions(SOURCE)
        )
        self.assertIn("No repair candidates", prompt)
        self.assertNotIn("CAND_001", prompt)

    def test_valid_repair_ir_passes_and_maps_to_gamma_operation(self) -> None:
        ir = gold_ir()
        validation = validate_predicted_ir(ir, source_path=SOURCE, evidence=EVIDENCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(validation["status"], "VALID")
        self.assertEqual(to_gamma_operation(ir)["operator"], "REPLACE_PROPERTY_VALUE")

    def test_candidate_field_is_rejected(self) -> None:
        ir = gold_ir()
        ir["candidate_id"] = "CAND_001"
        validation = validate_predicted_ir(ir, source_path=SOURCE, evidence=EVIDENCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(validation["status"], "INVALID_IR")
        self.assertIn("unexpected_fields", validation["errors"])

    def test_nonverbatim_evidence_is_rejected(self) -> None:
        ir = gold_ir()
        ir["evidence_spans"] = ["invented evidence span"]
        validation = validate_predicted_ir(ir, source_path=SOURCE, evidence=EVIDENCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(validation["status"], "INVALID_IR")
        self.assertIn("evidence_span_not_verbatim", validation["errors"])

    def test_wrong_old_value_fails_precondition(self) -> None:
        ir = gold_ir()
        ir["target"]["old_value"]["lexical"] = "not-in-ontology"
        validation = validate_predicted_ir(ir, source_path=SOURCE, evidence=EVIDENCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(validation["status"], "PRECONDITION_FAILED")

    def test_no_change_and_abstain_do_not_require_repair_fields(self) -> None:
        for decision in ("NO_CHANGE", "ABSTAIN"):
            with self.subTest(decision=decision):
                ir = {"schema_version": SCHEMA_VERSION, "decision": decision, "reason": "insufficient evidence"}
                validation = validate_predicted_ir(ir, source_path=SOURCE, evidence="anything")
                self.assertEqual(validation["status"], "VALID")

    def test_surface_canonicalizer_only_changes_whitespace(self) -> None:
        ir = gold_ir()
        original = ir["replacement"]["new_value"]["lexical"]
        ir["replacement"]["new_value"]["lexical"] = f"  {original}  "
        canonical = canonicalize_surface(ir)
        self.assertEqual(canonical["replacement"]["new_value"]["lexical"], original)

    def test_surface_text_is_encoded_without_candidates(self) -> None:
        ir = gold_ir()
        ir["replacement"]["new_value"]["lexical"] = ir["evidence_spans"][0]
        canonical = canonicalize_surface(ir)
        self.assertEqual(
            canonical["replacement"]["new_value"]["lexical"],
            gold_ir()["replacement"]["new_value"]["lexical"],
        )

    def test_public_builder_literal_encoding_is_reproducible(self) -> None:
        ir = gold_ir()
        evidence = EVIDENCE_PATH.read_text(encoding="utf-8")
        span = evidence.split("[SOURCE_WINDOW_1]", 1)[1].split("[SOURCE_WINDOW_2]", 1)[0].strip()
        derived = benchmark_literal_from_span(ir["target"]["old_value"]["lexical"], span)
        self.assertEqual(derived, ir["replacement"]["new_value"]["lexical"])

    def test_public_builder_literal_encoding_deduplicates_tokens(self) -> None:
        old = "dimension=unmodeled"
        derived = benchmark_literal_from_span(old, "Resolver MUST validate resolver signature.")
        self.assertEqual(derived, "dimension=resolver_validate_signature")

    def test_source_windows_are_parsed_without_markers(self) -> None:
        windows = source_windows(EVIDENCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(windows), 5)
        self.assertEqual(windows[0][0], "1")
        self.assertNotIn("[SOURCE_WINDOW_", windows[0][1])


if __name__ == "__main__":
    unittest.main()
