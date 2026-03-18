"""Review agent: post-generation LLM quality assessment for generated articles.

Design principles
-----------------
- All constants are typed ``Final``.
- JSON parsing is isolated behind ``_parse_review_json`` with explicit safe
  defaults — callers always receive a well-typed ``ArticleReview`` dataclass,
  never a raw dict with unknown keys.
- The retry loop is index-free and its exit condition is explicit.
- ``OPENAI_API_KEY`` check removed — key validation belongs in
  ``create_openai_client()``.
- Structured logging on both success and parse-error paths.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from config import OPENAI_MODEL, OPENAI_TEMPERATURE, openai_generation_kwargs
from utils.observability import get_logger, log_event
from utils.openai_logging import create_openai_client, responses_create_logged

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROMPT_PATH: Final[Path] = Path("prompts/review_prompt.txt")
MAX_OUTPUT_TOKENS: Final[int] = 900
GENERATION_TEMPERATURE: Final[float] = 0.30
MAX_PARSE_ATTEMPTS: Final[int] = 2

# Score range enforced by _clamp_score.
SCORE_MIN: Final[int] = 1
SCORE_MAX: Final[int] = 10

# Prefix written into the issues list when JSON parsing fails — used as the
# retry trigger signal.
_PARSE_ERROR_PREFIX: Final[str] = "parse_error:"

LOGGER = get_logger("pipeline.review")

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"```(?:json)?\s*(.*?)\s*```", re.DOTALL
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArticleReview:
    """Structured quality assessment returned by the review agent."""

    overall_quality: int
    technical_depth: int
    actionability: int
    issues: tuple[str, ...]
    strengths: tuple[str, ...]

    @property
    def is_parse_error(self) -> bool:
        """True when the review represents a JSON parse failure, not real scores."""
        return bool(self.issues) and self.issues[0].startswith(_PARSE_ERROR_PREFIX)

    def to_dict(self) -> dict:
        """Return a plain dict for serialisation and backward compatibility."""
        return {
            "overall_quality": self.overall_quality,
            "technical_depth": self.technical_depth,
            "actionability": self.actionability,
            "issues": list(self.issues),
            "strengths": list(self.strengths),
        }


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def _load_template(path: Path = PROMPT_PATH) -> str:
    """Read and return the review prompt template.

    Raises
    ------
    FileNotFoundError
        When the template file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Review prompt template not found at '{path}'. "
            "Verify PROMPT_PATH or the process working directory."
        )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------


def _clamp_score(value: object) -> int:
    """Coerce *value* to an integer within [SCORE_MIN, SCORE_MAX]; default 0."""
    try:
        return max(SCORE_MIN, min(SCORE_MAX, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _to_str_list(value: object) -> tuple[str, ...]:
    """Safely coerce a JSON array to a tuple of non-empty strings."""
    if isinstance(value, list):
        return tuple(str(item) for item in value if item)
    return ()


def _parse_review_json(raw: str) -> ArticleReview:
    """Parse an LLM review response into an ``ArticleReview``.

    Strips fenced code blocks before attempting JSON parsing. Returns a
    zero-score ``ArticleReview`` with a ``parse_error:`` issue entry on any
    parse failure so callers can detect and retry without catching exceptions.
    """
    text = raw.strip()

    fence_match = _FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ArticleReview(
            overall_quality=0,
            technical_depth=0,
            actionability=0,
            issues=(f"{_PARSE_ERROR_PREFIX} {raw[:120]}",),
            strengths=(),
        )

    return ArticleReview(
        overall_quality=_clamp_score(data.get("overall_quality")),
        technical_depth=_clamp_score(data.get("technical_depth")),
        actionability=_clamp_score(data.get("actionability")),
        issues=_to_str_list(data.get("issues")),
        strengths=_to_str_list(data.get("strengths")),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def review_article(topic: str, article: str) -> dict:
    """Run an LLM self-review pass on the article and return structured quality metrics.

    Retries once on a JSON parse failure (e.g., truncated model output).
    Always returns a dict with keys: ``overall_quality``, ``technical_depth``,
    ``actionability``, ``issues``, ``strengths``.

    Parameters
    ----------
    topic:
        Article topic string injected into the prompt.
    article:
        Full article markdown body to evaluate.

    Returns
    -------
    dict
        Scores and feedback lists; scores are 0 on parse failure.

    Raises
    ------
    FileNotFoundError
        When the prompt template file is missing.
    """
    client = create_openai_client()
    prompt = (
        _load_template()
        .replace("{topic}", topic)
        .replace("{article}", article)
    )

    result = ArticleReview(
        overall_quality=0, technical_depth=0, actionability=0,
        issues=(), strengths=(),
    )

    for attempt in range(1, MAX_PARSE_ATTEMPTS + 1):
        response = responses_create_logged(
            client,
            agent_name="review_agent",
            operation="review_article",
            model=OPENAI_MODEL,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            input=prompt,
            **openai_generation_kwargs(min(OPENAI_TEMPERATURE, GENERATION_TEMPERATURE)),
        )

        result = _parse_review_json(response.output_text or "")

        if result.is_parse_error:
            log_event(
                LOGGER,
                "review_parse_error",
                level=logging.WARNING,
                topic=topic,
                attempt=attempt,
                raw_excerpt=(response.output_text or "")[:120],
            )
            if attempt < MAX_PARSE_ATTEMPTS:
                continue   # Retry once on truncated / malformed JSON.
        else:
            break

    log_event(
        LOGGER,
        "review_complete",
        level=logging.INFO,
        topic=topic,
        overall_quality=result.overall_quality,
        technical_depth=result.technical_depth,
        actionability=result.actionability,
        issue_count=len(result.issues),
        strength_count=len(result.strengths),
        parse_error=result.is_parse_error,
    )
    return result.to_dict()