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

TOPIC_FAMILIES: list[tuple[str, list[str]]] = [
    ("architecture", [
        "Swift architecture patterns for iOS apps",
        "Modular iOS app architecture with Swift packages",
        "Dependency injection patterns in SwiftUI apps",
        "MVVM vs @Observable in production SwiftUI apps",
        "Composable navigation with NavigationStack",
    ]),
    ("performance", [
        "Profiling SwiftUI rendering with Instruments",
        "Reducing app launch time on iOS",
        "Xcode Time Profiler for iOS hang detection",
        "os_signpost for custom performance markers in iOS",
        "Memory management in Swift actors",
    ]),
    ("concurrency", [
        "Swift async await patterns in iOS SwiftUI apps",
        "Structured Concurrency SwiftUI iOS patterns",
        "Actor isolation and data races in Swift 6",
        "Async sequences for real-time data in SwiftUI",
        "Task groups and cancellation in Swift concurrency",
    ]),
    ("swiftui_features", [
        "SwiftUI Layout protocol for custom layouts",
        "Verified SwiftUI modifiers tips and tricks",
        "SwiftUI animations with PhaseAnimator and KeyframeAnimator",
        "Adaptive layouts for iPad and iPhone in SwiftUI",
        "SwiftUI environment values and custom keys",
    ]),
    ("tooling_debugging", [
        "Xcode tips and tricks iOS debugging build performance",
        "Swift Package plugins for Xcode automation",
        "Improving Xcode build times with explicit modules",
        "Debugging memory leaks with Xcode Memory Graph",
        "Testing async Swift code with Swift Testing framework",
    ]),
    ("frameworks_apis", [
        "App Intents Apple Intelligence APIs iOS",
        "WidgetKit App Intents iOS development",
        "SwiftData and persistence patterns",
        "visionOS development with RealityKit",
        "Swift 6.3 Macros iOS SwiftUI practical usage",
    ]),
    ("accessibility_design", [
        "Accessibility in SwiftUI apps for iOS",
        "Dynamic Type and scalable layouts in SwiftUI",
        "VoiceOver support for custom SwiftUI components",
        "Dark mode and Color Scheme best practices in iOS",
        "Haptic feedback design with CoreHaptics in iOS",
    ]),
    ("migration", [
        "Swift 6 strict concurrency migration deprecated iOS APIs",
        "UIKit to SwiftUI migration patterns Apple platforms",
        "SwiftData Core Data migration Apple platforms",
        "Replacing Combine with AsyncSequence in SwiftUI",
        "Moving from completion handlers to async await in Swift",
    ]),
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


def _sample_topic_family(recent_titles: list[str]) -> tuple[str, list[str]]:
    """Pick a topic family biased away from recently used families and migration."""
    import random

    family_scores: list[tuple[int, tuple[str, list[str]]]] = []
    for family_name, queries in TOPIC_FAMILIES:
        matches = sum(
            1 for title in recent_titles
            if any(
                re.search(r"\b" + re.escape(kw.lower().split()[0]) + r"\b", title.lower())
                for kw in queries
            )
        )
        family_scores.append((matches, (family_name, queries)))

    family_scores.sort(key=lambda x: x[0])

    weights: list[float] = []
    families: list[tuple[str, list[str]]] = []
    for score, (name, queries) in family_scores:
        base_weight = max(1.0, 4.0 - score * 1.5)
        if name == "migration":
            base_weight *= 0.5
        weights.append(base_weight)
        families.append((name, queries))

    chosen_name, chosen_queries = random.choices(families, weights=weights, k=1)[0]
    return chosen_name, chosen_queries


def _filtered_interests(topic_interests: list[str], recent_titles: list[str] | None = None) -> list[str]:
    """Build a varied, family-weighted interest list for the current run."""
    family_name, family_queries = _sample_topic_family(recent_titles or [])
    primary = list(family_queries)
    cleaned = [item.strip() for item in topic_interests if item and item.strip()]
    supplemental = [
        item for item in cleaned
        if _contains_pattern(item, APPLE_WORD_PATTERNS)
        and not _contains_pattern(item, AI_WORD_PATTERNS)
        and not _contains_pattern(item, MIGRATION_WORD_PATTERNS)
    ]
    return primary + supplemental[:4]


def _fallback_topic_title(recent_titles: Iterable[str]) -> str:
    """Use Apple-only fallback titles when model responses violate constraints."""
    candidates = [
        "Swift 6.3 Macros for iOS Codebases",                    # frameworks_apis
        "Reducing SwiftUI Boilerplate in Real Projects",         # architecture
        "Structured Concurrency Patterns for SwiftUI Apps",      # concurrency
        "Profiling SwiftUI List Performance with Instruments",   # performance
        "VoiceOver Support for Custom SwiftUI Views",            # accessibility_design
        "Xcode Build Performance with Explicit Swift Modules",   # tooling_debugging
        "SwiftUI KeyframeAnimator for Fluid Transitions",        # swiftui_features
        "Migrating Deprecated iOS APIs to Swift 6 Safely",      # migration (1 of 8)
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
    filtered_interests = _filtered_interests(topic_interests, recent_titles)
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
