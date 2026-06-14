"""Tests for the incremental-learnings digest builder (deterministic, no API)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from utils import learnings


def _rec(review=None, inline=None, layout=None) -> dict:
    return {
        "review_issues": review or [],
        "inline_snippet_issues": inline or [],
        "layout_issues": layout or [],
    }


class CodeLearningsTests(unittest.TestCase):
    def test_recurring_inline_error_surfaces(self) -> None:
        err = "inline block ('enum E {'): value of type '[T]' has no member 'chunked'"
        recs = [_rec(inline=[err]), _rec(inline=[err]), _rec(inline=["other"])]
        out = learnings.code_learnings(recs, window=10, min_count=2, max_items=8)
        self.assertEqual(len(out), 1)
        self.assertIn("chunked", out[0])
        self.assertNotIn("inline block", out[0])  # block-name prefix stripped

    def test_single_occurrence_is_ignored(self) -> None:
        recs = [_rec(inline=["inline block ('x'): missing argument for parameter 'category'"])]
        self.assertEqual(learnings.code_learnings(recs, window=10, min_count=2, max_items=8), [])

    def test_tooling_noise_filtered(self) -> None:
        noise = "inline block ('x'): command timed out after 12s"
        recs = [_rec(inline=[noise]), _rec(inline=[noise]), _rec(inline=[noise])]
        self.assertEqual(learnings.code_learnings(recs, window=10, min_count=2, max_items=8), [])


class EditorialLearningsTests(unittest.TestCase):
    def test_recurring_critique_surfaces(self) -> None:
        # The reviewer emits consistent structural critiques; recurring ones surface.
        c = "Closing takeaway section exists as plain text — promote it to a ## heading."
        recs = [_rec(review=[c]), _rec(review=[c]), _rec(review=["one-off note about tone"])]
        out = learnings.editorial_learnings(recs, window=10, min_count=2, max_items=6)
        self.assertEqual(len(out), 1)
        self.assertIn("Closing takeaway", out[0])

    def test_reordered_phrasing_clusters(self) -> None:
        # Same significant tokens, reordered -> order-independent fingerprint matches.
        recs = [
            _rec(review=["Duplicate Validation Observability sections detected — merge"]),
            _rec(review=["Detected duplicate Observability Validation sections — merge"]),
        ]
        out = learnings.editorial_learnings(recs, window=10, min_count=2, max_items=6)
        self.assertEqual(len(out), 1)


class DigestTests(unittest.TestCase):
    def _write(self, records: list[dict]) -> str:
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(records, f)
        f.close()
        return f.name

    def test_empty_history_is_noop(self) -> None:
        self.assertEqual(learnings.build_code_digest(self._write([])), "")
        self.assertEqual(learnings.build_editorial_digest(self._write([])), "")

    def test_missing_file_is_noop(self) -> None:
        self.assertEqual(learnings.build_code_digest("/no/such/file.json"), "")

    def test_code_digest_renders_recurring(self) -> None:
        err = "inline block ('x'): cannot find type 'Foo' in scope"
        path = self._write([_rec(inline=[err]), _rec(inline=[err])])
        digest = learnings.build_code_digest(path, window=10, min_count=2)
        self.assertIn("do NOT repeat", digest)
        self.assertIn("Foo", digest)


if __name__ == "__main__":
    unittest.main()
