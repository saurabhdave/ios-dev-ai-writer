"""Focused tests for OpenAI model compatibility helpers."""

from __future__ import annotations

import unittest

import config


class OpenAIConfigCompatibilityTests(unittest.TestCase):
    def test_reasoning_effort_accepts_none_and_xhigh(self) -> None:
        self.assertEqual(
            config._normalize_reasoning_effort("none", model="gpt-5.1"),
            "none",
        )
        self.assertEqual(
            config._normalize_reasoning_effort("xhigh", model="gpt-5.4"),
            "xhigh",
        )

    def test_gpt51_defaults_to_none_reasoning(self) -> None:
        self.assertEqual(
            config._normalize_reasoning_effort(None, model="gpt-5.1"),
            "none",
        )

    def test_gpt51_allows_temperature_only_with_reasoning_none(self) -> None:
        self.assertEqual(
            config.openai_generation_kwargs(
                0.4,
                model="gpt-5.1",
                reasoning_effort="none",
            ),
            {"reasoning": {"effort": "none"}, "temperature": 0.4},
        )
        self.assertEqual(
            config.openai_generation_kwargs(
                0.4,
                model="gpt-5.1",
                reasoning_effort="low",
            ),
            {"reasoning": {"effort": "low"}},
        )

    def test_gpt5_models_omit_temperature_and_keep_reasoning(self) -> None:
        self.assertEqual(
            config.openai_generation_kwargs(
                0.7,
                model="gpt-5-mini",
                reasoning_effort="low",
            ),
            {"reasoning": {"effort": "low"}},
        )

    def test_o_series_can_include_temperature_and_reasoning(self) -> None:
        self.assertEqual(
            config.openai_generation_kwargs(
                0.2,
                model="o3",
                reasoning_effort="high",
            ),
            {"reasoning": {"effort": "high"}, "temperature": 0.2},
        )

    def test_gpt5_pro_clamps_reasoning_to_high(self) -> None:
        self.assertEqual(
            config.openai_generation_kwargs(
                0.3,
                model="gpt-5-pro",
                reasoning_effort="low",
            ),
            {"reasoning": {"effort": "high"}},
        )


if __name__ == "__main__":
    unittest.main()
