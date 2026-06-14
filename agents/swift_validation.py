"""Stub-tolerant, multi-SDK Swift type-checking for generated snippets.

WHY THIS EXISTS
---------------
``code_agent`` historically validated snippets with ``swiftc -frontend -parse``
(syntax only) or a single iOS-SDK ``-typecheck``. Syntax-only validation cannot
catch *semantic* API misuse — wrong argument labels, nonexistent members,
illegal declarations (``@MainActor actor``), invented types (``Task.Handle``) —
which is exactly the class of hallucination that reached published articles.

This module adds real type-checking that is usable on the article's inline
snippets, which are deliberately incomplete:

* **Stub tolerance** — snippets reference undefined helper symbols on purpose, so
  "cannot find … in scope" diagnostics (and cascades that mention a stub name)
  are ignored. What survives is genuine API misuse.
* **Import preamble** — an SDK-appropriate set of imports is injected so an
  import-less fragment resolves its framework types (without pulling in
  conflicting modules — e.g. RealityKit would make SwiftUI's ``Scene`` ambiguous).
* **Multi-SDK routing** — a block is routed to the iOS, macOS, or watchOS SDK
  based on the frameworks it references, so AppKit / HealthKit code is not
  falsely failed against the iOS SDK.
* **Hard vs. concurrency** — actor-isolation / global-mutable-state diagnostics
  are mode-sensitive (Swift 5 vs 6) and reported separately so callers can choose
  not to block on them.

Everything degrades to "valid" when ``swiftc``/``xcrun`` or an SDK is missing
(e.g. a non-macOS CI runner), matching the rest of ``code_agent``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from config import SWIFT_COMPILER_LANGUAGE_MODE

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

TYPECHECK_TIMEOUT: Final[int] = 25
DIAG_MAX_CHARS: Final[int] = 760

# Per-SDK compile targets (mirror the iOS target already used by code_agent).
_TARGETS: Final[dict[str, str]] = {
    "iphonesimulator": "arm64-apple-ios18.0-simulator",
    "macosx": "arm64-apple-macos14.0",
    "watchsimulator": "arm64-apple-watchos11.0-simulator",
}

# Always-safe base imports + framework signals (inject only what the block needs,
# so we never pull in a module that makes a common symbol ambiguous).
_BASE_IMPORTS: Final[tuple[str, ...]] = (
    "Foundation", "Combine", "OSLog", "os", "Observation", "CoreGraphics",
)
_FRAMEWORK_SIGNALS: Final[list[tuple[str, re.Pattern[str]]]] = [
    ("SwiftUI", re.compile(r"\b(View|Text|VStack|HStack|ZStack|some View|@State|@Environment|"
                           r"EnvironmentKey|EnvironmentValues|ViewModifier|Layout|Scene|App|"
                           r"NavigationStack|ContentSizeCategory|ProposedViewSize)\b")),
    ("UIKit", re.compile(r"\bUI[A-Z]\w+")),
    ("AppKit", re.compile(r"\bNS(View|Accessibility|Color|App|Window|ViewController|Image|"
                          r"ViewRepresentable)\w*")),
    ("RealityKit", re.compile(r"\b(RealityKit|ModelEntity|AnchorEntity|MeshResource|ARView)\b"
                              r"|\bEntity\b")),
    ("AppIntents", re.compile(r"\b(AppIntent|IntentResult|AppEnum|ParameterSummary|"
                              r"LocalizedStringResource|ReturnsValue|@Parameter)\b")),
    ("WidgetKit", re.compile(r"\b(WidgetCenter|TimelineProvider|TimelineEntry|Widget)\b")),
    ("HealthKit", re.compile(r"\bHK[A-Z]\w+")),
    ("CryptoKit", re.compile(r"\b(CryptoKit|SHA256|SHA512|HMAC|SymmetricKey)\b")),
]
_SDK_AVAILABLE: Final[dict[str, set[str]]] = {
    "iphonesimulator": {"SwiftUI", "UIKit", "RealityKit", "AppIntents", "WidgetKit", "CryptoKit"},
    "macosx": {"SwiftUI", "AppKit", "RealityKit", "AppIntents", "WidgetKit", "CryptoKit"},
    "watchsimulator": {"SwiftUI", "AppIntents", "WidgetKit", "CryptoKit", "HealthKit"},
}

# Diagnostics that mean "you referenced an undefined helper" — expected for
# illustrative snippets, so they (and cascades naming them) are dropped.
_STUB_DIAG_RE: Final[re.Pattern[str]] = re.compile(
    r"cannot find (?:type |protocol )?'([^']+)' in scope"
    r"|use of unresolved identifier '([^']+)'"
)
_ERROR_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<file>[^:]+):\d+:\d+: error: (?P<msg>.*)$"
)
# Mode-sensitive concurrency diagnostics (reported, not necessarily failing).
# Deliberately excludes "global actor" so the @MainActor-on-actor *declaration*
# error stays a hard error.
_CONCURRENCY_DIAG_RE: Final[re.Pattern[str]] = re.compile(
    r"#ActorIsolatedCall|#MutableGlobalVariable|#SendableClosureCaptures"
    r"|actor-isolated|concurrency-safe|nonisolated|is not concurrent"
    r"|non-Sendable|: 'Sendable'|isolated conformance",
    re.IGNORECASE,
)
_UNSUPPORTED_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"unknown argument.*-swift-version|invalid value.*-swift-version", re.IGNORECASE
)
_MAIN_ATTR_RE: Final[re.Pattern[str]] = re.compile(r"^\s*@main\b", re.MULTILINE)

_PLATFORM_DIR: Final[dict[str, str]] = {
    "iphonesimulator": "iPhoneSimulator",
    "watchsimulator": "WatchSimulator",
    "macosx": "MacOSX",
}


@dataclass
class TypecheckResult:
    """Outcome of a stub-tolerant type-check."""
    available: bool                       # was swiftc + an SDK actually usable?
    hard_errors: list[str] = field(default_factory=list)
    concurrency: list[str] = field(default_factory=list)
    sdk: str = ""

    @property
    def ok(self) -> bool:
        """Valid when no hard errors (or when the checker could not run)."""
        return not self.hard_errors

    def summary(self) -> str:
        return "\n".join(self.hard_errors)[-DIAG_MAX_CHARS:]


# ---------------------------------------------------------------------------
# Toolchain discovery (cached)
# ---------------------------------------------------------------------------

_SDK_PATH_CACHE: dict[str, str | None] = {}
_FW_DIR_CACHE: dict[str, str | None] = {}


def _platform_fw_dir(xcrun: str, sdk: str) -> str | None:
    """Developer framework dir holding XCTest for *sdk* (for ``import XCTest``)."""
    if sdk in _FW_DIR_CACHE:
        return _FW_DIR_CACHE[sdk]
    plat = _PLATFORM_DIR.get(sdk)
    found: str | None = None
    if plat:
        try:
            dev = subprocess.check_output(
                [xcrun, "xcode-select", "-p"], text=True, timeout=20
            ).strip()
        except Exception:
            try:
                dev = subprocess.check_output(["xcode-select", "-p"], text=True, timeout=20).strip()
            except Exception:
                dev = ""
        if dev:
            cand = Path(dev) / "Platforms" / f"{plat}.platform" / "Developer" / "Library" / "Frameworks"
            if cand.is_dir():
                found = str(cand)
    _FW_DIR_CACHE[sdk] = found
    return found


def _sdk_path(xcrun: str, sdk: str) -> str | None:
    if sdk not in _SDK_PATH_CACHE:
        try:
            out = subprocess.check_output(
                [xcrun, "--sdk", sdk, "--show-sdk-path"], text=True, timeout=20
            ).strip()
            _SDK_PATH_CACHE[sdk] = out or None
        except Exception:
            _SDK_PATH_CACHE[sdk] = None
    return _SDK_PATH_CACHE[sdk]


def _signaled_frameworks(code: str) -> set[str]:
    return {name for name, rx in _FRAMEWORK_SIGNALS if rx.search(code)}


def _route_sdk(signals: set[str]) -> str:
    if "HealthKit" in signals:
        return "watchsimulator"
    if "AppKit" in signals:
        return "macosx"
    return "iphonesimulator"  # UIKit / SwiftUI / RealityKit / AppIntents / default


def _existing_imports(code: str) -> set[str]:
    return set(re.findall(
        r"^\s*(?:@_implementationOnly\s+|public\s+|internal\s+|fileprivate\s+|private\s+)?"
        r"import\s+(\w+)",
        code, re.MULTILINE,
    ))


def _preamble(code: str, sdk: str, signals: set[str]) -> str:
    already = _existing_imports(code)
    mods = [m for m in _BASE_IMPORTS if m not in already]
    for fw in ("SwiftUI", "UIKit", "AppKit", "RealityKit", "AppIntents",
               "WidgetKit", "HealthKit", "CryptoKit"):
        if fw in signals and fw in _SDK_AVAILABLE.get(sdk, set()) and fw not in already:
            mods.append(fw)
    seen: list[str] = []
    for m in mods:
        if m not in seen:
            seen.append(m)
    return "".join(f"import {m}\n" for m in seen)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def toolchain_available() -> bool:
    """True when swiftc and xcrun are both on PATH (i.e. a macOS runner)."""
    return bool(shutil.which("swiftc") and shutil.which("xcrun"))


def typecheck_snippet(code: str) -> TypecheckResult:
    """Stub-tolerantly type-check one Swift block against the right SDK.

    Returns a TypecheckResult. When swiftc/xcrun or the SDK is unavailable, the
    result has ``available=False`` and ``ok=True`` (non-blocking), matching the
    rest of code_agent's CI-safe behavior.
    """
    swiftc = shutil.which("swiftc")
    xcrun = shutil.which("xcrun")
    if not (code.strip() and swiftc and xcrun):
        return TypecheckResult(available=False)

    signals = _signaled_frameworks(code)
    sdk = _route_sdk(signals)
    sdk_path = _sdk_path(xcrun, sdk)
    if not sdk_path:
        return TypecheckResult(available=False, sdk=sdk)

    source = _preamble(code, sdk, signals) + "\n" + code
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "Snippet.swift"
        src.write_text(source, encoding="utf-8")
        module_cache = Path(tmp) / "ModuleCache"

        # A block declaring @main is a whole-program entry point; XCTest blocks
        # need the platform framework search path so `import XCTest` resolves.
        extra: list[str] = []
        if _MAIN_ATTR_RE.search(code):
            extra.append("-parse-as-library")
        if "xctest" in code.lower():
            fw = _platform_fw_dir(xcrun, sdk)
            if fw:
                extra += ["-F", fw, "-I", fw]

        def _run(with_version: bool) -> tuple[int, str]:
            version_args = (
                ["-swift-version", SWIFT_COMPILER_LANGUAGE_MODE]
                if with_version and SWIFT_COMPILER_LANGUAGE_MODE
                else []
            )
            cmd = [
                swiftc, *version_args, "-typecheck",
                "-target", _TARGETS[sdk], "-sdk", sdk_path,
                "-module-cache-path", str(module_cache), *extra, str(src),
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=TYPECHECK_TIMEOUT, check=False,
                )
                return proc.returncode, proc.stderr
            except subprocess.TimeoutExpired:
                return -1, "type-check timed out"
            except OSError as exc:
                return -1, f"type-check OSError: {exc}"

        rc, stderr = _run(with_version=True)
        if rc != 0 and _UNSUPPORTED_VERSION_RE.search(stderr):
            rc, stderr = _run(with_version=False)

    return _classify(stderr, sdk)


def _classify(stderr: str, sdk: str) -> TypecheckResult:
    diags = stderr.splitlines()
    stub_names: set[str] = set()
    for line in diags:
        m = _STUB_DIAG_RE.search(line)
        if m:
            stub_names.update(n for n in m.groups() if n)

    hard: list[str] = []
    concurrency: list[str] = []
    for line in diags:
        em = _ERROR_LINE_RE.match(line.strip())
        if not em:
            continue
        msg = em.group("msg")
        if _STUB_DIAG_RE.search(line):
            continue  # the stub declaration itself
        if any(re.search(rf"'{re.escape(n)}'", msg) for n in stub_names):
            continue  # cascade referencing a stub
        if _CONCURRENCY_DIAG_RE.search(msg):
            concurrency.append(line.strip())
        else:
            hard.append(line.strip())

    return TypecheckResult(available=True, hard_errors=hard, concurrency=concurrency, sdk=sdk)
