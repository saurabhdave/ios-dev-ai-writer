"""Topic agent: generate a trending Apple-platform development topic."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE, TOPIC_INTERESTS, openai_generation_kwargs
from utils.openai_logging import create_openai_client, responses_create_logged

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
        "apple",
        "ios",
        "swift",
        "swiftui",
    }
    return {word for word in words if len(word) > 2 and word not in stop_words}


APPLE_WORD_PATTERNS = [
    r"\bapple\b",
    r"\bios\b",
    r"\bipados\b",
    r"\bmacos\b",
    r"\bwatchos\b",
    r"\bvisionos\b",
    r"\bswift\b",
    r"\bswiftui\b",
    r"\buikit\b",
    r"\bappkit\b",
    r"\bxcode\b",
    r"\bcombine\b",
    r"\bswiftdata\b",
    r"\bcore\s?data\b",
    r"\bwidgetkit\b",
    r"\bapp\sintents?\b",
    r"\basync\s*/\s*await\b",
    r"\bstructured concurrency\b",
    r"\bmodifier(s)?\b",
    r"\bperformance\b",
    r"\binstruments?\b",
    r"\bapple intelligence\b",
    r"\bfoundation models?\b",
    r"\bmacro(s)?\b",
    r"\bswift\s*6\.?3\b",
    r"\bboilerplate\b",
]

AI_WORD_PATTERNS = [
    r"\bai\b",
    r"\bagentic\b",
    r"\bagent(s)?\b",
    r"\bgenerative\b",
    r"\bllm(s)?\b",
    r"\bprompt(s)?\b",
    r"\binference\b",
    r"\bautomation\b",
    r"\bmachine learning\b",
    r"\bcore\s?ml\b",
]

MIGRATION_WORD_PATTERNS = [
    r"\bmigration\b",
    r"\bmigrate\b",
    r"\bdeprecated?\b",
    r"\blegacy\b",
    r"\bswift\s*6\b",
    r"\bstrict concurrency\b",
]

MIGRATION_INTEREST_DEFAULTS = [
    "Swift 6 migration and strict concurrency",
    "Deprecated Apple APIs to modern replacements",
    "Legacy UIKit patterns to modern SwiftUI",
]

APPLE_INTELLIGENCE_ALLOWLIST = [
    r"\bapple intelligence\b",
    r"\bapple intelligence api(s)?\b",
    r"\bfoundation models?\b",
    r"\bapp\sintents?\b",
]


def _contains_pattern(text: str, patterns: list[str]) -> bool:
    """Check whether any keyword pattern appears in text."""
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in patterns)


def _has_allowed_intelligence_context(text: str) -> bool:
    """Allow explicit Apple Intelligence contexts while blocking generic AI framing."""
    return _contains_pattern(text, APPLE_INTELLIGENCE_ALLOWLIST)


def _is_apple_programming_topic(title: str) -> bool:
    """Validate generated title scope for Apple-platform programming only."""
    has_apple_signal = _contains_pattern(title, APPLE_WORD_PATTERNS)
    has_ai_signal = _contains_pattern(title, AI_WORD_PATTERNS)
    has_allowed_intelligence = _has_allowed_intelligence_context(title)
    return has_apple_signal and (not has_ai_signal or has_allowed_intelligence)


def _filtered_interests(topic_interests: list[str]) -> list[str]:
    """Keep Apple-programming interests and drop AI-first topics."""
    cleaned = [item.strip() for item in topic_interests if item and item.strip()]
    apple_interests = [
        item
        for item in cleaned
        if _contains_pattern(item, APPLE_WORD_PATTERNS) and not _contains_pattern(item, AI_WORD_PATTERNS)
    ]
    if not apple_interests:
        apple_interests = [
            "Swift async await patterns",
            "Structured Concurrency on Apple platforms",
            "SwiftUI performance improvements with Instruments",
            "Swift 6.3 Macros adoption patterns",
            "Reducing boilerplate in real Apple-platform projects",
            "App Intents and Apple Intelligence APIs",
            "Xcode tips and debugging workflows",
            "Verified SwiftUI modifiers and rendering behavior",
            "SwiftData and persistence patterns",
        ]

    has_migration_interest = any(
        _contains_pattern(item, MIGRATION_WORD_PATTERNS) for item in apple_interests
    )
    if not has_migration_interest:
        apple_interests.extend(MIGRATION_INTEREST_DEFAULTS)
    return apple_interests


def _fallback_topic_title(recent_titles: Iterable[str]) -> str:
    """Use Apple-only fallback titles when model responses violate constraints."""
    candidates = [
        "Swift 6.3 Macros for iOS Codebases",
        "Reducing SwiftUI Boilerplate in Real Projects",
        "Migrating Deprecated iOS APIs to Swift 6 Safely",
        "Swift Async Await Migration from Completion Handlers",
        "Structured Concurrency Patterns for SwiftUI Apps",
        "Scaling SwiftUI State Management in Production Apps",
        "Reliable Background Tasks with Swift Concurrency on iOS",
        "Profiling SwiftUI List Performance with Instruments",
    ]
    for candidate in candidates:
        normalized = _constrain_title_length(candidate)
        if normalized and not _is_repetitive(normalized, recent_titles):
            return normalized
    return _constrain_title_length(candidates[0])


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


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _is_semantically_repetitive(
    candidate: str,
    recent_titles: list[str],
    client: object,
    threshold: float = 0.88,
) -> bool:
    """Check semantic similarity via embeddings — catches near-duplicates that word overlap misses.

    Uses a single batched embeddings call. Falls back silently to False on any error
    so the word-set check in _is_repetitive always remains the safety net.
    """
    if not recent_titles:
        return False
    try:
        from openai import OpenAI  # noqa: PLC0415

        assert isinstance(client, OpenAI)
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=[candidate] + list(recent_titles),
        )
        embeddings = [item.embedding for item in response.data]
        candidate_emb = embeddings[0]
        for prev_emb in embeddings[1:]:
            if _cosine_similarity(candidate_emb, prev_emb) >= threshold:
                return True
    except Exception:
        pass
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


def generate_topic(
    trend_context: str = "",
    recent_titles: list[str] | None = None,
    topic_interests: list[str] | None = None,
    topic_mode: str | None = None,
) -> str:
    """Generate a single Apple-platform topic suitable for a Medium article title."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = create_openai_client()
    recent_titles = recent_titles or []
    topic_interests = topic_interests or TOPIC_INTERESTS
    _ = topic_mode  # Deprecated. Topic generation is Apple-platform only.
    filtered_interests = _filtered_interests(topic_interests)
    recent_titles_context = "\n".join(f"- {title}" for title in recent_titles[:15]) or "- None"
    topic_interests_context = "\n".join(f"- {item}" for item in filtered_interests) or "- SwiftUI"
    prompt_template = _load_prompt_template()

    candidate = ""
    for _attempt in range(5):
        prompt = prompt_template.format(
            trend_context=trend_context.strip()
            or "No external trend signals were available this run.",
            recent_titles=recent_titles_context,
            topic_interests=topic_interests_context,
        )
        response = responses_create_logged(
            client,
            agent_name="topic_agent",
            operation="generate_topic",
            model=OPENAI_MODEL,
            max_output_tokens=220,
            input=prompt,
            log_fields={"attempt": _attempt + 1},
            **openai_generation_kwargs(OPENAI_TEMPERATURE),
        )

        output_text = (response.output_text or "").strip()
        if not output_text:
            continue
        candidate = output_text.splitlines()[0].strip().strip('"')
        candidate = re.sub(r"^\s*\d+[\.)]\s*", "", candidate).strip()
        candidate = _constrain_title_length(candidate)
        if (
            candidate
            and _is_apple_programming_topic(candidate)
            and not _is_repetitive(candidate, recent_titles)
            and not _is_semantically_repetitive(candidate, recent_titles, client)
        ):
            return candidate

    return _fallback_topic_title(recent_titles)
