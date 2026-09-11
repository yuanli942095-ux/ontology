from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_policy_v4_calibration import (  # noqa: E402
    event_grouped_folds,
    fit_isotonic,
    fit_platt,
    predict_isotonic,
    predict_platt,
)


class EventGroupedFoldTests(unittest.TestCase):
    def test_same_event_never_splits_across_train_and_val(self) -> None:
        rows = [
            {"event_id": "E1", "run": 1},
            {"event_id": "E1", "run": 2},
            {"event_id": "E1", "run": 3},
            {"event_id": "E2", "run": 1},
            {"event_id": "E3", "run": 1},
            {"event_id": "E3", "run": 2},
            {"event_id": "E4", "run": 1},
            {"event_id": "E5", "run": 1},
        ]
        for train, val in event_grouped_folds(rows, folds=4):
            train_events = {row["event_id"] for row in train}
            val_events = {row["event_id"] for row in val}
            self.assertFalse(train_events & val_events)
            self.assertEqual(len(train) + len(val), len(rows))


class IsotonicAndPlattTests(unittest.TestCase):
    def test_isotonic_is_nondecreasing(self) -> None:
        scores = [0.05, 0.10, 0.12, 0.40, 0.80, 0.90]
        labels = [0, 0, 1, 1, 1, 1]
        model = fit_isotonic(scores, labels)
        preds = [predict_isotonic(model, score) for score in scores]
        self.assertEqual(preds, sorted(preds))
        self.assertGreater(preds[-1], preds[0])

    def test_platt_tracks_high_vs_low_scores(self) -> None:
        scores = [0.05] * 20 + [0.90] * 20
        labels = [0] * 18 + [1] * 2 + [1] * 18 + [0] * 2
        model = fit_platt(scores, labels)
        low = predict_platt(model, 0.05)
        high = predict_platt(model, 0.90)
        self.assertGreater(high, low)


if __name__ == "__main__":
    unittest.main()
