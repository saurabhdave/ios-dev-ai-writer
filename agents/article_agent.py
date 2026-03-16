"""Article agent: produce a full Medium-style article body."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE, openai_generation_kwargs
from utils.observability import get_logger, log_event
from utils.openai_logging import create_openai_client, responses_create_logged

PROMPT_PATH = Path("prompts/article_prompt.txt")
LOGGER = get_logger("pipeline.article")

# Regex that matches common Swift API names NOT already wrapped in backticks.
# Splitting on fenced code blocks first keeps the replacements in prose only.
_SWIFT_API_RE = re.compile(
    r"(?<!`)"
    r"("
    # @ attributes
    r"@(?:Observable|MainActor|Sendable|Published|StateObject|ObservedObject"
    r"|EnvironmentObject|Bindable|State\b|Binding\b|Environment\b"
    r"|discardableResult|objc\b)"
    # Swift Concurrency
    r"|withCheckedThrowingContinuation\b"
    r"|withCheckedContinuation\b"
    r"|withUnsafeThrowingContinuation\b"
    r"|withUnsafeContinuation\b"
    r"|withThrowingTaskGroup\b"
    r"|withTaskGroup\b"
    r"|AsyncStream\b|AsyncThrowingStream\b|AsyncSequence\b"
    r"|AsyncIteratorProtocol\b|CheckedContinuation\b|CancellationError\b"
    r"|Task\.isCancelled\b|Task\.cancel\b|Task\.detached\b|Task\.sleep\b"
    r"|TaskGroup\b|DiscardingTaskGroup\b"
    # URLSession
    r"|URLSession\.data\(for:\)|URLSession\.bytes\(from:\)"
    r"|URLSession\.shared\b|URLSessionTaskMetrics\b|URLProtocol\b"
    # Common SwiftUI / Foundation
    r"|NavigationStack\b|LazyVStack\b|LazyHStack\b"
    r"|DispatchQueue\.main\b|DispatchQueue\.global\b"
    r")"
)
_CODE_FENCE_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)


def apply_swift_backticks(markdown: str) -> str:
    """Wrap known Swift API names in inline backticks, skipping fenced code blocks."""
    parts = _CODE_FENCE_RE.split(markdown)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # inside a fenced code block — leave untouched
            result.append(part)
        else:
            result.append(_SWIFT_API_RE.sub(r"`\1`", part))
    return "".join(result)
APPLE_SPECIFIC_SIGNALS = (
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
    "xc test",
    "xctest",
)
PRACTICAL_SIGNALS = (
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
    "vs",
)


def _load_prompt_template() -> str:
    """Load the article prompt template."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def _remove_unapproved_links(markdown: str) -> str:
    """Strip inline links/URLs from the body to prevent fabricated references."""
    # Convert markdown links to plain text: [text](url) -> text
    without_md_links = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1", markdown)
    # Remove remaining raw URLs.
    without_urls = re.sub(r"https?://[^\s)]+", "", without_md_links)
    # Keep whitespace readable after replacements.
    return re.sub(r"[ \t]+", " ", without_urls).strip()


def _remove_reference_sections(markdown: str) -> str:
    """Drop model-generated references section; pipeline appends verified sources."""
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        normalized = line.strip().lower()
        if normalized in {"## references", "## sources", "## further reading"}:
            return "\n".join(lines[:index]).strip()
    return markdown.strip()


def _normalize_article(markdown: str) -> str:
    """Normalize model output to a safe article body shape."""
    article = markdown.strip()
    if article.startswith("# "):
        article = "\n".join(article.splitlines()[1:]).strip()
    article = _remove_reference_sections(article)
    article = _remove_unapproved_links(article)
    article = apply_swift_backticks(article)
    return article


def _looks_practical_and_apple_specific(markdown: str) -> bool:
    """Heuristic gate to reduce generic outputs."""
    lowered = markdown.lower()
    apple_hits = sum(1 for token in APPLE_SPECIFIC_SIGNALS if token in lowered)
    practical_hits = sum(1 for token in PRACTICAL_SIGNALS if token in lowered)
    has_decision_language = (" when to " in f" {lowered} ") or (" vs " in f" {lowered} ")
    return apple_hits >= 2 and practical_hits >= 3 and has_decision_language


def generate_article(topic: str, outline: str, allowed_references: str) -> str:
    """Generate a professional Medium-style markdown body from topic + outline."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = create_openai_client()
    prompt = _load_prompt_template().format(
        topic=topic,
        outline=outline,
        allowed_references=allowed_references.strip() or "- None",
    )

    # Generate a long-form post with practical details and production advice.
    response = responses_create_logged(
        client,
        agent_name="article_agent",
        operation="generate_article",
        model=OPENAI_MODEL,
        max_output_tokens=2600,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, 0.45)),
    )

    article = _normalize_article(response.output_text)
    if article and not _looks_practical_and_apple_specific(article):
        log_event(
            LOGGER,
            "article_quality_retry_requested",
            level=logging.WARNING,
            topic=topic,
            output_chars=len(article),
        )
        retry_prompt = (
            f"{prompt}\n\n"
            "Quality retry requirements:\n"
            "- Rewrite from a senior Apple software engineering perspective.\n"
            "- Replace generic advice with concrete decisions, constraints, and operational guidance.\n"
            "- Mention specific Apple frameworks/tools relevant to the topic.\n"
            "- Include testing + observability + rollout considerations.\n"
            "- Keep structure and constraints unchanged.\n"
        )
        retry_response = responses_create_logged(
            client,
            agent_name="article_agent",
            operation="generate_article_quality_retry",
            model=OPENAI_MODEL,
            max_output_tokens=2600,
            input=retry_prompt,
            **openai_generation_kwargs(min(OPENAI_TEMPERATURE, 0.35)),
        )
        retried = _normalize_article(retry_response.output_text)
        if retried:
            article = retried
    if not article:
        raise RuntimeError("Article generation returned empty output.")

    return article
