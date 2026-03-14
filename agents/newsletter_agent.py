"""Newsletter agent: assemble a weekly developer newsletter from pipeline outputs."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scanners.trend_scanner import TrendSignal

from config import (
    NEWSLETTER_ENABLED,
    NEWSLETTER_ISSUE_FILE,
    NEWSLETTER_NAME,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    openai_generation_kwargs,
)
from utils.openai_logging import create_openai_client, responses_create_logged

PROMPT_PATH = Path("prompts/newsletter_prompt.txt")

# Sources treated as community picks (non-Apple-docs, developer community)
_COMMUNITY_SOURCE_KEYWORDS = {"reddit", "dev.to", "hackernews", "medium", "hn"}

# Minimum keywords required in a signal's title/summary to qualify as iOS/Apple relevant
_IOS_TOPIC_KEYWORDS = {
    "ios", "swift", "swiftui", "xcode", "apple tv", "appkit", "uikit",
    "macos", "watchos", "visionos", "iphone", "ipad", "app store",
    "testflight", "swiftdata", "widgetkit", "apple", "wwdc",
}


# ---------------------------------------------------------------------------
# Issue number persistence
# ---------------------------------------------------------------------------


def _read_and_increment_issue(issue_file: Path) -> int:
    """Read the current issue number from disk, increment it, persist, return new value."""
    issue_file.parent.mkdir(parents=True, exist_ok=True)
    current = 0
    if issue_file.exists():
        try:
            current = int(issue_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            current = 0
    next_issue = current + 1
    issue_file.write_text(str(next_issue), encoding="utf-8")
    return next_issue


# ---------------------------------------------------------------------------
# Signal / snippet selection helpers
# ---------------------------------------------------------------------------


def _pick_top_trends(trends: list["TrendSignal"], max_items: int = 5) -> list["TrendSignal"]:
    """Select top iOS-relevant trend signals, preferring those that carry a source URL."""
    relevant = [t for t in trends if _is_ios_relevant(t)]
    with_url = [t for t in relevant if t.url and t.url.startswith("http")]
    without_url = [t for t in relevant if not (t.url and t.url.startswith("http"))]
    ranked = sorted(with_url, key=lambda t: t.score, reverse=True)
    ranked += sorted(without_url, key=lambda t: t.score, reverse=True)
    return ranked[:max_items]


def _is_ios_relevant(signal: "TrendSignal") -> bool:
    """Return True if the signal title/summary contains at least one iOS/Apple keyword."""
    text = f"{signal.title} {signal.summary}".lower()
    return any(kw in text for kw in _IOS_TOPIC_KEYWORDS)


def _pick_community_links(
    trends: list["TrendSignal"], max_items: int = 3
) -> list["TrendSignal"]:
    """Pick community picks from non-Apple-docs sources (reddit, dev.to, medium, hackernews).

    Only signals that mention an iOS/Apple topic keyword are eligible, to prevent
    off-topic items (hardware news, general programming, etc.) from appearing.
    """
    community = [
        t
        for t in trends
        if t.url
        and t.url.startswith("http")
        and any(kw in t.source.lower() for kw in _COMMUNITY_SOURCE_KEYWORDS)
        and _is_ios_relevant(t)
    ]
    return sorted(community, key=lambda t: t.score, reverse=True)[:max_items]


_SNIPPET_MAX_LINES = 25


def _pick_best_snippet(codegen: dict) -> str:
    """Return the best validated Swift snippet, capped at _SNIPPET_MAX_LINES lines.

    Prefers the direct validation path (snippet compiled without a wrapper).
    Truncates long snippets so the newsletter stays scannable.
    """
    code = codegen.get("code", "").strip()
    if not code:
        return code
    lines = code.splitlines()
    if len(lines) > _SNIPPET_MAX_LINES:
        code = "\n".join(lines[:_SNIPPET_MAX_LINES]) + "\n// ... (truncated for newsletter)"
    return code


def _unescape_code_blocks(markdown: str) -> str:
    """Fix double-escaped braces inside fenced code blocks.

    Some LLMs output {{ and }} inside code blocks as if escaping Python format
    strings. Split on ``` fences; odd-indexed parts are inside a code fence and
    get their {{ → { and }} → } normalized.
    """
    parts = markdown.split("```")
    for i in range(1, len(parts), 2):
        parts[i] = parts[i].replace("{{", "{").replace("}}", "}")
    return "```".join(parts)


def _article_teaser(body: str, max_chars: int = 400) -> str:
    """Extract a 3-sentence teaser from the article body."""
    sentences = re.split(r"(?<=[.!?])\s+", body.strip())
    teaser = " ".join(sentences[:3]).strip()
    if len(teaser) > max_chars:
        teaser = teaser[:max_chars - 3] + "..."
    return teaser


# ---------------------------------------------------------------------------
# Safe template formatting
# ---------------------------------------------------------------------------


def _safe_format(template: str, **kwargs: str) -> str:
    """Format a prompt template.

    str.format() does not re-scan replacement values for {} patterns, so values
    containing Swift braces are substituted literally without any escaping.
    The only protection needed is against literal {} in the template that are NOT
    placeholders — those must be escaped as {{ }} in the template file itself.
    """
    return template.format(**kwargs)


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_H_STYLE = "font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;margin:24px 0 8px"
_P_STYLE = (
    "font-family:Georgia,serif;font-size:15px;line-height:1.7;color:#333;margin:0 0 16px"
)
_LI_STYLE = (
    "font-family:Georgia,serif;font-size:15px;line-height:1.7;color:#333;margin-bottom:6px"
)
_CODE_STYLE = (
    "background:#f4f4f4;border-left:3px solid #e05c00;padding:16px;"
    "font-family:'Courier New',monospace;font-size:13px;line-height:1.5;"
    "white-space:pre;overflow-x:auto;display:block;margin:16px 0;color:#1a1a1a"
)
_HR_STYLE = "border:0;border-top:1px solid #e8e8e8;margin:24px 0"
_A_STYLE = "color:#e05c00;text-decoration:none"


def _render_inline(text: str) -> str:
    """Convert inline markdown (bold, links) to HTML."""
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2))}" style="{_A_STYLE}">{html.escape(m.group(1))}</a>',
        text,
    )
    return text


def _render_html(markdown: str, newsletter_name: str, issue_number: int) -> str:
    """Convert newsletter markdown to an email-safe HTML document (inline styles, max-width 600px)."""
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
        # Fenced code block toggle
        if line.strip().startswith("```"):
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

        # Horizontal rule
        if line.strip() == "---":
            _close_list()
            body_parts.append(f'<hr style="{_HR_STYLE}">')
            continue

        # H2
        if line.startswith("## "):
            _close_list()
            text = line[3:].strip()
            body_parts.append(
                f'<h2 style="{_H_STYLE};font-size:22px">{_render_inline(html.escape(text))}</h2>'
            )
            continue

        # H3
        if line.startswith("### "):
            _close_list()
            text = line[4:].strip()
            body_parts.append(
                f'<h3 style="{_H_STYLE};font-size:18px">{_render_inline(html.escape(text))}</h3>'
            )
            continue

        # Bullet point (- or •)
        if re.match(r"^[-•]\s+", line.strip()):
            if not in_list:
                body_parts.append(
                    '<ul style="margin:0 0 16px;padding-left:20px;list-style-type:disc">'
                )
                in_list = True
            content = re.sub(r"^[-•]\s+", "", line.strip())
            body_parts.append(f'<li style="{_LI_STYLE}">{_render_inline(content)}</li>')
            continue

        # Blank line
        if not line.strip():
            _close_list()
            continue

        # Regular paragraph
        _close_list()
        body_parts.append(f'<p style="{_P_STYLE}">{_render_inline(line.strip())}</p>')

    _close_list()
    content_html = "\n".join(body_parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(newsletter_name)} — Issue #{issue_number}</title>
</head>
<body style="margin:0;padding:0;background:#f0f0f0;font-family:Georgia,serif">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f0f0f0">
  <tr><td align="center" style="padding:32px 16px">
    <table width="600" cellpadding="0" cellspacing="0" border="0"
           style="background:#ffffff;max-width:600px;width:100%;border-radius:4px">
      <tr>
        <td style="background:#1a1a1a;padding:24px 40px;border-radius:4px 4px 0 0">
          <p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#aaa;letter-spacing:2px;text-transform:uppercase">Issue #{issue_number}</p>
          <h1 style="margin:4px 0 0;font-family:Arial,Helvetica,sans-serif;font-size:26px;color:#ffffff;letter-spacing:-0.5px">{html.escape(newsletter_name)}</h1>
        </td>
      </tr>
      <tr>
        <td style="padding:32px 40px">
{content_html}
        </td>
      </tr>
      <tr>
        <td style="background:#f8f8f8;padding:20px 40px;border-radius:0 0 4px 4px;border-top:1px solid #e8e8e8">
          <p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#888;text-align:center">{html.escape(newsletter_name)} &middot; For iOS engineers, by iOS engineers</p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_newsletter(
    article: dict,
    trends: list,
    codegen: dict,
    linkedin_post: str,
    config,  # config module — accepted for API consistency; module-level imports are used directly
) -> dict:
    """Assemble a weekly newsletter from pipeline outputs.

    Args:
        article:      {"title": str, "body": str}
        trends:       list[TrendSignal] from the trend scanner
        codegen:      {"code": str, "path": str, ...} — snippet + generation metadata
        linkedin_post: generated LinkedIn post text (used for closing CTA tone)
        config:       config module (accepted for API symmetry; constants are imported directly)

    Returns:
        {"markdown": str, "html": str, "issue_number": int}
        Returns empty-string values with issue_number=0 when NEWSLETTER_ENABLED is false.
    """
    if not NEWSLETTER_ENABLED:
        return {"markdown": "", "html": "", "issue_number": 0}

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    issue_number = _read_and_increment_issue(Path(NEWSLETTER_ISSUE_FILE))

    top_trends = _pick_top_trends(trends, max_items=5)
    community_picks = _pick_community_links(trends, max_items=3)
    best_snippet = _pick_best_snippet(codegen)

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
    )

    community_links_json = json.dumps(
        [{"source": t.source, "title": t.title, "url": t.url} for t in community_picks],
        indent=2,
    )

    article_title = article.get("title", "")
    article_teaser = _article_teaser(article.get("body", ""))

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = _safe_format(
        prompt_template,
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
        max_output_tokens=1400,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, 0.5)),
    )

    newsletter_markdown = _unescape_code_blocks(response.output_text.strip())
    if not newsletter_markdown:
        raise RuntimeError("Newsletter generation returned empty output.")

    newsletter_html = _render_html(newsletter_markdown, NEWSLETTER_NAME, issue_number)

    return {
        "markdown": newsletter_markdown,
        "html": newsletter_html,
        "issue_number": issue_number,
    }
