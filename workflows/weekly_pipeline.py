"""Weekly pipeline: orchestrate topic, outline, article, and code generation."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from agents.article_agent import generate_article
from agents.code_agent import generate_code_with_metadata
from utils.article_repair import repair_article as _repair_article

from agents.review_agent import review_article
from agents.editor_agent import (
    LayoutAssessment,
    assess_medium_layout,
    enforce_factual_grounding,
    polish_article,
    reinforce_medium_layout,
    repair_from_review,
)
from agents.linkedin_agent import generate_linkedin_post
from agents.newsletter_agent import generate_newsletter
from agents.outline_agent import generate_outline
from agents.topic_agent import generate_topic
import config as _config_module
from config import (
    EDITOR_PASS_ENABLED,
    FACT_GROUNDING_ENABLED,
    FACT_GROUNDING_MAX_PASSES,
    MEDIUM_LAYOUT_MAX_REPAIR_PASSES,
    MEDIUM_LAYOUT_MIN_SCORE,
    MEDIUM_LAYOUT_REINFORCEMENT_ENABLED,
    NEWSLETTER_ENABLED,
    OUTPUT_ARTICLES_DIR,
    OUTPUT_CODEGEN_DIR,
    OUTPUT_LINKEDIN_DIR,
    OUTPUT_NEWSLETTER_DIR,
    OUTPUT_QUALITY_HISTORY_PATH,
    LINKEDIN_POST_ENABLED,
    REVIEW_REPAIR_ENABLED,
    REVIEW_REPAIR_MIN_SCORE,
    SELF_REVIEW_ENABLED,
    SWIFT_COMPILER_LANGUAGE_MODE,
    SWIFT_LANGUAGE_VERSION,
    TOPIC_INTERESTS,
    TREND_DISCOVERY_ENABLED,
)
from scanners.trend_scanner import TrendSignal, discover_ios_trends, save_trend_snapshot
from utils.observability import get_logger, log_event, reset_run_context, set_run_context, timed_step

REFERENCE_TOKEN_STOPWORDS = {
    "about",
    "across",
    "and",
    "app",
    "apps",
    "architecture",
    "building",
    "for",
    "from",
    "guide",
    "how",
    "in",
    "ios",
    "mobile",
    "on",
    "platform",
    "smarter",
    "the",
    "to",
    "with",
}

IOS_ANCHOR_TERMS = {
    "apple",
    "app store",
    "ios",
    "ipad",
    "iphone",
    "swift",
    "swiftui",
    "uikit",
    "visionos",
    "watchos",
    "xcode",
}

HIGH_QUALITY_REFERENCE_DOMAINS = {
    "developer.apple.com",
    "swift.org",
    "forums.swift.org",
    "avanderlee.com",
    "github.com",
}

TRUSTED_REFERENCE_DOMAINS = {
    "developer.apple.com",
    "swift.org",
    "forums.swift.org",
    "avanderlee.com",
    "github.com",
}

LOW_SIGNAL_REFERENCE_DOMAINS = {
    "reddit.com",
    "news.ycombinator.com",
    "dev.to",
    "medium.com",
}

LOW_SIGNAL_TITLE_PATTERNS = [
    r"\bevery .* should know\b",
    r"\bthe impact of\b",
    r"\bwhere is the\b",
    r"\btop \d+\b",
    r"\bultimate guide\b",
]

LOGGER = get_logger("pipeline.workflow")

# Static Apple/Swift documentation seeds.
# Each entry: (topic_keywords, source_label, link_title, url)
# Keywords are matched case-insensitively as substrings of the article topic.
_APPLE_DOC_SEEDS: list[tuple[frozenset[str], str, str, str]] = [
    (frozenset({"concurr", "async", "await", "task", "actor", "continuation", "structured concurr"}),
     "Apple Documentation", "Swift Concurrency",
     "https://developer.apple.com/documentation/swift/concurrency"),
    (frozenset({"asyncsequence", "asyncstream", "asynciterator"}),
     "Apple Documentation", "AsyncSequence",
     "https://developer.apple.com/documentation/swift/asyncsequence"),
    (frozenset({"urlsession", "networking", "network", "http request", "completion handler"}),
     "Apple Documentation", "URLSession",
     "https://developer.apple.com/documentation/foundation/urlsession"),
    (frozenset({"swiftui", "swiftui view", "navigationstack", "lazyvstack"}),
     "Apple Documentation", "SwiftUI",
     "https://developer.apple.com/documentation/swiftui"),
    (frozenset({"observable", "observation", "@observable"}),
     "Apple Documentation", "Observation",
     "https://developer.apple.com/documentation/observation"),
    (frozenset({"app intent", "siri", "shortcuts", "appintent"}),
     "Apple Documentation", "App Intents",
     "https://developer.apple.com/documentation/appintents"),
    (frozenset({"delegate", "uikit", "uiviewcontroller", "uitableview", "uikit delegate"}),
     "Apple Documentation", "UIKit",
     "https://developer.apple.com/documentation/uikit"),
    (frozenset({"combine", "publisher", "subscriber", "passthrough"}),
     "Apple Documentation", "Combine",
     "https://developer.apple.com/documentation/combine"),
    (frozenset({"kvo", "key-value", "key value observ", "nsobject"}),
     "Apple Documentation", "Key-Value Observing",
     "https://developer.apple.com/documentation/swift/using-key-value-observing-in-swift"),
    (frozenset({"notificationcenter", "nsnotification", "notification observer"}),
     "Apple Documentation", "NotificationCenter",
     "https://developer.apple.com/documentation/foundation/notificationcenter"),
    (frozenset({"instruments", "metrickit", "ossignposter", "os_signpost", "signpost", "profil", "time profiler"}),
     "Apple Documentation", "Instruments Help",
     "https://developer.apple.com/documentation/xcode/gathering-information-for-debugging"),
    (frozenset({"macro", "swift macro", "@attached", "@freestanding"}),
     "Apple Documentation", "Swift Macros",
     "https://developer.apple.com/documentation/swift/macros"),
    (frozenset({"widgetkit", "widget", "home screen widget"}),
     "Apple Documentation", "WidgetKit",
     "https://developer.apple.com/documentation/widgetkit"),
    (frozenset({"swiftdata", "swift data", "persistenc", "core data"}),
     "Apple Documentation", "SwiftData",
     "https://developer.apple.com/documentation/swiftdata"),
]

_SWIFT_ORG_SEED = ("Swift.org", "Swift Documentation", "https://www.swift.org/documentation/")


def _seed_reference_items(topic: str) -> list[tuple[str, str, str]]:
    """Return (source, title, url) tuples for Apple/Swift docs relevant to the topic."""
    lowered = topic.lower()
    results: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()

    for keywords, source, title, url in _APPLE_DOC_SEEDS:
        if url not in seen_urls and any(kw in lowered for kw in keywords):
            results.append((source, title, url))
            seen_urls.add(url)

    # Always include the general Swift docs page as a stable foundation reference.
    swift_url = _SWIFT_ORG_SEED[2]
    if swift_url not in seen_urls:
        results.append(_SWIFT_ORG_SEED)

    return results

REFERENCE_EXCLUSION_PATTERNS = [
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
]

REFERENCE_ALLOWED_INTELLIGENCE_PATTERNS = [
    r"\bapple intelligence\b",
    r"\bapple intelligence api(s)?\b",
    r"\bfoundation models?\b",
    r"\bapp\sintents?\b",
]


def _has_allowed_intelligence_context(text: str) -> bool:
    """Allow explicit Apple Intelligence contexts while filtering generic AI noise."""
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in REFERENCE_ALLOWED_INTELLIGENCE_PATTERNS)


def _slugify(text: str) -> str:
    """Build a filesystem-safe slug from article title text."""
    cleaned = text.lower().strip()
    cleaned = re.sub(r"[^a-z0-9\s-]", "", cleaned)
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned.strip("-")[:90] or "ios-article"


def _load_recent_titles(max_items: int = 20) -> list[str]:
    """Load recently generated article titles to avoid repetitive topics.

    Reads from quality_history.json first (all-time history committed to the
    source repo), then falls back to local article markdown files so that
    deduplication works on CI where only the current run's article is present
    in outputs/articles/.
    """
    titles: list[str] = []

    # Primary source: quality_history.json (committed to source repo, full history)
    if OUTPUT_QUALITY_HISTORY_PATH.exists():
        try:
            history: list[dict] = json.loads(
                OUTPUT_QUALITY_HISTORY_PATH.read_text(encoding="utf-8")
            )
            if isinstance(history, list):
                for entry in reversed(history):  # most recent first
                    topic = entry.get("topic", "")
                    if topic and topic not in titles:
                        titles.append(topic)
                    if len(titles) >= max_items:
                        return titles
        except (json.JSONDecodeError, ValueError, OSError):
            pass  # fall through to markdown fallback

    # Fallback: local article markdown files (for manual/dev runs)
    if OUTPUT_ARTICLES_DIR.exists():
        markdown_files = sorted(OUTPUT_ARTICLES_DIR.glob("*.md"), reverse=True)
        for path in markdown_files:
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue

            for line in content.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    if title and title not in titles:
                        titles.append(title)
                    break

            if len(titles) >= max_items:
                break

    return titles


def _sanitize_body_urls(markdown: str) -> str:
    """Final safety pass to strip prose URLs while preserving code blocks."""
    # Split on fenced code blocks to protect their content from URL stripping.
    # Odd-indexed parts are code blocks; even-indexed parts are prose.
    parts = re.split(r"(```[\s\S]*?```)", markdown)
    processed: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Code block — preserve as-is.
            processed.append(part)
        else:
            # Prose section — strip markdown hyperlinks (keep text) and bare URLs.
            prose = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1", part)
            prose = re.sub(r"https?://[^\s)]+", "", prose)
            processed.append(prose)
    result = "".join(processed)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return re.sub(r"[ \t]+", " ", result).strip()


def _topic_terms(topic: str) -> set[str]:
    """Extract lightweight relevance terms from the selected topic."""
    normalized = re.sub(r"[^a-z0-9\s]", " ", topic.lower())
    raw_terms = [token for token in normalized.split() if token]
    terms = {
        token
        for token in raw_terms
        if (len(token) >= 4 or token == "ios") and token not in REFERENCE_TOKEN_STOPWORDS
    }
    return terms


def _is_reference_relevant(source: str, title: str, topic_terms: set[str]) -> bool:
    """Keep references aligned with article topic instead of broad trend noise."""
    if not topic_terms:
        return True

    text = f"{source} {title}".lower()
    if (
        any(re.search(pattern, text) for pattern in REFERENCE_EXCLUSION_PATTERNS)
        and not _has_allowed_intelligence_context(text)
    ):
        return False
    has_ios_anchor = any(anchor in text for anchor in IOS_ANCHOR_TERMS)

    matched_terms = {
        term
        for term in topic_terms
        if re.search(rf"\b{re.escape(term)}\b", text)
    }
    non_generic_matches = matched_terms - {"ios"}

    if has_ios_anchor and len(non_generic_matches) >= 1:
        return True
    if "ios" in matched_terms and len(non_generic_matches) >= 1:
        return True
    if len(non_generic_matches) >= 2:
        return True
    return False


def _domain_from_url(url: str) -> str:
    """Extract normalized domain for quality filtering."""
    netloc = urlparse(url).netloc.strip().lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _domain_in(domain: str, candidates: set[str]) -> bool:
    """Support exact and subdomain membership checks."""
    return any(domain == candidate or domain.endswith(f".{candidate}") for candidate in candidates)


def _is_trusted_reference_domain(url: str) -> bool:
    """Return True when URL belongs to a trusted technical source domain."""
    return _domain_in(_domain_from_url(url), TRUSTED_REFERENCE_DOMAINS)


def _reference_quality_score(source: str, title: str, url: str, topic_terms: set[str]) -> int:
    """Score references to prefer stronger sources and suppress listicle noise."""
    text = f"{source} {title}".lower()
    domain = _domain_from_url(url)
    matched_terms = {
        term
        for term in topic_terms
        if re.search(rf"\b{re.escape(term)}\b", text)
    }
    non_generic_matches = matched_terms - {"ios"}

    score = 0
    if _domain_in(domain, HIGH_QUALITY_REFERENCE_DOMAINS):
        score += 3
    if _domain_in(domain, LOW_SIGNAL_REFERENCE_DOMAINS):
        score -= 2
    if any(re.search(pattern, title.lower()) for pattern in LOW_SIGNAL_TITLE_PATTERNS):
        score -= 2
    if "ios" in text or any(anchor in text for anchor in IOS_ANCHOR_TERMS):
        score += 1
    score += min(2, len(non_generic_matches))
    return score


def _reference_items(
    trends: list[TrendSignal], topic: str, max_items: int = 8
) -> list[tuple[str, str, str]]:
    """Build validated reference list as (source, title, url)."""
    seen_urls: set[str] = set()
    topic_terms = _topic_terms(topic)
    trusted_candidates: list[tuple[int, float, tuple[str, str, str]]] = []
    blocked_url_substrings = {
        "news.google.com/rss/articles/",
    }
    blocked_title_terms = {
        "is hiring",
        "jobs",
        "careers",
        "feedback requested",
        " ai ",
        "agent",
        "agentic",
        "generative",
        "llm",
        "prompt",
        "inference",
        "automation",
        "machine learning",
        "core ml",
    }

    for trend in trends:
        title = trend.title.strip()
        url = trend.url.strip()
        source = trend.source.strip()
        lowered_title = title.lower()
        combined_text = f"{source} {title}".lower()

        if not title or not url or not url.startswith("http"):
            continue
        if len(title) > 140:
            continue
        if (
            any(re.search(pattern, combined_text) for pattern in REFERENCE_EXCLUSION_PATTERNS)
            and not _has_allowed_intelligence_context(combined_text)
        ):
            continue
        if not _is_reference_relevant(source, title, topic_terms):
            continue
        quality_score = _reference_quality_score(source, title, url, topic_terms)
        if any(term in lowered_title for term in blocked_title_terms) and not _has_allowed_intelligence_context(
            combined_text
        ):
            continue
        if any(fragment in url for fragment in blocked_url_substrings):
            continue
        if url in seen_urls:
            continue

        seen_urls.add(url)
        payload = (source, title, url)
        if quality_score >= 1 and _is_trusted_reference_domain(url):
            trusted_candidates.append((quality_score, trend.score, payload))

    # Trust-first publication policy: include only trusted technical source domains.
    trusted_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [payload for _, _, payload in trusted_candidates[:max_items]]


def _references_for_prompt(
    trends: list[TrendSignal], max_items: int = 8, topic: str | None = None
) -> str:
    """Create broader source list for prompt grounding (less strict than publish refs)."""
    def _collect(apply_topic_filter: bool) -> list[str]:
        seen_urls: set[str] = set()
        lines: list[str] = []
        topic_terms = _topic_terms(topic or "") if apply_topic_filter and topic else set()

        for trend in trends:
            title = trend.title.strip()
            url = trend.url.strip()
            source = trend.source.strip()

            if not title or not url or not url.startswith("http"):
                continue
            if apply_topic_filter and topic_terms and not _is_reference_relevant(source, title, topic_terms):
                continue
            if apply_topic_filter and topic_terms:
                quality_score = _reference_quality_score(source, title, url, topic_terms)
                if quality_score < 0:
                    continue
            if url in seen_urls:
                continue

            seen_urls.add(url)
            lines.append(f"- [{source}] {title} | {url}")
            if len(lines) >= max_items:
                break
        return lines

    lines = _collect(apply_topic_filter=bool(topic))
    if not lines and topic:
        # Fallback so generation never has empty context when strict matching yields no hits.
        lines = _collect(apply_topic_filter=False)

    # Always append static Apple/Swift doc seeds so agents have stable grounding anchors.
    if topic:
        seen_in_lines = {line.split("| ")[-1].strip() for line in lines if "| http" in line}
        for source, title_str, url in _seed_reference_items(topic):
            if url not in seen_in_lines:
                lines.append(f"- [{source}] {title_str} | {url}")
                seen_in_lines.add(url)

    if not lines:
        return "- None"
    return "\n".join(lines[:max_items])


def _compose_markdown(
    title: str,
    article: str,
    code: str,
    trends: list[TrendSignal],
) -> str:
    """Compose final Medium-ready markdown output."""
    trend_refs = _reference_items(trends, topic=title, max_items=8)
    seed_refs = _seed_reference_items(title)
    # Merge: trend-sourced refs first, then seeds not already present
    seen_urls = {url for _, _, url in trend_refs}
    merged_refs = trend_refs + [(s, t, u) for s, t, u in seed_refs if u not in seen_urls]
    references = merged_refs[:10]
    references_block = (
        "\n".join(f"- [{ref_title}]({ref_url})" for _, ref_title, ref_url in references)
        if references
        else "- No verified external references were available this run."
    )
    code = code.strip()
    code_section = (
        "\n".join(
            [
                "## Swift/SwiftUI Code Example",
                "",
                "```swift",
                code,
                "```",
                "",
            ]
        )
        if code
        else "\n".join(
            [
                "## Swift/SwiftUI Code Example",
                "",
                "_A code example for this topic is not included in this edition._",
                "",
            ]
        )
    )

    parts: list[str] = []
    parts.extend(
        [
            f"# {title}",
            "",
            article.strip(),
            "",
            code_section,
            "## References",
            "",
            references_block,
            "",
        ]
    )

    return "\n".join(parts)


def _save_markdown(title: str, markdown: str) -> Path:
    """Persist markdown to outputs/articles/{date}-{slug}.md."""
    OUTPUT_ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slugify(title)
    output_path = OUTPUT_ARTICLES_DIR / f"{date_prefix}-{slug}.md"
    output_path.write_text(markdown, encoding="utf-8")

    return output_path


def _save_linkedin_post(title: str, post_text: str) -> Path:
    """Persist LinkedIn post to outputs/linkedin/{date}-{slug}-linkedin.md."""
    OUTPUT_LINKEDIN_DIR.mkdir(parents=True, exist_ok=True)

    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slugify(title)
    output_path = OUTPUT_LINKEDIN_DIR / f"{date_prefix}-{slug}-linkedin.md"
    output_path.write_text(post_text.strip() + "\n", encoding="utf-8")

    return output_path


def _save_newsletter(title: str, markdown: str, html_content: str, issue_number: int) -> tuple[Path, Path]:
    """Persist newsletter to outputs/newsletter/{date}-issue-N.{md,html}."""
    OUTPUT_NEWSLETTER_DIR.mkdir(parents=True, exist_ok=True)

    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stem = f"{date_prefix}-issue-{issue_number}"
    md_path = OUTPUT_NEWSLETTER_DIR / f"{stem}.md"
    html_path = OUTPUT_NEWSLETTER_DIR / f"{stem}.html"
    md_path.write_text(markdown.strip() + "\n", encoding="utf-8")
    html_path.write_text(html_content, encoding="utf-8")
    return md_path, html_path


def _save_codegen_metadata(title: str, metadata: dict[str, str | int]) -> Path:
    """Persist code generation metadata for observability."""
    OUTPUT_CODEGEN_DIR.mkdir(parents=True, exist_ok=True)

    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slugify(title)
    output_path = OUTPUT_CODEGEN_DIR / f"{date_prefix}-{slug}-codegen.json"
    output_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return output_path


def _append_quality_history(record: dict) -> None:
    """Append a quality record to the running quality_history.json file."""
    path = OUTPUT_QUALITY_HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, ValueError):
            existing = []
    existing.append(record)
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_weekly_pipeline() -> Path:
    """Execute the end-to-end weekly content generation workflow."""
    run_id = f"weekly-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    context_tokens = set_run_context(run_id=run_id, workflow="weekly_pipeline")
    log_event(LOGGER, "pipeline_run_started", trend_discovery_enabled=TREND_DISCOVERY_ENABLED)

    try:
        trends: list[TrendSignal] = []
        recent_titles = _load_recent_titles(max_items=24)

        if TREND_DISCOVERY_ENABLED:
            with timed_step(LOGGER, "discover_trends") as step:
                trends = discover_ios_trends()
                save_trend_snapshot(trends)
                step["trend_count"] = len(trends)
        else:
            log_event(LOGGER, "trend_discovery_skipped", reason="TREND_DISCOVERY_ENABLED=false")

        with timed_step(LOGGER, "build_topic_context") as step:
            trend_context = _references_for_prompt(trends, max_items=14)
            step["reference_count"] = trend_context.count("\n") + (0 if trend_context == "- None" else 1)

        with timed_step(LOGGER, "generate_topic") as step:
            topic = generate_topic(
                trend_context=trend_context,
                recent_titles=recent_titles,
                topic_interests=TOPIC_INTERESTS,
            )
            step["topic"] = topic

        with timed_step(LOGGER, "generate_outline", topic=topic):
            outline = generate_outline(topic)

        with timed_step(LOGGER, "build_reference_context", topic=topic) as step:
            reference_context = _references_for_prompt(trends, max_items=10, topic=topic)
            step["reference_count"] = reference_context.count("\n") + (
                0 if reference_context == "- None" else 1
            )

        with timed_step(LOGGER, "generate_article", topic=topic):
            article = generate_article(
                topic=topic,
                outline=outline,
                allowed_references=reference_context,
            )

        with timed_step(
            LOGGER,
            "editor_pass",
            topic=topic,
            enabled=EDITOR_PASS_ENABLED,
        ):
            polished_article = (
                polish_article(topic=topic, article=article, allowed_references=reference_context)
                if EDITOR_PASS_ENABLED
                else article
            )

        with timed_step(
            LOGGER,
            "factual_grounding",
            topic=topic,
            enabled=FACT_GROUNDING_ENABLED,
            max_passes=FACT_GROUNDING_MAX_PASSES,
        ):
            if FACT_GROUNDING_ENABLED:
                polished_article = enforce_factual_grounding(
                    topic=topic,
                    article=polished_article,
                    allowed_references=reference_context,
                    max_passes=FACT_GROUNDING_MAX_PASSES,
                )

        layout_assessment: LayoutAssessment
        with timed_step(
            LOGGER,
            "medium_layout_reinforcement",
            topic=topic,
            enabled=MEDIUM_LAYOUT_REINFORCEMENT_ENABLED,
            max_passes=MEDIUM_LAYOUT_MAX_REPAIR_PASSES,
            min_score=MEDIUM_LAYOUT_MIN_SCORE,
        ):
            if MEDIUM_LAYOUT_REINFORCEMENT_ENABLED:
                polished_article, layout_assessment = reinforce_medium_layout(
                    topic=topic,
                    article=polished_article,
                    allowed_references=reference_context,
                    max_passes=MEDIUM_LAYOUT_MAX_REPAIR_PASSES,
                    min_score=MEDIUM_LAYOUT_MIN_SCORE,
                )
            else:
                layout_assessment = assess_medium_layout(
                    polished_article, min_score=MEDIUM_LAYOUT_MIN_SCORE
                )

        with timed_step(LOGGER, "deterministic_repair", topic=topic) as step:
            polished_article, repair_report = _repair_article(polished_article)
            step["backtick_fixes"] = len(repair_report["backtick_fixes"])
            step["operational_note_fixes"] = repair_report.get("operational_note_fixes", 0)
            step["version_warnings"] = len(repair_report["version_warnings"])
            if repair_report["backtick_fixes"]:
                log_event(
                    LOGGER,
                    "backtick_fixes_applied",
                    level=logging.INFO,
                    fixes=repair_report["backtick_fixes"],
                )
            if repair_report.get("operational_note_fixes", 0):
                log_event(
                    LOGGER,
                    "operational_note_labels_removed",
                    level=logging.INFO,
                    count=repair_report["operational_note_fixes"],
                )
            if repair_report["version_warnings"]:
                log_event(
                    LOGGER,
                    "version_callouts_missing",
                    level=logging.WARNING,
                    warnings=repair_report["version_warnings"],
                )

        with timed_step(LOGGER, "sanitize_article", topic=topic):
            polished_article = _sanitize_body_urls(polished_article)

        with timed_step(LOGGER, "generate_code", topic=topic) as step:
            code_result = generate_code_with_metadata(topic, article_body=polished_article)
            code = code_result.code
            step["code_path"] = code_result.path
            step["repair_attempts"] = code_result.repair_attempts

        with timed_step(LOGGER, "save_codegen_metadata", topic=topic):
            _save_codegen_metadata(
                topic,
                {
                    "topic": topic,
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "path": code_result.path,
                    "repair_attempts": code_result.repair_attempts,
                    "swift_language_version": SWIFT_LANGUAGE_VERSION,
                    "swift_language_mode": SWIFT_COMPILER_LANGUAGE_MODE,
                    "diagnostics_excerpt": code_result.diagnostics,
                },
            )

        review_result: dict = {}
        with timed_step(LOGGER, "self_review_article", topic=topic, enabled=SELF_REVIEW_ENABLED):
            if SELF_REVIEW_ENABLED:
                review_result = review_article(topic=topic, article=polished_article)
                log_event(
                    LOGGER,
                    "article_self_review_completed",
                    review_overall=review_result.get("overall_quality"),
                    review_technical_depth=review_result.get("technical_depth"),
                    review_actionability=review_result.get("actionability"),
                    review_issues=review_result.get("issues", []),
                )

        review_repair_triggered = False
        with timed_step(
            LOGGER,
            "review_repair",
            topic=topic,
            enabled=REVIEW_REPAIR_ENABLED,
        ):
            if REVIEW_REPAIR_ENABLED and SELF_REVIEW_ENABLED and review_result:
                review_issues = review_result.get("issues", [])
                review_min = min(
                    review_result.get("overall_quality", 10),
                    review_result.get("technical_depth", 10),
                    review_result.get("actionability", 10),
                )
                if review_issues and review_min < REVIEW_REPAIR_MIN_SCORE:
                    review_repair_triggered = True
                    polished_article = repair_from_review(
                        topic=topic,
                        article=polished_article,
                        allowed_references=reference_context,
                        review_issues=review_issues,
                    )
                    log_event(
                        LOGGER,
                        "review_repair_triggered",
                        level=logging.INFO,
                        topic=topic,
                        review_min_score=review_min,
                        issue_count=len(review_issues),
                    )

        with timed_step(LOGGER, "append_quality_history", topic=topic):
            slug = _slugify(topic)
            has_refs = bool(_reference_items(trends, topic=topic, max_items=1)) or bool(_seed_reference_items(topic))
            quality_record = {
                "date": datetime.now(timezone.utc).date().isoformat(),
                "slug": slug,
                "topic": topic,
                "layout_score": layout_assessment.score,
                "layout_max_score": layout_assessment.max_score,
                "layout_issues": list(layout_assessment.issues),
                "code_path": code_result.path,
                "code_repair_attempts": code_result.repair_attempts,
                "has_references": has_refs,
                "review_overall": review_result.get("overall_quality"),
                "review_technical_depth": review_result.get("technical_depth"),
                "review_actionability": review_result.get("actionability"),
                "review_issues": review_result.get("issues", []),
                "review_strengths": review_result.get("strengths", []),
                "review_repair_triggered": review_repair_triggered,
            }
            _append_quality_history(quality_record)
            log_event(
                LOGGER,
                "article_quality_recorded",
                **{k: v for k, v in quality_record.items()
                   if k not in ("layout_issues", "review_issues", "review_strengths")},
            )

        with timed_step(LOGGER, "compose_markdown", topic=topic):
            markdown = _compose_markdown(topic, polished_article, code, trends)

        with timed_step(LOGGER, "save_article", topic=topic) as step:
            article_path = _save_markdown(topic, markdown)
            step["output_path"] = str(article_path)

        linkedin_post = ""
        if LINKEDIN_POST_ENABLED:
            with timed_step(LOGGER, "generate_linkedin_post", topic=topic):
                linkedin_post = generate_linkedin_post(
                    topic=topic,
                    article_body=polished_article,
                    code_example=code,
                    allowed_references=reference_context,
                    factual_passes=FACT_GROUNDING_MAX_PASSES if FACT_GROUNDING_ENABLED else 0,
                )

            with timed_step(LOGGER, "save_linkedin_post", topic=topic):
                _save_linkedin_post(topic, linkedin_post)
        else:
            log_event(LOGGER, "linkedin_post_skipped", topic=topic, reason="LINKEDIN_POST_ENABLED=false")

        if NEWSLETTER_ENABLED:
            with timed_step(LOGGER, "generate_newsletter", topic=topic) as step:
                newsletter_result = generate_newsletter(
                    article={"title": topic, "body": polished_article},
                    trends=trends,
                    codegen={"code": code, "path": code_result.path},
                    linkedin_post=linkedin_post,
                    config=_config_module,
                )
                step["issue_number"] = newsletter_result["issue_number"]

            with timed_step(LOGGER, "save_newsletter", topic=topic) as step:
                nl_md_path, nl_html_path = _save_newsletter(
                    topic,
                    newsletter_result["markdown"],
                    newsletter_result["html"],
                    newsletter_result["issue_number"],
                )
                step["md_path"] = str(nl_md_path)
                step["html_path"] = str(nl_html_path)
        else:
            log_event(LOGGER, "newsletter_skipped", topic=topic, reason="NEWSLETTER_ENABLED=false")

        log_event(
            LOGGER,
            "pipeline_run_completed",
            topic=topic,
            article_path=str(article_path),
            trend_count=len(trends),
            code_path=code_result.path,
            code_repair_attempts=code_result.repair_attempts,
        )
        return article_path
    except Exception as exc:
        log_event(
            LOGGER,
            "pipeline_run_failed",
            level=logging.ERROR,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise
    finally:
        reset_run_context(context_tokens)
