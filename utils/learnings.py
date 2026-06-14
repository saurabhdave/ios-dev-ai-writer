"""Mine quality_history.json for recurring failures and turn them into a bounded
"avoid these" digest that is injected back into the generation prompts.

This closes the feedback loop: the pipeline already *records* every run's review
critiques and type-check failures in ``outputs/quality_history.json``; this module
*reads* the recent window, surfaces patterns that recur (so one-off noise is
ignored), and produces a short markdown block the code/article agents prepend to
their prompts. The next run is then steered away from last month's mistakes.

Two digests:
  * **code** — recurring inline type-check failures (``inline_snippet_issues``):
    wrong labels, nonexistent members, protocol-conformance gaps, …
  * **editorial** — recurring review/layout critiques (``review_issues`` /
    ``layout_issues``).

Everything is bounded (window, min recurrence, max items) and deterministic, and
returns ``""`` when there is nothing recurring — so it is a safe no-op on a fresh
history.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Final

# Issue strings that are tooling noise, not a code lesson to learn from.
_CODE_NOISE_RE: Final[re.Pattern[str]] = re.compile(
    r"timed out|parse failed|type-check failed$|gate-banned", re.IGNORECASE
)
# Grammatical filler dropped before fingerprinting free-text review critiques.
_STOPWORDS: Final[frozenset[str]] = frozenset({
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "is", "are",
    "with", "without", "this", "that", "it", "its", "as", "but", "not", "no",
    "use", "uses", "using", "code", "article", "should", "would", "could",
    "may", "might", "from", "into", "via", "when", "which", "while", "about",
})


def _load(path: str | Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _inline_issue_essence(issue: str) -> str:
    """`inline block ('…'): <error>` -> `<error>` (trimmed)."""
    return issue.split("): ", 1)[-1].strip() if "): " in issue else issue.strip()


def _norm_code(msg: str) -> str:
    """Normalize a compiler error for recurrence counting (keep identifiers)."""
    return re.sub(r"\s+", " ", msg.strip().lower())


def _fingerprint_text(text: str) -> str:
    """Order-independent keyword fingerprint for free-text critiques: the sorted
    set of significant tokens. Clusters verbatim and reordered repeats (the
    reviewer's consistent phrasing) without the brittleness of a fixed-size cut."""
    toks = {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 3 and t not in _STOPWORDS}
    return " ".join(sorted(toks))


def _recurring(
    pairs: list[tuple[str, str]], *, min_count: int, max_items: int
) -> list[str]:
    """pairs = (fingerprint, representative_text). Return representatives whose
    fingerprint recurs >= min_count, most frequent first, deduped, capped."""
    counts: Counter[str] = Counter(fp for fp, _ in pairs)
    rep: dict[str, str] = {}
    for fp, text in pairs:
        rep.setdefault(fp, text)
    ranked = [
        rep[fp] for fp, c in counts.most_common()
        if c >= min_count and fp
    ]
    return ranked[:max_items]


def code_learnings(
    records: list[dict], *, window: int, min_count: int, max_items: int
) -> list[str]:
    pairs: list[tuple[str, str]] = []
    for rec in records[-window:]:
        for issue in (rec.get("inline_snippet_issues") or []):
            essence = _inline_issue_essence(issue)
            if not essence or _CODE_NOISE_RE.search(essence):
                continue
            pairs.append((_norm_code(essence), essence))
    return _recurring(pairs, min_count=min_count, max_items=max_items)


def editorial_learnings(
    records: list[dict], *, window: int, min_count: int, max_items: int
) -> list[str]:
    pairs: list[tuple[str, str]] = []
    for rec in records[-window:]:
        for issue in (rec.get("review_issues") or []) + (rec.get("layout_issues") or []):
            text = str(issue).strip()
            if len(text) < 8:
                continue
            pairs.append((_fingerprint_text(text), text))
    return _recurring(pairs, min_count=min_count, max_items=max_items)


def build_code_digest(
    path: str | Path, *, window: int = 25, min_count: int = 2, max_items: int = 8
) -> str:
    items = code_learnings(_load(path), window=window, min_count=min_count, max_items=max_items)
    if not items:
        return ""
    body = "\n".join(f"- {it}" for it in items)
    return (
        "## Learnings — do NOT repeat these compiler errors\n"
        "Each recurred across recent generations. Use the correct, current API "
        "instead; never emit code that would produce one of these:\n"
        f"{body}\n"
    )


def build_editorial_digest(
    path: str | Path, *, window: int = 25, min_count: int = 2, max_items: int = 6
) -> str:
    items = editorial_learnings(_load(path), window=window, min_count=min_count, max_items=max_items)
    if not items:
        return ""
    body = "\n".join(f"- {it}" for it in items)
    return (
        "## Learnings — recurring review critiques to avoid\n"
        "Reviewers flagged these repeatedly in recent articles; preempt them:\n"
        f"{body}\n"
    )
