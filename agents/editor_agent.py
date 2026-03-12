"""Editor agent: quality pass for professionalism and Medium-style structure."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE, openai_generation_kwargs
from utils.openai_logging import create_openai_client, responses_create_logged

PROMPT_PATH = Path("prompts/editor_prompt.txt")
LAYOUT_REPAIR_PROMPT_PATH = Path("prompts/layout_repair_prompt.txt")
FACTUALITY_PROMPT_PATH = Path("prompts/article_factuality_prompt.txt")

SECTION_CONTEXT_KEYWORDS = ("why this matters", "context", "foundation", "overview")
SECTION_RISK_KEYWORDS = ("tradeoff", "pitfall", "risk", "limit")
SECTION_CHECKLIST_KEYWORDS = ("checklist", "playbook", "implementation steps")
SECTION_CLOSING_KEYWORDS = ("takeaway", "conclusion", "final thoughts", "wrap-up")


def _load_prompt_template() -> str:
    """Load editor prompt template."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def _load_layout_repair_template() -> str:
    """Load layout repair prompt template."""
    return LAYOUT_REPAIR_PROMPT_PATH.read_text(encoding="utf-8")


def _load_factuality_template() -> str:
    """Load article factuality prompt template."""
    return FACTUALITY_PROMPT_PATH.read_text(encoding="utf-8")


def _render_model_response(
    client: OpenAI,
    prompt: str,
    *,
    operation: str,
    temperature: float = 0.45,
) -> str:
    """Generate markdown text from a provided prompt."""
    response = responses_create_logged(
        client,
        agent_name="editor_agent",
        operation=operation,
        model=OPENAI_MODEL,
        max_output_tokens=2600,
        input=prompt,
        **openai_generation_kwargs(temperature),
    )
    text = response.output_text.strip()
    if text.startswith("# "):
        text = "\n".join(text.splitlines()[1:]).strip()
    return text


@dataclass(frozen=True)
class LayoutAssessment:
    """Heuristic quality score for Medium article layout fidelity."""

    score: int
    max_score: int
    min_score: int
    issues: tuple[str, ...]
    required_issues: tuple[str, ...]

    @property
    def needs_repair(self) -> bool:
        """Return True when article should go through a layout repair pass."""
        return self.score < self.min_score or bool(self.required_issues)


def _first_intro_block(markdown: str) -> str:
    """Return text block before first h2 heading."""
    match = re.search(r"^##\s+", markdown, flags=re.MULTILINE)
    if not match:
        return ""
    intro = markdown[: match.start()].strip()
    return intro.split("\n\n")[0].strip() if intro else ""


def _split_prose_paragraphs(markdown: str) -> list[str]:
    """Extract body paragraphs while skipping headings and list bullets."""
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", markdown.strip()):
        stripped = block.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if lines and all(
            line.startswith("- ")
            or line.startswith("* ")
            or bool(re.match(r"^\d+\.\s+", line))
            for line in lines
        ):
            continue
        paragraphs.append(" ".join(lines))
    return paragraphs


def _sentence_count(text: str) -> int:
    """Count sentence-like spans for readability heuristics."""
    return len([part for part in re.split(r"[.!?]+(?:\s|$)", text.strip()) if part.strip()])


def _contains_keyword(headings: list[str], keywords: tuple[str, ...]) -> bool:
    """Check if any heading contains one of the expected section keywords."""
    lowered = [heading.lower() for heading in headings]
    return any(any(keyword in heading for keyword in keywords) for heading in lowered)


def _contains_markdown_list(markdown: str) -> bool:
    """Return True when markdown includes bullet or numbered list items."""
    return bool(re.search(r"^(?:- |\* |\d+\.\s+)", markdown, flags=re.MULTILINE))


def _contains_blockquote(markdown: str) -> bool:
    """Return True when markdown includes at least one pull-quote block."""
    return bool(re.search(r"^>\s+\S+", markdown, flags=re.MULTILINE))


def _uses_supported_heading_levels(markdown: str) -> bool:
    """Medium article bodies should stay within h2/h3 structure only."""
    return not bool(re.search(r"^####+\s+", markdown, flags=re.MULTILINE))


def assess_medium_layout(article: str, min_score: int = 7) -> LayoutAssessment:
    """Score markdown article against a Medium-style layout rubric."""
    body = article.strip()
    h2_headings = re.findall(r"^##\s+(.+)$", body, flags=re.MULTILINE)
    h3_headings = re.findall(r"^###\s+(.+)$", body, flags=re.MULTILINE)
    paragraphs = _split_prose_paragraphs(body)
    intro = _first_intro_block(body)

    score = 0
    max_score = 14
    issues: list[str] = []
    required_issues: list[str] = []

    if 6 <= len(h2_headings) <= 10:
        score += 2
    else:
        issues.append("Use 6-10 `##` sections to keep the article scannable and Medium-like.")

    if len(h3_headings) >= 4:
        score += 1
    else:
        message = "Add more `###` subheadings in core sections for faster scanning."
        issues.append(message)
        required_issues.append(message)

    intro_sentences = _sentence_count(intro)
    if intro and 1 <= intro_sentences <= 3 and len(intro) >= 60:
        score += 1
    else:
        message = "Add a short hook intro (1-2 concise paragraphs) before the first heading."
        issues.append(message)
        required_issues.append(message)

    if _contains_keyword(h2_headings, SECTION_CONTEXT_KEYWORDS):
        score += 1
    else:
        issues.append("Include an early context section explaining why this matters for iOS teams.")

    if _contains_keyword(h2_headings, SECTION_RISK_KEYWORDS):
        score += 1
    else:
        issues.append("Add a dedicated tradeoffs/pitfalls section.")

    if _contains_keyword(h2_headings, SECTION_CHECKLIST_KEYWORDS):
        score += 1
    else:
        issues.append("Include an implementation checklist section with concrete action items.")

    if _contains_keyword(h2_headings, SECTION_CLOSING_KEYWORDS):
        score += 1
    else:
        issues.append("End with a concise closing takeaway section.")

    if _contains_markdown_list(body):
        score += 1
    else:
        issues.append("Add at least one concise markdown list for better scanning.")

    if _contains_blockquote(body):
        score += 1
    else:
        issues.append("Add one short pull-quote style insight using markdown blockquote.")

    sentence_counts = [_sentence_count(paragraph) for paragraph in paragraphs if paragraph]
    average_sentences = sum(sentence_counts) / len(sentence_counts) if sentence_counts else 0
    if sentence_counts and average_sentences <= 4.2:
        score += 1
    else:
        issues.append("Shorten paragraphs to roughly 2-4 sentences each.")

    numbered_h2 = [
        heading for heading in h2_headings if re.match(r"^\d+[\).\:-]?\s+", heading.strip())
    ]
    if len(numbered_h2) >= 3:
        score += 1
    else:
        message = "Use numbered core sections (for example: `## 1. ...`, `## 2. ...`)."
        issues.append(message)
        required_issues.append(message)

    if h2_headings:
        compact_headings = [heading for heading in h2_headings if len(heading.split()) <= 12]
        compact_ratio = len(compact_headings) / len(h2_headings)
        if compact_ratio >= 0.8:
            score += 1
        else:
            issues.append("Keep most section headings under 12 words.")
    else:
        issues.append("Add clear `##` section headings.")

    if _uses_supported_heading_levels(body):
        score += 1
    else:
        issues.append("Use only `##` and `###` headings for clean Medium structure.")

    return LayoutAssessment(
        score=score,
        max_score=max_score,
        min_score=max(1, min_score),
        issues=tuple(issues),
        required_issues=tuple(required_issues),
    )


def polish_article(topic: str, article: str, allowed_references: str) -> str:
    """Refine article for clarity, professionalism, and Medium readability."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = create_openai_client()
    prompt = _load_prompt_template().format(
        topic=topic,
        article=article,
        allowed_references=allowed_references.strip() or "- None",
    )

    polished = _render_model_response(
        client=client,
        prompt=prompt,
        operation="polish_article",
        temperature=min(OPENAI_TEMPERATURE, 0.5),
    )

    if not polished:
        raise RuntimeError("Editor pass returned empty output.")

    return polished


def reinforce_medium_layout(
    topic: str,
    article: str,
    allowed_references: str,
    max_passes: int = 2,
    min_score: int = 7,
) -> str:
    """Repair article layout using iterative rubric feedback (reinforcement-style loop)."""
    if max_passes <= 0:
        return article.strip()

    if not OPENAI_API_KEY:
        return article.strip()

    client = create_openai_client()
    current = article.strip()
    assessment = assess_medium_layout(current, min_score=min_score)

    for _ in range(max_passes):
        if not assessment.needs_repair:
            break

        feedback_lines = assessment.issues or (
            "Improve section flow and readability while preserving technical accuracy.",
        )
        feedback = "\n".join(f"- {line}" for line in feedback_lines)
        prompt = _load_layout_repair_template().format(
            topic=topic,
            allowed_references=allowed_references.strip() or "- None",
            article=current,
            feedback=feedback,
        )
        repaired = _render_model_response(
            client=client,
            prompt=prompt,
            operation="reinforce_medium_layout",
            temperature=min(OPENAI_TEMPERATURE, 0.45),
        )
        if repaired:
            current = repaired
        assessment = assess_medium_layout(current, min_score=min_score)

    return current


def enforce_factual_grounding(
    topic: str,
    article: str,
    allowed_references: str,
    max_passes: int = 1,
) -> str:
    """Reduce hallucination risk by rewriting unsupported claims conservatively."""
    if max_passes <= 0:
        return article.strip()
    if not OPENAI_API_KEY:
        return article.strip()

    client = create_openai_client()
    current = article.strip()
    template = _load_factuality_template()

    for _ in range(max_passes):
        prompt = template.format(
            topic=topic,
            allowed_references=allowed_references.strip() or "- None",
            article=current,
        )
        revised = _render_model_response(
            client=client,
            prompt=prompt,
            operation="enforce_factual_grounding",
            temperature=min(OPENAI_TEMPERATURE, 0.3),
        )
        if not revised:
            break
        current = revised

    return current.strip()
