"""Article agent: produce a full Medium-style article body."""

from __future__ import annotations

import re
from pathlib import Path

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE

PROMPT_PATH = Path("prompts/article_prompt.txt")


def _load_prompt_template() -> str:
    """Load the article prompt template."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def _remove_unapproved_links(markdown: str) -> str:
    """Strip inline links/URLs from the body to prevent fabricated references."""
    # Convert markdown links to plain text: [text](url) -> text
    without_md_links = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1", markdown)
    # Remove remaining raw URLs.
    without_urls = re.sub(r"https?://[^\s)]+", "", without_md_links)
    # Keep whitespace readable after replacements.
    return re.sub(r"[ \t]+", " ", without_urls).strip()


def _remove_reference_sections(markdown: str) -> str:
    """Drop model-generated references section; pipeline appends verified sources."""
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        normalized = line.strip().lower()
        if normalized in {"## references", "## sources", "## further reading"}:
            return "\n".join(lines[:index]).strip()
    return markdown.strip()


def generate_article(topic: str, outline: str, allowed_references: str) -> str:
    """Generate a professional Medium-style markdown body from topic + outline."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = _load_prompt_template().format(
        topic=topic,
        outline=outline,
        allowed_references=allowed_references.strip() or "- None",
    )

    # Generate a long-form post with practical details and production advice.
    response = client.responses.create(
        model=OPENAI_MODEL,
        temperature=OPENAI_TEMPERATURE,
        max_output_tokens=2600,
        input=prompt,
    )

    article = response.output_text.strip()
    # Remove accidental top-level title if the model adds one.
    if article.startswith("# "):
        article = "\n".join(article.splitlines()[1:]).strip()
    article = _remove_reference_sections(article)
    article = _remove_unapproved_links(article)
    if not article:
        raise RuntimeError("Article generation returned empty output.")

    return article
