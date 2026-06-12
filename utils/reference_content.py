"""Fetch trusted reference pages and extract prompt-ready text excerpts.

The pipeline historically passed only reference *titles* to the agents, so
articles about timely topics (WWDC announcements, new OS releases) came out
evergreen-generic — the model's training data predates the news. This module
fetches the top trusted reference URLs and extracts plain-text excerpts that
weekly_pipeline injects into the article and factual-grounding prompts.

Design notes
------------
- stdlib-only HTML extraction (html.parser); no new dependencies.
- Pages that yield too little text (JS-rendered SPA shells, e.g. some
  developer.apple.com/documentation pages) are skipped, not padded.
- Apple/Swift.org domains are preferred when more pages are available than
  the configured budget.
- Network failures are per-page and never propagate.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import Final

import requests

from config import (
    REFERENCE_CONTENT_MAX_CHARS,
    REFERENCE_CONTENT_MAX_PAGES,
    TREND_HTTP_TIMEOUT_SECONDS,
)
from utils.observability import get_logger

LOGGER = get_logger("pipeline.reference_content")

#: Minimum extracted characters for a page to count as real content.
MIN_USEFUL_CHARS: Final[int] = 200

#: Domains preferred when ranking candidate pages.
_PREFERRED_DOMAIN_FRAGMENTS: Final[tuple[str, ...]] = (
    "developer.apple.com",
    "apple.com",
    "swift.org",
)

_SKIP_TAGS: Final[frozenset[str]] = frozenset(
    {"script", "style", "noscript", "svg", "nav", "header", "footer", "form"}
)

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE: Final[re.Pattern[str]] = re.compile(r"\n{2,}")

_FETCH_WORKERS: Final[int] = 4


class _TextExtractor(HTMLParser):
    """Collect visible text, skipping script/style/navigation containers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data.strip())

    def text(self) -> str:
        joined = "\n".join(self._chunks)
        joined = _WHITESPACE_RE.sub(" ", joined)
        return _BLANK_LINES_RE.sub("\n", joined).strip()


def extract_text(html: str) -> str:
    """Return visible plain text from *html* (best-effort, stdlib-only)."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # html.parser is lenient, but stay defensive.
        pass
    return parser.text()


def _domain_rank(url: str) -> int:
    for rank, fragment in enumerate(_PREFERRED_DOMAIN_FRAGMENTS):
        if fragment in url:
            return rank
    return len(_PREFERRED_DOMAIN_FRAGMENTS)


def _fetch_page_text(url: str, timeout: int) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "ios-dev-ai-writer/1.0 (reference grounding)"},
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type and "text" not in content_type:
        return ""
    return extract_text(response.text)


def fetch_reference_excerpts(
    refs: list[tuple[str, str, str]],
    max_pages: int = REFERENCE_CONTENT_MAX_PAGES,
    max_chars_per_page: int = REFERENCE_CONTENT_MAX_CHARS,
    timeout: int = TREND_HTTP_TIMEOUT_SECONDS,
) -> str:
    """Fetch up to *max_pages* reference pages and return a prompt-ready block.

    *refs* is the ``(source, title, url)`` shape produced by
    ``_reference_items`` / ``_seed_reference_items``. Returns an empty string
    when nothing useful could be fetched. Excerpts are titled but never
    include URLs, so the model cannot leak links into the article body.
    """
    if max_pages <= 0 or not refs:
        return ""

    # Deduplicate by URL, preserve order, then prefer Apple/Swift.org pages.
    seen: set[str] = set()
    candidates: list[tuple[str, str, str]] = []
    for source, title, url in refs:
        if url and url.startswith("http") and url not in seen:
            seen.add(url)
            candidates.append((source, title, url))
    candidates.sort(key=lambda item: _domain_rank(item[2]))

    # Fetch more than the budget so SPA shells / failures can be skipped.
    fetch_count = min(len(candidates), max_pages * 2)
    to_fetch = candidates[:fetch_count]

    texts: list[str] = []
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS, thread_name_prefix="ref-content") as pool:
        futures = [pool.submit(_fetch_page_text, url, timeout) for _, _, url in to_fetch]
        for (source, title, url), future in zip(to_fetch, futures):
            try:
                text = future.result()
            except Exception as exc:
                LOGGER.warning("reference_fetch_error url=%s error=%r", url, exc)
                continue
            if len(text) < MIN_USEFUL_CHARS:
                LOGGER.info("reference_content_too_thin url=%s chars=%d", url, len(text))
                continue
            excerpt = text[:max_chars_per_page].rstrip()
            texts.append(f"— {title} ({source}):\n{excerpt}")
            if len(texts) >= max_pages:
                break

    return "\n\n".join(texts)
