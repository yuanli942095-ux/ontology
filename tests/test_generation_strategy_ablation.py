from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from generation_strategy_ablation import (  # noqa: E402
    GenerationAttempt,
    GenerationStrategy,
    build_pilot_manifest,
    needs_format_retry,
    needs_length_retry,
    stratified_event_sample,
)


def fake_events() -> list[dict[str, str]]:
    rows = []
    domains = ["insurance", "web_accessibility", "eu_regulation", "us_regulation"]
    index = 1
    for semantic_type in (
        "TEMPORAL_VERSION",
        "GENERAL_RULE_EXCEPTION",
        "CROSS_SENTENCE_SCOPE",
    ):
        for domain in domains:
            for _ in range(5):
                rows.append(
                    {
                        "event_id": f"EXT_E{index:03d}",
                        "semantic_type": semantic_type,
                        "domain": domain,
                        "status": "READY",
                    }
                )
                index += 1
    return rows


class PilotSamplingTests(unittest.TestCase):
    def test_stratified_sample_is_deterministic(self) -> None:
        events = fake_events()
        first = stratified_event_sample(events, semantic_type="TEMPORAL_VERSION", count=20, seed=99)
        second = stratified_event_sample(events, semantic_type="TEMPORAL_VERSION", count=20, seed=99)
        self.assertEqual([row["event_id"] for row in first], [row["event_id"] for row in second])

    def test_manifest_has_180_slots(self) -> None:
        manifest = build_pilot_manifest(fake_events(), seed=123)
        self.assertEqual(len(manifest["events"]), 60)
        self.assertEqual(manifest["attempt_count"], 180)


class AdaptiveDecisionTests(unittest.TestCase):
    def test_length_retry(self) -> None:
        attempt = GenerationAttempt(
            attempt_index=1,
            strategy_step="baseline_free",
            format_constraint="none",
            num_predict=1000,
            num_ctx=4096,
            response_text="",
            qwen_response={},
            runtime_ms=1,
            parsed=None,
            generation_status="INVALID_JSON",
            validation_reason="not_json_object",
            schema_valid=False,
            done_reason="length",
        )
        self.assertTrue(needs_length_retry(attempt))
        self.assertFalse(needs_format_retry(attempt))

    def test_format_retry_only_for_invalid_json(self) -> None:
        attempt = GenerationAttempt(
            attempt_index=1,
            strategy_step="baseline_free",
            format_constraint="none",
            num_predict=1000,
            num_ctx=4096,
            response_text="{}",
            qwen_response={},
            runtime_ms=1,
            parsed=None,
            generation_status="INVALID_JSON",
            validation_reason="not_json_object",
            schema_valid=False,
            done_reason="stop",
        )
        self.assertTrue(needs_format_retry(attempt))


if __name__ == "__main__":
    unittest.main()
