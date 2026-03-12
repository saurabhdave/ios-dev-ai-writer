"""Code agent: generate practical Swift/SwiftUI code examples for a topic."""

from __future__ import annotations

from dataclasses import dataclass
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

PROMPT_PATH = Path("prompts/code_prompt.txt")
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
        "- Keep comments concise and useful.\n"
        "- Keep it practical for a Medium article.\n"
        "- Avoid undefined placeholder model types unless you define them.\n"
        "- Remove unresolved placeholders such as <# ... #>.\n"
        "- Prefer focused snippets instead of full app entry points.\n"
    )

    response = client.responses.create(
        model=OPENAI_MODEL,
        max_output_tokens=1400,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, 0.35)),
    )
    return _clean_generated_code(response.output_text)


def generate_code_with_metadata(topic: str) -> CodeGenerationResult:
    """Generate Swift code and return validation-path metadata."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = _load_prompt_template().format(
        topic=topic,
        swift_language_version=SWIFT_LANGUAGE_VERSION,
        swift_language_mode=SWIFT_COMPILER_LANGUAGE_MODE,
    )

    response = client.responses.create(
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
    attempts = 0
    path = "direct"
    while not is_valid and attempts < MAX_REPAIR_ATTEMPTS:
        repaired_code = _repair_code(
            client=client,
            topic=topic,
            code=code,
            diagnostics=diagnostics,
            validation_mode=validation_mode,
        )
        if not repaired_code:
            break
        code = repaired_code
        is_valid, diagnostics = _validate_generated_code(code, validation_mode)
        attempts += 1
        path = "repaired"

    unknown_api_diag = ""
    if is_valid and validation_mode != "none":
        unknown_api_diag = _unknown_api_diagnostics(code)
        if unknown_api_diag and attempts < MAX_REPAIR_ATTEMPTS:
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

    if is_valid:
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

    if failure_mode == "omit":
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


def generate_code(topic: str) -> str:
    """Backward-compatible code-only API."""
    return generate_code_with_metadata(topic).code
