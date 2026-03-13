"""Review agent: post-generation LLM quality assessment for generated articles."""

from __future__ import annotations

import json
import re
from pathlib import Path

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE, openai_generation_kwargs
from utils.openai_logging import create_openai_client, responses_create_logged

PROMPT_PATH = Path("prompts/review_prompt.txt")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_review_json(raw: str) -> dict:
    """Parse LLM review response into a structured dict with safe defaults."""
    text = raw.strip()
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {
            "overall_quality": 0,
            "technical_depth": 0,
            "actionability": 0,
            "issues": [f"parse_error: {raw[:120]}"],
            "strengths": [],
        }

    def _clamp_int(value: object, default: int = 0) -> int:
        try:
            return max(1, min(10, int(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    def _str_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if item]
        return []

    return {
        "overall_quality": _clamp_int(data.get("overall_quality")),
        "technical_depth": _clamp_int(data.get("technical_depth")),
        "actionability": _clamp_int(data.get("actionability")),
        "issues": _str_list(data.get("issues")),
        "strengths": _str_list(data.get("strengths")),
    }


def review_article(topic: str, article: str) -> dict:
    """Run an LLM self-review pass on the generated article and return structured quality metrics."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = create_openai_client()
    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = prompt_template.format(topic=topic, article=article)

    response = responses_create_logged(
        client,
        agent_name="review_agent",
        operation="review_article",
        model=OPENAI_MODEL,
        max_output_tokens=600,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, 0.3)),
    )

    return _parse_review_json(response.output_text or "")
