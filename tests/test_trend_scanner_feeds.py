"""Tests for RSS feed fetching in scanners/trend_scanner.py.

Feeds must be fetched through the retry-configured requests session with an
explicit timeout — never by feedparser's own urllib fetching, which has no
timeout and can hang a pipeline run indefinitely.
"""

from __future__ import annotations

import unittest
from unittest import mock

import requests

from scanners import trend_scanner

_RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test Feed</title>
<item>
<title>SwiftUI performance profiling with Instruments</title>
<link>https://example.com/swiftui-performance</link>
<description>Deep dive into SwiftUI rendering performance.</description>
<pubDate>Mon, 08 Jun 2026 10:00:00 GMT</pubDate>
</item>
</channel></rss>
"""

# RFC 2606 reserved TLD — guaranteed to never resolve if a real fetch is attempted.
_FEED_URL = "https://feed.invalid/rss"


class ParseRssFeedTests(unittest.TestCase):
    def test_fetches_feed_via_session_with_timeout(self):
        response = mock.MagicMock()
        response.content = _RSS_SAMPLE
        with mock.patch.object(trend_scanner._SESSION, "get", return_value=response) as get:
            signals = trend_scanner._parse_rss_feed(_FEED_URL, "Test Source", limit=5)

        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], _FEED_URL)
        self.assertEqual(
            get.call_args.kwargs.get("timeout"),
            trend_scanner.TREND_HTTP_TIMEOUT_SECONDS,
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].title, "SwiftUI performance profiling with Instruments")
        self.assertEqual(signals[0].url, "https://example.com/swiftui-performance")

    def test_returns_empty_list_on_network_error(self):
        with mock.patch.object(
            trend_scanner._SESSION, "get", side_effect=requests.ConnectionError("boom")
        ):
            signals = trend_scanner._parse_rss_feed(_FEED_URL, "Test Source", limit=5)
        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
