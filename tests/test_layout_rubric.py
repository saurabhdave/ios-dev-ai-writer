"""Tests for the inline-code check in editor_agent.assess_medium_layout.

The article prompt requires inline ```swift snippets, but LLM compliance is
imperfect — the rubric must flag a missing inline snippet as a *required*
issue so the layout repair loop fixes it deterministically.
"""

from __future__ import annotations

import unittest

from agents.editor_agent import LAYOUT_MAX_SCORE, assess_medium_layout

_SNIPPET = """```swift
@Observable
final class CartModel {
    var items: [String] = []
}
```"""

# Satisfies every *required* rubric check except (possibly) inline code:
# hook intro, ≥4 H3s, blockquote, ≥3 numbered H2 sections, no duplicates.
_ARTICLE_TEMPLATE = """Swift concurrency keeps shipping sharp edges that surface in production. Here is what changed this cycle and why your team should care.

## 1. Why This Matters

### The Production Reality

Teams adopting Swift 6 strict concurrency hit data-race diagnostics in code that compiled silently for years. The fix is structural, not cosmetic.

> Inline code is the fastest way to make an abstract pattern concrete.

## 2. Adopting @Observable

### Migration Steps

Replace `ObservableObject` conformances incrementally, starting with leaf view models. Keep `@Published` removal in the same change set.

{snippet}

## 3. Tradeoffs And Pitfalls

### Known Failure Modes

- Bridging notifications into observation tracking
- Over-invalidating views from coarse-grained models

## 4. Validation And Observability

### Signals To Watch

Wire `OSSignposter` intervals around model mutation and watch hangs in Instruments. MetricKit confirms the fix in the field.

## Practical Checklist

### Ship Steps

- Migrate one leaf view model and profile before continuing
- Gate the rollout behind a remote flag

## Closing Takeaway

### Final Thought

Strict concurrency is a forcing function. Adopt it deliberately and the compiler becomes your fastest reviewer.
"""

_WITH_CODE = _ARTICLE_TEMPLATE.format(snippet=_SNIPPET)
_WITHOUT_CODE = _ARTICLE_TEMPLATE.format(snippet="The same idea expressed only in prose.")


class InlineCodeRubricTests(unittest.TestCase):
    def test_max_score_includes_inline_code_point(self):
        self.assertEqual(LAYOUT_MAX_SCORE, 15)
        self.assertEqual(assess_medium_layout(_WITH_CODE).max_score, 15)

    def test_article_with_inline_snippet_passes(self):
        assessment = assess_medium_layout(_WITH_CODE)
        self.assertFalse(any("```swift snippet" in issue for issue in assessment.issues))
        self.assertFalse(assessment.needs_repair)

    def test_missing_inline_snippet_is_a_required_issue(self):
        assessment = assess_medium_layout(_WITHOUT_CODE)
        flagged = [issue for issue in assessment.required_issues if "```swift snippet" in issue]
        self.assertEqual(len(flagged), 1)
        self.assertTrue(assessment.needs_repair)

    def test_with_code_scores_one_point_above_without(self):
        with_code = assess_medium_layout(_WITH_CODE).score
        without_code = assess_medium_layout(_WITHOUT_CODE).score
        self.assertEqual(with_code, without_code + 1)


class GateBannedApiRubricTests(unittest.TestCase):
    """The content repo's editorial gate deletes articles whose swift blocks
    contain legacy APIs — the rubric must flag them as required issues so the
    layout repair loop rewrites the snippet before publication."""

    def test_published_in_inline_code_is_required_issue(self):
        bad = _ARTICLE_TEMPLATE.format(
            snippet="```swift\nclass M: ObservableObject {\n    @Published var n = 0\n}\n```"
        )
        assessment = assess_medium_layout(bad)
        flagged = [i for i in assessment.required_issues if "legacy APIs" in i]
        self.assertEqual(len(flagged), 1)
        self.assertTrue(assessment.needs_repair)

    def test_modern_snippet_not_flagged(self):
        assessment = assess_medium_layout(_WITH_CODE)
        self.assertFalse(any("legacy APIs" in i for i in assessment.issues))

    def test_legacy_api_in_prose_is_allowed(self):
        # Only fenced code is gate-scanned; prose migration discussion is fine.
        prose = _ARTICLE_TEMPLATE.format(
            snippet=_SNIPPET
        ).replace(
            "Replace `ObservableObject` conformances incrementally",
            "Replace legacy `ObservableObject` and `@Published` usage incrementally",
        )
        assessment = assess_medium_layout(prose)
        self.assertFalse(any("legacy APIs" in i for i in assessment.issues))


if __name__ == "__main__":
    unittest.main()
