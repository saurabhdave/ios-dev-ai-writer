"""Code agent: generate practical Swift/SwiftUI code examples for a topic."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from openai import OpenAI

from config import (
    CODEGEN_FAILURE_MODE,
    CODEGEN_VALIDATION_MODE,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    SWIFT_COMPILER_LANGUAGE_MODE,
    SWIFT_LANGUAGE_VERSION,
    openai_generation_kwargs,
)
from utils.observability import get_logger, log_event
from utils.openai_logging import create_openai_client, responses_create_logged

PROMPT_PATH = Path("prompts/code_prompt.txt")
LOGGER = get_logger("pipeline.code")
_IMPL_PATTERN_RE = re.compile(r"###\s+Implementation Pattern\b", re.IGNORECASE)
_ARTICLE_EXCERPT_MAX_CHARS = 1200
IOS_SIMULATOR_TARGET = "arm64-apple-ios16.0-simulator"
SWIFT_BOOK_ABOUT_URL = (
    "https://docs.swift.org/swift-book/documentation/the-swift-programming-language/aboutswift/"
)
MAX_REPAIR_ATTEMPTS = 2
VALID_FAILURE_MODES = {"omit", "error"}
VALID_VALIDATION_MODES = {"snippet", "compile", "none"}
UNKNOWN_SYMBOL_PATTERNS = (
    r"error: cannot find '[^']+' in scope",
    r"error: cannot find type '[^']+' in scope",
    r"error: use of unresolved identifier '[^']+'",
    r"error: value of type '[^']+' has no member '[^']+'",
    r"error: type '[^']+' has no member '[^']+'",
)
UNSUPPORTED_SWIFT_VERSION_PATTERN = r"invalid value '[^']+' in '-swift-version"
LEGACY_OBSERVATION_PATTERNS = (
    r"\bObservableObject\b",
    r"@Published\b",
    r"@StateObject\b",
    r"@ObservedObject\b",
    r"@EnvironmentObject\b",
)
OBSERVABLE_BINDABLE_MISUSE_PATTERN = r"@Observable[\s\S]*?@Bindable\s+var\b"


@dataclass
class CodeGenerationResult:
    """Structured code generation output for observability."""

    code: str
    path: str
    repair_attempts: int
    diagnostics: str


def _load_prompt_template() -> str:
    """Load the code generation prompt template."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def _article_excerpt(body: str, max_chars: int = _ARTICLE_EXCERPT_MAX_CHARS) -> str:
    """Extract the most codeable section from the article body for prompt grounding."""
    sections: list[str] = []
    for match in _IMPL_PATTERN_RE.finditer(body):
        start = match.start()
        next_heading = re.search(r"\n##", body[start + 1:])
        end = start + 1 + next_heading.start() if next_heading else len(body)
        sections.append(body[start:end].strip())
    if sections:
        return "\n\n".join(sections)[:max_chars]
    return body[:max_chars]


def _clean_generated_code(raw: str) -> str:
    """Normalize model output to plain Swift code."""
    code = raw.strip()
    if code.startswith("```"):
        lines = code.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            code = "\n".join(lines[1:-1]).strip()
    return code


def _swift_frontend_parse_command(
    swiftc_bin: str,
    source_path: Path,
    include_language_mode: bool = True,
) -> list[str]:
    """Build a frontend parse command pinned to configured Swift language mode."""
    command = [
        swiftc_bin,
        "-frontend",
        "-parse",
        str(source_path),
    ]
    if include_language_mode:
        command = [
            swiftc_bin,
            "-frontend",
            "-swift-version",
            SWIFT_COMPILER_LANGUAGE_MODE,
            "-parse",
            str(source_path),
        ]
    return command


def _swift_typecheck_command(
    swiftc_bin: str,
    source_path: Path,
    sdk_path: str,
    module_cache: Path,
    include_language_mode: bool = True,
) -> list[str]:
    """Build a typecheck command pinned to configured Swift language mode."""
    command = [
        swiftc_bin,
        "-typecheck",
        "-target",
        IOS_SIMULATOR_TARGET,
        "-sdk",
        sdk_path,
        "-module-cache-path",
        str(module_cache),
        str(source_path),
    ]
    if include_language_mode:
        command = [
            swiftc_bin,
            "-swift-version",
            SWIFT_COMPILER_LANGUAGE_MODE,
            "-typecheck",
            "-target",
            IOS_SIMULATOR_TARGET,
            "-sdk",
            sdk_path,
            "-module-cache-path",
            str(module_cache),
            str(source_path),
        ]
    return command


def _is_unsupported_swift_version_diagnostic(diagnostics: str) -> bool:
    """Detect local toolchains that do not support configured -swift-version mode."""
    return bool(re.search(UNSUPPORTED_SWIFT_VERSION_PATTERN, diagnostics))


def _swift_compile_validate(code: str) -> tuple[bool, str]:
    """Strictly typecheck code against iOS simulator SDK when possible."""
    if not code.strip():
        return False, "Generated code is empty."

    swiftc_bin = shutil.which("swiftc")
    if not swiftc_bin:
        return True, ""

    with tempfile.TemporaryDirectory() as temp_dir:
        source_path = Path(temp_dir) / "GeneratedArticleCode.swift"
        source_path.write_text(code, encoding="utf-8")

        try:
            xcrun_bin = shutil.which("xcrun")
            if xcrun_bin:
                sdk_result = subprocess.run(
                    [xcrun_bin, "--sdk", "iphonesimulator", "--show-sdk-path"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                sdk_path = sdk_result.stdout.strip()
                if sdk_result.returncode == 0 and sdk_path:
                    result = subprocess.run(
                        _swift_typecheck_command(
                            swiftc_bin=swiftc_bin,
                            source_path=source_path,
                            sdk_path=sdk_path,
                            module_cache=Path(temp_dir) / "ModuleCache",
                        ),
                        capture_output=True,
                        text=True,
                        timeout=22,
                        check=False,
                    )
                    if result.returncode != 0 and _is_unsupported_swift_version_diagnostic(result.stderr):
                        result = subprocess.run(
                            _swift_typecheck_command(
                                swiftc_bin=swiftc_bin,
                                source_path=source_path,
                                sdk_path=sdk_path,
                                module_cache=Path(temp_dir) / "ModuleCache",
                                include_language_mode=False,
                            ),
                            capture_output=True,
                            text=True,
                            timeout=22,
                            check=False,
                        )
                    return result.returncode == 0, result.stderr.strip()[-4000:]

            parse_result = subprocess.run(
                _swift_frontend_parse_command(swiftc_bin=swiftc_bin, source_path=source_path),
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            if parse_result.returncode != 0 and _is_unsupported_swift_version_diagnostic(
                parse_result.stderr
            ):
                parse_result = subprocess.run(
                    _swift_frontend_parse_command(
                        swiftc_bin=swiftc_bin,
                        source_path=source_path,
                        include_language_mode=False,
                    ),
                    capture_output=True,
                    text=True,
                    timeout=12,
                    check=False,
                )
            return parse_result.returncode == 0, parse_result.stderr.strip()[-4000:]
        except subprocess.TimeoutExpired:
            return False, "[validation:compile] swiftc timed out — snippet skipped."


def _swift_parse_validate(code: str) -> tuple[bool, str]:
    """Syntax-parse Swift snippet without requiring full type resolution."""
    if not code.strip():
        return False, "Generated code is empty."

    if "<#" in code and "#>" in code:
        return False, "Snippet contains unresolved Xcode placeholders (<# ... #>)."

    swiftc_bin = shutil.which("swiftc")
    if not swiftc_bin:
        return True, ""

    with tempfile.TemporaryDirectory() as temp_dir:
        source_path = Path(temp_dir) / "GeneratedArticleCode.swift"
        source_path.write_text(code, encoding="utf-8")

        try:
            parse_result = subprocess.run(
                _swift_frontend_parse_command(swiftc_bin=swiftc_bin, source_path=source_path),
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            if parse_result.returncode != 0 and _is_unsupported_swift_version_diagnostic(
                parse_result.stderr
            ):
                parse_result = subprocess.run(
                    _swift_frontend_parse_command(
                        swiftc_bin=swiftc_bin,
                        source_path=source_path,
                        include_language_mode=False,
                    ),
                    capture_output=True,
                    text=True,
                    timeout=12,
                    check=False,
                )
            return parse_result.returncode == 0, parse_result.stderr.strip()[-4000:]
        except subprocess.TimeoutExpired:
            return False, "[validation:snippet] swiftc timed out — snippet skipped."


def _normalize_validation_mode() -> str:
    """Resolve validation mode with a safe default."""
    mode = CODEGEN_VALIDATION_MODE.strip().lower()
    return mode if mode in VALID_VALIDATION_MODES else "snippet"


def _validate_generated_code(code: str, validation_mode: str) -> tuple[bool, str]:
    """Validate snippet according to configured strictness."""
    if validation_mode == "none":
        return True, "Validation skipped by CODEGEN_VALIDATION_MODE=none."
    if validation_mode == "compile":
        return _swift_compile_validate(code)
    return _swift_parse_validate(code)


def _extract_unknown_symbol_diagnostics(diagnostics: str) -> str:
    """Keep only unknown symbol/member/type diagnostics for typo/API checks."""
    if not diagnostics.strip():
        return ""

    selected: list[str] = []
    for line in diagnostics.splitlines():
        normalized = line.strip()
        if any(re.search(pattern, normalized) for pattern in UNKNOWN_SYMBOL_PATTERNS):
            selected.append(normalized)

    # Preserve ordering while deduplicating.
    unique: list[str] = []
    seen: set[str] = set()
    for item in selected:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return "\n".join(unique[:12])


def _observation_style_diagnostics(code: str) -> str:
    """Detect Swift 6 observation patterns we want to avoid in generated snippets."""
    if not code.strip():
        return ""

    issues: list[str] = []
    for pattern in LEGACY_OBSERVATION_PATTERNS:
        if re.search(pattern, code):
            issues.append(
                "Avoid legacy observation wrappers (`ObservableObject`, `@Published`, "
                "`@StateObject`, `@ObservedObject`, `@EnvironmentObject`) in Swift 6 snippets."
            )
            break

    if re.search(OBSERVABLE_BINDABLE_MISUSE_PATTERN, code):
        bindable_vars = re.findall(r"@Bindable\s+var\s+(\w+)", code)
        names = ", ".join(f"`{n}`" for n in bindable_vars[:5]) if bindable_vars else "one or more properties"
        issues.append(
            f"Remove `@Bindable` from model properties ({names}) inside `@Observable` — "
            "keep them as plain stored properties. "
            "Move `@Bindable` to the SwiftUI View that holds a reference to this model: "
            "`struct MyView: View { @Bindable var model: MyModel }`."
        )

    return "\n".join(dict.fromkeys(issues))


_BRACE_PAIRS = {"(": ")", "[": "]", "{": "}"}


def _brace_balance_diagnostic(code: str) -> str:
    """Return a human-readable message when brace/bracket/paren pairs are unbalanced."""
    issues: list[str] = []
    for open_ch, close_ch in _BRACE_PAIRS.items():
        opens = code.count(open_ch)
        closes = code.count(close_ch)
        if opens != closes:
            diff = opens - closes
            direction = "extra opens" if diff > 0 else "extra closes"
            issues.append(
                f"Unbalanced `{open_ch}{close_ch}`: {opens} opens, {closes} closes "
                f"({abs(diff)} {direction})."
            )
    return "\n".join(issues)


def _unknown_api_diagnostics(code: str) -> str:
    """Advisory typecheck pass focused on typos and unknown API usage."""
    is_valid, diagnostics = _swift_compile_validate(code)
    if is_valid:
        return ""
    return _extract_unknown_symbol_diagnostics(diagnostics)


def _repair_code(
    client: OpenAI,
    topic: str,
    code: str,
    diagnostics: str,
    validation_mode: str,
) -> str:
    """Ask the model to repair validation failures in generated code."""
    compact_diagnostics = diagnostics.strip() or "No diagnostics captured."
    compact_diagnostics = compact_diagnostics[-2500:]
    validation_goal = (
        "compile cleanly under swiftc typecheck."
        if validation_mode == "compile"
        else "be syntactically valid and usable as a standalone article snippet."
    )

    prompt = (
        "You are a senior iOS engineer fixing Swift code.\n\n"
        f"Topic:\n{topic}\n\n"
        "Compiler diagnostics:\n"
        f"{compact_diagnostics}\n\n"
        "Current code:\n"
        f"{code}\n\n"
        "Requirements:\n"
        "- Return only fixed Swift code.\n"
        f"- Validation target: {validation_goal}\n"
        f"- Target Swift language version: {SWIFT_LANGUAGE_VERSION}\n"
        f"- Ensure code is valid for swiftc `-swift-version {SWIFT_COMPILER_LANGUAGE_MODE}`.\n"
        f"- Use Swift language rules from: {SWIFT_BOOK_ABOUT_URL}\n"
        "- Include required imports.\n"
        "- Prefer Swift Observation in Swift 6+ (`@Observable` + `import Observation`).\n"
        "- Avoid `ObservableObject`, `@Published`, `@StateObject`, `@ObservedObject`, and "
        "`@EnvironmentObject` unless this snippet is explicitly about legacy migration.\n"
        "- For owned observable instances in SwiftUI views, prefer `@State`.\n"
        "- Use `@Bindable` only where `$` bindings are needed.\n"
        "- CRITICAL `@Bindable` fix: NEVER put `@Bindable` on properties inside an `@Observable` type.\n"
        "  WRONG:   @Observable class M { @Bindable var x: Int = 0 }\n"
        "  CORRECT: @Observable class M { var x: Int = 0 }   // plain stored property\n"
        "           struct V: View { @Bindable var m: M }     // @Bindable only in the View\n"
        "- Keep comments concise and useful.\n"
        "- Count all `{`, `}`, `[`, `]`, `(`, `)` pairs and ensure they are balanced before returning.\n"
        "- Keep it practical for a Medium article.\n"
        "- If the code is longer than 35 lines or has deeply nested closures causing brace errors, "
        "SIMPLIFY the snippet to demonstrate only the core concept. Shorter and correct beats longer and broken.\n"
        "- For Swift 6 concurrency errors (race statement, sendability): add @MainActor to the class/struct "
        "or eliminate async mutation of state properties entirely.\n"
        "- Avoid undefined placeholder model types unless you define them.\n"
        "- Remove unresolved placeholders such as <# ... #>.\n"
        "- Prefer focused snippets instead of full app entry points.\n"
    )

    response = responses_create_logged(
        client,
        agent_name="code_agent",
        operation="repair_code",
        model=OPENAI_MODEL,
        max_output_tokens=1400,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, 0.35)),
    )
    return _clean_generated_code(response.output_text)


def generate_code_with_metadata(topic: str, article_body: str = "") -> CodeGenerationResult:
    """Generate Swift code and return validation-path metadata."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = create_openai_client()
    article_context = ""
    if article_body.strip():
        excerpt = _article_excerpt(article_body)
        article_context = (
            "Article context — implement one of the concrete patterns described below:\n"
            f"{excerpt}\n\n"
        )
    # Two-step substitution: .format() handles safe scalar fields first (sentinel keeps
    # the article_context slot intact so Swift braces in the excerpt never reach .format()).
    # Step 1: substitute topic/version fields; article_context slot becomes the sentinel.
    base = _load_prompt_template().format(
        topic=topic,
        article_context="__ARTICLE_CTX__",
        swift_language_version=SWIFT_LANGUAGE_VERSION,
        swift_language_mode=SWIFT_COMPILER_LANGUAGE_MODE,
    )
    # Step 2: replace sentinel with raw article context (may contain unescaped braces).
    prompt = base.replace("__ARTICLE_CTX__", article_context)

    response = responses_create_logged(
        client,
        agent_name="code_agent",
        operation="generate_code",
        model=OPENAI_MODEL,
        max_output_tokens=1200,
        input=prompt,
        **openai_generation_kwargs(OPENAI_TEMPERATURE),
    )

    code = _clean_generated_code(response.output_text)
    if not code:
        raise RuntimeError("Code generation returned empty output.")

    validation_mode = _normalize_validation_mode()
    is_valid, diagnostics = _validate_generated_code(code, validation_mode)
    style_diagnostics = _observation_style_diagnostics(code)
    attempts = 0
    path = "direct"
    while (not is_valid or style_diagnostics) and attempts < MAX_REPAIR_ATTEMPTS:
        log_event(
            LOGGER,
            "code_repair_requested",
            level=logging.WARNING,
            topic=topic,
            attempt=attempts + 1,
            validation_mode=validation_mode,
            diagnostics_excerpt=(diagnostics or style_diagnostics)[-500:],
            has_style_diagnostics=bool(style_diagnostics),
        )
        brace_diag = _brace_balance_diagnostic(code)
        combined_diagnostics = diagnostics
        extra_parts = []
        if style_diagnostics:
            extra_parts.append(f"Observation style diagnostics:\n{style_diagnostics}")
        if brace_diag:
            extra_parts.append(f"Brace balance diagnostics:\n{brace_diag}")
        if extra_parts:
            combined_diagnostics = (
                combined_diagnostics + "\n\n" + "\n\n".join(extra_parts)
            ).strip()
        repaired_code = _repair_code(
            client=client,
            topic=topic,
            code=code,
            diagnostics=combined_diagnostics,
            validation_mode=validation_mode,
        )
        if not repaired_code:
            break
        code = repaired_code
        is_valid, diagnostics = _validate_generated_code(code, validation_mode)
        style_diagnostics = _observation_style_diagnostics(code)
        attempts += 1
        path = "repaired"

    unknown_api_diag = ""
    if is_valid and validation_mode != "none":
        unknown_api_diag = _unknown_api_diagnostics(code)
        if unknown_api_diag and attempts < MAX_REPAIR_ATTEMPTS:
            log_event(
                LOGGER,
                "code_unknown_api_repair_requested",
                level=logging.WARNING,
                topic=topic,
                attempt=attempts + 1,
                diagnostics_excerpt=unknown_api_diag[-500:],
            )
            repaired_code = _repair_code(
                client=client,
                topic=topic,
                code=code,
                diagnostics=(
                    "Unknown symbol/API diagnostics from advisory typecheck:\n"
                    f"{unknown_api_diag}"
                ),
                validation_mode=validation_mode,
            )
            if repaired_code:
                code = repaired_code
                is_valid, diagnostics = _validate_generated_code(code, validation_mode)
                attempts += 1
                path = "repaired"
                if is_valid:
                    unknown_api_diag = _unknown_api_diagnostics(code)

    if is_valid and not style_diagnostics:
        advisory_suffix = (
            f" | advisory_unknown_symbols={unknown_api_diag[:420]}"
            if unknown_api_diag
            else ""
        )
        return CodeGenerationResult(
            code=code,
            path=path,
            repair_attempts=attempts,
            diagnostics=(
                f"[validation:{validation_mode}] {diagnostics.strip()[-560:]}{advisory_suffix}"
            ).strip(),
        )
    failure_mode = CODEGEN_FAILURE_MODE if CODEGEN_FAILURE_MODE in VALID_FAILURE_MODES else "omit"
    diagnostics_excerpt = f"[validation:{validation_mode}] {diagnostics.strip()[-760:]}".strip()
    if style_diagnostics:
        diagnostics_excerpt = (
            f"{diagnostics_excerpt} | observation_style={style_diagnostics[:360]}"
        ).strip()

    if failure_mode == "omit":
        log_event(
            LOGGER,
            "code_generation_omitted",
            level=logging.WARNING,
            topic=topic,
            validation_mode=validation_mode,
            repair_attempts=attempts,
            diagnostics_excerpt=diagnostics_excerpt[-500:],
        )
        return CodeGenerationResult(
            code="",
            path="omitted",
            repair_attempts=attempts,
            diagnostics=diagnostics_excerpt,
        )

    raise RuntimeError(
        "Code generation produced non-compilable output and CODEGEN_FAILURE_MODE=error. "
        f"Last diagnostics: {diagnostics_excerpt[:400]}"
    )


def generate_code(topic: str, article_body: str = "") -> str:
    """Backward-compatible code-only API."""
    return generate_code_with_metadata(topic, article_body=article_body).code
