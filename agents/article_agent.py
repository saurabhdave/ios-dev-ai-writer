"""Article agent: produce a full Medium-style article body.

Design principles
-----------------
- All regex patterns are compiled once at module level with named groups
  where it aids readability.
- Quality validation is data-driven (signal tables) and separated from
  the generation/retry orchestration.
- Normalisation steps are composed through a small pipeline rather than
  an ever-growing monolithic function.
- Retry logic is encapsulated and configurable; retry temperature and
  the quality threshold are explicit constants, not magic numbers.
- Every non-trivial decision is logged with structured key=value pairs.
- Public surface is one function: `generate_article`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Final

from config import OPENAI_MODEL, OPENAI_TEMPERATURE, openai_generation_kwargs
from utils.observability import get_logger, log_event
from utils.openai_logging import create_openai_client, responses_create_logged

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROMPT_PATH: Final[Path] = Path("prompts/article_prompt.txt")

#: Maximum output tokens for both first-pass and retry generation.
MAX_OUTPUT_TOKENS: Final[int] = 2_600

#: Temperature cap for the first-pass generation.
GENERATION_TEMPERATURE: Final[float] = 0.45

#: Temperature cap for the quality retry (lower = more deterministic).
RETRY_TEMPERATURE: Final[float] = 0.35

#: Minimum Apple-specific signal hits required to pass the quality gate.
MIN_APPLE_HITS: Final[int] = 2

#: Minimum practical-signal hits required to pass the quality gate.
MIN_PRACTICAL_HITS: Final[int] = 3

LOGGER = get_logger("pipeline.article")

# ---------------------------------------------------------------------------
# Regex patterns — compiled once
# ---------------------------------------------------------------------------

# Splits a markdown document on fenced code blocks so we can skip them.
_CODE_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"(```[\s\S]*?```)",
    re.MULTILINE,
)

# Matches inline markdown links: [label](url)
_MD_LINK_RE: Final[re.Pattern[str]] = re.compile(
    r"\[([^\]]+)\]\((https?://[^\s)]+)\)"
)

# Matches bare URLs left after stripping markdown links.
_BARE_URL_RE: Final[re.Pattern[str]] = re.compile(r"https?://\S+")

# Matches runs of horizontal whitespace (used to tidy up after link removal).
_HSPACE_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]+")

# Reference-section heading variants the model commonly generates.
_REFERENCE_HEADINGS: Final[frozenset[str]] = frozenset(
    {"## references", "## sources", "## further reading"}
)

# Swift API names that should be wrapped in inline backticks when found in prose.
# Each group is kept separate so the pattern stays readable and maintainable.
_SWIFT_API_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!`)"  # negative lookbehind: skip already-wrapped names
    r"("
    # --- Property wrappers and attributes ---
    r"@(?:Observable|MainActor|Sendable|Published|StateObject|ObservedObject"
    r"|EnvironmentObject|Bindable|State\b|Binding\b|Environment\b"
    r"|discardableResult|objc\b)"
    # --- Swift Concurrency ---
    r"|withCheckedThrowingContinuation\b"
    r"|withCheckedContinuation\b"
    r"|withUnsafeThrowingContinuation\b"
    r"|withUnsafeContinuation\b"
    r"|withThrowingTaskGroup\b"
    r"|withTaskGroup\b"
    r"|AsyncStream\b|AsyncThrowingStream\b|AsyncSequence\b"
    r"|AsyncIteratorProtocol\b|CheckedContinuation\b|CancellationError\b"
    r"|Task\.isCancelled\b|Task\.cancel\(\)\b|Task\.detached\b|Task\.sleep\b"
    r"|TaskGroup\b|DiscardingTaskGroup\b"
    # --- URLSession ---
    r"|URLSession\.data\(for:\)|URLSession\.bytes\(from:\)"
    r"|URLSession\.shared\b|URLSessionTaskMetrics\b|URLProtocol\b"
    # --- SwiftUI / UIKit / Foundation ---
    r"|NavigationStack\b|LazyVStack\b|LazyHStack\b"
    r"|DispatchQueue\.main\b|DispatchQueue\.global\b"
    r")"
)

# ---------------------------------------------------------------------------
# Quality-gate signal tables
# ---------------------------------------------------------------------------

#: Tokens whose presence indicates Apple-platform specificity.
_APPLE_SIGNALS: Final[tuple[str, ...]] = (
    "swiftui",
    "uikit",
    "appkit",
    "xcode",
    "instruments",
    "swiftdata",
    "core data",
    "uiviewcontroller",
    "nsmanagedobjectcontext",
    "metrickit",
    "os_log",
    "signpost",
    "app intents",
    "widgetkit",
    "urlsession",
    "xctest",
)

#: Tokens whose presence indicates production-relevant, practical content.
_PRACTICAL_SIGNALS: Final[tuple[str, ...]] = (
    "tradeoff",
    "pitfall",
    "failure mode",
    "rollout",
    "migration",
    "backward compatibility",
    "observability",
    "instrumentation",
    "monitoring",
    "testing",
    "incident",
    "debug",
    "profil",
    "production",
    "checklist",
    "choose",
    " vs ",
)

#: Phrases that indicate explicit engineering decision language.
_DECISION_PHRASES: Final[tuple[str, ...]] = (
    " when to ",
    " vs ",
    "choose x when",
    "choose y when",
    "prefer x when",
)

# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def _load_prompt_template(path: Path = PROMPT_PATH) -> str:
    """Read and return the article prompt template.

    Raises
    ------
    FileNotFoundError
        When the prompt file does not exist — surfaces the misconfiguration
        immediately rather than producing a confusing downstream error.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Article prompt template not found at '{path}'. "
            "Check PROMPT_PATH or the working directory."
        )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Markdown normalisation pipeline
# ---------------------------------------------------------------------------


def _strip_top_level_title(markdown: str) -> str:
    """Remove a leading H1 title if the model added one despite instructions."""
    lines = markdown.splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join(lines[1:]).lstrip()
    return markdown


def _strip_reference_section(markdown: str) -> str:
    """Drop any model-generated references section.

    The pipeline appends verified sources separately; a model-generated
    section would contain unverified links.
    """
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() in _REFERENCE_HEADINGS:
            LOGGER.debug("reference_section_stripped line=%d heading=%r", index, line.strip())
            return "\n".join(lines[:index]).strip()
    return markdown.strip()


def _strip_unapproved_links(markdown: str) -> str:
    """Remove inline and bare URLs from prose to prevent fabricated references.

    Markdown links are converted to their label text so reading flow is
    preserved; bare URLs are removed entirely.
    """
    text = _MD_LINK_RE.sub(r"\1", markdown)
    text = _BARE_URL_RE.sub("", text)
    return _HSPACE_RE.sub(" ", text).strip()


def apply_swift_backticks(markdown: str) -> str:
    """Wrap known Swift API names in inline backticks, skipping fenced code blocks.

    Fenced blocks are left verbatim; only prose sections are transformed.
    This is safe to call multiple times — the negative lookbehind in
    ``_SWIFT_API_RE`` prevents double-wrapping.
    """
    parts = _CODE_FENCE_RE.split(markdown)
    result: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:          # odd indices are the captured fence blocks
            result.append(part)
        else:
            result.append(_SWIFT_API_RE.sub(r"`\1`", part))
    return "".join(result)


def _normalize_article(raw: str) -> str:
    """Apply the full normalisation pipeline to raw model output.

    Steps (order is significant):
      1. Strip surrounding whitespace.
      2. Remove leading H1 title.
      3. Remove model-generated references section.
      4. Strip unapproved URLs and links.
      5. Apply Swift API backtick formatting.
    """
    text = raw.strip()
    text = _strip_top_level_title(text)
    text = _strip_reference_section(text)
    text = _strip_unapproved_links(text)
    text = apply_swift_backticks(text)
    return text.strip()


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------


def _passes_quality_gate(markdown: str) -> bool:
    """Return True when the article meets minimum Apple-specificity and
    practical-depth thresholds.

    The gate is intentionally heuristic — it catches obviously generic
    outputs before they reach publication without over-constraining the
    model. Thresholds are module-level constants so they are easy to tune.
    """
    lowered = markdown.lower()
    apple_hits = sum(1 for token in _APPLE_SIGNALS if token in lowered)
    practical_hits = sum(1 for token in _PRACTICAL_SIGNALS if token in lowered)
    has_decision_language = any(phrase in f" {lowered} " for phrase in _DECISION_PHRASES)
    return apple_hits >= MIN_APPLE_HITS and practical_hits >= MIN_PRACTICAL_HITS and has_decision_language


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

_RETRY_SUFFIX: Final[str] = """\

Quality retry requirements:
- Rewrite from a senior Apple software engineering perspective.
- Replace generic advice with concrete decisions, constraints, and operational guidance.
- Name specific Apple frameworks and tools relevant to the topic.
- Include testing, observability, and rollout considerations.
- Keep structure and all other constraints unchanged.
"""


def _call_model(
    client: object,
    *,
    operation: str,
    prompt: str,
    temperature: float,
) -> str:
    """Invoke the model and return normalised article text.

    Centralises the call site so changes to API kwargs propagate from
    one place. Returns an empty string on an empty or whitespace-only
    response rather than raising — callers decide how to handle it.
    """
    response = responses_create_logged(
        client,
        agent_name="article_agent",
        operation=operation,
        model=OPENAI_MODEL,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        input=prompt,
        **openai_generation_kwargs(temperature),
    )
    return _normalize_article(response.output_text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_article(topic: str, outline: str, allowed_references: str) -> str:
    """Generate a professional Medium-style markdown body from topic + outline.

    Parameters
    ----------
    topic:
        Article topic string used to fill the prompt template.
    outline:
        Section outline produced by the outline agent.
    allowed_references:
        Verified source URLs/titles the article may reference. Passed to the
        prompt template; the model is instructed not to cite other sources.

    Returns
    -------
    str
        Normalised markdown article body, ready for the critic/editor pipeline.

    Raises
    ------
    FileNotFoundError
        When the prompt template file is missing.
    RuntimeError
        When generation returns empty output after the retry attempt.
    """
    client = create_openai_client()

    template = _load_prompt_template()
    base_prompt = (
        template
        .replace("{topic}", topic)
        .replace("{outline}", outline)
        .replace("{allowed_references}", allowed_references.strip() or "- None")
    )

    # --- First-pass generation ---
    article = _call_model(
        client,
        operation="generate_article",
        prompt=base_prompt,
        temperature=min(OPENAI_TEMPERATURE, GENERATION_TEMPERATURE),
    )

    log_event(
        LOGGER,
        "article_generated",
        level=logging.INFO,
        topic=topic,
        output_chars=len(article),
        passed_quality_gate=_passes_quality_gate(article),
    )

    # --- Quality retry ---
    if article and not _passes_quality_gate(article):
        log_event(
            LOGGER,
            "article_quality_retry",
            level=logging.WARNING,
            topic=topic,
            output_chars=len(article),
        )
        retry_prompt = base_prompt + _RETRY_SUFFIX
        retried = _call_model(
            client,
            operation="generate_article_quality_retry",
            prompt=retry_prompt,
            temperature=min(OPENAI_TEMPERATURE, RETRY_TEMPERATURE),
        )
        if retried:
            log_event(
                LOGGER,
                "article_quality_retry_accepted",
                level=logging.INFO,
                topic=topic,
                output_chars=len(retried),
                passed_quality_gate=_passes_quality_gate(retried),
            )
            article = retried
        else:
            log_event(
                LOGGER,
                "article_quality_retry_empty",
                level=logging.WARNING,
                topic=topic,
            )

    if not article:
        raise RuntimeError(
            f"Article generation returned empty output for topic={topic!r}. "
            "Check model API connectivity and prompt template."
        )

    return article