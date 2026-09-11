from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from http.client import IncompleteRead
from unittest.mock import patch

from evaluate_rfc213_phase3_gamma_closure import summarize
from combine_holdout_pipeline_details import combine_m16
from m13_llm_backends import LlmCallResult, call_llm_text, resume_artifact_is_complete
from run_rfc213_phase3_pipeline import ensure_empty_recovery, profile_manifest, read_csv
from semantic_v2_common import PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "rfc-213-confirmatory-core"


class RFC213Phase3Tests(unittest.TestCase):
    def test_profiles_have_explicit_expected_sizes(self) -> None:
        self.assertEqual(len(profile_manifest(BENCHMARK, "smoke")), 5)
        self.assertEqual(len(profile_manifest(BENCHMARK, "engineering")), 213)
        self.assertEqual(len(profile_manifest(BENCHMARK, "confirmatory")), 1065)

    def test_confirmatory_profile_has_five_runs_per_event(self) -> None:
        rows = profile_manifest(BENCHMARK, "confirmatory")
        event_rows = [row for row in rows if row["event_id"] == "H5_E001"]
        self.assertEqual([row["run"] for row in event_rows], ["1", "2", "3", "4", "5"])
        self.assertEqual(len({row["seed"] for row in event_rows}), 5)

    def test_empty_recovery_is_a_valid_header_only_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ir" / "details.csv"
            ensure_empty_recovery(path)
            self.assertTrue(path.is_file())
            self.assertEqual(read_csv(path), [])

    def test_selective_metrics_use_attempt_denominators(self) -> None:
        rows = [
            {"event_id": "E1", "decision": "SELECT", "wrong_repair": False, "ses_success": True,
             "semantic_ir_valid": True, "predicted_gamma_ir_exact": True, "gamma_accept": True,
             "precondition_failed": False, "target_cq_pass": True, "non_target_cq_preserved": True,
             "source_unchanged": True},
            {"event_id": "E2", "decision": "SELECT", "wrong_repair": True, "ses_success": False,
             "semantic_ir_valid": True, "predicted_gamma_ir_exact": False, "gamma_accept": True,
             "precondition_failed": False, "target_cq_pass": False, "non_target_cq_preserved": True,
             "source_unchanged": True},
            {"event_id": "E3", "decision": "ABSTAIN", "wrong_repair": False, "ses_success": False,
             "semantic_ir_valid": False, "predicted_gamma_ir_exact": False, "gamma_accept": False,
             "precondition_failed": False, "target_cq_pass": False, "non_target_cq_preserved": False,
             "source_unchanged": True},
        ]
        result = summarize("ALL", rows)
        self.assertAlmostEqual(result["coverage"], 2 / 3)
        self.assertAlmostEqual(result["wrr"], 1 / 3)
        self.assertAlmostEqual(result["selective_risk"], 1 / 2)
        self.assertAlmostEqual(result["ses"], 1 / 3)
        self.assertEqual(result["source_unchanged_rate"], 1.0)

    def test_transport_retry_succeeds_on_third_attempt(self) -> None:
        success = LlmCallResult("ok", 1, 1, "stop", "deepseek_api", "test")
        with patch("m13_llm_backends.call_deepseek_text", side_effect=[IncompleteRead(b"a"), IncompleteRead(b"b"), success]) as call:
            with patch("m13_llm_backends.time.sleep") as sleep:
                result = call_llm_text("deepseek_api", "prompt", 1, 30)
        self.assertEqual(call.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(result.attempt_count, 3)
        self.assertEqual(len(result.retry_errors), 2)

    def test_non_transport_error_is_not_retried(self) -> None:
        with patch("m13_llm_backends.call_deepseek_text", side_effect=ValueError("bad schema")) as call:
            with self.assertRaises(ValueError):
                call_llm_text("deepseek_api", "prompt", 1, 30)
        self.assertEqual(call.call_count, 1)

    def test_resume_rejects_transport_failed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            failed = Path(tmp) / "failed.json"
            failed.write_text('{"status":"error:IncompleteRead"}', encoding="utf-8")
            good = Path(tmp) / "good.json"
            good.write_text('{"status":"OK"}', encoding="utf-8")
            self.assertFalse(resume_artifact_is_complete(failed))
            self.assertTrue(resume_artifact_is_complete(good))

    def test_m16_merge_preserves_upstream_ir_status(self) -> None:
        upstream = [{
            "event_id": "E1", "run": "1", "seed": "7", "semantic_type": "GENERAL_RULE_EXCEPTION",
            "domain": "test", "ir_status": "OK", "decision_path": "IR_RANK_ABSTAIN",
            "full_closure_success": "False",
        }]
        prior = [{
            "event_id": "E1", "run": "1", "seed": "7", "semantic_type": "GENERAL_RULE_EXCEPTION",
            "domain": "test", "final_decision_path": "IR_RANK_ABSTAIN", "used": "M14_GRE_CSS",
        }]
        recovery = [{
            "event_id": "E1", "run": "1", "seed": "7", "semantic_type": "GENERAL_RULE_EXCEPTION",
            "domain": "test", "selected_candidate_id": "CAND_001", "decision_path": "M16_ENTAILMENT_SELECT",
            "selection_oracle_correct": "True", "full_closure_success": "True",
        }]
        full, _ = combine_m16(upstream, prior, recovery)
        self.assertEqual(full[0]["ir_status"], "OK")


if __name__ == "__main__":
    unittest.main()
