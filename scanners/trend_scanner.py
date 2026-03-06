"""Automatic iOS trend discovery from public sources.

This module collects weak signals from multiple platforms and normalizes them
into a single ranked list that can be used as context for topic generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus

import feedparser
import requests

from config import (
    CUSTOM_TRENDS_FILE,
    OUTPUT_TRENDS_DIR,
    REDDIT_USER_AGENT,
    TREND_HTTP_TIMEOUT_SECONDS,
    TREND_MAX_ITEMS_PER_SOURCE,
    TREND_SOURCES,
)


IOS_KEYWORDS = {
    "ios",
    "swift",
    "swiftui",
    "uikit",
    "xcode",
    "testflight",
    "app store",
    "appstore",
    "core data",
    "swiftdata",
    "async",
    "concurrency",
    "visionos",
    "wwdc",
}

DEFAULT_VIRAL_QUERIES = [
    "iOS SwiftUI Swift Concurrency architecture AI app development",
    "site:medium.com iOS SwiftUI",
    "site:x.com iOS app development SwiftUI",
    "site:linkedin.com/posts iOS SwiftUI",
    "site:dev.to iOS Swift",
]


@dataclass
class TrendSignal:
    """Normalized trend signal used across all upstream sources."""

    source: str
    title: str
    url: str
    score: float
    published_at: str
    summary: str = ""


def _is_ios_related(text: str) -> bool:
    """Quick keyword filter to retain iOS-relevant items."""
    normalized = text.lower()
    return any(keyword in normalized for keyword in IOS_KEYWORDS)


def _iso_now() -> str:
    """Return UTC timestamp for records that do not include publish time."""
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: object, default: float) -> float:
    """Best-effort numeric parsing helper."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_get_json(url: str, headers: dict[str, str] | None = None) -> dict | list:
    """Perform a guarded JSON GET with timeout and HTTP status validation."""
    response = requests.get(url, headers=headers, timeout=TREND_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _score_from_recency(published_parsed: tuple | None, base: float = 10.0) -> float:
    """Derive a soft score from age when explicit engagement metrics are missing."""
    if not published_parsed:
        return base

    published = datetime(*published_parsed[:6], tzinfo=timezone.utc)
    age_hours = max((datetime.now(timezone.utc) - published).total_seconds() / 3600.0, 0.0)

    # A simple half-life style decay keeps newer entries ranked higher.
    return round(base / (1.0 + age_hours / 48.0), 3)


def _parse_feed(
    feed_url: str,
    source_name: str,
    limit: int,
    ios_filter: bool = True,
) -> list[TrendSignal]:
    """Parse an RSS/Atom feed and normalize entries."""
    parsed = feedparser.parse(feed_url)
    signals: list[TrendSignal] = []

    for entry in parsed.entries:
        title = str(getattr(entry, "title", "") or "").strip()
        url = str(getattr(entry, "link", "") or "").strip()
        summary = re.sub(r"\s+", " ", str(getattr(entry, "summary", "") or "")).strip()

        if not title:
            continue

        combined = f"{title} {summary} {url}"
        if ios_filter and not _is_ios_related(combined):
            continue

        published_at = _iso_now()
        if getattr(entry, "published_parsed", None):
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            published_at = published.isoformat()

        score = _score_from_recency(getattr(entry, "published_parsed", None), base=15.0)
        signals.append(
            TrendSignal(
                source=source_name,
                title=title,
                url=url,
                score=score,
                published_at=published_at,
                summary=summary[:240],
            )
        )

        if len(signals) >= limit:
            break

    return signals


def _fetch_google_news_query(query: str, source_name: str, limit: int) -> list[TrendSignal]:
    """Fetch a Google News RSS query and normalize the result."""
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    return _parse_feed(feed_url=url, source_name=source_name, limit=limit, ios_filter=True)


def _load_custom_trend_config(path: Path = CUSTOM_TRENDS_FILE) -> dict[str, list[dict[str, object]]]:
    """Load user-defined trend sources from JSON for easy extension.

    Expected keys:
    - google_news_queries: [{"name": "LinkedIn iOS", "query": "site:linkedin.com/posts iOS SwiftUI"}]
    - rss_feeds: [{"name": "Some Feed", "url": "https://example.com/feed.xml", "ios_filter": true}]
    - manual_signals: [{"source": "Manual", "title": "...", "url": "...", "score": 60}]
    """
    default_payload: dict[str, list[dict[str, object]]] = {
        "google_news_queries": [],
        "rss_feeds": [],
        "manual_signals": [],
    }

    if not path.exists():
        return default_payload

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_payload

    if not isinstance(loaded, dict):
        return default_payload

    normalized = default_payload.copy()
    for key in normalized:
        value = loaded.get(key, [])
        normalized[key] = value if isinstance(value, list) else []

    return normalized


def fetch_hackernews_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Fetch iOS-relevant top stories from Hacker News."""
    top_story_ids = _safe_get_json("https://hacker-news.firebaseio.com/v0/topstories.json")

    signals: list[TrendSignal] = []
    for story_id in top_story_ids[:80]:
        item = _safe_get_json(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json")
        if not isinstance(item, dict):
            continue

        title = str(item.get("title", "")).strip()
        url = str(item.get("url", f"https://news.ycombinator.com/item?id={story_id}")).strip()
        combined = f"{title} {url}"

        if not title or not _is_ios_related(combined):
            continue

        score = float(item.get("score", 0)) + float(item.get("descendants", 0)) * 0.5
        unix_time = int(item.get("time", 0) or 0)
        published_at = (
            datetime.fromtimestamp(unix_time, tz=timezone.utc).isoformat()
            if unix_time
            else _iso_now()
        )

        signals.append(
            TrendSignal(
                source="HackerNews",
                title=title,
                url=url,
                score=round(score, 3),
                published_at=published_at,
                summary="",
            )
        )

        if len(signals) >= limit:
            break

    return signals


def fetch_reddit_iosprogramming_trends(
    limit: int = TREND_MAX_ITEMS_PER_SOURCE,
) -> list[TrendSignal]:
    """Fetch hot posts from r/iOSProgramming."""
    payload = _safe_get_json(
        "https://www.reddit.com/r/iOSProgramming/hot.json?limit=60",
        headers={"User-Agent": REDDIT_USER_AGENT},
    )

    children = payload.get("data", {}).get("children", []) if isinstance(payload, dict) else []

    signals: list[TrendSignal] = []
    for child in children:
        data = child.get("data", {})
        title = str(data.get("title", "")).strip()
        permalink = str(data.get("permalink", "")).strip()
        url = f"https://reddit.com{permalink}" if permalink else ""

        if not title:
            continue

        score = float(data.get("ups", 0)) + float(data.get("num_comments", 0)) * 0.35
        created_utc = float(data.get("created_utc", 0) or 0)
        published_at = (
            datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()
            if created_utc
            else _iso_now()
        )

        signals.append(
            TrendSignal(
                source="Reddit r/iOSProgramming",
                title=title,
                url=url,
                score=round(score, 3),
                published_at=published_at,
                summary=str(data.get("selftext", "") or "")[:240],
            )
        )

        if len(signals) >= limit:
            break

    return signals


def fetch_apple_docs_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Fetch Apple developer release/news signals (documentation + platform updates proxy)."""
    feed_urls = [
        "https://developer.apple.com/documentation/updates/rss",
        "https://developer.apple.com/news/releases/rss/releases.rss",
        "https://developer.apple.com/news/rss/news.rss",
    ]

    signals: list[TrendSignal] = []
    per_feed_limit = max(limit // max(len(feed_urls), 1), 1)

    for feed_url in feed_urls:
        signals.extend(
            _parse_feed(
                feed_url=feed_url,
                source_name="Apple Docs/Developer",
                limit=per_feed_limit,
                ios_filter=True,
            )
        )

    return signals[:limit]


def fetch_wwdc_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Fetch recent WWDC/session topics from Apple videos RSS feed."""
    return _parse_feed(
        feed_url="https://developer.apple.com/videos/rss/videos.rss",
        source_name="WWDC",
        limit=limit,
        ios_filter=True,
    )


def fetch_viral_ios_web_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Discover viral iOS articles/posts via Google News RSS queries."""
    per_query_limit = max(limit // max(len(DEFAULT_VIRAL_QUERIES), 1), 1)
    signals: list[TrendSignal] = []

    for query in DEFAULT_VIRAL_QUERIES:
        signals.extend(
            _fetch_google_news_query(
                query=query,
                source_name="Viral iOS Web",
                limit=per_query_limit,
            )
        )

    return signals[:limit]


def fetch_custom_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Fetch user-defined trend sources from scanners/custom_trends.json."""
    custom = _load_custom_trend_config()
    signals: list[TrendSignal] = []

    query_sources = custom["google_news_queries"]
    if query_sources:
        per_query_limit = max(limit // max(len(query_sources), 1), 1)
        for entry in query_sources:
            query = str(entry.get("query", "") or "").strip()
            source_name = str(entry.get("name", "Custom Query") or "Custom Query").strip()
            if not query:
                continue
            signals.extend(
                _fetch_google_news_query(
                    query=query,
                    source_name=source_name,
                    limit=per_query_limit,
                )
            )

    rss_sources = custom["rss_feeds"]
    if rss_sources:
        per_feed_limit = max(limit // max(len(rss_sources), 1), 1)
        for entry in rss_sources:
            feed_url = str(entry.get("url", "") or "").strip()
            source_name = str(entry.get("name", "Custom RSS") or "Custom RSS").strip()
            ios_filter = bool(entry.get("ios_filter", True))
            if not feed_url:
                continue
            signals.extend(
                _parse_feed(
                    feed_url=feed_url,
                    source_name=source_name,
                    limit=per_feed_limit,
                    ios_filter=ios_filter,
                )
            )

    for entry in custom["manual_signals"][:limit]:
        title = str(entry.get("title", "") or "").strip()
        url = str(entry.get("url", "") or "").strip()
        source_name = str(entry.get("source", "Manual") or "Manual").strip()
        summary = str(entry.get("summary", "") or "").strip()
        score = _to_float(entry.get("score", 50.0), 50.0)

        if not title or not url:
            continue

        if bool(entry.get("ios_filter", True)) and not _is_ios_related(f"{title} {summary} {url}"):
            continue

        signals.append(
            TrendSignal(
                source=source_name,
                title=title,
                url=url,
                score=score,
                published_at=_iso_now(),
                summary=summary[:240],
            )
        )

    signals.sort(key=lambda x: (x.score, x.published_at), reverse=True)
    return signals[:limit]


SOURCE_FETCHERS: dict[str, Callable[[int], list[TrendSignal]]] = {
    "hackernews": fetch_hackernews_trends,
    "reddit": fetch_reddit_iosprogramming_trends,
    "apple": fetch_apple_docs_trends,
    "wwdc": fetch_wwdc_trends,
    "viral": fetch_viral_ios_web_trends,
    "custom": fetch_custom_trends,
}


def discover_ios_trends(
    limit_per_source: int = TREND_MAX_ITEMS_PER_SOURCE,
    enabled_sources: list[str] | None = None,
) -> list[TrendSignal]:
    """Run selected trend fetchers and return one deduplicated, ranked list.

    Source keys are resolved from `TREND_SOURCES` unless `enabled_sources` is
    provided explicitly.
    """
    source_keys = [key.strip().lower() for key in (enabled_sources or list(TREND_SOURCES)) if key]

    collected: list[TrendSignal] = []
    for source_key in source_keys:
        fetcher = SOURCE_FETCHERS.get(source_key)
        if fetcher is None:
            continue
        try:
            collected.extend(fetcher(limit_per_source))
        except Exception:
            # Keep pipeline resilient: one source failure should not block publication.
            continue

    # Deduplicate by URL or normalized title.
    deduped: list[TrendSignal] = []
    seen: set[str] = set()
    for signal in collected:
        key = signal.url.strip().lower() or re.sub(r"\s+", " ", signal.title.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)

    # Rank by score then recency.
    deduped.sort(key=lambda x: (x.score, x.published_at), reverse=True)
    return deduped


def format_trends_for_prompt(signals: list[TrendSignal], max_items: int = 30) -> str:
    """Format trend signals into compact text context for topic generation prompt."""
    if not signals:
        return "No external trend signals were available this run."

    lines = []
    for signal in signals[:max_items]:
        published_date = signal.published_at[:10] if signal.published_at else "unknown"
        lines.append(
            f"- [{signal.source}] {signal.title} | score={signal.score} | date={published_date} | url={signal.url}"
        )

    return "\n".join(lines)


def save_trend_snapshot(signals: list[TrendSignal]) -> Path:
    """Persist discovered signals for observability/debugging in weekly runs."""
    OUTPUT_TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    output_path = OUTPUT_TRENDS_DIR / f"{timestamp}-trend-signals.json"

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "count": len(signals),
        "signals": [signal.__dict__ for signal in signals],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path
