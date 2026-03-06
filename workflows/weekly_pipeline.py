"""Weekly pipeline: orchestrate topic, outline, article, and code generation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from agents.article_agent import generate_article
from agents.code_agent import generate_code
from agents.outline_agent import generate_outline
from agents.topic_agent import generate_topic
from config import OUTPUT_ARTICLES_DIR, TREND_DISCOVERY_ENABLED
from scanners.trend_scanner import (
    TrendSignal,
    discover_ios_trends,
    format_trends_for_prompt,
    save_trend_snapshot,
)


def _slugify(text: str) -> str:
    """Build a filesystem-safe slug from article title text."""
    cleaned = text.lower().strip()
    cleaned = re.sub(r"[^a-z0-9\s-]", "", cleaned)
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned.strip("-")[:90] or "ios-article"


def _compose_markdown(
    title: str, outline: str, article: str, code: str, trends: list[TrendSignal]
) -> str:
    """Combine all generated artifacts into one markdown document."""
    trend_lines = (
        [
            f"- [{signal.source}] {signal.title} - {signal.url}"
            for signal in trends[:12]
            if signal.title and signal.url
        ]
        or ["- No external trend signals available for this run."]
    )

    return "\n".join(
        [
            f"# {title}",
            "",
            "## Trend Signals (Auto-Discovered)",
            "",
            *trend_lines,
            "",
            "## Outline",
            "",
            outline.strip(),
            "",
            "## Article",
            "",
            article.strip(),
            "",
            "## Code Example",
            "",
            "```swift",
            code.strip(),
            "```",
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
    trend_context = ""

    if TREND_DISCOVERY_ENABLED:
        trends = discover_ios_trends()
        trend_context = format_trends_for_prompt(trends)
        save_trend_snapshot(trends)

    topic = generate_topic(trend_context=trend_context)
    outline = generate_outline(topic)
    article = generate_article(topic, outline)
    code = generate_code(topic)

    markdown = _compose_markdown(topic, outline, article, code, trends)
    return _save_markdown(topic, markdown)
