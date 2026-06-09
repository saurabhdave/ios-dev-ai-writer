"""Tests for scripts/health_check.py pure helpers."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "health_check.py"
_spec = importlib.util.spec_from_file_location("health_check", _MODULE_PATH)
health_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_spec and health_check)


class LifetimeTotalRunsTests(unittest.TestCase):
    def test_prefers_meta_sidecar_when_present(self):
        history = [{"date": "2026-06-01"}] * 3
        meta = {"total_runs": 57, "first_run_date": "2025-09-01"}
        self.assertEqual(health_check._lifetime_total_runs(history, meta), 57)

    def test_falls_back_to_history_length_without_meta(self):
        history = [{"date": "2026-06-01"}] * 3
        self.assertEqual(health_check._lifetime_total_runs(history, None), 3)
        self.assertEqual(health_check._lifetime_total_runs(history, {}), 3)
        self.assertEqual(health_check._lifetime_total_runs(history, "garbage"), 3)

    def test_falls_back_when_meta_total_below_history_length(self):
        # A lifetime total can never be smaller than the trimmed window on disk.
        history = [{"date": "2026-06-01"}] * 10
        self.assertEqual(
            health_check._lifetime_total_runs(history, {"total_runs": 4}), 10
        )


class AvgReviewScoreTests(unittest.TestCase):
    def test_ignores_entries_without_review_score(self):
        window = [
            {"review_overall": 9},
            {"review_overall": None},
            {"review_overall": 8},
            {},
        ]
        self.assertEqual(health_check._avg_review_score(window), 8.5)

    def test_returns_none_when_no_entries_scored(self):
        window = [{"review_overall": None}, {}, {"review_overall": "n/a"}]
        self.assertIsNone(health_check._avg_review_score(window))


if __name__ == "__main__":
    unittest.main()
