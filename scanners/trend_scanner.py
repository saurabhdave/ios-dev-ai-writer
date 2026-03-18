"""Automatic iOS trend discovery from public sources.

Collects weak signals from multiple platforms and normalises them into a
single ranked, deduplicated list for downstream topic generation.

Design principles
-----------------
- Every public function is typed and documented.
- Network errors are retried with exponential back-off; one source failure
  never aborts the full pipeline run.
- All I/O is guarded: invalid JSON, missing keys, and encoding errors are
  caught at the boundary and logged, not propagated.
- Structured logging (key=value pairs) throughout for easy ingestion by
  log aggregators (Datadog, CloudWatch, etc.).
- Constants live in `config`; nothing is hard-coded here.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Final
from urllib.parse import quote_plus

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    CUSTOM_TRENDS_FILE,
    OUTPUT_TRENDS_DIR,
    REDDIT_USER_AGENT,
    TREND_HTTP_TIMEOUT_SECONDS,
    TREND_MAX_ITEMS_PER_SOURCE,
    TREND_SOURCES,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IOS_KEYWORD_PATTERNS: Final[list[str]] = [
    r"\bapple\b",
    r"\bios\b",
    r"\bipados\b",
    r"\bmacos\b",
    r"\bwatchos\b",
    r"\bvisionos\b",
    r"\bswift\b",
    r"\bswiftui\b",
    r"\buikit\b",
    r"\bappkit\b",
    r"\bxcode\b",
    r"\btestflight\b",
    r"\bapp\s?store\b",
    r"\bcore\sdata\b",
    r"\bswiftdata\b",
    r"\bwidgetkit\b",
    r"\bapp\sintents?\b",
    r"\basync\s*/\s*await\b",
    r"\bstructured concurrency\b",
    r"\bmodifier(s)?\b",
    r"\bperformance\b",
    r"\binstruments?\b",
    r"\bapple intelligence\b",
    r"\bfoundation models?\b",
    r"\bmacro(s)?\b",
    r"\bswift\s*6\.?3\b",
    r"\bboilerplate\b",
    r"\basync\b",
    r"\bconcurrency\b",
    r"\bwwdc\b",
]

_EXCLUSION_PATTERNS: Final[list[str]] = [
    r"\bai\b",
    r"\bagent(s)?\b",
    r"\bagentic\b",
    r"\bgenerative\b",
    r"\bllm(s)?\b",
    r"\bprompt(s)?\b",
    r"\binference\b",
    r"\bautomation\b",
    r"\bmachine learning\b",
    r"\bcore\s?ml\b",
]

# Exclusion exceptions: items matching these are allowed through even if they
# also match an exclusion pattern (Apple Intelligence is intentional).
_INTELLIGENCE_ALLOWLIST: Final[list[str]] = [
    r"\bapple intelligence\b",
    r"\bapple intelligence api(s)?\b",
    r"\bfoundation models?\b",
    r"\bapp\sintents?\b",
]

_LOW_SIGNAL_PATTERNS: Final[list[str]] = [
    r"\bis hiring\b",
    r"\bjobs?\b",
    r"\bcareers?\b",
    r"\bfeedback requested\b",
    r"#buildinpublic",
]

_VIRAL_QUERIES: Final[list[str]] = [
    "Reducing boilerplate in real iOS SwiftUI projects",
    "Swift 6.3 Macros iOS SwiftUI practical usage",
    "Swift async await patterns in iOS SwiftUI apps",
    "Structured Concurrency SwiftUI iOS patterns",
    "SwiftUI performance improvements Instruments Xcode",
    "Verified SwiftUI modifiers tips and tricks",
    "Xcode tips and tricks iOS debugging build performance",
    "App Intents Apple Intelligence APIs iOS",
    "iOS SwiftUI Swift Concurrency architecture app development",
    "Xcode performance debugging Instruments iOS apps",
    "SwiftUI accessibility VoiceOver custom components iOS",
    "Swift Testing framework XCTest replacement iOS Xcode",
    "UIKit to SwiftUI migration patterns Apple platforms",
    "Swift 6 Adoption",
    "WidgetKit App Intents iOS development",
    "site:linkedin.com/posts iOS SwiftUI",
]

_SOCIAL_WEB_QUERIES: Final[list[tuple[str, str]]] = [
    (
        "X.com iOS SwiftUI",
        "site:x.com iOS SwiftUI OR Swift async await OR Structured Concurrency OR Swift Testing OR visionOS",
    ),
    (
        "dev.to iOS SwiftUI",
        "site:dev.to iOS SwiftUI OR verified modifiers OR Xcode tips OR accessibility SwiftUI",
    ),
    (
        "Medium iOS SwiftUI",
        "site:medium.com iOS SwiftUI OR App Intents OR Apple Intelligence API OR Swift Testing OR UIKit migration",
    ),
]

_PLATFORM_RSS_FEEDS: Final[list[tuple[str, str]]] = [
    ("dev.to", "https://dev.to/feed/tag/ios"),
    ("dev.to", "https://dev.to/feed/tag/swift"),
    ("dev.to", "https://dev.to/feed/tag/swiftui"),
    ("Medium", "https://medium.com/feed/tag/ios"),
    ("Medium", "https://medium.com/feed/tag/swift"),
    ("Medium", "https://medium.com/feed/tag/swiftui"),
]

_WEBSEARCH_QUERIES: Final[list[str]] = [
    "top 10 trending topics in iOS development",
    "top trending iOS Swift topics 2026",
    "most popular iOS development topics developers",
]

# Summary truncation length stored in one place so it's easy to adjust.
_SUMMARY_MAX_CHARS: Final[int] = 240

# HTTP retry policy applied to all outbound requests.
_RETRY_POLICY: Final[Retry] = Retry(
    total=3,
    backoff_factor=0.5,          # 0.5 s, 1 s, 2 s
    status_forcelist={429, 500, 502, 503, 504},
    allowed_methods={"GET"},
    raise_on_status=False,
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrendSignal:
    """Immutable, normalised trend signal produced by every upstream fetcher."""

    source: str
    title: str
    url: str
    score: float
    published_at: str           # ISO-8601 UTC string
    summary: str = field(default="")

    def dedup_key(self) -> str:
        """Stable key for URL-or-title deduplication."""
        if self.url.strip():
            return self.url.strip().lower()
        return re.sub(r"\s+", " ", self.title.lower()).strip()


# ---------------------------------------------------------------------------
# HTTP session factory
# ---------------------------------------------------------------------------


def _build_session(user_agent: str | None = None) -> requests.Session:
    """Return a Session pre-configured with retry logic and a shared adapter."""
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY_POLICY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if user_agent:
        session.headers["User-Agent"] = user_agent
    return session


# One shared session for generic requests; Reddit gets its own (custom UA).
_SESSION: requests.Session = _build_session()
_REDDIT_SESSION: requests.Session = _build_session(user_agent=REDDIT_USER_AGENT)

# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------


def _is_ios_related(text: str) -> bool:
    """Return True when *text* is Apple-platform programming relevant."""
    normalized = text.lower()
    has_signal = any(re.search(p, normalized) for p in _IOS_KEYWORD_PATTERNS)
    is_excluded = any(re.search(p, normalized) for p in _EXCLUSION_PATTERNS)
    is_allowed = any(re.search(p, normalized) for p in _INTELLIGENCE_ALLOWLIST)
    return has_signal and (not is_excluded or is_allowed)


def _is_low_signal(title: str) -> bool:
    """Return True for job posts, promos, and other non-editorial noise."""
    lowered = title.lower()
    return any(re.search(p, lowered) for p in _LOW_SIGNAL_PATTERNS)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _recency_score(published_parsed: tuple | None, base: float = 10.0) -> float:
    """Decay score by age using a 48-hour half-life."""
    if not published_parsed:
        return base
    try:
        published = datetime(*published_parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return base
    age_hours = max((datetime.now(timezone.utc) - published).total_seconds() / 3600.0, 0.0)
    return round(base / (1.0 + age_hours / 48.0), 3)


def _truncate(text: str, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------


def _get_json(
    url: str,
    session: requests.Session = _SESSION,
    timeout: int = TREND_HTTP_TIMEOUT_SECONDS,
) -> dict | list:
    """GET *url* and return parsed JSON, raising on HTTP or parse errors."""
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _parse_rss_feed(
    feed_url: str,
    source_name: str,
    limit: int,
    apply_topic_filter: bool = True,
) -> list[TrendSignal]:
    """Parse an RSS/Atom feed and return normalised TrendSignal list."""
    try:
        parsed = feedparser.parse(feed_url)
    except Exception as exc:  # feedparser rarely raises, but guard anyway
        log.warning("feed_parse_error source=%s url=%s error=%r", source_name, feed_url, exc)
        return []

    signals: list[TrendSignal] = []

    for entry in parsed.entries:
        title = str(getattr(entry, "title", "") or "").strip()
        url = str(getattr(entry, "link", "") or "").strip()
        raw_summary = str(getattr(entry, "summary", "") or "")
        summary = _truncate(re.sub(r"\s+", " ", raw_summary).strip())

        if not title:
            continue
        if _is_low_signal(title):
            continue
        if apply_topic_filter and not _is_ios_related(f"{title} {summary} {url}"):
            continue

        published_parsed = getattr(entry, "published_parsed", None)
        published_at = _utc_now_iso()
        if published_parsed:
            try:
                published_at = datetime(*published_parsed[:6], tzinfo=timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError):
                pass

        signals.append(
            TrendSignal(
                source=source_name,
                title=title,
                url=url,
                score=_recency_score(published_parsed, base=15.0),
                published_at=published_at,
                summary=summary,
            )
        )

        if len(signals) >= limit:
            break

    return signals


def _google_news_rss(
    query: str,
    source_name: str,
    limit: int,
) -> list[TrendSignal]:
    """Fetch a Google News RSS query and normalise results."""
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    return _parse_rss_feed(feed_url=url, source_name=source_name, limit=limit)


def _per_item_limit(total_limit: int, num_sources: int) -> int:
    """Fair per-source limit that always returns at least 1."""
    return max(total_limit // max(num_sources, 1), 1)


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------


def fetch_hackernews_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Fetch iOS-relevant top stories from Hacker News."""
    top_ids: list[int] = _get_json(  # type: ignore[assignment]
        "https://hacker-news.firebaseio.com/v0/topstories.json"
    )

    signals: list[TrendSignal] = []
    for story_id in top_ids[:80]:
        try:
            item: dict = _get_json(  # type: ignore[assignment]
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
            )
        except Exception as exc:
            log.debug("hn_item_fetch_error id=%s error=%r", story_id, exc)
            continue

        if not isinstance(item, dict):
            continue

        title = str(item.get("title", "")).strip()
        url = str(item.get("url", f"https://news.ycombinator.com/item?id={story_id}")).strip()

        if not title or _is_low_signal(title) or not _is_ios_related(f"{title} {url}"):
            continue

        unix_time = int(item.get("time", 0) or 0)
        published_at = (
            datetime.fromtimestamp(unix_time, tz=timezone.utc).isoformat()
            if unix_time
            else _utc_now_iso()
        )

        signals.append(
            TrendSignal(
                source="HackerNews",
                title=title,
                url=url,
                score=round(
                    _to_float(item.get("score"), 0.0)
                    + _to_float(item.get("descendants"), 0.0) * 0.5,
                    3,
                ),
                published_at=published_at,
            )
        )

        if len(signals) >= limit:
            break

    return signals


def fetch_reddit_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Fetch hot posts from r/iOSProgramming."""
    try:
        payload: dict = _get_json(  # type: ignore[assignment]
            "https://www.reddit.com/r/iOSProgramming/hot.json?limit=60",
            session=_REDDIT_SESSION,
        )
    except Exception as exc:
        log.warning("reddit_fetch_error error=%r", exc)
        return []

    children = payload.get("data", {}).get("children", []) if isinstance(payload, dict) else []

    signals: list[TrendSignal] = []
    for child in children:
        data: dict = child.get("data", {})
        title = str(data.get("title", "")).strip()
        permalink = str(data.get("permalink", "")).strip()
        url = f"https://reddit.com{permalink}" if permalink else ""

        if not title or _is_low_signal(title) or not _is_ios_related(title):
            continue

        created_utc = _to_float(data.get("created_utc"), 0.0)
        published_at = (
            datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()
            if created_utc
            else _utc_now_iso()
        )

        signals.append(
            TrendSignal(
                source="Reddit r/iOSProgramming",
                title=title,
                url=url,
                score=round(
                    _to_float(data.get("ups"), 0.0)
                    + _to_float(data.get("num_comments"), 0.0) * 0.35,
                    3,
                ),
                published_at=published_at,
                summary=_truncate(str(data.get("selftext", "") or "")),
            )
        )

        if len(signals) >= limit:
            break

    return signals


def fetch_apple_docs_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Fetch Apple developer release and news signals."""
    feed_urls = [
        "https://developer.apple.com/documentation/updates/rss",
        "https://developer.apple.com/news/releases/rss/releases.rss",
        "https://developer.apple.com/news/rss/news.rss",
    ]
    per_limit = _per_item_limit(limit, len(feed_urls))
    signals: list[TrendSignal] = []
    for url in feed_urls:
        signals.extend(_parse_rss_feed(url, "Apple Docs/Developer", per_limit))
    return signals[:limit]


def fetch_wwdc_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Fetch recent WWDC session topics from Apple's video RSS feed."""
    return _parse_rss_feed(
        "https://developer.apple.com/videos/rss/videos.rss",
        source_name="WWDC",
        limit=limit,
    )


def fetch_viral_ios_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Discover viral iOS content via targeted Google News RSS queries."""
    per_limit = _per_item_limit(limit, len(_VIRAL_QUERIES))
    signals: list[TrendSignal] = []
    for query in _VIRAL_QUERIES:
        signals.extend(_google_news_rss(query, "Viral iOS Web", per_limit))
    return signals[:limit]


def fetch_social_web_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Discover trends from source-scoped web/social queries."""
    per_limit = _per_item_limit(limit, len(_SOCIAL_WEB_QUERIES))
    signals: list[TrendSignal] = []
    for source_name, query in _SOCIAL_WEB_QUERIES:
        signals.extend(_google_news_rss(query, source_name, per_limit))
    return signals[:limit]


def fetch_platform_rss_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Fetch direct RSS feeds from Medium and dev.to tag pages."""
    per_limit = _per_item_limit(limit, len(_PLATFORM_RSS_FEEDS))
    signals: list[TrendSignal] = []
    for source_name, feed_url in _PLATFORM_RSS_FEEDS:
        signals.extend(_parse_rss_feed(feed_url, source_name, per_limit))
    return signals[:limit]


def fetch_websearch_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Discover iOS trends via broad web search queries."""
    per_limit = _per_item_limit(limit, len(_WEBSEARCH_QUERIES))
    signals: list[TrendSignal] = []
    for query in _WEBSEARCH_QUERIES:
        signals.extend(_google_news_rss(query, "WebSearch", per_limit))
    return signals[:limit]


# ---------------------------------------------------------------------------
# Custom trend loader
# ---------------------------------------------------------------------------


def _load_custom_config(path: Path = CUSTOM_TRENDS_FILE) -> dict[str, list[dict]]:
    """Load user-defined trend sources from JSON.

    Expected shape::

        {
          "google_news_queries": [{"name": "...", "query": "..."}],
          "rss_feeds":           [{"name": "...", "url": "...", "topic_filter": true}],
          "manual_signals":      [{"source": "...", "title": "...", "url": "...", "score": 50}]
        }

    Missing keys default to empty lists; malformed files return all-empty.
    """
    defaults: dict[str, list[dict]] = {
        "google_news_queries": [],
        "rss_feeds": [],
        "manual_signals": [],
    }

    if not path.exists():
        return defaults

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("custom_trends_load_error path=%s error=%r", path, exc)
        return defaults

    if not isinstance(raw, dict):
        log.warning("custom_trends_invalid_shape path=%s", path)
        return defaults

    return {key: raw.get(key, []) if isinstance(raw.get(key), list) else [] for key in defaults}


def fetch_custom_trends(limit: int = TREND_MAX_ITEMS_PER_SOURCE) -> list[TrendSignal]:
    """Fetch user-defined trend sources from ``scanners/custom_trends.json``."""
    custom = _load_custom_config()
    signals: list[TrendSignal] = []

    # Google News RSS queries
    query_sources = custom["google_news_queries"]
    if query_sources:
        per_limit = _per_item_limit(limit, len(query_sources))
        for entry in query_sources:
            query = str(entry.get("query", "") or "").strip()
            name = str(entry.get("name", "Custom Query") or "Custom Query").strip()
            if not query:
                continue
            signals.extend(_google_news_rss(query, name, per_limit))

    # Arbitrary RSS feeds
    rss_sources = custom["rss_feeds"]
    if rss_sources:
        per_limit = _per_item_limit(limit, len(rss_sources))
        for entry in rss_sources:
            feed_url = str(entry.get("url", "") or "").strip()
            name = str(entry.get("name", "Custom RSS") or "Custom RSS").strip()
            apply_filter = bool(entry.get("topic_filter", entry.get("ios_filter", True)))
            if not feed_url:
                continue
            signals.extend(_parse_rss_feed(feed_url, name, per_limit, apply_filter))

    # Manually specified signals
    for entry in custom["manual_signals"][:limit]:
        title = str(entry.get("title", "") or "").strip()
        url = str(entry.get("url", "") or "").strip()
        source = str(entry.get("source", "Manual") or "Manual").strip()
        summary = _truncate(str(entry.get("summary", "") or "").strip())
        score = _to_float(entry.get("score"), 50.0)

        if not title or not url:
            continue

        apply_filter = bool(entry.get("topic_filter", entry.get("ios_filter", True)))
        if apply_filter and not _is_ios_related(f"{title} {summary} {url}"):
            continue

        signals.append(
            TrendSignal(
                source=source,
                title=title,
                url=url,
                score=score,
                published_at=_utc_now_iso(),
                summary=summary,
            )
        )

    signals.sort(key=lambda s: (s.score, s.published_at), reverse=True)
    return signals[:limit]


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

#: Maps config keys to fetcher callables. Extend here to add new sources.
SOURCE_FETCHERS: Final[dict[str, Callable[[int], list[TrendSignal]]]] = {
    "hackernews": fetch_hackernews_trends,
    "reddit": fetch_reddit_trends,
    "apple": fetch_apple_docs_trends,
    "wwdc": fetch_wwdc_trends,
    "viral": fetch_viral_ios_trends,
    "social": fetch_social_web_trends,
    "platforms": fetch_platform_rss_trends,
    "custom": fetch_custom_trends,
    "websearch": fetch_websearch_trends,
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover_ios_trends(
    limit_per_source: int = TREND_MAX_ITEMS_PER_SOURCE,
    enabled_sources: list[str] | None = None,
) -> list[TrendSignal]:
    """Run selected trend fetchers and return one deduplicated, ranked list.

    Parameters
    ----------
    limit_per_source:
        Maximum signals collected from each individual source.
    enabled_sources:
        Source keys to run. Defaults to ``TREND_SOURCES`` from config.
        Unknown keys are silently skipped.

    Returns
    -------
    list[TrendSignal]
        Deduplicated signals sorted by (score DESC, published_at DESC).
    """
    source_keys = [k.strip().lower() for k in (enabled_sources or list(TREND_SOURCES)) if k]

    collected: list[TrendSignal] = []
    for key in source_keys:
        fetcher = SOURCE_FETCHERS.get(key)
        if fetcher is None:
            log.warning("unknown_source_key key=%s", key)
            continue

        start = time.monotonic()
        try:
            batch = fetcher(limit_per_source)
            collected.extend(batch)
            log.info(
                "source_fetched source=%s count=%d elapsed_ms=%.0f",
                key,
                len(batch),
                (time.monotonic() - start) * 1000,
            )
        except Exception as exc:
            # One source failure must never abort the pipeline run.
            log.warning(
                "source_fetch_error source=%s elapsed_ms=%.0f error=%r",
                key,
                (time.monotonic() - start) * 1000,
                exc,
            )

    # Deduplicate by URL or normalised title.
    seen: set[str] = set()
    deduped: list[TrendSignal] = []
    for signal in collected:
        key = signal.dedup_key()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)

    deduped.sort(key=lambda s: (s.score, s.published_at), reverse=True)
    log.info("discovery_complete total=%d unique=%d", len(collected), len(deduped))
    return deduped


def format_trends_for_prompt(signals: list[TrendSignal], max_items: int = 30) -> str:
    """Format *signals* into compact, prompt-ready plain text.

    Returns a human-readable fallback string when the list is empty so that
    downstream prompts receive a non-empty value and can handle the absence
    gracefully.
    """
    if not signals:
        return "No external trend signals were available this run."

    lines = [
        f"- [{s.source}] {s.title} | score={s.score} | date={s.published_at[:10]} | url={s.url}"
        for s in signals[:max_items]
    ]
    return "\n".join(lines)


def save_trend_snapshot(signals: list[TrendSignal]) -> Path:
    """Persist *signals* to a timestamped JSON file for observability.

    The output directory is created if it does not exist. Returns the path
    of the written file.
    """
    OUTPUT_TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    output_path = OUTPUT_TRENDS_DIR / f"{timestamp}-trend-signals.json"

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "count": len(signals),
        "signals": [s.__dict__ for s in signals],
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("snapshot_saved path=%s count=%d", output_path, len(signals))
    return output_path