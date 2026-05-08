"""
utils/article_repair.py

Deterministic post-processor for generated articles.
Runs after medium_layout_reinforcement, before sanitize_article.

Fixes pattern-based issues that LLM prompts catch inconsistently:
  - Malformed backtick patterns: `with`Word`` → `withWord`
  - Audits for missing Swift version / deployment target callouts (log-only)
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Backtick repair
# ---------------------------------------------------------------------------

# Matches the malformed split-backtick pattern: `prefix`Suffix``
# The prefix is any word (not just "with") so this catches:
#   `with`TaskGroup``           → `withTaskGroup`
#   `withThrowing`TaskGroup``   → `withThrowingTaskGroup`
#   `withChecked`Continuation`` → `withCheckedContinuation`
_MALFORMED_BACKTICK_RE = re.compile(r'`(\w+)`(\w+)``')


def repair_malformed_backticks(text: str) -> Tuple[str, List[str]]:
    """Fix `` `with`Word`` `` → `` `withWord` ``.

    Returns (repaired_text, list_of_substitutions_made).
    """
    fixes: List[str] = []

    def _replacer(m: re.Match) -> str:
        original = m.group(0)
        fixed = f"`{m.group(1)}{m.group(2)}`"
        fixes.append(f"{original!r} → {fixed!r}")
        return fixed

    repaired = _MALFORMED_BACKTICK_RE.sub(_replacer, text)
    return repaired, fixes


# ---------------------------------------------------------------------------
# Swift @Bindable / @Observable repair
# ---------------------------------------------------------------------------

# Detects an @Observable type declaration up to its body's opening brace.
# Permissive: handles `@Observable\nfinal class M`, `@Observable @MainActor class M:
# Proto1, Proto2 {`, etc. Stops at the first `{` (body opener).
_OBSERVABLE_DECL_RE = re.compile(
    r"@Observable\b[^{]*?(?:class|struct|actor)\s+\w+[^{]*?\{",
    re.DOTALL,
)

# `@Bindable var foo` (or `let`) at the start of a line inside a class body.
# The leading whitespace is preserved so indentation isn't lost.
_BINDABLE_PROP_RE = re.compile(
    r"^(\s*)@Bindable\s+((?:var|let)\s+)",
    re.MULTILINE,
)


def strip_bindable_from_observable(code: str) -> Tuple[str, int]:
    """Remove ``@Bindable`` from stored properties inside ``@Observable`` types.

    The model frequently emits::

        @Observable class M { @Bindable var x: Int = 0 }

    which fails the repair loop's style check. This function strips the
    ``@Bindable`` annotation from properties **only when they appear inside an
    @Observable-annotated class/struct/actor body** — leaving correct
    `View`-side ``@Bindable`` declarations untouched.

    Returns ``(cleaned_code, count_of_fixes)``.
    """
    if not code or "@Observable" not in code or "@Bindable" not in code:
        return code, 0

    parts: List[str] = []
    fixes = 0
    cursor = 0

    while cursor < len(code):
        match = _OBSERVABLE_DECL_RE.search(code, cursor)
        if not match:
            parts.append(code[cursor:])
            break

        # Append everything up to and including the body-opening brace verbatim.
        parts.append(code[cursor:match.end()])

        # Walk the body, tracking brace depth, until the matching close brace.
        body_start = match.end()
        depth = 1
        idx = body_start
        while idx < len(code) and depth > 0:
            ch = code[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            idx += 1

        body_end = idx - 1  # position of the matching closing brace
        body = code[body_start:body_end]
        cleaned_body, n = _BINDABLE_PROP_RE.subn(r"\1\2", body)
        fixes += n
        parts.append(cleaned_body)

        # Append the closing brace, advance past it.
        if body_end < len(code):
            parts.append(code[body_end:idx])
        cursor = idx

    return "".join(parts), fixes


# ---------------------------------------------------------------------------
# Operational note label strip
# ---------------------------------------------------------------------------

_OPERATIONAL_NOTE_RE = re.compile(
    r"\bOperational note:\s*([a-zA-Z]?)",
    re.IGNORECASE,
)


def strip_operational_note_labels(text: str) -> Tuple[str, int]:
    """Remove 'Operational note:' prefixes, capitalizing the following sentence.

    Returns (cleaned_text, count_of_replacements).
    """
    count: List[int] = [0]

    def _replacer(m: re.Match) -> str:
        count[0] += 1
        first_char = m.group(1)
        return first_char.upper() if first_char else ""

    result = _OPERATIONAL_NOTE_RE.sub(_replacer, text)
    return result, count[0]


# ---------------------------------------------------------------------------
# Version callout audit (log-only — does not modify article)
# ---------------------------------------------------------------------------

# Only track APIs that require a post-baseline deployment callout.
# The repo now assumes iOS 18 / Swift 6 as the default baseline, so APIs
# available on iOS 18 or earlier should not trigger warnings.
_VERSION_CHECKS: List[Tuple[str, str]] = []


def audit_missing_version_callouts(text: str) -> List[str]:
    """Return a list of warnings for APIs found without a deployment target nearby.

    Diagnostic only — callers should log these as warnings but not block
    the pipeline.
    """
    warnings: List[str] = []
    for api, version_substring in _VERSION_CHECKS:
        if api in text and version_substring not in text:
            warnings.append(
                f"{api} appears in article but no '{version_substring}' callout found"
            )
    return warnings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def repair_article(text: str) -> Tuple[str, Dict]:
    """Run all deterministic repairs on article body text.

    Returns:
        (repaired_text, report_dict) where report_dict contains:
          'backtick_fixes'         — list of substitution descriptions (empty if none)
          'operational_note_fixes' — count of 'Operational note:' labels removed
          'version_warnings'       — list of missing version callout warnings
    """
    text, backtick_fixes = repair_malformed_backticks(text)
    text, operational_note_fixes = strip_operational_note_labels(text)
    version_warnings = audit_missing_version_callouts(text)
    return text, {
        "backtick_fixes": backtick_fixes,
        "operational_note_fixes": operational_note_fixes,
        "version_warnings": version_warnings,
    }
