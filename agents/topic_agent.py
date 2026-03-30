"""Topic agent: generate a trending Apple-platform development topic.

Design principles
-----------------
- All constants are typed ``Final``; magic numbers have named homes.
- ``random`` is imported at the top level, not inside a function.
- ``_sample_topic_family`` uses a ``WeightedFamily`` dataclass instead of
  parallel lists joined by index arithmetic.
- ``_is_semantically_repetitive`` documents its fallback contract and avoids
  a bare ``except Exception: pass`` — logs a debug warning instead.
- The generation retry loop is annotated clearly with per-step logging.
- ``OPENAI_API_KEY`` check removed — key validation belongs in
  ``create_openai_client()``.
- Structured logging on success, fallback, and constraint-violation paths.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable

from config import OPENAI_MODEL, OPENAI_TEMPERATURE, TOPIC_INTERESTS, TOPIC_SIMILARITY_THRESHOLD, openai_generation_kwargs
from utils.observability import get_logger, log_event
from utils.openai_logging import create_openai_client, embeddings_create_with_retry, responses_create_logged

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROMPT_PATH: Final[Path] = Path("prompts/topic_prompt.txt")
MAX_OUTPUT_TOKENS: Final[int] = 1_500
MAX_GENERATION_ATTEMPTS: Final[int] = 5
RECENT_TITLES_DISPLAY_LIMIT: Final[int] = 24  # Show all loaded titles, not a capped subset.
SUPPLEMENTAL_INTERESTS_LIMIT: Final[int] = 4

# Topic novelty thresholds.
WORD_OVERLAP_THRESHOLD: Final[float] = 0.50
# Semantic threshold loaded from config (env: TOPIC_SIMILARITY_THRESHOLD, default 0.72).
# Lowered from 0.80 → 0.72 to catch "same concept, different audience" duplicates.
SEMANTIC_SIMILARITY_THRESHOLD: Final[float] = TOPIC_SIMILARITY_THRESHOLD

# Theme cluster saturation: block a theme once this many recent titles already cover it.
THEME_CLUSTER_SATURATION_LIMIT: Final[int] = 2

# Title length constraints.
TITLE_MAX_CHARS: Final[int] = 60
TITLE_MAX_WORDS: Final[int] = 10

# Weight multiplier that de-prioritises migration topics.
MIGRATION_FAMILY_WEIGHT_FACTOR: Final[float] = 0.5
BASE_WEIGHT: Final[float] = 4.0
WEIGHT_PENALTY_PER_MATCH: Final[float] = 1.5

# Embedding model used for semantic deduplication.
EMBEDDING_MODEL: Final[str] = "text-embedding-3-small"

LOGGER = get_logger("pipeline.topic")

# ---------------------------------------------------------------------------
# Regex patterns — compiled once
# ---------------------------------------------------------------------------

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
_LEADING_NUMBERING_RE: Final[re.Pattern[str]] = re.compile(r"^\s*\d+[\.)]\s*")
_MIGRATION_TARGET_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(completion handler|callback|delegate|kvo|nsnotification|urlsession|combine|uikit)\b",
    re.IGNORECASE,
)

# Theme clusters used to detect topic saturation across recent articles.
# A title matches a cluster if ANY of its patterns fires (case-insensitive).
# When THEME_CLUSTER_SATURATION_LIMIT titles in `recent` match a cluster,
# a new candidate that also matches that cluster is rejected.
_THEME_CLUSTER_PATTERNS: Final[dict[str, list[str]]] = {
    "Swift concurrency / async-await": [
        r"\basync\b", r"\bawait\b", r"\bconcurren",
        r"\bactor\b", r"\bcontinuation\b", r"\btask\s*group\b",
    ],
    "UIKit migration": [
        r"\buikit\b", r"\buitableview\b", r"\buiviewcontroller\b",
        r"\bnavigationcontroller\b", r"\bdelegat\w+.*\bswift\b",
    ],
    "SwiftUI performance profiling": [
        r"\bswiftui\b.*\bperforman", r"\bperforman.*\bswiftui\b",
        r"\bswiftui\b.*\bprofil", r"\bprofil.*\bswiftui\b",
        r"\bswiftui\b.*\binstrument",
    ],
}

_TRAILING_STOP_WORDS: Final[frozenset[str]] = frozenset(
    {"for", "to", "with", "and", "or", "of", "in", "on"}
)
_TITLE_STRIP_CHARS: Final[str] = ".,:;-"

# ---------------------------------------------------------------------------
# Keyword pattern tables
# ---------------------------------------------------------------------------

APPLE_WORD_PATTERNS: Final[list[str]] = [
    r"\bapple\b", r"\bios\b", r"\bipados\b", r"\bmacos\b",
    r"\bwatchos\b", r"\bvisionos\b", r"\bswift\b", r"\bswiftui\b",
    r"\buikit\b", r"\bappkit\b", r"\bxcode\b", r"\bcombine\b",
    r"\bswiftdata\b", r"\bcore\s?data\b", r"\bwidgetkit\b",
    r"\bapp\sintents?\b", r"\basync\s*/\s*await\b",
    r"\bstructured concurrency\b", r"\bmodifier(s)?\b",
    r"\bperformance\b", r"\binstruments?\b", r"\bapple intelligence\b",
    r"\bfoundation models?\b", r"\bmacro(s)?\b", r"\bswift\s*6\.?3\b",
    r"\bboilerplate\b",
]

AI_WORD_PATTERNS: Final[list[str]] = [
    r"\bai\b", r"\bagentic\b", r"\bagent(s)?\b", r"\bgenerative\b",
    r"\bllm(s)?\b", r"\bprompt(s)?\b", r"\binference\b",
    r"\bautomation\b", r"\bmachine learning\b", r"\bcore\s?ml\b",
]

MIGRATION_WORD_PATTERNS: Final[list[str]] = [
    r"\bmigration\b", r"\bmigrate\b", r"\bdeprecated?\b",
    r"\blegacy\b", r"\bswift\s*6\b", r"\bstrict concurrency\b",
]

APPLE_INTELLIGENCE_ALLOWLIST: Final[list[str]] = [
    r"\bapple intelligence\b",
    r"\bapple intelligence api(s)?\b",
    r"\bfoundation models?\b",
    r"\bapp\sintents?\b",
]

# ---------------------------------------------------------------------------
# Topic families
# ---------------------------------------------------------------------------

TOPIC_FAMILIES: Final[list[tuple[str, list[str]]]] = [
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
        "OSSignposter for custom performance markers in iOS",
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

# ---------------------------------------------------------------------------
# Stop-word and word-set helpers
# ---------------------------------------------------------------------------

_STOP_WORDS: Final[frozenset[str]] = frozenset(
    {"the", "a", "an", "for", "and", "with", "to", "in", "on", "of",
     "apple", "ios", "swift"}
)


def _stem(word: str) -> str:
    """Lightweight stem: truncate long words to 5-char prefix to match word forms."""
    return word[:5] if len(word) >= 6 else word


def _word_set(text: str) -> set[str]:
    """Build a normalised, stop-word-filtered word set for overlap checks."""
    return {
        _stem(w) for w in re.findall(r"[a-z0-9]+", text.lower())
        if len(w) > 2 and w not in _STOP_WORDS
    }


# Stopwords for title normalisation — broader than _STOP_WORDS to strip
# grammatical filler that inflates apparent topic distance.
_NORMALISE_STOPWORDS: Final[frozenset[str]] = frozenset({
    "a", "an", "the", "and", "or", "for", "in", "on", "of", "to", "with",
    "using", "via", "how", "what", "your", "my", "our", "their",
    "is", "are", "was", "were", "be", "been", "being", "have", "has",
})


def normalise_title(title: str) -> set[str]:
    """Lowercase, remove stopwords, stem verb forms (profiling→profil), return a set.

    Simple verb normalisation: strip trailing 'ing' from tokens longer than 6
    characters so that 'profiling' and 'profile' produce overlapping stems.
    """
    tokens = re.findall(r"\w+", title.lower())
    tokens = [t for t in tokens if t not in _NORMALISE_STOPWORDS]
    normalised: list[str] = []
    for t in tokens:
        if t.endswith("ing") and len(t) > 6:
            normalised.append(t[:-3])  # "profiling" → "profil", "rendering" → "render"
        else:
            normalised.append(t)
    return set(normalised)


# ---------------------------------------------------------------------------
# Keyword matching helpers
# ---------------------------------------------------------------------------


def _matches_any(text: str, patterns: list[str]) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered) for p in patterns)


def _is_apple_programming_topic(title: str) -> bool:
    """Return True when *title* is scoped to Apple-platform programming."""
    has_apple = _matches_any(title, APPLE_WORD_PATTERNS)
    has_ai = _matches_any(title, AI_WORD_PATTERNS)
    has_allowed_intelligence = _matches_any(title, APPLE_INTELLIGENCE_ALLOWLIST)
    return has_apple and (not has_ai or has_allowed_intelligence)


# ---------------------------------------------------------------------------
# Novelty checks
# ---------------------------------------------------------------------------


def _is_repetitive(
    candidate: str,
    recent_titles: Iterable[str],
    threshold: float = WORD_OVERLAP_THRESHOLD,
) -> bool:
    """Return True when *candidate* has too much word overlap with a recent title.

    Uses ``normalise_title`` so verb forms like 'profiling' and 'profile' are
    treated as the same token, preventing near-duplicates from slipping through.
    """
    candidate_words = normalise_title(candidate)
    if not candidate_words:
        return False
    for previous in recent_titles:
        prev_words = normalise_title(previous)
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
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _is_semantically_repetitive(
    candidate: str,
    recent_titles: list[str],
    client: object,
    threshold: float = SEMANTIC_SIMILARITY_THRESHOLD,
) -> bool:
    """Return True when *candidate* is semantically similar to a recent title.

    Uses a single batched embeddings call for efficiency. Falls back to
    ``False`` on any API or type error so ``_is_repetitive`` remains the
    primary safety net — the failure is logged at DEBUG level for observability.
    """
    if not recent_titles:
        return False
    try:
        from openai import OpenAI  # noqa: PLC0415
        assert isinstance(client, OpenAI)
        response = embeddings_create_with_retry(
            client,
            model=EMBEDDING_MODEL,
            input=[candidate, *recent_titles],
        )
        embeddings = [item.embedding for item in response.data]
        candidate_emb = embeddings[0]
        for prev_emb in embeddings[1:]:
            if _cosine_similarity(candidate_emb, prev_emb) >= threshold:
                return True
    except Exception as exc:  # noqa: BLE001
        log_event(
            LOGGER,
            "semantic_similarity_check_failed",
            level=logging.WARNING,
            candidate=candidate,
            error=repr(exc),
        )
    return False


def _shares_migration_target(
    candidate: str,
    recent_titles: Iterable[str],
) -> bool:
    """Return True when *candidate* shares a migration source API with a recent title."""
    candidate_targets = set(_MIGRATION_TARGET_RE.findall(candidate.lower()))
    if not candidate_targets:
        return False
    for prev in recent_titles:
        if candidate_targets & set(_MIGRATION_TARGET_RE.findall(prev.lower())):
            return True
    return False


def _cluster_match(title: str, patterns: list[str]) -> bool:
    """Return True when *title* matches any pattern in a theme cluster."""
    lowered = title.lower()
    return any(re.search(p, lowered) for p in patterns)


def _is_theme_cluster_saturated(
    candidate: str,
    recent_titles: list[str],
) -> bool:
    """Return True when *candidate* falls in a theme cluster already at the saturation limit.

    A cluster is saturated when ``THEME_CLUSTER_SATURATION_LIMIT`` or more of the
    recent titles already cover that theme.  This is a hard block — the candidate
    is rejected regardless of how different the title wording is.
    """
    for patterns in _THEME_CLUSTER_PATTERNS.values():
        if not _cluster_match(candidate, patterns):
            continue
        count = sum(1 for t in recent_titles if _cluster_match(t, patterns))
        if count >= THEME_CLUSTER_SATURATION_LIMIT:
            return True
    return False


def _theme_concentration_summary(recent_titles: list[str]) -> str:
    """Return a formatted prompt warning for saturated theme clusters.

    Lists each cluster where ``THEME_CLUSTER_SATURATION_LIMIT`` or more recent
    titles already cover that theme.  Returns a 'none' notice when clear.
    """
    warnings: list[str] = []
    for cluster_name, patterns in _THEME_CLUSTER_PATTERNS.items():
        count = sum(1 for t in recent_titles if _cluster_match(t, patterns))
        if count >= THEME_CLUSTER_SATURATION_LIMIT:
            warnings.append(
                f'- "{cluster_name}" — {count} recent articles already cover this theme. '
                "AVOID THIS THEME ENTIRELY this run."
            )
    return "\n".join(warnings) if warnings else "- None — all themes are available this run."


# ---------------------------------------------------------------------------
# Title length constraint
# ---------------------------------------------------------------------------


def _constrain_title_length(
    title: str,
    max_chars: int = TITLE_MAX_CHARS,
    max_words: int = TITLE_MAX_WORDS,
) -> str:
    """Trim *title* to fit character and word limits without mid-word cuts."""
    cleaned = _WHITESPACE_RE.sub(" ", title).strip().strip('"')
    words = cleaned.split()[:max_words]

    kept: list[str] = []
    for word in words:
        candidate = " ".join([*kept, word])
        if len(candidate) <= max_chars:
            kept.append(word)
        else:
            break

    result = " ".join(kept).strip()
    if not result:
        result = (words[0][:max_chars].rstrip(" ,:;-") if words else "")

    # Drop trailing connectors left by truncation.
    parts = result.split()
    while parts and parts[-1].lower() in _TRAILING_STOP_WORDS:
        parts.pop()

    return " ".join(parts).rstrip(_TITLE_STRIP_CHARS)


# ---------------------------------------------------------------------------
# Topic family sampling
# ---------------------------------------------------------------------------


@dataclass()
class _WeightedFamily:
    name: str
    queries: list[str]
    weight: float


def _sample_topic_family(recent_titles: list[str]) -> tuple[str, list[str]]:
    """Pick a topic family, biased away from recently used families and migration."""
    weighted: list[_WeightedFamily] = []

    for family_name, queries in TOPIC_FAMILIES:
        # Count how many recent titles share keywords with this family.
        matches = sum(
            1 for title in recent_titles
            if any(
                re.search(r"\b" + re.escape(kw.lower().split()[0]) + r"\b", title.lower())
                for kw in queries
            )
        )
        weight = max(1.0, BASE_WEIGHT - matches * WEIGHT_PENALTY_PER_MATCH)
        if family_name == "migration":
            weight *= MIGRATION_FAMILY_WEIGHT_FACTOR
        weighted.append(_WeightedFamily(name=family_name, queries=queries, weight=weight))

    chosen = random.choices(weighted, weights=[f.weight for f in weighted], k=1)[0]
    return chosen.name, chosen.queries


# ---------------------------------------------------------------------------
# Interest list filtering
# ---------------------------------------------------------------------------


def _filtered_interests(
    topic_interests: list[str],
    recent_titles: list[str],
) -> list[str]:
    """Build a varied, family-weighted interest list for the current run."""
    _family_name, family_queries = _sample_topic_family(recent_titles)
    supplemental = [
        item.strip() for item in topic_interests
        if item and item.strip()
        and _matches_any(item, APPLE_WORD_PATTERNS)
        and not _matches_any(item, AI_WORD_PATTERNS)
        and not _matches_any(item, MIGRATION_WORD_PATTERNS)
    ]
    return list(family_queries) + supplemental[:SUPPLEMENTAL_INTERESTS_LIMIT]


# ---------------------------------------------------------------------------
# Fallback titles
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def _load_template(path: Path = PROMPT_PATH) -> str:
    """Read and return the topic prompt template.

    Raises
    ------
    FileNotFoundError
        When the template file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Topic prompt template not found at '{path}'. "
            "Verify PROMPT_PATH or the process working directory."
        )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_topic(
    trend_context: str = "",
    recent_titles: list[str] | None = None,
    topic_interests: list[str] | None = None,
    topic_mode: str | None = None,  # Deprecated — Apple-platform only.
) -> str:
    """Generate a single Apple-platform topic suitable for a Medium article title.

    Parameters
    ----------
    trend_context:
        Formatted trend signal text from the scanner (may be empty).
    recent_titles:
        Previously published titles used for novelty checks.
    topic_interests:
        Priority topic themes; defaults to ``TOPIC_INTERESTS`` from config.
    topic_mode:
        Deprecated parameter — ignored. Topic generation is Apple-platform only.

    Returns
    -------
    str
        A title-cased topic string within ``TITLE_MAX_CHARS`` / ``TITLE_MAX_WORDS``.

    Raises
    ------
    FileNotFoundError
        When the prompt template file is missing.
    """
    _ = topic_mode  # Deprecated; retained for backward compatibility.

    client = create_openai_client()
    recent = recent_titles or []
    interests = topic_interests or TOPIC_INTERESTS
    filtered = _filtered_interests(interests, recent)

    recent_context = "\n".join(f"- {t}" for t in recent[:RECENT_TITLES_DISPLAY_LIMIT]) or "- None"
    interests_context = "\n".join(f"- {i}" for i in filtered) or "- SwiftUI"
    theme_warnings = _theme_concentration_summary(recent)
    template = _load_template()

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        prompt = (
            template
            .replace("{trend_context}", trend_context.strip() or "No external trend signals were available this run.")
            .replace("{recent_titles}", recent_context)
            .replace("{topic_interests}", interests_context)
            .replace("{theme_warnings}", theme_warnings)
        )
        response = responses_create_logged(
            client,
            agent_name="topic_agent",
            operation="generate_topic",
            model=OPENAI_MODEL,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            input=prompt,
            **openai_generation_kwargs(OPENAI_TEMPERATURE),
        )

        raw = (response.output_text or "").strip()
        if not raw:
            log_event(LOGGER, "topic_generation_empty", level=logging.WARNING, attempt=attempt)
            continue

        candidate = _LEADING_NUMBERING_RE.sub("", raw.splitlines()[0].strip().strip('"'))
        candidate = _constrain_title_length(candidate)

        if not candidate:
            continue

        violations: list[str] = []
        if not _is_apple_programming_topic(candidate):
            violations.append("not_apple_platform")
        if _is_repetitive(candidate, recent):
            violations.append("word_repetitive")
        if _is_semantically_repetitive(candidate, recent, client):
            violations.append("semantic_repetitive")
        if _shares_migration_target(candidate, recent):
            violations.append("migration_target_duplicate")
        if _is_theme_cluster_saturated(candidate, recent):
            violations.append("theme_cluster_saturated")

        if violations:
            log_event(
                LOGGER,
                "topic_candidate_rejected",
                level=logging.INFO,
                attempt=attempt,
                candidate=candidate,
                violations=",".join(violations),
            )
            continue

        log_event(
            LOGGER,
            "topic_generated",
            level=logging.INFO,
            attempt=attempt,
            topic=candidate,
        )
        return candidate

    log_event(
        LOGGER,
        "topic_generation_exhausted",
        level=logging.ERROR,
        attempts=MAX_GENERATION_ATTEMPTS,
    )
    raise RuntimeError(
        f"Failed to generate a novel Apple-platform topic after {MAX_GENERATION_ATTEMPTS} attempts. "
        "Check logs for rejection reasons (violations field)."
    )