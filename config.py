"""Central configuration for the ios-dev-ai-writer project."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from a local .env file (if present).
load_dotenv()

LOGGER = logging.getLogger(__name__)
_REASONING_EFFORT_VALUES = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh"}
)


def _normalize_swift_language_mode(value: str) -> str:
    """Normalize `swiftc -swift-version` values to supported major modes."""
    cleaned = value.strip()
    if cleaned in {"4", "4.2", "5", "6"}:
        return cleaned
    major = cleaned.split(".", 1)[0].strip()
    if major in {"4", "5", "6"}:
        return major
    return "6"


def _normalized_model_name(model: str | None) -> str:
    """Return a trimmed lowercase model identifier."""
    return (model or OPENAI_MODEL).strip().lower()


def _is_gpt5_family(model: str | None) -> bool:
    """Return True for GPT-5 family models."""
    return _normalized_model_name(model).startswith("gpt-5")


def _is_gpt51_family(model: str | None) -> bool:
    """Return True for GPT-5.1 models and snapshots."""
    return _normalized_model_name(model).startswith("gpt-5.1")


def _is_gpt5_pro_family(model: str | None) -> bool:
    """Return True for GPT-5 Pro models."""
    return _normalized_model_name(model).startswith("gpt-5-pro")


def _is_o_series(model: str | None) -> bool:
    """Return True for OpenAI o-series reasoning models."""
    return _normalized_model_name(model).startswith("o")


def openai_model_supports_reasoning(model: str | None = None) -> bool:
    """Return whether the selected model supports the `reasoning` parameter."""
    selected = _normalized_model_name(model)
    return selected.startswith("gpt-5") or selected.startswith("o")


def _default_reasoning_effort(model: str | None = None) -> str:
    """Return the project default reasoning effort for the selected model."""
    if _is_gpt5_pro_family(model):
        return "high"
    if _is_gpt51_family(model):
        return "none"
    return "low"


def _normalize_reasoning_effort(value: str | None, *, model: str | None = None) -> str:
    """Normalize OpenAI reasoning effort with model-aware compatibility rules."""
    selected = _normalized_model_name(model)
    cleaned = (value or "").strip().lower()
    default = _default_reasoning_effort(selected)

    if not cleaned:
        return default
    if cleaned not in _REASONING_EFFORT_VALUES:
        LOGGER.warning(
            "Unsupported OPENAI_REASONING_EFFORT %r for model %s; using %s.",
            value,
            selected,
            default,
        )
        return default

    if _is_gpt5_pro_family(selected):
        if cleaned != "high":
            LOGGER.warning(
                "Model %s only supports reasoning effort 'high'; using 'high'.",
                selected,
            )
        return "high"

    if _is_gpt51_family(selected):
        if cleaned == "minimal":
            LOGGER.warning(
                "Model %s does not support reasoning effort 'minimal'; using 'low'.",
                selected,
            )
            return "low"
        if cleaned == "xhigh":
            LOGGER.warning(
                "Model %s does not support reasoning effort 'xhigh'; using 'high'.",
                selected,
            )
            return "high"
        return cleaned

    if _is_gpt5_family(selected) and cleaned == "none":
        LOGGER.warning(
            "Model %s does not support reasoning effort 'none'; using %s.",
            selected,
            default,
        )
        return default

    return cleaned

# OpenAI credentials and model settings.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.7"))
_OPENAI_REASONING_EFFORT_ENV = os.getenv("OPENAI_REASONING_EFFORT")
OPENAI_REASONING_EFFORT = _normalize_reasoning_effort(
    _OPENAI_REASONING_EFFORT_ENV,
    model=OPENAI_MODEL,
)


def openai_model_supports_temperature(
    model: str | None = None,
    *,
    reasoning_effort: str | None = None,
) -> bool:
    """Return whether the selected model accepts the `temperature` parameter."""
    selected = _normalized_model_name(model)
    if _is_gpt51_family(selected):
        effective_reasoning = _normalize_reasoning_effort(
            reasoning_effort if reasoning_effort is not None else _OPENAI_REASONING_EFFORT_ENV,
            model=selected,
        )
        return effective_reasoning == "none"
    return not _is_gpt5_family(selected)


def openai_generation_kwargs(
    temperature: float | None = None,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, object]:
    """Build model-compatible optional generation arguments."""
    selected = _normalized_model_name(model)
    kwargs: dict[str, object] = {}

    effective_reasoning = _normalize_reasoning_effort(
        reasoning_effort if reasoning_effort is not None else _OPENAI_REASONING_EFFORT_ENV,
        model=selected,
    )
    if openai_model_supports_reasoning(selected):
        kwargs["reasoning"] = {"effort": effective_reasoning}

    if temperature is not None and openai_model_supports_temperature(
        selected,
        reasoning_effort=effective_reasoning,
    ):
        kwargs["temperature"] = temperature

    return kwargs

# Output directory for generated markdown articles.
OUTPUT_ARTICLES_DIR = Path("outputs/articles")
OUTPUT_TRENDS_DIR = Path("outputs/trends")
OUTPUT_LINKEDIN_DIR = Path("outputs/linkedin")
OUTPUT_CODEGEN_DIR = Path("outputs/codegen")
OUTPUT_NEWSLETTER_DIR = Path("outputs/newsletter")

# Trend discovery configuration.
TREND_DISCOVERY_ENABLED = os.getenv("TREND_DISCOVERY_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TREND_MAX_ITEMS_PER_SOURCE = int(os.getenv("TREND_MAX_ITEMS_PER_SOURCE", "10"))
TREND_HTTP_TIMEOUT_SECONDS = int(os.getenv("TREND_HTTP_TIMEOUT_SECONDS", "12"))
REDDIT_USER_AGENT = os.getenv(
    "REDDIT_USER_AGENT",
    "ios-dev-ai-writer/1.0 (weekly trend scanner)",
)
TREND_SOURCES = tuple(
    source.strip().lower()
    for source in os.getenv(
        "TREND_SOURCES",
        "hackernews,reddit,apple,wwdc,viral,social,platforms,custom,websearch",
    ).split(",")
    if source.strip()
)
CUSTOM_TRENDS_FILE = Path(os.getenv("CUSTOM_TRENDS_FILE", "scanners/custom_trends.json"))

# Content quality controls.
EDITOR_PASS_ENABLED = os.getenv("EDITOR_PASS_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MEDIUM_LAYOUT_REINFORCEMENT_ENABLED = os.getenv(
    "MEDIUM_LAYOUT_REINFORCEMENT_ENABLED", "true"
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MEDIUM_LAYOUT_MAX_REPAIR_PASSES = max(
    0, int(os.getenv("MEDIUM_LAYOUT_MAX_REPAIR_PASSES", "2"))
)
MEDIUM_LAYOUT_MIN_SCORE = max(1, int(os.getenv("MEDIUM_LAYOUT_MIN_SCORE", "8")))
FACT_GROUNDING_ENABLED = os.getenv("FACT_GROUNDING_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
FACT_GROUNDING_MAX_PASSES = max(0, int(os.getenv("FACT_GROUNDING_MAX_PASSES", "1")))

TOPIC_INTERESTS = [
    item.strip()
    for item in os.getenv(
        "TOPIC_INTERESTS",
        # Deduplicated: "Swift async await patterns" removed (covered by "Structured Concurrency").
        # "UIKit interoperability" kept as distinct from "UIKit to SwiftUI migration patterns"
        # (interoperability = coexistence; migration = full cutover).
        "Structured Concurrency,SwiftUI architecture,Swift 6 Adoption,iOS performance improvements,Xcode tips and debugging workflows,UIKit interoperability,SwiftData persistence,App Intents,Apple Intelligence APIs,WidgetKit,verified Swift tips and tricks,verified SwiftUI modifiers,Swift 6.3 Macros,Reducing Boilerplate in Real Projects,visionOS development,accessibility in SwiftUI,Swift Testing framework,SwiftUI animations and transitions,UIKit to SwiftUI migration patterns",
    ).split(",")
    if item.strip()
]

# Semantic similarity threshold for topic deduplication.
# Lowered from 0.80 → 0.72 to catch same-concept/different-wording duplicates.
TOPIC_SIMILARITY_THRESHOLD = float(os.getenv("TOPIC_SIMILARITY_THRESHOLD", "0.72"))

# Topic composition policy is now Apple-platform programming only.
# `TOPIC_MODE` remains for backward compatibility and is normalized to `ios_only`.
TOPIC_MODE = "ios_only"

# Newsletter assembly.
NEWSLETTER_ENABLED = os.getenv("NEWSLETTER_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
NEWSLETTER_NAME = os.getenv("NEWSLETTER_NAME", "iOS Dev Weekly").strip()
NEWSLETTER_ISSUE_FILE = Path(
    os.getenv("NEWSLETTER_ISSUE_FILE", ".newsletter_issue_number")
)

# LinkedIn post generation.
LINKEDIN_POST_ENABLED = os.getenv("LINKEDIN_POST_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LINKEDIN_CODE_SNIPPET_MODE = os.getenv("LINKEDIN_CODE_SNIPPET_MODE", "auto").strip().lower()

# Swift language targeting policy for generated snippets.
# - SWIFT_LANGUAGE_VERSION documents the target stable release.
# - SWIFT_COMPILER_LANGUAGE_MODE maps to `swiftc -swift-version` (e.g. 5 or 6).
SWIFT_LANGUAGE_VERSION = os.getenv("SWIFT_LANGUAGE_VERSION", "6.2.4").strip()
SWIFT_COMPILER_LANGUAGE_MODE = _normalize_swift_language_mode(
    os.getenv("SWIFT_COMPILER_LANGUAGE_MODE", SWIFT_LANGUAGE_VERSION)
)

# Code generation failure handling:
# - omit: publish article without code block if no validated snippet is available
# - error: fail the pipeline run when no validated snippet is available
CODEGEN_FAILURE_MODE = os.getenv("CODEGEN_FAILURE_MODE", "omit").strip().lower()

# Code snippet validation mode:
# - snippet: validate syntax/placeholder quality only (best for article snippets)
# - compile: strict Swift typecheck against available iOS SDK
# - none: skip validation completely
CODEGEN_VALIDATION_MODE = os.getenv("CODEGEN_VALIDATION_MODE", "snippet").strip().lower()

# Post-generation self-review and quality history.
SELF_REVIEW_ENABLED = os.getenv("SELF_REVIEW_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
OUTPUT_QUALITY_HISTORY_PATH = Path(
    os.getenv("OUTPUT_QUALITY_HISTORY_PATH", "outputs/quality_history.json")
)

# Review-triggered repair: re-run the editor pass when review scores are below threshold.
REVIEW_REPAIR_ENABLED = os.getenv("REVIEW_REPAIR_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
REVIEW_REPAIR_MIN_SCORE = max(1, int(os.getenv("REVIEW_REPAIR_MIN_SCORE", "7")))

# Voice fingerprint removal: rewrite AI-pattern prose to read like a senior engineer.
VOICE_PASS_ENABLED = os.getenv("VOICE_PASS_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
