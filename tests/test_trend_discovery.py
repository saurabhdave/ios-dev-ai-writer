"""Tests for parallel trend discovery and the single-request HN fetcher.

`discover_ios_trends` fans sources out across a thread pool but must keep
the behavioural contract of the old sequential loop: dedup precedence
follows source listing order, one source failure never aborts the run, and
unknown source keys are skipped with a warning.
"""

from __future__ import annotations

import unittest
from unittest import mock

from scanners import trend_scanner
from scanners.trend_scanner import TrendSignal


def _signal(source: str, title: str, url: str, score: float = 10.0) -> TrendSignal:
    return TrendSignal(
        source=source,
        title=title,
        url=url,
        score=score,
        published_at="2026-06-09T10:00:00+00:00",
    )


class DiscoverTrendsTests(unittest.TestCase):
    def test_dedup_precedence_follows_source_listing_order(self):
        # Both sources return the same URL; the first-listed source must win,
        # exactly as in the old sequential implementation.
        duplicate_url = "https://example.com/swiftui-performance"
        fetchers = {
            "alpha": lambda limit: [_signal("Alpha", "SwiftUI performance deep dive", duplicate_url)],
            "beta": lambda limit: [_signal("Beta", "SwiftUI performance deep dive", duplicate_url)],
        }
        with mock.patch.dict(trend_scanner.SOURCE_FETCHERS, fetchers, clear=True):
            result = trend_scanner.discover_ios_trends(enabled_sources=["alpha", "beta"])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source, "Alpha")

    def test_one_source_failure_does_not_abort_discovery(self):
        def _boom(limit: int) -> list[TrendSignal]:
            raise RuntimeError("network down")

        fetchers = {
            "broken": _boom,
            "healthy": lambda limit: [
                _signal("Healthy", "Swift 6 migration notes", "https://example.com/swift6")
            ],
        }
        with mock.patch.dict(trend_scanner.SOURCE_FETCHERS, fetchers, clear=True):
            result = trend_scanner.discover_ios_trends(enabled_sources=["broken", "healthy"])

        self.assertEqual([s.source for s in result], ["Healthy"])

    def test_unknown_source_keys_are_skipped(self):
        fetchers = {
            "known": lambda limit: [
                _signal("Known", "Xcode build performance", "https://example.com/xcode")
            ],
        }
        with mock.patch.dict(trend_scanner.SOURCE_FETCHERS, fetchers, clear=True):
            result = trend_scanner.discover_ios_trends(enabled_sources=["known", "nope"])

        self.assertEqual(len(result), 1)

    def test_results_sorted_by_score_descending(self):
        fetchers = {
            "one": lambda limit: [
                _signal("One", "Low score story about Swift", "https://example.com/low", score=1.0)
            ],
            "two": lambda limit: [
                _signal("Two", "High score story about SwiftUI", "https://example.com/high", score=99.0)
            ],
        }
        with mock.patch.dict(trend_scanner.SOURCE_FETCHERS, fetchers, clear=True):
            result = trend_scanner.discover_ios_trends(enabled_sources=["one", "two"])

        self.assertEqual([s.score for s in result], [99.0, 1.0])


class HackerNewsAlgoliaTests(unittest.TestCase):
    _PAYLOAD = {
        "hits": [
            {
                "objectID": "101",
                "title": "SwiftUI rendering performance in iOS 27",
                "url": "https://example.com/swiftui-rendering",
                "points": 120,
                "num_comments": 40,
                "created_at_i": 1_780_000_000,
            },
            {
                "objectID": "102",
                "title": "Show HN: my LLM agent framework",
                "url": "https://example.com/agent",
                "points": 300,
                "num_comments": 200,
                "created_at_i": 1_780_000_000,
            },
            {
                "objectID": "103",
                "title": "Ask HN: Swift concurrency pitfalls?",
                "url": "",
                "points": 50,
                "num_comments": 30,
                "created_at_i": 0,
            },
        ]
    }

    def test_single_request_maps_fields_and_filters(self):
        with mock.patch.object(
            trend_scanner, "_get_json", return_value=self._PAYLOAD
        ) as get_json:
            signals = trend_scanner.fetch_hackernews_trends(limit=10)

        get_json.assert_called_once()
        self.assertIn("hn.algolia.com/api/v1/search", get_json.call_args.args[0])

        # The generic-AI story is filtered out; the two Apple-platform ones stay.
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].title, "SwiftUI rendering performance in iOS 27")
        self.assertEqual(signals[0].score, 120 + 40 * 0.5)
        # Missing URL falls back to the HN item page.
        self.assertEqual(signals[1].url, "https://news.ycombinator.com/item?id=103")

    def test_respects_limit(self):
        with mock.patch.object(trend_scanner, "_get_json", return_value=self._PAYLOAD):
            signals = trend_scanner.fetch_hackernews_trends(limit=1)
        self.assertEqual(len(signals), 1)


if __name__ == "__main__":
    unittest.main()
