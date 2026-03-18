"""Newsletter agent: assemble a weekly developer newsletter from pipeline outputs.

Design principles
-----------------
- All constants (paths, limits, style strings) are typed ``Final``.
- Issue number persistence is atomic: write to a temp file then rename,
  preventing partial writes from corrupting the counter.
- ``_safe_format`` is the only template substitution path — no ``str.format()``
  anywhere in the module, so Swift braces in snippets never raise ``KeyError``.
- HTML rendering is a pure function with no I/O — fully unit-testable.
- Structured logging on the generation path and on every early-exit condition.
- ``OPENAI_API_KEY`` check removed — key validation belongs in
  ``create_openai_client()``.
"""

from __future__ import annotations

import html
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from scanners.trend_scanner import TrendSignal

from config import (
    NEWSLETTER_ENABLED,
    NEWSLETTER_ISSUE_FILE,
    NEWSLETTER_NAME,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    openai_generation_kwargs,
)
from utils.observability import get_logger, log_event
from utils.openai_logging import create_openai_client, responses_create_logged

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROMPT_PATH: Final[Path] = Path("prompts/newsletter_prompt.txt")

MAX_OUTPUT_TOKENS: Final[int] = 1_400
GENERATION_TEMPERATURE: Final[float] = 0.50

TREND_MAX_ITEMS: Final[int] = 5
COMMUNITY_MAX_ITEMS: Final[int] = 3
SNIPPET_MAX_LINES: Final[int] = 25
ARTICLE_TEASER_MAX_CHARS: Final[int] = 400
ARTICLE_TEASER_SENTENCES: Final[int] = 3
ARTICLE_EXCERPT_MAX_LINES: Final[int] = 20

# Sources treated as community picks (non-Apple-docs, developer community).
_COMMUNITY_SOURCES: Final[frozenset[str]] = frozenset(
    {"reddit", "dev.to", "hackernews", "medium", "hn"}
)

# Keywords that mark a signal as iOS/Apple platform relevant.
_IOS_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "ios", "swift", "swiftui", "xcode", "apple tv", "appkit", "uikit",
        "macos", "watchos", "visionos", "iphone", "ipad", "app store",
        "testflight", "swiftdata", "widgetkit", "apple", "wwdc",
    }
)

LOGGER = get_logger("pipeline.newsletter")

# ---------------------------------------------------------------------------
# Regex patterns — compiled once
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")
_BOLD_RE: Final[re.Pattern[str]] = re.compile(r"\*\*([^*]+)\*\*")
_MD_LINK_RE: Final[re.Pattern[str]] = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BULLET_RE: Final[re.Pattern[str]] = re.compile(r"^[-•]\s+")
_CODE_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"^```")

# ---------------------------------------------------------------------------
# HTML style constants — defined once for consistent email rendering
# ---------------------------------------------------------------------------

_H_BASE: Final[str] = "font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;margin:24px 0 8px"
_P_STYLE: Final[str] = (
    "font-family:Georgia,serif;font-size:15px;line-height:1.7;color:#333;margin:0 0 16px"
)
_LI_STYLE: Final[str] = (
    "font-family:Georgia,serif;font-size:15px;line-height:1.7;color:#333;margin-bottom:6px"
)
_CODE_STYLE: Final[str] = (
    "background:#f4f4f4;border-left:3px solid #e05c00;padding:16px;"
    "font-family:'Courier New',monospace;font-size:13px;line-height:1.5;"
    "white-space:pre;overflow-x:auto;display:block;margin:16px 0;color:#1a1a1a"
)
_HR_STYLE: Final[str] = "border:0;border-top:1px solid #e8e8e8;margin:24px 0"
_A_STYLE: Final[str] = "color:#e05c00;text-decoration:none"
_UL_STYLE: Final[str] = "margin:0 0 16px;padding-left:20px;list-style-type:disc"

# ---------------------------------------------------------------------------
# Issue number persistence
# ---------------------------------------------------------------------------


def _read_and_increment_issue(issue_file: Path) -> int:
    """Read the current issue number, increment it, persist atomically, return new value.

    Uses a write-to-tempfile-then-rename pattern to prevent partial writes from
    corrupting the counter if the process is interrupted mid-write.
    """
    issue_file.parent.mkdir(parents=True, exist_ok=True)
    current = 0
    if issue_file.exists():
        try:
            current = int(issue_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError) as exc:
            log_event(
                LOGGER,
                "issue_counter_read_error",
                level=logging.WARNING,
                path=str(issue_file),
                error=repr(exc),
            )

    next_issue = current + 1

    # Atomic write: temp file in same directory so rename is on the same filesystem.
    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=issue_file.parent, suffix=".tmp")
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            f.write(str(next_issue))
        Path(tmp_path_str).replace(issue_file)
    except OSError as exc:
        Path(tmp_path_str).unlink(missing_ok=True)
        log_event(
            LOGGER,
            "issue_counter_write_error",
            level=logging.ERROR,
            path=str(issue_file),
            error=repr(exc),
        )
        raise

    log_event(
        LOGGER,
        "issue_counter_incremented",
        level=logging.INFO,
        issue_number=next_issue,
    )
    return next_issue


# ---------------------------------------------------------------------------
# Signal / snippet selection helpers
# ---------------------------------------------------------------------------


def _is_ios_relevant(signal: "TrendSignal") -> bool:
    """Return True when the signal title or summary contains an iOS/Apple keyword."""
    text = f"{signal.title} {signal.summary}".lower()
    return any(kw in text for kw in _IOS_KEYWORDS)


def _pick_top_trends(
    trends: list["TrendSignal"],
    max_items: int = TREND_MAX_ITEMS,
) -> list["TrendSignal"]:
    """Select top iOS-relevant signals, preferring those with a valid HTTP URL."""
    relevant = [t for t in trends if _is_ios_relevant(t)]
    with_url = sorted(
        [t for t in relevant if t.url and t.url.startswith("http")],
        key=lambda t: t.score,
        reverse=True,
    )
    without_url = sorted(
        [t for t in relevant if not (t.url and t.url.startswith("http"))],
        key=lambda t: t.score,
        reverse=True,
    )
    return (with_url + without_url)[:max_items]


def _pick_community_links(
    trends: list["TrendSignal"],
    max_items: int = COMMUNITY_MAX_ITEMS,
) -> list["TrendSignal"]:
    """Pick community picks from non-Apple-docs sources that are iOS-relevant."""
    community = [
        t for t in trends
        if t.url
        and t.url.startswith("http")
        and any(kw in t.source.lower() for kw in _COMMUNITY_SOURCES)
        and _is_ios_relevant(t)
    ]
    return sorted(community, key=lambda t: t.score, reverse=True)[:max_items]


def _extract_article_code_block(article_body: str, max_lines: int = ARTICLE_EXCERPT_MAX_LINES) -> str:
    """Extract the first fenced code block from the article as a newsletter fallback snippet."""
    in_fence = False
    lines: list[str] = []
    for line in article_body.splitlines():
        if _CODE_FENCE_RE.match(line.strip()):
            if in_fence:
                break
            in_fence = True
            continue
        if in_fence:
            lines.append(line)
    return "\n".join(lines[:max_lines]) if lines else ""


def _pick_best_snippet(codegen: dict, article_body: str = "") -> str:
    """Return the best validated Swift snippet, capped at ``SNIPPET_MAX_LINES`` lines.

    Falls back to the first fenced code block in the article body when codegen
    produced no snippet. Appends a truncation comment on long snippets.
    """
    code = codegen.get("code", "").strip()
    if not code and article_body:
        code = _extract_article_code_block(article_body)
    if not code:
        return ""

    lines = code.splitlines()
    if len(lines) > SNIPPET_MAX_LINES:
        return "\n".join(lines[:SNIPPET_MAX_LINES]) + "\n// … (truncated for newsletter)"
    return code


def _article_teaser(body: str, max_chars: int = ARTICLE_TEASER_MAX_CHARS) -> str:
    """Extract a short teaser from the first few sentences of the article body."""
    sentences = _SENTENCE_SPLIT_RE.split(body.strip())
    teaser = " ".join(sentences[:ARTICLE_TEASER_SENTENCES]).strip()
    if len(teaser) > max_chars:
        teaser = teaser[: max_chars - 3] + "..."
    return teaser


# ---------------------------------------------------------------------------
# Safe template formatting
# ---------------------------------------------------------------------------


def _safe_format(template: str, **kwargs: str) -> str:
    """Replace ``{key}`` placeholders using plain string substitution.

    Unlike ``str.format()``, this only replaces the exact ``{key}`` tokens
    present in ``kwargs``, leaving all other braces (Swift code, inline
    examples, backtick snippets) completely untouched. No escaping of
    ``{{`` or ``}}`` is ever needed in template files used with this function.
    """
    result = template
    for key, value in kwargs.items():
        result = result.replace(f"{{{key}}}", value)
    return result


def _unescape_code_blocks(markdown: str) -> str:
    """Normalise double-escaped braces inside fenced code blocks.

    Some models emit ``{{`` and ``}}`` inside code blocks as if escaping
    Python format strings. Odd-indexed parts of a ``split("```")`` are
    inside a fence — their doubled braces are collapsed to singles.
    """
    parts = markdown.split("```")
    for i in range(1, len(parts), 2):
        parts[i] = parts[i].replace("{{", "{").replace("}}", "}")
    return "```".join(parts)


# ---------------------------------------------------------------------------
# HTML rendering — pure function, no I/O
# ---------------------------------------------------------------------------


def _render_inline(text: str) -> str:
    """Convert inline markdown (bold, links) to inline HTML."""
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _MD_LINK_RE.sub(
        lambda m: (
            f'<a href="{html.escape(m.group(2))}" style="{_A_STYLE}">'
            f"{html.escape(m.group(1))}</a>"
        ),
        text,
    )
    return text


def _render_html(markdown: str, newsletter_name: str, issue_number: int) -> str:
    """Convert newsletter markdown to an email-safe HTML document.

    Uses inline styles and a max-width 600px single-column layout for
    broad email client compatibility.
    """
    lines = markdown.splitlines()
    body_parts: list[str] = []
    in_code_block = False
    code_lines: list[str] = []
    in_list = False

    def _close_list() -> None:
        nonlocal in_list
        if in_list:
            body_parts.append("</ul>")
            in_list = False

    for line in lines:
        # --- Fenced code block ---
        if _CODE_FENCE_RE.match(line.strip()):
            if not in_code_block:
                _close_list()
                in_code_block = True
                code_lines = []
            else:
                in_code_block = False
                code_html = html.escape("\n".join(code_lines))
                body_parts.append(f'<code style="{_CODE_STYLE}">{code_html}</code>')
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # --- Horizontal rule ---
        if line.strip() == "---":
            _close_list()
            body_parts.append(f'<hr style="{_HR_STYLE}">')
            continue

        # --- H2 ---
        if line.startswith("## "):
            _close_list()
            text = html.escape(line[3:].strip())
            body_parts.append(
                f'<h2 style="{_H_BASE};font-size:22px">{_render_inline(text)}</h2>'
            )
            continue

        # --- H3 ---
        if line.startswith("### "):
            _close_list()
            text = html.escape(line[4:].strip())
            body_parts.append(
                f'<h3 style="{_H_BASE};font-size:18px">{_render_inline(text)}</h3>'
            )
            continue

        # --- Bullet item ---
        if _BULLET_RE.match(line.strip()):
            if not in_list:
                body_parts.append(f'<ul style="{_UL_STYLE}">')
                in_list = True
            content = _BULLET_RE.sub("", line.strip())
            body_parts.append(f'<li style="{_LI_STYLE}">{_render_inline(content)}</li>')
            continue

        # --- Blank line ---
        if not line.strip():
            _close_list()
            continue

        # --- Regular paragraph ---
        _close_list()
        body_parts.append(
            f'<p style="{_P_STYLE}">{_render_inline(html.escape(line.strip()))}</p>'
        )

    _close_list()
    content_html = "\n".join(body_parts)
    escaped_name = html.escape(newsletter_name)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escaped_name} \u2014 Issue #{issue_number}</title>
</head>
<body style="margin:0;padding:0;background:#f0f0f0;font-family:Georgia,serif">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f0f0f0">
  <tr><td align="center" style="padding:32px 16px">
    <table width="600" cellpadding="0" cellspacing="0" border="0"
           style="background:#ffffff;max-width:600px;width:100%;border-radius:4px">
      <tr>
        <td style="background:#1a1a1a;padding:24px 40px;border-radius:4px 4px 0 0">
          <p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#aaa;letter-spacing:2px;text-transform:uppercase">Issue #{issue_number}</p>
          <h1 style="margin:4px 0 0;font-family:Arial,Helvetica,sans-serif;font-size:26px;color:#ffffff;letter-spacing:-0.5px">{escaped_name}</h1>
        </td>
      </tr>
      <tr>
        <td style="padding:32px 40px">
{content_html}
        </td>
      </tr>
      <tr>
        <td style="background:#f8f8f8;padding:20px 40px;border-radius:0 0 4px 4px;border-top:1px solid #e8e8e8">
          <p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#888;text-align:center">{escaped_name} &middot; For iOS engineers, by iOS engineers</p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def _load_template(path: Path) -> str:
    """Read and return a prompt template.

    Raises
    ------
    FileNotFoundError
        When the template file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt template not found at '{path}'. "
            "Verify the path constant or the process working directory."
        )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_newsletter(
    article: dict,
    trends: list,
    codegen: dict,
    linkedin_post: str,
    config=None,  # Accepted for API symmetry; module-level imports are used directly.
) -> dict:
    """Assemble a weekly newsletter from pipeline outputs.

    Parameters
    ----------
    article:
        ``{"title": str, "body": str}``
    trends:
        ``list[TrendSignal]`` from the trend scanner.
    codegen:
        ``{"code": str, "path": str, …}`` — snippet and generation metadata.
    linkedin_post:
        Generated LinkedIn post text (used for closing CTA tone inspiration).
    config:
        Config module; accepted for API symmetry. Constants are imported directly.

    Returns
    -------
    dict
        ``{"markdown": str, "html": str, "issue_number": int}``
        Returns empty-string values with ``issue_number=0`` when
        ``NEWSLETTER_ENABLED`` is false.

    Raises
    ------
    FileNotFoundError
        When the prompt template file is missing.
    RuntimeError
        When generation returns empty output.
    OSError
        When the issue counter file cannot be written atomically.
    """
    if not NEWSLETTER_ENABLED:
        log_event(LOGGER, "newsletter_skipped", level=logging.INFO, reason="NEWSLETTER_ENABLED=false")
        return {"markdown": "", "html": "", "issue_number": 0}

    issue_number = _read_and_increment_issue(Path(NEWSLETTER_ISSUE_FILE))

    top_trends = _pick_top_trends(trends)
    community_picks = _pick_community_links(trends)
    best_snippet = _pick_best_snippet(codegen, article_body=article.get("body", ""))

    log_event(
        LOGGER,
        "newsletter_inputs_prepared",
        level=logging.INFO,
        issue_number=issue_number,
        trend_count=len(top_trends),
        community_count=len(community_picks),
        has_snippet=bool(best_snippet),
    )

    trend_signals_json = json.dumps(
        [
            {
                "source": t.source,
                "title": t.title,
                "url": t.url,
                "summary": t.summary,
            }
            for t in top_trends
        ],
        indent=2,
        ensure_ascii=False,
    )
    community_links_json = json.dumps(
        [{"source": t.source, "title": t.title, "url": t.url} for t in community_picks],
        indent=2,
        ensure_ascii=False,
    )

    article_title = article.get("title", "")
    article_teaser = _article_teaser(article.get("body", ""))

    prompt = _safe_format(
        _load_template(PROMPT_PATH),
        newsletter_name=NEWSLETTER_NAME,
        issue_number=str(issue_number),
        article_title=article_title,
        article_teaser=article_teaser,
        trend_signals_json=trend_signals_json,
        best_snippet=best_snippet or "No validated snippet available this run.",
        community_links_json=community_links_json,
        linkedin_post=linkedin_post.strip() if linkedin_post else "",
    )

    client = create_openai_client()
    response = responses_create_logged(
        client,
        agent_name="newsletter_agent",
        operation="generate_newsletter",
        model=OPENAI_MODEL,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, GENERATION_TEMPERATURE)),
    )

    newsletter_markdown = _unescape_code_blocks(response.output_text.strip())
    if not newsletter_markdown:
        raise RuntimeError(
            f"Newsletter generation returned empty output for issue #{issue_number}."
        )

    newsletter_html = _render_html(newsletter_markdown, NEWSLETTER_NAME, issue_number)

    log_event(
        LOGGER,
        "newsletter_generated",
        level=logging.INFO,
        issue_number=issue_number,
        markdown_chars=len(newsletter_markdown),
        html_chars=len(newsletter_html),
    )

    return {
        "markdown": newsletter_markdown,
        "html": newsletter_html,
        "issue_number": issue_number,
    }