"""Outline agent: create a structured Medium-style article outline."""

from __future__ import annotations

from pathlib import Path

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE, openai_generation_kwargs

PROMPT_PATH = Path("prompts/outline_prompt.txt")


def _load_prompt_template() -> str:
    """Load the outline prompt template."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def generate_outline(topic: str) -> str:
    """Generate a markdown outline for the provided topic."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = _load_prompt_template().format(topic=topic)

    # The outline should include sections and key talking points.
    response = client.responses.create(
        model=OPENAI_MODEL,
        max_output_tokens=700,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, 0.45)),
    )

    outline = response.output_text.strip()
    if not outline:
        raise RuntimeError("Outline generation returned empty output.")

    return outline
