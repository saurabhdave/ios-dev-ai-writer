"""Topic agent: generate a trending iOS development topic."""

from __future__ import annotations

import re
from pathlib import Path

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE

PROMPT_PATH = Path("prompts/topic_prompt.txt")


def _load_prompt_template() -> str:
    """Load the prompt template for topic generation."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def generate_topic(trend_context: str = "") -> str:
    """Generate a single trending iOS topic suitable for a Medium article title."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = _load_prompt_template().format(
        trend_context=trend_context.strip()
        or "No external trend signals were available this run."
    )

    # Ask the model for one high-signal topic line.
    response = client.responses.create(
        model=OPENAI_MODEL,
        temperature=OPENAI_TEMPERATURE,
        max_output_tokens=220,
        input=prompt,
    )

    topic = response.output_text.strip().splitlines()[0].strip().strip('"')
    # Normalize occasional numbering like "1. Title" from model outputs.
    topic = re.sub(r"^\s*\d+[\.)]\s*", "", topic).strip()
    if not topic:
        raise RuntimeError("Topic generation returned empty output.")

    return topic
