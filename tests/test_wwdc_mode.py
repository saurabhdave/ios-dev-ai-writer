"""Tests for WWDC mode: the config window gate and topic-agent behavior."""

from __future__ import annotations

import shutil
import unittest
from datetime import date
from unittest import mock

import config
from agents import code_agent, topic_agent


def _wwdc_window(start: str = "2026-06-08", end: str = "2026-06-12"):
    """Patch the WWDC window in both modules that captured it at import."""
    return mock.patch.multiple(config, WWDC_START_DATE=start, WWDC_END_DATE=end)


class WwdcWindowTests(unittest.TestCase):
    def test_window_inclusive_of_boundaries(self):
        with _wwdc_window():
            self.assertTrue(config.wwdc_window_active(date(2026, 6, 8)))
            self.assertTrue(config.wwdc_window_active(date(2026, 6, 12)))
            self.assertFalse(config.wwdc_window_active(date(2026, 6, 7)))
            self.assertFalse(config.wwdc_window_active(date(2026, 6, 13)))

    def test_unset_or_invalid_dates_disable_mode(self):
        with _wwdc_window(start="", end=""):
            self.assertFalse(config.wwdc_window_active(date(2026, 6, 10)))
        with _wwdc_window(start="June 8", end="2026-06-12"):
            self.assertFalse(config.wwdc_window_active(date(2026, 6, 10)))


class WwdcTopicContextTests(unittest.TestCase):
    def test_returns_none_outside_window(self):
        with _wwdc_window(start="", end=""):
            self.assertIsNone(topic_agent._wwdc_topic_context(date(2026, 6, 10)))

    def test_angles_cycle_by_day_offset(self):
        with _wwdc_window(), mock.patch.object(
            topic_agent, "WWDC_START_DATE", "2026-06-08"
        ):
            day0 = topic_agent._wwdc_topic_context(date(2026, 6, 8))
            day1 = topic_agent._wwdc_topic_context(date(2026, 6, 9))
            self.assertIn("keynote", day0.queries[0].lower())
            self.assertIn("swiftui", day1.queries[0].lower())
            self.assertNotEqual(day0.queries[0], day1.queries[0])
            self.assertEqual(day0.family, topic_agent.WWDC_FAMILY_NAME)
            self.assertIn("WWDC 2026", day0.directive)

    def test_wwdc_titles_count_as_apple_topics(self):
        self.assertTrue(
            topic_agent._is_apple_programming_topic(
                "What WWDC 2026 Means For Your Team"
            )
        )

    def test_exact_duplicate_check(self):
        recent = ["Swift Concurrency At WWDC 2026"]
        self.assertTrue(
            topic_agent._is_exact_duplicate("swift concurrency at wwdc 2026", recent)
        )
        self.assertFalse(
            topic_agent._is_exact_duplicate("SwiftUI At WWDC 2026", recent)
        )


class InlineSnippetValidationTests(unittest.TestCase):
    _ARTICLE = """Intro paragraph.

## 1. Pattern

```swift
@Observable
final class Cart {
    @Bindable var items: [String] = []
}
```
"""

    def test_bindable_fix_applied_inside_body_fences(self):
        fixed, _ = code_agent.validate_inline_snippets(self._ARTICLE)
        self.assertNotIn("@Bindable var items", fixed)
        self.assertIn("var items: [String] = []", fixed)

    @unittest.skipUnless(shutil.which("swiftc"), "swiftc not available")
    def test_unparseable_block_is_reported(self):
        broken = "Intro.\n\n```swift\nlet x = { missing brace\n```\n"
        _, issues = code_agent.validate_inline_snippets(broken)
        self.assertEqual(len(issues), 1)
        self.assertIn("inline block", issues[0])
        self.assertIn("missing brace", issues[0])

    def test_clean_article_reports_no_issues(self):
        clean = "Intro.\n\n```swift\nlet total = items.reduce(0, +)\n```\n"
        result, issues = code_agent.validate_inline_snippets(clean)
        self.assertEqual(issues, [])
        self.assertIn("let total = items.reduce(0, +)", result)


if __name__ == "__main__":
    unittest.main()
