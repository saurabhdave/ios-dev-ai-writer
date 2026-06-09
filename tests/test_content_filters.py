"""Tests for utils/content_filters.py — the shared AI-exclusion filter.

These patterns previously lived in three near-identical copies
(trend_scanner, weekly_pipeline, topic_agent) and drifted; the shared module
is now the single source of truth and all three import from it.
"""

from __future__ import annotations

import unittest

from utils import content_filters


class ExclusionTests(unittest.TestCase):
    def test_generic_ai_topics_are_excluded(self):
        for text in (
            "Best AI agents for coding",
            "Running LLMs on device",
            "Prompt engineering tips",
            "Core ML model deployment",
            "CoreML quantization guide",
            "Machine learning pipelines in production",
        ):
            with self.subTest(text=text):
                self.assertTrue(content_filters.is_excluded_ai_topic(text))

    def test_apple_platform_topics_pass(self):
        for text in (
            "SwiftUI performance profiling with Instruments",
            "Swift 6 strict concurrency migration",
            "NavigationStack patterns for iOS apps",
        ):
            with self.subTest(text=text):
                self.assertFalse(content_filters.is_excluded_ai_topic(text))

    def test_apple_intelligence_contexts_are_allowed(self):
        for text in (
            "Apple Intelligence APIs for iOS developers",
            "Foundation Models framework on-device inference",
            "App Intents automation with Shortcuts",
        ):
            with self.subTest(text=text):
                self.assertTrue(content_filters.has_allowed_intelligence_context(text))
                self.assertFalse(content_filters.is_excluded_ai_topic(text))

    def test_allowlist_overrides_a_real_exclusion_match(self):
        # These hit an exclusion pattern (inference/automation) but carry an
        # Apple Intelligence context, so the final verdict must let them through.
        for text in (
            "Foundation Models framework on-device inference",
            "App Intents automation with Shortcuts",
        ):
            with self.subTest(text=text):
                self.assertTrue(content_filters.matches_ai_exclusion(text))
                self.assertFalse(content_filters.is_excluded_ai_topic(text))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(content_filters.is_excluded_ai_topic("GENERATIVE video models"))
        self.assertFalse(content_filters.is_excluded_ai_topic("APPLE INTELLIGENCE in iOS 27"))

    def test_word_boundaries_avoid_false_positives(self):
        # "ai" inside a word, "prompt" as adjective-like substring of other words.
        self.assertFalse(content_filters.is_excluded_ai_topic("Maintain UI responsiveness"))
        self.assertFalse(content_filters.is_excluded_ai_topic("Plain SwiftUI views"))


class ConsumersShareOneCopyTests(unittest.TestCase):
    def test_topic_agent_aliases_shared_patterns(self):
        from agents import topic_agent

        self.assertIs(topic_agent.AI_WORD_PATTERNS, content_filters.AI_EXCLUSION_PATTERNS)
        self.assertIs(
            topic_agent.APPLE_INTELLIGENCE_ALLOWLIST,
            content_filters.APPLE_INTELLIGENCE_ALLOWLIST,
        )


if __name__ == "__main__":
    unittest.main()
