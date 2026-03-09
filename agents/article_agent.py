"""Article agent: produce a full Medium-style article body."""

from __future__ import annotations

from pathlib import Path

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE

PROMPT_PATH = Path("prompts/article_prompt.txt")


def _load_prompt_template() -> str:
    """Load the article prompt template."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def generate_article(topic: str, outline: str) -> str:
    """Generate a ~600-word markdown article body from topic + outline."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = _load_prompt_template().format(topic=topic, outline=outline)

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
    if not article:
        raise RuntimeError("Article generation returned empty output.")

    return article
