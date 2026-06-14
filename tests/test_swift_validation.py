"""Tests for stub-tolerant, multi-SDK Swift type-checking.

The type-checking tests need a macOS toolchain (swiftc + xcrun + SDKs) and are
skipped elsewhere — matching the existing swiftc-gated tests. The stub/no-op
behavior is tested without a toolchain via mocking.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agents import code_agent
from agents import swift_validation
from agents.swift_validation import toolchain_available, typecheck_snippet

_HAS_TOOLCHAIN = toolchain_available()


class ToolchainAbsentTests(unittest.TestCase):
    def test_typecheck_is_noop_without_swiftc(self) -> None:
        with mock.patch.object(swift_validation.shutil, "which", return_value=None):
            result = typecheck_snippet("func teardown(_ e: Entity) { e.destroy() }")
        self.assertFalse(result.available)
        self.assertTrue(result.ok)  # non-blocking when we cannot actually compile


@unittest.skipUnless(_HAS_TOOLCHAIN, "swiftc/xcrun toolchain not available")
class TypecheckTests(unittest.TestCase):
    def test_stub_references_are_tolerated(self) -> None:
        # `items` is undefined on purpose — an illustrative fragment must pass.
        result = typecheck_snippet("let total = items.reduce(0, +)")
        self.assertTrue(result.ok, msg=result.summary())

    def test_catches_wrong_ossignposter_label(self) -> None:
        code = (
            "import os\n"
            "func go(_ sp: OSSignposter, _ id: OSSignpostID) {\n"
            "    sp.endInterval(\"Op\", id: id)\n"
            "}\n"
        )
        result = typecheck_snippet(code)
        self.assertFalse(result.ok)
        self.assertTrue(any("id:" in e or "argument" in e for e in result.hard_errors))

    def test_catches_realitykit_entity_destroy(self) -> None:
        code = "import RealityKit\nfunc t(_ e: Entity) { e.destroy() }\n"
        result = typecheck_snippet(code)
        self.assertFalse(result.ok)
        self.assertTrue(any("destroy" in e for e in result.hard_errors))

    def test_routes_appkit_to_macos_sdk(self) -> None:
        # isAccessibilityElement is a method on AppKit; the `var` override is wrong.
        code = (
            "final class B: NSView {\n"
            "  override var isAccessibilityElement: Bool { true }\n"
            "}\n"
        )
        result = typecheck_snippet(code)
        self.assertEqual(result.sdk, "macosx")
        self.assertFalse(result.ok)

    def test_mainactor_actor_is_a_hard_error(self) -> None:
        result = typecheck_snippet("@MainActor actor Foo {}\n")
        self.assertFalse(result.ok)


@unittest.skipUnless(_HAS_TOOLCHAIN, "swiftc/xcrun toolchain not available")
class InlineSnippetTypecheckTests(unittest.TestCase):
    def test_semantic_api_misuse_in_inline_block_is_reported(self) -> None:
        article = (
            "Intro.\n\n```swift\nimport os\n"
            "func go(_ sp: OSSignposter, _ id: OSSignpostID) {\n"
            "    sp.endInterval(\"Op\", id: id)\n}\n```\n"
        )
        _, issues = code_agent.validate_inline_snippets(article)  # repair off
        self.assertEqual(len(issues), 1)
        self.assertIn("inline block", issues[0])

    def test_stub_heavy_inline_block_is_clean(self) -> None:
        article = "Intro.\n\n```swift\nlet total = items.reduce(0, +)\n```\n"
        _, issues = code_agent.validate_inline_snippets(article)
        self.assertEqual(issues, [])

    def test_unrepairable_block_is_stripped_when_repairing(self) -> None:
        # A real semantic error (wrong OSSignposter label); force repair to fail
        # so the strip guarantee (Option B) is exercised deterministically.
        article = (
            "Intro.\n\n```swift\nimport os\n"
            "func go(_ sp: OSSignposter, _ id: OSSignpostID) {\n"
            "    sp.endInterval(\"Op\", id: id)\n}\n```\n"
        )
        with mock.patch.object(code_agent, "_repair_inline_block", return_value=""):
            fixed, issues = code_agent.validate_inline_snippets(
                article, repair=True, topic="t"
            )
        self.assertNotIn("```swift", fixed)   # broken block removed
        self.assertNotIn("endInterval", fixed)
        self.assertEqual(len(issues), 1)      # still recorded for observability

    def test_unrepairable_block_kept_when_not_repairing(self) -> None:
        # Detection-only (repair=False) must not strip — preserves prior behavior.
        article = (
            "Intro.\n\n```swift\nimport os\n"
            "func go(_ sp: OSSignposter, _ id: OSSignpostID) {\n"
            "    sp.endInterval(\"Op\", id: id)\n}\n```\n"
        )
        fixed, issues = code_agent.validate_inline_snippets(article)
        self.assertIn("endInterval", fixed)   # block kept
        self.assertEqual(len(issues), 1)


if __name__ == "__main__":
    unittest.main()
