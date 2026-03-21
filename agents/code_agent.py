"""Code agent: generate practical Swift/SwiftUI code examples for a topic.

Design principles
-----------------
- All constants are typed ``Final`` and colocated at the top of the module.
- Compiler invocation is fully encapsulated: command-building helpers return
  plain ``list[str]``; no subprocess logic leaks into business logic.
- The repair loop is data-driven: each pass collects syntax, style, and
  brace diagnostics in one place before delegating to ``_repair_code``.
- Diagnostic truncation lengths are named constants, not magic numbers.
- Every log call uses structured key=value pairs for aggregator ingestion.
- Public surface: ``generate_code`` (str) and ``generate_code_with_metadata``
  (``CodeGenerationResult``) — backward-compatible.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from config import (
    CODEGEN_FAILURE_MODE,
    CODEGEN_VALIDATION_MODE,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    SWIFT_COMPILER_LANGUAGE_MODE,
    SWIFT_LANGUAGE_VERSION,
    openai_generation_kwargs,
)
from utils.observability import get_logger, log_event
from utils.openai_logging import create_openai_client, responses_create_logged

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROMPT_PATH: Final[Path] = Path("prompts/code_prompt.txt")
SWIFT_BOOK_URL: Final[str] = (
    "https://docs.swift.org/swift-book/documentation/"
    "the-swift-programming-language/aboutswift/"
)
IOS_SIMULATOR_TARGET: Final[str] = "arm64-apple-ios18.0-simulator"

MAX_REPAIR_ATTEMPTS: Final[int] = 2
ARTICLE_EXCERPT_MAX_CHARS: Final[int] = 1_200

# Maximum characters of diagnostics forwarded to the model in a repair prompt.
REPAIR_DIAG_MAX_CHARS: Final[int] = 2_500
# Maximum characters of diagnostics stored in CodeGenerationResult.
RESULT_DIAG_MAX_CHARS: Final[int] = 760
ADVISORY_DIAG_MAX_CHARS: Final[int] = 420
STYLE_DIAG_MAX_CHARS: Final[int] = 360
UNKNOWN_SYMBOL_MAX_LINES: Final[int] = 12

# Max output tokens for generation vs. repair calls.
CODEGEN_MAX_TOKENS: Final[int] = 1_200
REPAIR_MAX_TOKENS: Final[int] = 1_400

REPAIR_TEMPERATURE: Final[float] = 0.35

VALID_FAILURE_MODES: Final[frozenset[str]] = frozenset({"omit", "error"})
VALID_VALIDATION_MODES: Final[frozenset[str]] = frozenset({"snippet", "compile", "none"})

# Subprocess timeouts (seconds).
TYPECHECK_TIMEOUT: Final[int] = 22
PARSE_TIMEOUT: Final[int] = 12
SDK_QUERY_TIMEOUT: Final[int] = 10

LOGGER = get_logger("pipeline.code")

# ---------------------------------------------------------------------------
# Regex patterns — compiled once
# ---------------------------------------------------------------------------

_IMPL_SECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"###\s+Implementation Pattern\b", re.IGNORECASE
)
_CODE_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"^```[^\n]*\n?", re.MULTILINE)
_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(r"<#[^#]*#>")

# Matches the intentional legacy "// ❌ Before" block so style checks skip it.
_BEFORE_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"//\s*[❌✗xX]\s*Before.*?(?=//\s*[✅✓]\s*After|$)",
    re.IGNORECASE | re.DOTALL,
)

# Swift compiler diagnostic patterns that signal unknown/undefined symbols.
_UNKNOWN_SYMBOL_PATTERNS: Final[tuple[str, ...]] = (
    r"error: cannot find '[^']+' in scope",
    r"error: cannot find type '[^']+' in scope",
    r"error: use of unresolved identifier '[^']+'",
    r"error: value of type '[^']+' has no member '[^']+'",
    r"error: type '[^']+' has no member '[^']+'",
)

# Compiler output pattern that signals the local toolchain doesn't support
# the configured -swift-version flag.
_UNSUPPORTED_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"invalid value '[^']+' in '-swift-version"
)

# Swift 6 observation wrappers that should not appear in the modern ("After") block.
_LEGACY_OBSERVATION_PATTERNS: Final[tuple[str, ...]] = (
    r"\bObservableObject\b",
    r"@Published\b",
    r"@StateObject\b",
    r"@ObservedObject\b",
    r"@EnvironmentObject\b",
)

# @Bindable used inside @Observable class body — a common model misuse.
_BINDABLE_IN_OBSERVABLE_RE: Final[re.Pattern[str]] = re.compile(
    r"@Observable[\s\S]*?@Bindable\s+var\b"
)
_BINDABLE_VAR_RE: Final[re.Pattern[str]] = re.compile(r"@Bindable\s+var\s+(\w+)")

# Brace pairs to balance-check.
_BRACE_PAIRS: Final[dict[str, str]] = {"(": ")", "[": "]", "{": "}"}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CodeGenerationResult:
    """Structured output of the code generation pipeline."""

    code: str
    path: str                   # "direct" | "repaired" | "omitted"
    repair_attempts: int
    diagnostics: str = field(default="")

# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def _load_prompt_template(path: Path = PROMPT_PATH) -> str:
    """Return the code generation prompt template.

    Raises
    ------
    FileNotFoundError
        When the template file is absent — surfaces misconfiguration early.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Code prompt template not found at '{path}'. "
            "Verify PROMPT_PATH or the process working directory."
        )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Article excerpt extraction
# ---------------------------------------------------------------------------


def _article_excerpt(body: str, max_chars: int = ARTICLE_EXCERPT_MAX_CHARS) -> str:
    """Extract the most code-relevant section from the article body.

    Prefers ``### Implementation Pattern`` sections; falls back to the
    first ``max_chars`` characters of the whole body.
    """
    sections: list[str] = []
    for match in _IMPL_SECTION_RE.finditer(body):
        start = match.start()
        next_heading = re.search(r"\n##", body[start + 1:])
        end = start + 1 + next_heading.start() if next_heading else len(body)
        sections.append(body[start:end].strip())

    if sections:
        return "\n\n".join(sections)[:max_chars]
    return body[:max_chars]


# ---------------------------------------------------------------------------
# Code cleaning
# ---------------------------------------------------------------------------


def _clean_generated_code(raw: str) -> str:
    """Normalise model output to plain Swift code.

    Removes leading/trailing fenced code block markers if present.
    """
    code = raw.strip()
    if not code.startswith("```"):
        return code

    lines = code.splitlines()
    # Strip opening fence (```swift or ```)
    start = 1
    # Strip closing fence if present
    end = len(lines) - 1 if lines and lines[-1].strip() == "```" else len(lines)
    return "\n".join(lines[start:end]).strip()


# ---------------------------------------------------------------------------
# Swift compiler helpers
# ---------------------------------------------------------------------------


def _swift_version_args() -> list[str]:
    """Return the -swift-version flag args, or empty list if not configured."""
    mode = SWIFT_COMPILER_LANGUAGE_MODE.strip()
    return ["-swift-version", mode] if mode else []


def _build_parse_command(swiftc: str, source: Path, *, with_version: bool = True) -> list[str]:
    """Build a ``swiftc -frontend -parse`` command."""
    version_args = _swift_version_args() if with_version else []
    return [swiftc, "-frontend", *version_args, "-parse", str(source)]


def _build_typecheck_command(
    swiftc: str,
    source: Path,
    sdk_path: str,
    module_cache: Path,
    *,
    with_version: bool = True,
) -> list[str]:
    """Build a ``swiftc -typecheck`` command targeting the iOS simulator SDK."""
    version_args = _swift_version_args() if with_version else []
    return [
        swiftc,
        *version_args,
        "-typecheck",
        "-target", IOS_SIMULATOR_TARGET,
        "-sdk", sdk_path,
        "-module-cache-path", str(module_cache),
        str(source),
    ]


def _is_unsupported_version_error(diagnostics: str) -> bool:
    """Return True when the local toolchain rejects the configured -swift-version."""
    return bool(_UNSUPPORTED_VERSION_RE.search(diagnostics))


def _run_swiftc(command: list[str], timeout: int) -> tuple[int, str]:
    """Execute a swiftc command and return (returncode, stderr).

    Returns (-1, error_message) on timeout or OSError so callers get a
    uniform tuple rather than an exception to handle.
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, f"swiftc timed out after {timeout}s"
    except OSError as exc:
        return -1, f"swiftc OSError: {exc}"


def _swift_compile_validate(code: str) -> tuple[bool, str]:
    """Typecheck *code* against the iOS simulator SDK when available.

    Falls back to parse-only validation when ``xcrun`` or the SDK is absent.
    Returns ``(True, "")`` when ``swiftc`` is not on PATH (CI environments).
    """
    if not code.strip():
        return False, "Generated code is empty."

    swiftc = shutil.which("swiftc")
    if not swiftc:
        return True, ""

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "GeneratedArticleCode.swift"
        source.write_text(code, encoding="utf-8")
        module_cache = Path(tmp) / "ModuleCache"

        xcrun = shutil.which("xcrun")
        if xcrun:
            rc, sdk_path = _run_swiftc(
                [xcrun, "--sdk", "iphonesimulator", "--show-sdk-path"],
                timeout=SDK_QUERY_TIMEOUT,
            )
            if rc == 0 and sdk_path:
                rc, stderr = _run_swiftc(
                    _build_typecheck_command(swiftc, source, sdk_path, module_cache),
                    timeout=TYPECHECK_TIMEOUT,
                )
                if rc != 0 and _is_unsupported_version_error(stderr):
                    rc, stderr = _run_swiftc(
                        _build_typecheck_command(
                            swiftc, source, sdk_path, module_cache, with_version=False
                        ),
                        timeout=TYPECHECK_TIMEOUT,
                    )
                return rc == 0, stderr[-RESULT_DIAG_MAX_CHARS:]

        # xcrun unavailable or SDK query failed — fall back to parse-only.
        return _swift_parse_validate(code)


def _swift_parse_validate(code: str) -> tuple[bool, str]:
    """Syntax-only validation: no type resolution, no SDK required."""
    if not code.strip():
        return False, "Generated code is empty."

    if _PLACEHOLDER_RE.search(code):
        return False, "Snippet contains unresolved Xcode placeholders (<# … #>)."

    swiftc = shutil.which("swiftc")
    if not swiftc:
        return True, ""

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "GeneratedArticleCode.swift"
        source.write_text(code, encoding="utf-8")

        rc, stderr = _run_swiftc(
            _build_parse_command(swiftc, source),
            timeout=PARSE_TIMEOUT,
        )
        if rc != 0 and _is_unsupported_version_error(stderr):
            rc, stderr = _run_swiftc(
                _build_parse_command(swiftc, source, with_version=False),
                timeout=PARSE_TIMEOUT,
            )
        return rc == 0, stderr[-RESULT_DIAG_MAX_CHARS:]


# ---------------------------------------------------------------------------
# Validation dispatch
# ---------------------------------------------------------------------------


def _resolve_validation_mode() -> str:
    """Return the effective validation mode, defaulting to 'snippet' on invalid config."""
    mode = CODEGEN_VALIDATION_MODE.strip().lower()
    if mode not in VALID_VALIDATION_MODES:
        LOGGER.warning("invalid_validation_mode mode=%r defaulting_to=snippet", mode)
        return "snippet"
    return mode


def _validate_code(code: str, mode: str) -> tuple[bool, str]:
    """Dispatch to the appropriate validator for *mode*."""
    if mode == "none":
        return True, "Validation skipped (CODEGEN_VALIDATION_MODE=none)."
    if mode == "compile":
        return _swift_compile_validate(code)
    return _swift_parse_validate(code)


# ---------------------------------------------------------------------------
# Diagnostic helpers
# ---------------------------------------------------------------------------


def _extract_unknown_symbol_lines(diagnostics: str) -> str:
    """Return deduplicated unknown-symbol error lines from compiler output."""
    if not diagnostics.strip():
        return ""

    matched: list[str] = []
    for line in diagnostics.splitlines():
        stripped = line.strip()
        if any(re.search(p, stripped) for p in _UNKNOWN_SYMBOL_PATTERNS):
            matched.append(stripped)

    # Preserve order while deduplicating.
    seen: set[str] = set()
    unique: list[str] = []
    for item in matched:
        if item not in seen:
            seen.add(item)
            unique.append(item)

    return "\n".join(unique[:UNKNOWN_SYMBOL_MAX_LINES])


def _observation_style_issues(code: str) -> str:
    """Detect Swift 6 observation anti-patterns in the modern ("After") block.

    The intentional legacy block is stripped first to avoid false positives
    on before/after comparison snippets.
    """
    if not code.strip():
        return ""

    modern_code = _BEFORE_BLOCK_RE.sub("", code)
    issues: list[str] = []

    if any(re.search(p, modern_code) for p in _LEGACY_OBSERVATION_PATTERNS):
        issues.append(
            "Avoid legacy observation wrappers (`ObservableObject`, `@Published`, "
            "`@StateObject`, `@ObservedObject`, `@EnvironmentObject`) in Swift 6+ snippets."
        )

    if _BINDABLE_IN_OBSERVABLE_RE.search(modern_code):
        names = ", ".join(
            f"`{n}`" for n in _BINDABLE_VAR_RE.findall(modern_code)[:5]
        ) or "one or more properties"
        issues.append(
            f"Remove `@Bindable` from model properties ({names}) inside `@Observable`. "
            "Plain stored properties belong in the model; "
            "`@Bindable` belongs only in the SwiftUI View: "
            "`struct MyView: View {{ @Bindable var model: MyModel }}`."
        )

    return "\n".join(dict.fromkeys(issues))  # dict.fromkeys preserves order, deduplicates


def _brace_balance_issues(code: str) -> str:
    """Return a diagnostic when any brace/bracket/paren pair is unbalanced."""
    issues: list[str] = []
    for open_ch, close_ch in _BRACE_PAIRS.items():
        opens = code.count(open_ch)
        closes = code.count(close_ch)
        if opens != closes:
            diff = opens - closes
            direction = "extra opens" if diff > 0 else "extra closes"
            issues.append(
                f"Unbalanced `{open_ch}{close_ch}`: "
                f"{opens} opens vs {closes} closes ({abs(diff)} {direction})."
            )
    return "\n".join(issues)


def _advisory_unknown_api_issues(code: str) -> str:
    """Run a compile-mode advisory pass to surface unknown symbol usage.

    Only called after the snippet already passes its primary validation so
    the result is advisory, not blocking.
    """
    is_valid, diagnostics = _swift_compile_validate(code)
    return "" if is_valid else _extract_unknown_symbol_lines(diagnostics)


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

_REPAIR_PROMPT_TEMPLATE: Final[str] = """\
You are a senior iOS engineer fixing Swift code.

Topic:
{topic}

Compiler / style diagnostics:
{diagnostics}

Current code:
{code}

Requirements:
- Return only fixed Swift code — no markdown fences, no prose.
- Validation target: {validation_goal}
- Target Swift language version: {swift_version}
- Minimum deployment target: iOS 18 / Swift 6. Do not use any API unavailable on iOS 18.
- Ensure code is valid for `swiftc -swift-version {swift_mode}`.
- Swift language reference: {swift_book_url}
- Include all required imports.
- NEVER use deprecated APIs. If an API is deprecated in iOS 18 / Swift 6, use its
  modern replacement unconditionally — not inside a // ❌ Before block, not at all.
- Prefer Swift Observation in Swift 6+ (`@Observable` + `import Observation`).
- Avoid `ObservableObject`, `@Published`, `@StateObject`, `@ObservedObject`, and
  `@EnvironmentObject` unless this snippet is explicitly about legacy migration.
- For owned observable instances in SwiftUI views, prefer `@State`.
- Use `@Bindable` ONLY in SwiftUI Views where `$` bindings are needed.
- CRITICAL `@Bindable` fix:
    WRONG:   @Observable class M {{ @Bindable var x: Int = 0 }}
    CORRECT: @Observable class M {{ var x: Int = 0 }}
             struct V: View {{ @Bindable var m: M }}
- Count all `{{`, `}}`, `[`, `]`, `(`, `)` pairs and ensure they are balanced.
- If the snippet exceeds 35 lines or has deeply nested closures, SIMPLIFY to the
  core concept — shorter and correct beats longer and broken.
- For Swift 6 concurrency / sendability errors: add `@MainActor` to the type or
  eliminate async mutation of state properties.
- Do not reference undefined placeholder types unless you define them.
- Remove any unresolved Xcode placeholders (<# … #>).
- Prefer focused snippets over full app entry points.
- Keep inline comments concise and meaningful.
"""


def _build_repair_diagnostics(
    compiler_diag: str,
    style_diag: str,
    brace_diag: str,
) -> str:
    """Combine all diagnostic categories into one repair prompt payload."""
    parts: list[str] = []
    if compiler_diag:
        parts.append(f"Compiler diagnostics:\n{compiler_diag[-REPAIR_DIAG_MAX_CHARS:]}")
    if style_diag:
        parts.append(f"Observation style issues:\n{style_diag}")
    if brace_diag:
        parts.append(f"Brace balance issues:\n{brace_diag}")
    return "\n\n".join(parts) or "No diagnostics captured."


def _repair_code(
    client: object,
    *,
    topic: str,
    code: str,
    compiler_diag: str,
    style_diag: str,
    brace_diag: str,
    validation_mode: str,
) -> str:
    """Ask the model to fix validation failures; return cleaned code."""
    validation_goal = (
        "compile cleanly under `swiftc -typecheck`."
        if validation_mode == "compile"
        else "be syntactically valid and usable as a standalone article snippet."
    )
    combined_diag = _build_repair_diagnostics(compiler_diag, style_diag, brace_diag)

    prompt = _REPAIR_PROMPT_TEMPLATE.format(
        topic=topic,
        diagnostics=combined_diag,
        code=code,
        validation_goal=validation_goal,
        swift_version=SWIFT_LANGUAGE_VERSION,
        swift_mode=SWIFT_COMPILER_LANGUAGE_MODE,
        swift_book_url=SWIFT_BOOK_URL,
    )

    response = responses_create_logged(
        client,
        agent_name="code_agent",
        operation="repair_code",
        model=OPENAI_MODEL,
        max_output_tokens=REPAIR_MAX_TOKENS,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, REPAIR_TEMPERATURE)),
    )
    return _clean_generated_code(response.output_text)


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

# Sentinel used to safely inject article context (which may contain Swift braces)
# into a prompt template without escaping conflicts with str.format().
_CTX_SENTINEL: Final[str] = "__ARTICLE_CTX__"


def _build_prompt(template: str, topic: str, article_context: str) -> str:
    """Substitute template variables safely using plain str.replace().

    Avoids str.format() entirely so Swift code examples in the template
    (e.g. ``{ @Published var count = 0 }``) are never misinterpreted
    as format-string placeholders.
    """
    return (
        template
        .replace("{topic}", topic)
        .replace("{article_context}", article_context)
        .replace("{swift_language_version}", SWIFT_LANGUAGE_VERSION)
        .replace("{swift_language_mode}", SWIFT_COMPILER_LANGUAGE_MODE)
    )


def _make_article_context(article_body: str) -> str:
    """Build the article_context block for prompt injection."""
    if not article_body.strip():
        return ""
    excerpt = _article_excerpt(article_body)
    return (
        "Article context — implement one of the concrete patterns described below:\n"
        f"{excerpt}\n\n"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_code_with_metadata(topic: str, article_body: str = "") -> CodeGenerationResult:
    """Generate Swift code and return full validation metadata.

    Parameters
    ----------
    topic:
        The article topic used to drive code generation.
    article_body:
        Optional article body; an implementation-pattern excerpt is injected
        into the prompt to ground the generated snippet.

    Returns
    -------
    CodeGenerationResult
        Contains the final code (may be empty if omitted), the generation
        path, repair attempt count, and diagnostic summary.

    Raises
    ------
    FileNotFoundError
        When the prompt template file is missing.
    RuntimeError
        When ``CODEGEN_FAILURE_MODE=error`` and the snippet cannot be fixed.
    """
    client = create_openai_client()
    template = _load_prompt_template()
    prompt = _build_prompt(template, topic, _make_article_context(article_body))
    validation_mode = _resolve_validation_mode()

    # --- Initial generation ---
    response = responses_create_logged(
        client,
        agent_name="code_agent",
        operation="generate_code",
        model=OPENAI_MODEL,
        max_output_tokens=CODEGEN_MAX_TOKENS,
        input=prompt,
        **openai_generation_kwargs(OPENAI_TEMPERATURE),
    )
    code = _clean_generated_code(response.output_text)
    if not code:
        raise RuntimeError(
            f"Code generation returned empty output for topic={topic!r}."
        )

    # --- Repair loop ---
    is_valid, compiler_diag = _validate_code(code, validation_mode)
    style_diag = _observation_style_issues(code)
    attempts = 0
    path = "direct"

    while (not is_valid or style_diag) and attempts < MAX_REPAIR_ATTEMPTS:
        brace_diag = _brace_balance_issues(code)
        log_event(
            LOGGER,
            "code_repair_requested",
            level=logging.WARNING,
            topic=topic,
            attempt=attempts + 1,
            validation_mode=validation_mode,
            has_compiler_diag=bool(compiler_diag),
            has_style_diag=bool(style_diag),
            has_brace_diag=bool(brace_diag),
        )
        repaired = _repair_code(
            client,
            topic=topic,
            code=code,
            compiler_diag=compiler_diag,
            style_diag=style_diag,
            brace_diag=brace_diag,
            validation_mode=validation_mode,
        )
        if not repaired:
            log_event(LOGGER, "code_repair_empty", level=logging.WARNING, topic=topic, attempt=attempts + 1)
            break
        code = repaired
        is_valid, compiler_diag = _validate_code(code, validation_mode)
        style_diag = _observation_style_issues(code)
        attempts += 1
        path = "repaired"

    # --- Advisory unknown-symbol pass (non-blocking) ---
    advisory_diag = ""
    if is_valid and validation_mode != "none":
        advisory_diag = _advisory_unknown_api_issues(code)
        if advisory_diag and attempts < MAX_REPAIR_ATTEMPTS:
            log_event(
                LOGGER,
                "code_unknown_api_repair_requested",
                level=logging.WARNING,
                topic=topic,
                attempt=attempts + 1,
                diag_excerpt=advisory_diag[-500:],
            )
            repaired = _repair_code(
                client,
                topic=topic,
                code=code,
                compiler_diag=f"Unknown symbol/API diagnostics:\n{advisory_diag}",
                style_diag="",
                brace_diag="",
                validation_mode=validation_mode,
            )
            if repaired:
                code = repaired
                is_valid, compiler_diag = _validate_code(code, validation_mode)
                attempts += 1
                path = "repaired"
                if is_valid:
                    advisory_diag = _advisory_unknown_api_issues(code)

    # --- Build result ---
    if is_valid and not style_diag:
        advisory_suffix = (
            f" | advisory_symbols={advisory_diag[:ADVISORY_DIAG_MAX_CHARS]}"
            if advisory_diag
            else ""
        )
        log_event(
            LOGGER,
            "code_generation_succeeded",
            level=logging.INFO,
            topic=topic,
            path=path,
            repair_attempts=attempts,
            has_advisory=bool(advisory_diag),
        )
        return CodeGenerationResult(
            code=code,
            path=path,
            repair_attempts=attempts,
            diagnostics=(
                f"[{validation_mode}] {compiler_diag[-RESULT_DIAG_MAX_CHARS:]}{advisory_suffix}"
            ).strip(),
        )

    # --- Failure handling ---
    diag_summary = f"[{validation_mode}] {compiler_diag[-RESULT_DIAG_MAX_CHARS:]}"
    if style_diag:
        diag_summary += f" | style={style_diag[:STYLE_DIAG_MAX_CHARS]}"

    failure_mode = (
        CODEGEN_FAILURE_MODE if CODEGEN_FAILURE_MODE in VALID_FAILURE_MODES else "omit"
    )
    log_event(
        LOGGER,
        "code_generation_failed",
        level=logging.WARNING,
        topic=topic,
        path=path,
        repair_attempts=attempts,
        failure_mode=failure_mode,
        diag_excerpt=diag_summary[-500:],
    )

    if failure_mode == "omit":
        return CodeGenerationResult(
            code="",
            path="omitted",
            repair_attempts=attempts,
            diagnostics=diag_summary,
        )

    raise RuntimeError(
        f"Code generation failed (CODEGEN_FAILURE_MODE=error). "
        f"topic={topic!r} attempts={attempts} "
        f"diagnostics={diag_summary[:400]!r}"
    )


def generate_code(topic: str, article_body: str = "") -> str:
    """Return generated Swift code as a plain string.

    Backward-compatible wrapper around ``generate_code_with_metadata``.
    """
    return generate_code_with_metadata(topic, article_body=article_body).code