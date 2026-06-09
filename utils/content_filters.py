"""Shared AI-topic exclusion filters.

Single source of truth for the generic-AI exclusion patterns and the Apple
Intelligence allowlist. Three modules previously carried near-identical
copies of these lists (trend_scanner, weekly_pipeline, topic_agent) and they
drifted when one copy was fixed — import from here instead.

Semantics: content matching an exclusion pattern is generic-AI noise and is
filtered out, UNLESS it also matches the allowlist (Apple Intelligence,
Foundation Models, and App Intents coverage is intentional).
"""

from __future__ import annotations

import re
from typing import Final

#: Patterns marking generic AI/ML content the pipeline must not publish about.
AI_EXCLUSION_PATTERNS: Final[tuple[str, ...]] = (
    r"\bai\b",
    r"\bagent(s)?\b",
    r"\bagentic\b",
    r"\bgenerative\b",
    r"\bllm(s)?\b",
    r"\bprompt(s)?\b",
    r"\binference\b",
    r"\bautomation\b",
    r"\bmachine learning\b",
    r"\bcore\s?ml\b",
)

#: Apple Intelligence surfaces that are allowed through the AI exclusion.
APPLE_INTELLIGENCE_ALLOWLIST: Final[tuple[str, ...]] = (
    r"\bapple intelligence\b",
    r"\bapple intelligence api(s)?\b",
    r"\bfoundation models?\b",
    r"\bapp\sintents?\b",
)

_AI_EXCLUSION_RE: Final[re.Pattern[str]] = re.compile("|".join(AI_EXCLUSION_PATTERNS))
_ALLOWLIST_RE: Final[re.Pattern[str]] = re.compile("|".join(APPLE_INTELLIGENCE_ALLOWLIST))


def matches_ai_exclusion(text: str) -> bool:
    """True when *text* matches any generic-AI exclusion pattern (allowlist ignored)."""
    return bool(_AI_EXCLUSION_RE.search(text.lower()))


def has_allowed_intelligence_context(text: str) -> bool:
    """True when *text* references an allowed Apple Intelligence surface."""
    return bool(_ALLOWLIST_RE.search(text.lower()))


def is_excluded_ai_topic(text: str) -> bool:
    """Final verdict: *text* is generic-AI noise with no Apple Intelligence context."""
    return matches_ai_exclusion(text) and not has_allowed_intelligence_context(text)
