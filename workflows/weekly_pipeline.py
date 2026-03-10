"""Weekly pipeline: orchestrate topic, outline, article, and code generation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from agents.article_agent import generate_article
from agents.code_agent import generate_code
from agents.editor_agent import polish_article
from agents.outline_agent import generate_outline
from agents.topic_agent import generate_topic
from config import EDITOR_PASS_ENABLED, OUTPUT_ARTICLES_DIR, TREND_DISCOVERY_ENABLED
from scanners.trend_scanner import TrendSignal, discover_ios_trends, save_trend_snapshot


def _slugify(text: str) -> str:
    """Build a filesystem-safe slug from article title text."""
    cleaned = text.lower().strip()
    cleaned = re.sub(r"[^a-z0-9\s-]", "", cleaned)
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned.strip("-")[:90] or "ios-article"


def _load_recent_titles(max_items: int = 20) -> list[str]:
    """Load recently generated article titles to avoid repetitive topics."""
    if not OUTPUT_ARTICLES_DIR.exists():
        return []

    markdown_files = sorted(OUTPUT_ARTICLES_DIR.glob("*.md"), reverse=True)
    titles: list[str] = []

    for path in markdown_files:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue

        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                if title:
                    titles.append(title)
                break

        if len(titles) >= max_items:
            break

    return titles


def _sanitize_body_urls(markdown: str) -> str:
    """Final safety pass to strip URLs and code fences from article body."""
    # Remove fenced code blocks from body; dedicated code section is appended separately.
    without_fences = re.sub(r"```[\s\S]*?```", "", markdown)
    without_md_links = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1", without_fences)
    without_urls = re.sub(r"https?://[^\s)]+", "", without_md_links)
    compact = re.sub(r"\n{3,}", "\n\n", without_urls)
    return re.sub(r"[ \t]+", " ", compact).strip()


def _reference_items(trends: list[TrendSignal], max_items: int = 8) -> list[tuple[str, str, str]]:
    """Build validated reference list as (source, title, url)."""
    items: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()
    blocked_url_substrings = {
        "news.google.com/rss/articles/",
    }
    blocked_title_terms = {
        "is hiring",
        "jobs",
        "careers",
        "feedback requested",
    }

    for trend in trends:
        title = trend.title.strip()
        url = trend.url.strip()
        source = trend.source.strip()
        lowered_title = title.lower()

        if not title or not url or not url.startswith("http"):
            continue
        if len(title) > 140:
            continue
        if any(term in lowered_title for term in blocked_title_terms):
            continue
        if any(fragment in url for fragment in blocked_url_substrings):
            continue
        if url in seen_urls:
            continue

        seen_urls.add(url)
        items.append((source, title, url))
        if len(items) >= max_items:
            break

    return items


def _references_for_prompt(trends: list[TrendSignal], max_items: int = 8) -> str:
    """Create source list for prompt grounding."""
    items = _reference_items(trends, max_items=max_items)
    if not items:
        return "- None"
    return "\n".join(f"- [{source}] {title} | {url}" for source, title, url in items)


def _compose_markdown(title: str, article: str, code: str, trends: list[TrendSignal]) -> str:
    """Compose final Medium-ready markdown output."""
    references = _reference_items(trends, max_items=10)
    references_block = (
        "\n".join(f"- [{ref_title}]({ref_url})" for _, ref_title, ref_url in references)
        if references
        else "- No verified external references were available this run."
    )

    return "\n".join(
        [
            f"# {title}",
            "",
            article.strip(),
            "",
            "## Swift/SwiftUI Code Example",
            "",
            "```swift",
            code.strip(),
            "```",
            "",
            "## References",
            "",
            references_block,
            "",
        ]
    )


def _save_markdown(title: str, markdown: str) -> Path:
    """Persist markdown to outputs/articles/{date}-{slug}.md."""
    OUTPUT_ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slugify(title)
    output_path = OUTPUT_ARTICLES_DIR / f"{date_prefix}-{slug}.md"
    output_path.write_text(markdown, encoding="utf-8")

    return output_path


def run_weekly_pipeline() -> Path:
    """Execute the end-to-end weekly content generation workflow."""
    trends: list[TrendSignal] = []
    recent_titles = _load_recent_titles(max_items=24)

    if TREND_DISCOVERY_ENABLED:
        trends = discover_ios_trends()
        save_trend_snapshot(trends)

    trend_context = _references_for_prompt(trends, max_items=14)
    topic = generate_topic(trend_context=trend_context, recent_titles=recent_titles)
    outline = generate_outline(topic)
    reference_context = _references_for_prompt(trends, max_items=10)

    article = generate_article(
        topic=topic,
        outline=outline,
        allowed_references=reference_context,
    )
    polished_article = (
        polish_article(topic=topic, article=article, allowed_references=reference_context)
        if EDITOR_PASS_ENABLED
        else article
    )
    polished_article = _sanitize_body_urls(polished_article)

    code = generate_code(topic)
    markdown = _compose_markdown(topic, polished_article, code, trends)
    return _save_markdown(topic, markdown)
