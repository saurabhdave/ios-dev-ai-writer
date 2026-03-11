"""Code agent: generate practical Swift/SwiftUI code examples for a topic."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess
import tempfile
from pathlib import Path

from openai import OpenAI

from config import CODEGEN_FAILURE_MODE, OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE

PROMPT_PATH = Path("prompts/code_prompt.txt")
IOS_SIMULATOR_TARGET = "arm64-apple-ios16.0-simulator"
MAX_REPAIR_ATTEMPTS = 2
VALID_FAILURE_MODES = {"omit", "error"}


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


def _swift_typecheck(code: str) -> tuple[bool, str]:
    """Typecheck code against iOS simulator SDK when possible."""
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
                    [
                        swiftc_bin,
                        "-typecheck",
                        "-target",
                        IOS_SIMULATOR_TARGET,
                        "-sdk",
                        sdk_path,
                        "-module-cache-path",
                        str(Path(temp_dir) / "ModuleCache"),
                        str(source_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=22,
                    check=False,
                )
                return result.returncode == 0, result.stderr.strip()[-4000:]

        parse_result = subprocess.run(
            [swiftc_bin, "-frontend", "-parse", str(source_path)],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        return parse_result.returncode == 0, parse_result.stderr.strip()[-4000:]


def _repair_code(client: OpenAI, topic: str, code: str, diagnostics: str) -> str:
    """Ask the model to repair compiler issues in generated code."""
    compact_diagnostics = diagnostics.strip() or "No diagnostics captured."
    compact_diagnostics = compact_diagnostics[-2500:]

    prompt = (
        "You are a senior iOS engineer fixing Swift code so it compiles cleanly.\n\n"
        f"Topic:\n{topic}\n\n"
        "Compiler diagnostics:\n"
        f"{compact_diagnostics}\n\n"
        "Current code:\n"
        f"{code}\n\n"
        "Requirements:\n"
        "- Return only fixed Swift code.\n"
        "- Include required imports.\n"
        "- Keep comments concise and useful.\n"
        "- Keep it practical for a Medium article.\n"
        "- Avoid undefined placeholder model types unless you define them.\n"
    )

    response = client.responses.create(
        model=OPENAI_MODEL,
        temperature=min(OPENAI_TEMPERATURE, 0.35),
        max_output_tokens=1400,
        input=prompt,
    )
    return _clean_generated_code(response.output_text)


def generate_code_with_metadata(topic: str) -> CodeGenerationResult:
    """Generate Swift code and return validation-path metadata."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = _load_prompt_template().format(topic=topic)

    response = client.responses.create(
        model=OPENAI_MODEL,
        temperature=OPENAI_TEMPERATURE,
        max_output_tokens=1200,
        input=prompt,
    )

    code = _clean_generated_code(response.output_text)
    if not code:
        raise RuntimeError("Code generation returned empty output.")

    is_valid, diagnostics = _swift_typecheck(code)
    attempts = 0
    path = "direct"
    while not is_valid and attempts < MAX_REPAIR_ATTEMPTS:
        repaired_code = _repair_code(client=client, topic=topic, code=code, diagnostics=diagnostics)
        if not repaired_code:
            break
        code = repaired_code
        is_valid, diagnostics = _swift_typecheck(code)
        attempts += 1
        path = "repaired"

    if is_valid:
        return CodeGenerationResult(
            code=code,
            path=path,
            repair_attempts=attempts,
            diagnostics=diagnostics.strip()[-800:],
        )
    failure_mode = CODEGEN_FAILURE_MODE if CODEGEN_FAILURE_MODE in VALID_FAILURE_MODES else "omit"
    diagnostics_excerpt = diagnostics.strip()[-800:]

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
