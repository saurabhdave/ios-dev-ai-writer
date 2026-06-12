"""Tests for the version-baseline note in utils/article_repair.py.

The reviewer penalizes articles with no stated deployment target while the
writer/editor prompts forbid per-API version callouts for baseline APIs —
`ensure_version_baseline_note` resolves that conflict deterministically.
"""

from __future__ import annotations

import unittest

from utils.article_repair import (
    build_version_baseline_note,
    ensure_version_baseline_note,
    repair_article,
)

_ARTICLE = """Swift concurrency keeps shipping sharp edges in production code.

## 1. Why This Matters

Strict concurrency surfaces data races the compiler used to ignore.

## 2. Adopting It

Migrate leaf view models first.
"""


class BaselineNoteTests(unittest.TestCase):
    def test_note_inserted_after_intro_before_first_heading(self):
        result, inserted = ensure_version_baseline_note(_ARTICLE, "6.2.4")
        self.assertTrue(inserted)
        intro, _, rest = result.partition("## 1.")
        self.assertIn("targets iOS 18+ and Swift 6.2", intro)
        self.assertNotIn("targets iOS", rest)

    def test_swift_version_trimmed_to_major_minor(self):
        note = build_version_baseline_note("6.2.4")
        self.assertIn("Swift 6.2 ", note)
        self.assertNotIn("6.2.4", note)

    def test_insertion_is_idempotent(self):
        once, _ = ensure_version_baseline_note(_ARTICLE, "6.2.4")
        twice, inserted_again = ensure_version_baseline_note(once, "6.2.4")
        self.assertFalse(inserted_again)
        self.assertEqual(once, twice)

    def test_existing_baseline_statement_is_respected(self):
        article = _ARTICLE.replace(
            "Swift concurrency keeps shipping sharp edges in production code.",
            "Everything below targets iOS 18 and assumes Swift 6.",
        )
        result, inserted = ensure_version_baseline_note(article, "6.2.4")
        self.assertFalse(inserted)
        self.assertEqual(result, article)

    def test_article_without_headings_gets_note_appended(self):
        result, inserted = ensure_version_baseline_note("Just one paragraph.", "6.2.4")
        self.assertTrue(inserted)
        self.assertTrue(result.rstrip().endswith("unless noted otherwise.*"))

    def test_repair_article_reports_insertion(self):
        repaired, report = repair_article(_ARTICLE, swift_version="6.2.4")
        self.assertTrue(report["version_note_inserted"])
        self.assertIn("targets iOS 18+", repaired)

    def test_repair_article_skips_note_without_swift_version(self):
        repaired, report = repair_article(_ARTICLE)
        self.assertFalse(report["version_note_inserted"])
        self.assertNotIn("targets iOS 18+", repaired)


if __name__ == "__main__":
    unittest.main()
