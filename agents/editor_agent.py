"""Editor agent: quality pass for professionalism and Medium-style structure."""

from __future__ import annotations

from pathlib import Path

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE

PROMPT_PATH = Path("prompts/editor_prompt.txt")


def _load_prompt_template() -> str:
    """Load editor prompt template."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def polish_article(topic: str, article: str, allowed_references: str) -> str:
    """Refine article for clarity, professionalism, and Medium readability."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = _load_prompt_template().format(
        topic=topic,
        article=article,
        allowed_references=allowed_references.strip() or "- None",
    )

    response = client.responses.create(
        model=OPENAI_MODEL,
        temperature=min(OPENAI_TEMPERATURE, 0.5),
        max_output_tokens=2600,
        input=prompt,
    )

    polished = response.output_text.strip()
    if polished.startswith("# "):
        polished = "\n".join(polished.splitlines()[1:]).strip()

    if not polished:
        raise RuntimeError("Editor pass returned empty output.")

    return polished
