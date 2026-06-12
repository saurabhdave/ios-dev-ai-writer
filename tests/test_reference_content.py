"""Tests for utils/reference_content.py — reference-page excerpt fetching."""

from __future__ import annotations

import unittest
from unittest import mock

from utils import reference_content

_RICH_HTML = (
    "<html><head><script>var x = 1;</script><style>.a {}</style></head>"
    "<body><nav>Menu</nav><h1>Swift 6.2 Released</h1>"
    "<p>" + "The Swift team announced strict concurrency improvements. " * 20 + "</p>"
    "<footer>footer junk</footer></body></html>"
)

_THIN_HTML = "<html><body><div id='app'></div></body></html>"


def _response(html: str) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.text = html
    resp.headers = {"Content-Type": "text/html; charset=utf-8"}
    resp.raise_for_status = mock.MagicMock()
    return resp


class ExtractTextTests(unittest.TestCase):
    def test_skips_script_style_and_nav_content(self):
        text = reference_content.extract_text(_RICH_HTML)
        self.assertIn("Swift 6.2 Released", text)
        self.assertIn("strict concurrency improvements", text)
        for junk in ("var x = 1", ".a {}", "Menu", "footer junk"):
            self.assertNotIn(junk, text)

    def test_spa_shell_yields_almost_nothing(self):
        self.assertLess(len(reference_content.extract_text(_THIN_HTML)), 10)


class FetchExcerptsTests(unittest.TestCase):
    def test_thin_pages_skipped_and_titles_used_without_urls(self):
        refs = [
            ("Apple Developer", "SPA docs page", "https://developer.apple.com/documentation/x"),
            ("Swift.org", "Swift 6.2 announcement", "https://swift.org/blog/swift-6-2"),
        ]
        responses = {
            "https://developer.apple.com/documentation/x": _response(_THIN_HTML),
            "https://swift.org/blog/swift-6-2": _response(_RICH_HTML),
        }
        with mock.patch.object(
            reference_content.requests, "get",
            side_effect=lambda url, **kw: responses[url],
        ):
            out = reference_content.fetch_reference_excerpts(refs, max_pages=2)

        self.assertIn("Swift 6.2 announcement (Swift.org)", out)
        self.assertNotIn("SPA docs page", out)
        self.assertNotIn("http", out)  # URLs never leak into the prompt block

    def test_page_budget_and_char_cap_respected(self):
        refs = [
            ("A", f"Page {i}", f"https://swift.org/page-{i}") for i in range(5)
        ]
        with mock.patch.object(
            reference_content.requests, "get",
            side_effect=lambda url, **kw: _response(_RICH_HTML),
        ):
            out = reference_content.fetch_reference_excerpts(
                refs, max_pages=2, max_chars_per_page=300
            )
        self.assertEqual(out.count("— Page"), 2)
        for block in out.split("\n\n"):
            self.assertLessEqual(len(block), 300 + 60)  # excerpt + title line

    def test_network_failure_is_isolated(self):
        refs = [
            ("A", "Broken", "https://swift.org/broken"),
            ("B", "Works", "https://swift.org/works"),
        ]

        def _get(url, **kw):
            if url.endswith("broken"):
                raise OSError("connection reset")
            return _response(_RICH_HTML)

        with mock.patch.object(reference_content.requests, "get", side_effect=_get):
            out = reference_content.fetch_reference_excerpts(refs, max_pages=2)
        self.assertIn("Works", out)
        self.assertNotIn("Broken", out)

    def test_disabled_budget_returns_empty(self):
        self.assertEqual(reference_content.fetch_reference_excerpts([], max_pages=3), "")
        self.assertEqual(
            reference_content.fetch_reference_excerpts(
                [("A", "T", "https://x.test")], max_pages=0
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
