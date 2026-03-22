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
from typing import Tuple, List, Dict

# ---------------------------------------------------------------------------
# Backtick repair
# ---------------------------------------------------------------------------

# Matches the malformed pattern: `with`Word``
# e.g. `with`TaskGroup`` → `withTaskGroup`
#      `with`CheckedThrowingContinuation`` → `withCheckedThrowingContinuation`
_MALFORMED_BACKTICK_RE = re.compile(r'`(with)`(\w+)``')


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

# (api_token_in_text, expected_version_substring)
_VERSION_CHECKS: List[Tuple[str, str]] = [
    ("`withTaskGroup`",                    "iOS 15"),
    ("`withThrowingTaskGroup`",            "iOS 15"),
    ("`withCheckedThrowingContinuation`",  "iOS 15"),
    ("`withCheckedContinuation`",          "iOS 15"),
    ("`AsyncSequence`",                    "iOS 15"),
    ("`AsyncStream`",                      "iOS 15"),
    ("`@Observable`",                      "iOS 17"),
    ("`@Bindable`",                        "iOS 17"),
]


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
