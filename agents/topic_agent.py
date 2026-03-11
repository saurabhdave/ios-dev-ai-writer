"""Topic agent: generate a trending iOS development topic."""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Iterable

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE, TOPIC_INTERESTS, TOPIC_MODE

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


AI_WORD_PATTERNS = [
    r"\bai\b",
    r"\bagentic\b",
    r"\bagent(s)?\b",
    r"\bgenerative\b",
    r"\bllm(s)?\b",
    r"\bautomation\b",
]

IOS_WORD_PATTERNS = [
    r"\bios\b",
    r"\bswift\b",
    r"\bswiftui\b",
    r"\bxcode\b",
    r"\buikit\b",
    r"\bapple\b",
    r"\bvisionos\b",
    r"\bwatchos\b",
    r"\bmacos\b",
]


def _contains_pattern(text: str, patterns: list[str]) -> bool:
    """Check whether any keyword pattern appears in text."""
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in patterns)


def _classify_title_mode(title: str) -> str:
    """Classify title as ios_only, ai_only, hybrid, or other."""
    has_ai = _contains_pattern(title, AI_WORD_PATTERNS)
    has_ios = _contains_pattern(title, IOS_WORD_PATTERNS)

    if has_ai and has_ios:
        return "hybrid"
    if has_ai:
        return "ai_only"
    if has_ios:
        return "ios_only"
    return "other"


def _select_focus_mode(recent_titles: list[str], requested_mode: str) -> str:
    """Choose topic focus mode from requested policy and recent title history."""
    allowed_modes = {"balanced", "ios_only", "ai_only", "hybrid"}
    mode = requested_mode if requested_mode in allowed_modes else "balanced"
    if mode != "balanced":
        return mode

    window = recent_titles[:12]
    ios_count = sum(1 for title in window if _classify_title_mode(title) == "ios_only")
    ai_count = sum(1 for title in window if _classify_title_mode(title) == "ai_only")
    hybrid_count = sum(1 for title in window if _classify_title_mode(title) == "hybrid")
    counts = {
        "ios_only": ios_count,
        "ai_only": ai_count,
        "hybrid": hybrid_count,
    }

    min_count = min(counts.values())
    candidates = [mode_name for mode_name, count in counts.items() if count == min_count]

    # Avoid repeating the immediately previous mode when we have alternatives.
    if len(candidates) > 1 and window:
        previous_mode = _classify_title_mode(window[0])
        if previous_mode in candidates:
            non_repeating = [mode_name for mode_name in candidates if mode_name != previous_mode]
            if non_repeating:
                candidates = non_repeating

    return random.choice(candidates or ["ios_only", "ai_only", "hybrid"])


def _matches_focus_mode(title: str, focus_mode: str) -> bool:
    """Validate that generated title follows the requested topic focus mode."""
    if focus_mode not in {"ios_only", "ai_only", "hybrid"}:
        return True
    return _classify_title_mode(title) == focus_mode


def _filtered_interests(topic_interests: list[str], focus_mode: str) -> list[str]:
    """Filter/default interests so prompts align with selected focus mode."""
    cleaned = [item.strip() for item in topic_interests if item and item.strip()]
    has_ai_interest = any(_contains_pattern(item, AI_WORD_PATTERNS) for item in cleaned)
    has_ios_interest = any(_contains_pattern(item, IOS_WORD_PATTERNS) for item in cleaned)

    if focus_mode == "ios_only":
        ios_interests = [item for item in cleaned if not _contains_pattern(item, AI_WORD_PATTERNS)]
        if ios_interests:
            return ios_interests
        return [
            "SwiftUI architecture",
            "iOS performance",
            "macOS app workflows",
            "watchOS app design",
            "visionOS interaction patterns",
        ]

    if focus_mode == "ai_only":
        ai_interests = [item for item in cleaned if _contains_pattern(item, AI_WORD_PATTERNS)]
        if ai_interests:
            return ai_interests
        return [
            "Agentic workflows",
            "LLM orchestration",
            "Prompt routing",
            "Model evaluation",
            "AI automation",
        ]

    # hybrid mode: make sure both Apple-platform and AI anchors are present.
    hybrid_interests = list(cleaned)
    if not has_ios_interest:
        hybrid_interests.extend(["iOS engineering", "SwiftUI apps"])
    if not has_ai_interest:
        hybrid_interests.extend(["Agentic AI", "Generative AI"])
    return hybrid_interests


def _fallback_topic_title(focus_mode: str, recent_titles: Iterable[str]) -> str:
    """Use mode-safe fallback titles when model responses keep violating constraints."""
    fallback_by_mode = {
        "ios_only": [
            "SwiftUI State Architecture Across iOS macOS watchOS",
            "Reliable Background Tasks on iOS macOS watchOS",
            "Testing Shared SwiftUI Features Across Apple Platforms",
        ],
        "ai_only": [
            "Designing Reliable Agent Memory for Mobile Apps",
            "Evaluating LLM Tool Calls for App Automation",
            "Prompt Routing Strategies for Autonomous App Workflows",
        ],
        "hybrid": [
            "On-Device AI Inference Pipelines in SwiftUI Apps",
            "Building Agentic Features with SwiftUI Background Tasks",
            "Secure AI Assistants for iOS Enterprise Workflows",
        ],
    }

    candidates = fallback_by_mode.get(focus_mode, fallback_by_mode["hybrid"])
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
    """Generate a single trending iOS topic suitable for a Medium article title."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    recent_titles = recent_titles or []
    topic_interests = topic_interests or TOPIC_INTERESTS
    focus_mode = _select_focus_mode(recent_titles, topic_mode or TOPIC_MODE)
    filtered_interests = _filtered_interests(topic_interests, focus_mode)
    recent_titles_context = "\n".join(f"- {title}" for title in recent_titles[:15]) or "- None"
    topic_interests_context = "\n".join(f"- {item}" for item in filtered_interests) or "- AI"
    prompt_template = _load_prompt_template()

    candidate = ""
    for _attempt in range(5):
        prompt = prompt_template.format(
            trend_context=trend_context.strip()
            or "No external trend signals were available this run.",
            recent_titles=recent_titles_context,
            topic_interests=topic_interests_context,
            focus_mode=focus_mode,
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
        if (
            candidate
            and _matches_focus_mode(candidate, focus_mode)
            and not _is_repetitive(candidate, recent_titles)
        ):
            return candidate

    return _fallback_topic_title(focus_mode, recent_titles)
