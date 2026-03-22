"""Outline agent: create a structured Medium-style article outline.

Design principles
-----------------
- All constants are typed ``Final``.
- Prompt loading validates file existence before reading, surfacing
  misconfiguration with a clear error rather than a generic OSError.
- ``OPENAI_API_KEY`` check removed — key validation belongs in
  ``create_openai_client()``.
- Structured logging on both success and failure paths.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from config import OPENAI_MODEL, OPENAI_TEMPERATURE, openai_generation_kwargs
from utils.observability import get_logger, log_event
from utils.openai_logging import create_openai_client, responses_create_logged

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROMPT_PATH: Final[Path] = Path("prompts/outline_prompt.txt")
MAX_OUTPUT_TOKENS: Final[int] = 2_500
GENERATION_TEMPERATURE: Final[float] = 0.45

LOGGER = get_logger("pipeline.outline")

# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def _load_template(path: Path = PROMPT_PATH) -> str:
    """Read and return the outline prompt template.

    Raises
    ------
    FileNotFoundError
        When the template file does not exist — surfaces misconfiguration
        immediately rather than producing a confusing downstream error.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Outline prompt template not found at '{path}'. "
            "Verify PROMPT_PATH or the process working directory."
        )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_outline(topic: str) -> str:
    """Generate a markdown outline for the provided topic.

    Parameters
    ----------
    topic:
        The article topic string injected into the prompt template.

    Returns
    -------
    str
        Structured markdown outline with sections and key talking points.

    Raises
    ------
    FileNotFoundError
        When the prompt template file is missing.
    RuntimeError
        When generation returns empty output.
    """
    client = create_openai_client()
    prompt = _load_template().replace("{topic}", topic)

    response = responses_create_logged(
        client,
        agent_name="outline_agent",
        operation="generate_outline",
        model=OPENAI_MODEL,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, GENERATION_TEMPERATURE)),
    )

    outline = response.output_text.strip()

    if not outline:
        log_event(
            LOGGER,
            "outline_generation_empty",
            level=logging.ERROR,
            topic=topic,
        )
        raise RuntimeError(
            f"Outline generation returned empty output for topic={topic!r}."
        )

    log_event(
        LOGGER,
        "outline_generated",
        level=logging.INFO,
        topic=topic,
        output_chars=len(outline),
    )
    return outline