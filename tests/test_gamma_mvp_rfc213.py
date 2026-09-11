from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from run_gamma_gold_ir_closure_eval import (  # noqa: E402
    materialize_update_literal_atomic,
    sha256_file,
)
from run_gamma_gold_ir_compile_eval import compile_update_literal  # noqa: E402


BENCHMARK = ROOT / "benchmark" / "rfc-213-confirmatory-core"


def first_ready_operation() -> tuple[str, dict[str, object], Path]:
    candidate_csv = BENCHMARK / "repair-stage/candidates/external-real-candidate-template.csv"
    with candidate_csv.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if row["event_id"] == "H5_E001" and row["candidate_id"] == "CAND_001":
                operation = json.loads(row["operation_json"])
                source = BENCHMARK / "repair-stage/mutants/H5_E001.owl"
                return row["event_id"], operation, source
    raise RuntimeError("missing H5_E001/CAND_001 operation")


class GammaMvpCompileTests(unittest.TestCase):
    def test_update_literal_valid_input_compiles_expected_patch(self) -> None:
        _, operation, _ = first_ready_operation()
        compiled = compile_update_literal(operation)
        self.assertEqual(compiled["status"], "COMPILED")
        self.assertEqual(compiled["gamma_operator"], "UPDATE_LITERAL")
        self.assertEqual(compiled["triples_removed"], 1)
        self.assertEqual(compiled["triples_added"], 1)
        self.assertEqual(compiled["subject_iri"], operation["subject_iri"])
        self.assertEqual(compiled["predicate_iri"], operation["predicate_iri"])

    def test_update_literal_missing_fields_are_invalid_ir(self) -> None:
        _, operation, _ = first_ready_operation()
        for field in ("subject_iri", "predicate_iri", "old_value", "new_value"):
            with self.subTest(field=field):
                broken = dict(operation)
                broken.pop(field)
                compiled = compile_update_literal(broken)
                self.assertEqual(compiled["status"], "INVALID_IR")
                self.assertIn("missing_fields", compiled["error"])

    def test_update_literal_value_object_type_errors_are_invalid_ir(self) -> None:
        _, operation, _ = first_ready_operation()
        for field, invalid in (("old_value", "literal"), ("new_value", 42)):
            with self.subTest(field=field):
                broken = json.loads(json.dumps(operation))
                broken[field] = invalid
                compiled = compile_update_literal(broken)
                self.assertEqual(compiled["status"], "INVALID_IR")

    def test_update_literal_missing_literal_members_are_invalid_ir(self) -> None:
        _, operation, _ = first_ready_operation()
        for value_field in ("old_value", "new_value"):
            for member in ("lexical", "datatype"):
                with self.subTest(value_field=value_field, member=member):
                    broken = json.loads(json.dumps(operation))
                    broken[value_field].pop(member)
                    compiled = compile_update_literal(broken)
                    self.assertEqual(compiled["status"], "INVALID_IR")

    def test_unsupported_construct_is_rejected_without_patch_atoms(self) -> None:
        _, operation, _ = first_ready_operation()
        for operator in ("REPLACE_SUBCLASS", "REPLACE_RANGE", "ADD_OBJECT_PROPERTY", "DELETE_AXIOM"):
            with self.subTest(operator=operator):
                broken = dict(operation)
                broken["operator"] = operator
                compiled = compile_update_literal(broken)
                self.assertEqual(compiled["status"], "UNSUPPORTED")
                self.assertNotIn("triples_removed", compiled)
                self.assertNotIn("triples_added", compiled)

    def test_non_literal_value_is_rejected_as_unsupported(self) -> None:
        _, operation, _ = first_ready_operation()
        for field in ("old_value", "new_value"):
            with self.subTest(field=field):
                broken = json.loads(json.dumps(operation))
                broken[field] = {"kind": "iri", "iri": "file:///example#Object"}
                compiled = compile_update_literal(broken)
                self.assertEqual(compiled["status"], "UNSUPPORTED")
                self.assertEqual(compiled["error"], "non_literal_update")


class GammaMvpAtomicMaterializationTests(unittest.TestCase):
    def test_valid_materialization_is_byte_deterministic(self) -> None:
        _, operation, source = first_ready_operation()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            outputs = []
            for index in range(10):
                destination = tmp_path / f"repeat-{index}.owl"
                result = materialize_update_literal_atomic(
                    source_path=source, operation=operation, dest_path=destination
                )
                self.assertEqual(result["status"], "PASS")
                outputs.append(destination.read_bytes())
            self.assertEqual(len(set(outputs)), 1)

    def test_source_hash_mismatch_fails_atomically(self) -> None:
        _, operation, source = first_ready_operation()
        before = sha256_file(source)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "should-not-exist.owl"
            result = materialize_update_literal_atomic(
                source_path=source,
                operation=operation,
                dest_path=dest,
                expected_source_sha256="0" * 64,
            )
            self.assertEqual(result["status"], "PRECONDITION_FAILED")
            self.assertEqual(result["triples_removed"], 0)
            self.assertEqual(result["triples_added"], 0)
            self.assertFalse(result["dest_written"])
            self.assertFalse(dest.exists())
            self.assertEqual(sha256_file(source), before)

    def test_missing_old_triple_fails_atomically(self) -> None:
        _, operation, source = first_ready_operation()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_copy = tmp_path / "source.owl"
            shutil.copy2(source, source_copy)
            before = sha256_file(source_copy)
            broken = json.loads(json.dumps(operation))
            broken["old_value"]["lexical"] = "not_present_in_source"
            dest = tmp_path / "should-not-exist.owl"
            result = materialize_update_literal_atomic(
                source_path=source_copy,
                operation=broken,
                dest_path=dest,
            )
            self.assertEqual(result["status"], "PRECONDITION_FAILED")
            self.assertEqual(result["triples_removed"], 0)
            self.assertEqual(result["triples_added"], 0)
            self.assertTrue(result["source_unchanged"])
            self.assertFalse(result["dest_written"])
            self.assertFalse(dest.exists())
            self.assertEqual(sha256_file(source_copy), before)

    def test_unsupported_operator_fails_atomically(self) -> None:
        _, operation, source = first_ready_operation()
        before = sha256_file(source)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "should-not-exist.owl"
            broken = dict(operation)
            broken["operator"] = "ADD_PROPERTY_VALUE"
            result = materialize_update_literal_atomic(
                source_path=source,
                operation=broken,
                dest_path=dest,
            )
            self.assertEqual(result["status"], "UNSUPPORTED")
            self.assertEqual(result["triples_removed"], 0)
            self.assertEqual(result["triples_added"], 0)
            self.assertFalse(result["dest_written"])
            self.assertFalse(dest.exists())
            self.assertEqual(sha256_file(source), before)

    def test_new_triple_already_present_fails_atomically(self) -> None:
        _, operation, source = first_ready_operation()
        before = sha256_file(source)
        broken = json.loads(json.dumps(operation))
        broken["new_value"] = broken["old_value"]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "should-not-exist.owl"
            result = materialize_update_literal_atomic(
                source_path=source, operation=broken, dest_path=dest
            )
            self.assertEqual(result["status"], "PRECONDITION_FAILED")
            self.assertEqual(result["error"], "new_triple_already_present")
            self.assertEqual(result["triples_removed"], 0)
            self.assertEqual(result["triples_added"], 0)
            self.assertFalse(dest.exists())
            self.assertEqual(sha256_file(source), before)


if __name__ == "__main__":
    unittest.main()
