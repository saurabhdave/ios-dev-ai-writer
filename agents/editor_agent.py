"""Editor agent: quality pass for professionalism and Medium-style structure.

Design principles
-----------------
- All prompt paths, thresholds, and token limits are typed ``Final`` constants.
- Prompt loading is centralised in ``_load_template`` — one function, one
  error surface, one log call on failure.
- ``_render_model_response`` is the single call site for every LLM invocation
  in this module; operation name and temperature are explicit parameters.
- Layout assessment is a pure, side-effect-free scoring function — no I/O,
  no logging, fully unit-testable.
- Each public function has a clear contract: input types, return types, and
  the one exception it may raise (``RuntimeError`` on empty output).
- ``OPENAI_API_KEY`` checks are removed — key validation belongs in
  ``create_openai_client()``, not duplicated in every agent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from config import OPENAI_MODEL, OPENAI_TEMPERATURE, openai_generation_kwargs
from utils.observability import get_logger, log_event
from utils.openai_logging import create_openai_client, responses_create_logged
from agents.article_agent import apply_swift_backticks

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROMPT_PATH: Final[Path] = Path("prompts/editor_prompt.txt")
LAYOUT_REPAIR_PROMPT_PATH: Final[Path] = Path("prompts/layout_repair_prompt.txt")
FACTUALITY_PROMPT_PATH: Final[Path] = Path("prompts/article_factuality_prompt.txt")

MAX_OUTPUT_TOKENS: Final[int] = 2_600

# Temperature caps per operation — lower = more deterministic/conservative.
POLISH_TEMPERATURE: Final[float] = 0.50
LAYOUT_TEMPERATURE: Final[float] = 0.45
FACTUALITY_TEMPERATURE: Final[float] = 0.30

# Layout rubric thresholds.
LAYOUT_MAX_SCORE: Final[int] = 14
LAYOUT_DEFAULT_MIN_SCORE: Final[int] = 7

# Acceptable h2 section count range for a well-structured Medium article.
H2_MIN: Final[int] = 6
H2_MAX: Final[int] = 10

# Minimum h3 subheadings for adequate section depth.
H3_MIN: Final[int] = 4

# Acceptable intro sentence range.
INTRO_SENTENCE_MIN: Final[int] = 1
INTRO_SENTENCE_MAX: Final[int] = 3
INTRO_CHAR_MIN: Final[int] = 60

# Paragraph readability ceiling (average sentences per paragraph).
PARAGRAPH_SENTENCE_CEILING: Final[float] = 4.2

# Minimum numbered core ## sections.
NUMBERED_H2_MIN: Final[int] = 3

# Maximum heading word count before flagging as too long.
HEADING_MAX_WORDS: Final[int] = 12
HEADING_COMPACT_RATIO: Final[float] = 0.80

# Tail of article body examined for plain-text closing-section detection.
CLOSING_TAIL_CHARS: Final[int] = 600

# Section keyword groups — checked against lowercased h2 headings.
_CONTEXT_KEYWORDS: Final[tuple[str, ...]] = ("why this matters", "context", "foundation", "overview")
_RISK_KEYWORDS: Final[tuple[str, ...]] = ("tradeoff", "pitfall", "risk", "limit")
_CHECKLIST_KEYWORDS: Final[tuple[str, ...]] = ("checklist", "playbook", "implementation steps")
_CLOSING_KEYWORDS: Final[tuple[str, ...]] = ("takeaway", "conclusion", "final thoughts", "wrap-up")

LOGGER = get_logger("pipeline.editor")

# ---------------------------------------------------------------------------
# Regex patterns — compiled once
# ---------------------------------------------------------------------------

_H2_RE: Final[re.Pattern[str]] = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_H3_RE: Final[re.Pattern[str]] = re.compile(r"^###\s+(.+)$", re.MULTILINE)
_DEEP_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^####+\s+", re.MULTILINE)
_FIRST_H2_RE: Final[re.Pattern[str]] = re.compile(r"^##\s+", re.MULTILINE)
_BLOCKQUOTE_RE: Final[re.Pattern[str]] = re.compile(r"^>\s+\S+", re.MULTILINE)
_LIST_ITEM_RE: Final[re.Pattern[str]] = re.compile(r"^(?:- |\* |\d+\.\s+)", re.MULTILINE)
_ORDERED_LIST_RE: Final[re.Pattern[str]] = re.compile(r"^\d+\.\s+")
_SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[.!?]+(?:\s|$)")
_NUMBERED_H2_RE: Final[re.Pattern[str]] = re.compile(r"^\d+[\).\:-]?\s+")
_CLOSING_PLAIN_TEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\n)(?:closing\s+takeaway|conclusion|final\s+thoughts|wrap-up)\b",
    re.IGNORECASE,
)
_TITLE_H1_RE: Final[re.Pattern[str]] = re.compile(r"^#\s+.+\n?")

# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def _load_template(path: Path) -> str:
    """Read and return a prompt template file.

    Raises
    ------
    FileNotFoundError
        When the template file does not exist, surfaces misconfiguration
        early with a message that names the path.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt template not found at '{path}'. "
            "Verify the path constant or the process working directory."
        )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# LLM call wrapper
# ---------------------------------------------------------------------------


def _render_model_response(
    client: object,
    prompt: str,
    *,
    operation: str,
    temperature: float,
) -> str:
    """Invoke the model and return normalised markdown text.

    Post-processing:
    - Strips leading/trailing whitespace.
    - Removes a spurious top-level H1 title if present.
    - Applies Swift API backtick formatting to prose.

    Returns an empty string when the model returns empty output so callers
    can decide how to handle it.
    """
    response = responses_create_logged(
        client,
        agent_name="editor_agent",
        operation=operation,
        model=OPENAI_MODEL,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        input=prompt,
        **openai_generation_kwargs(temperature),
    )
    text = response.output_text.strip()
    # Remove a leading H1 title the model sometimes adds despite instructions.
    text = _TITLE_H1_RE.sub("", text, count=1).strip()
    return apply_swift_backticks(text)


# ---------------------------------------------------------------------------
# Layout assessment — pure, side-effect-free
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayoutAssessment:
    """Heuristic quality score for Medium article layout fidelity."""

    score: int
    max_score: int
    min_score: int
    issues: tuple[str, ...]
    required_issues: tuple[str, ...]   # Issues that must be fixed regardless of score.

    @property
    def needs_repair(self) -> bool:
        """True when the article should enter the layout repair pass."""
        return self.score < self.min_score or bool(self.required_issues)


# --- Assessment helpers (pure functions) ------------------------------------


def _first_intro_block(markdown: str) -> str:
    """Return the first prose block before the first ## heading."""
    match = _FIRST_H2_RE.search(markdown)
    if not match:
        return ""
    intro = markdown[: match.start()].strip()
    return intro.split("\n\n")[0].strip() if intro else ""


def _split_prose_paragraphs(markdown: str) -> list[str]:
    """Extract prose paragraphs, skipping headings and list-only blocks."""
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", markdown.strip()):
        stripped = block.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        # Skip blocks that are entirely list items.
        if lines and all(
            line.startswith("- ")
            or line.startswith("* ")
            or bool(_ORDERED_LIST_RE.match(line))
            for line in lines
        ):
            continue
        paragraphs.append(" ".join(lines))
    return paragraphs


def _sentence_count(text: str) -> int:
    """Count sentence-like spans heuristically."""
    return sum(1 for part in _SENTENCE_SPLIT_RE.split(text.strip()) if part.strip())


def _any_heading_contains(headings: list[str], keywords: tuple[str, ...]) -> bool:
    """Return True when any heading contains at least one keyword."""
    return any(kw in heading.lower() for heading in headings for kw in keywords)


# --- Main scoring function --------------------------------------------------


def assess_medium_layout(
    article: str,
    min_score: int = LAYOUT_DEFAULT_MIN_SCORE,
) -> LayoutAssessment:
    """Score a markdown article body against the Medium layout rubric.

    Returns a ``LayoutAssessment`` with a score out of ``LAYOUT_MAX_SCORE``,
    a list of actionable issues, and a subset of required issues that must
    be fixed regardless of the overall score.
    """
    body = article.strip()
    h2_headings = _H2_RE.findall(body)
    h3_headings = _H3_RE.findall(body)
    paragraphs = _split_prose_paragraphs(body)
    intro = _first_intro_block(body)

    score = 0
    issues: list[str] = []
    required_issues: list[str] = []

    def _fail(message: str, *, required: bool = False) -> None:
        issues.append(message)
        if required:
            required_issues.append(message)

    # 1. H2 section count (2 pts)
    if H2_MIN <= len(h2_headings) <= H2_MAX:
        score += 2
    else:
        _fail(f"Use {H2_MIN}–{H2_MAX} `##` sections for scannable Medium structure.")

    # 2. H3 subheadings (1 pt)
    if len(h3_headings) >= H3_MIN:
        score += 1
    else:
        _fail("Add `###` subheadings in each core section for faster scanning.", required=True)

    # 3. Hook intro (1 pt)
    intro_sentences = _sentence_count(intro)
    if intro and INTRO_SENTENCE_MIN <= intro_sentences <= INTRO_SENTENCE_MAX and len(intro) >= INTRO_CHAR_MIN:
        score += 1
    else:
        _fail("Add a short hook intro (1–2 sentences) before the first `##` heading.", required=True)

    # 4. Context / why-this-matters section (1 pt)
    if _any_heading_contains(h2_headings, _CONTEXT_KEYWORDS):
        score += 1
    else:
        _fail("Include an early context section explaining why this topic matters for iOS teams.")

    # 5. Tradeoffs / pitfalls section (1 pt)
    if _any_heading_contains(h2_headings, _RISK_KEYWORDS):
        score += 1
    else:
        _fail("Add a dedicated tradeoffs/pitfalls section.")

    # 6. Checklist section (1 pt)
    if _any_heading_contains(h2_headings, _CHECKLIST_KEYWORDS):
        score += 1
    else:
        _fail("Include an implementation checklist section with concrete action items.")

    # 7. Closing takeaway section (1 pt)
    if _any_heading_contains(h2_headings, _CLOSING_KEYWORDS):
        score += 1
    elif _CLOSING_PLAIN_TEXT_RE.search(body[-CLOSING_TAIL_CHARS:]):
        score += 1
        _fail("Closing takeaway exists as plain text — promote it to a `##` heading.")
    else:
        _fail("End with a concise closing takeaway section.")

    # 8. Markdown list (1 pt)
    if _LIST_ITEM_RE.search(body):
        score += 1
    else:
        _fail("Add at least one markdown list (`-` or `1.`) for scannability.")

    # 9. Blockquote pull-insight (1 pt)
    if _BLOCKQUOTE_RE.search(body):
        score += 1
    else:
        _fail("Add one pull-quote insight using a markdown blockquote (`> …`).", required=True)

    # 10. Paragraph readability (1 pt)
    sentence_counts = [_sentence_count(p) for p in paragraphs if p]
    avg = sum(sentence_counts) / len(sentence_counts) if sentence_counts else 0.0
    if sentence_counts and avg <= PARAGRAPH_SENTENCE_CEILING:
        score += 1
    else:
        _fail("Shorten paragraphs to roughly 2–4 sentences each.")

    # 11. Numbered core ## sections (1 pt)
    numbered = [h for h in h2_headings if _NUMBERED_H2_RE.match(h.strip())]
    if len(numbered) >= NUMBERED_H2_MIN:
        score += 1
    else:
        _fail("Use numbered core sections (e.g., `## 1. …`, `## 2. …`).", required=True)

    # 12. Compact headings (1 pt)
    if h2_headings:
        compact = [h for h in h2_headings if len(h.split()) <= HEADING_MAX_WORDS]
        if len(compact) / len(h2_headings) >= HEADING_COMPACT_RATIO:
            score += 1
        else:
            _fail(f"Keep most section headings under {HEADING_MAX_WORDS} words.")
    else:
        _fail("Add clear `##` section headings.")

    # 13. No deep headings (#### or deeper) (1 pt)
    if not _DEEP_HEADING_RE.search(body):
        score += 1
    else:
        _fail("Use only `##` and `###` headings — avoid `####` and deeper levels.")

    return LayoutAssessment(
        score=score,
        max_score=LAYOUT_MAX_SCORE,
        min_score=max(1, min_score),
        issues=tuple(issues),
        required_issues=tuple(required_issues),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def polish_article(topic: str, article: str, allowed_references: str) -> str:
    """Refine article for clarity, professionalism, and Medium readability.

    Raises
    ------
    RuntimeError
        When the editor pass returns empty output.
    """
    client = create_openai_client()
    prompt = _load_template(PROMPT_PATH).format(
        topic=topic,
        article=article,
        allowed_references=allowed_references.strip() or "- None",
    )
    polished = _render_model_response(
        client,
        prompt,
        operation="polish_article",
        temperature=min(OPENAI_TEMPERATURE, POLISH_TEMPERATURE),
    )
    if not polished:
        raise RuntimeError(
            f"Editor polish pass returned empty output for topic={topic!r}."
        )
    log_event(
        LOGGER,
        "article_polished",
        level=logging.INFO,
        topic=topic,
        output_chars=len(polished),
    )
    return polished


def reinforce_medium_layout(
    topic: str,
    article: str,
    allowed_references: str,
    max_passes: int = 2,
    min_score: int = LAYOUT_DEFAULT_MIN_SCORE,
) -> tuple[str, LayoutAssessment]:
    """Iteratively repair article layout until the rubric score is acceptable.

    Returns the (possibly repaired) article body and its final
    ``LayoutAssessment``.  If ``max_passes`` is zero, the original article
    and its assessment are returned immediately without any model call.
    """
    current = article.strip()
    assessment = assess_medium_layout(current, min_score=min_score)

    if max_passes <= 0:
        return current, assessment

    client = create_openai_client()
    template = _load_template(LAYOUT_REPAIR_PROMPT_PATH)

    for pass_num in range(1, max_passes + 1):
        if not assessment.needs_repair:
            break

        feedback_lines = assessment.issues or (
            "Improve section flow and readability while preserving technical accuracy.",
        )
        feedback = "\n".join(f"- {line}" for line in feedback_lines)

        log_event(
            LOGGER,
            "layout_repair_started",
            level=logging.INFO,
            topic=topic,
            pass_num=pass_num,
            score=assessment.score,
            max_score=assessment.max_score,
            required_issue_count=len(assessment.required_issues),
        )

        prompt = (
            template
            .replace("{topic}", topic)
            .replace("{allowed_references}", allowed_references.strip() or "- None")
            .replace("{article}", current)
            .replace("{feedback}", feedback)
        )
        repaired = _render_model_response(
            client,
            prompt,
            operation="reinforce_medium_layout",
            temperature=min(OPENAI_TEMPERATURE, LAYOUT_TEMPERATURE),
        )
        if not repaired:
            log_event(
                LOGGER,
                "layout_repair_empty",
                level=logging.WARNING,
                topic=topic,
                pass_num=pass_num,
            )
            break

        current = repaired
        assessment = assess_medium_layout(current, min_score=min_score)
        log_event(
            LOGGER,
            "layout_repair_complete",
            level=logging.INFO,
            topic=topic,
            pass_num=pass_num,
            score=assessment.score,
            needs_repair=assessment.needs_repair,
        )

    return current, assessment


def enforce_factual_grounding(
    topic: str,
    article: str,
    allowed_references: str,
    max_passes: int = 1,
) -> str:
    """Rewrite unsupported claims conservatively to reduce hallucination risk.

    Each pass sends the current article body through the factuality prompt.
    An empty model response stops the loop early and returns the last
    successfully rewritten version.

    Returns the original article unchanged when ``max_passes`` is zero.
    """
    if max_passes <= 0:
        return article.strip()

    client = create_openai_client()
    template = _load_template(FACTUALITY_PROMPT_PATH)
    current = article.strip()

    for pass_num in range(1, max_passes + 1):
        prompt = (
            template
            .replace("{topic}", topic)
            .replace("{allowed_references}", allowed_references.strip() or "- None")
            .replace("{article}", current)
        )
        log_event(
            LOGGER,
            "factuality_pass_started",
            level=logging.INFO,
            topic=topic,
            pass_num=pass_num,
        )
        revised = _render_model_response(
            client,
            prompt,
            operation="enforce_factual_grounding",
            temperature=min(OPENAI_TEMPERATURE, FACTUALITY_TEMPERATURE),
        )
        if not revised:
            log_event(
                LOGGER,
                "factuality_pass_empty",
                level=logging.WARNING,
                topic=topic,
                pass_num=pass_num,
            )
            break
        current = revised
        log_event(
            LOGGER,
            "factuality_pass_complete",
            level=logging.INFO,
            topic=topic,
            pass_num=pass_num,
            output_chars=len(current),
        )

    return current.strip()