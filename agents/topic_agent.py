"""Topic agent: generate a trending iOS development topic."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE

PROMPT_PATH = Path("prompts/topic_prompt.txt")


def _load_prompt_template() -> str:
    """Load the prompt template for topic generation."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def _word_set(text: str) -> set[str]:
    """Build a normalized word set for overlap checks."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop_words = {
        "the",
        "a",
        "an",
        "for",
        "and",
        "with",
        "to",
        "in",
        "on",
        "of",
        "ios",
        "swift",
        "swiftui",
    }
    return {word for word in words if len(word) > 2 and word not in stop_words}


def _is_repetitive(candidate: str, recent_titles: Iterable[str], threshold: float = 0.6) -> bool:
    """Check whether topic candidate is too similar to recent topic history."""
    candidate_words = _word_set(candidate)
    if not candidate_words:
        return False

    for previous in recent_titles:
        prev_words = _word_set(previous)
        if not prev_words:
            continue

        overlap = len(candidate_words & prev_words) / len(candidate_words | prev_words)
        if overlap >= threshold:
            return True

    return False


def _constrain_title_length(title: str, max_chars: int = 60, max_words: int = 10) -> str:
    """Constrain title length for professional Medium readability."""
    cleaned = re.sub(r"\s+", " ", title).strip().strip('"')
    words = cleaned.split()[:max_words]

    trailing_stop_words = {"for", "to", "with", "and", "or", "of", "in", "on"}

    constrained_words: list[str] = []
    for word in words:
        candidate = " ".join(constrained_words + [word]).strip()
        if len(candidate) <= max_chars:
            constrained_words.append(word)
            continue
        break

    cleaned = " ".join(constrained_words).strip()
    if not cleaned:
        cleaned = words[0][:max_chars].rstrip(" ,:;-") if words else ""
    # Remove awkward trailing connector words caused by truncation.
    parts = cleaned.split()
    while parts and parts[-1].lower() in trailing_stop_words:
        parts.pop()
    cleaned = " ".join(parts).strip()
    # Avoid trailing punctuation artifacts after truncation.
    cleaned = cleaned.rstrip(".,:;-")
    return cleaned


def generate_topic(trend_context: str = "", recent_titles: list[str] | None = None) -> str:
    """Generate a single trending iOS topic suitable for a Medium article title."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    recent_titles = recent_titles or []
    recent_titles_context = "\n".join(f"- {title}" for title in recent_titles[:15]) or "- None"
    prompt_template = _load_prompt_template()

    candidate = ""
    for _attempt in range(3):
        prompt = prompt_template.format(
            trend_context=trend_context.strip()
            or "No external trend signals were available this run.",
            recent_titles=recent_titles_context,
        )
        response = client.responses.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMPERATURE,
            max_output_tokens=220,
            input=prompt,
        )

        candidate = response.output_text.strip().splitlines()[0].strip().strip('"')
        candidate = re.sub(r"^\s*\d+[\.)]\s*", "", candidate).strip()
        candidate = _constrain_title_length(candidate)
        if candidate and not _is_repetitive(candidate, recent_titles):
            return candidate

    if not candidate:
        raise RuntimeError("Topic generation returned empty output.")
    return candidate
