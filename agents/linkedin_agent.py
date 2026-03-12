"""LinkedIn agent: generate a professional promotional post from article content."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

from openai import OpenAI

from config import (
    LINKEDIN_CODE_SNIPPET_MODE,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    SWIFT_COMPILER_LANGUAGE_MODE,
    SWIFT_LANGUAGE_VERSION,
    openai_generation_kwargs,
)

PROMPT_PATH = Path("prompts/linkedin_prompt.txt")
FACTUALITY_PROMPT_PATH = Path("prompts/linkedin_factuality_prompt.txt")
VALID_SNIPPET_MODES = {"auto", "always", "never"}
SNIPPET_REQUIREMENT_BY_MODE = {
    "auto": (
        "Code snippet is optional. Include one short Swift snippet (3-8 lines) only when it "
        "materially improves clarity; skip code for conceptual posts."
    ),
    "always": (
        "Include one small Swift snippet (3-8 lines) in a single ```swift fenced block. "
        "Keep it simple, educational, and syntactically valid."
    ),
    "never": "Do not include any code snippet in this post.",
}
SWIFT_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$")
IOS_SIMULATOR_TARGET = "arm64-apple-ios16.0-simulator"
UNSUPPORTED_SWIFT_VERSION_PATTERN = re.compile(r"invalid value '[^']+' in '-swift-version")


DEFAULT_HASHTAGS = [
    "#iOS",
    "#Swift",
    "#SwiftUI",
    "#iOSArchitecture",
    "#SoftwareArchitecture",
    "#MobileDevelopment",
    "#SoftwareEngineering",
    "#TechLeadership",
]


def _load_prompt_template() -> str:
    """Load the LinkedIn post generation prompt template."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def _load_factuality_template() -> str:
    """Load LinkedIn factuality prompt template."""
    return FACTUALITY_PROMPT_PATH.read_text(encoding="utf-8")


def _normalize_snippet_mode(snippet_mode: str) -> str:
    """Return a supported snippet policy value."""
    normalized = snippet_mode.strip().lower()
    if normalized in VALID_SNIPPET_MODES:
        return normalized
    return "auto"


def _snippet_requirement_for_mode(snippet_mode: str) -> str:
    """Build the prompt instruction for snippet policy."""
    mode = _normalize_snippet_mode(snippet_mode)
    return SNIPPET_REQUIREMENT_BY_MODE[mode]


def _extract_first_code_block(text: str) -> tuple[str, str]:
    """Return (text_without_code_block, first_code_block)."""
    match = re.search(r"```(?:\w+)?\n([\s\S]*?)```", text)
    if not match:
        return text, ""

    code_block = match.group(1).strip()
    text_without = text[: match.start()] + text[match.end() :]
    return text_without.strip(), code_block


def _trim_code_block(code_block: str, max_lines: int = 10) -> str:
    """Keep a concise but syntactically complete snippet for LinkedIn readability."""
    if not code_block:
        return ""

    lines = [line.rstrip() for line in code_block.splitlines() if line.strip()]
    selected: list[str] = []
    brace_balance = 0

    for line in lines:
        selected.append(line)
        brace_balance += line.count("{") - line.count("}")

        # Prefer stopping at a natural closure once we have a meaningful snippet.
        if len(selected) >= 4 and brace_balance <= 0 and line.strip().endswith("}"):
            break
        if len(selected) >= max_lines:
            break

    # If truncated while a block is still open, append minimal closing braces.
    while brace_balance > 0 and len(selected) < max_lines + 2:
        selected.append("}")
        brace_balance -= 1

    return "\n".join(selected).strip()


def _extract_imports_and_body(code_block: str) -> tuple[list[str], str]:
    """Split out explicit imports and snippet body."""
    imports: list[str] = []
    body_lines: list[str] = []
    for line in code_block.splitlines():
        match = SWIFT_IMPORT_RE.match(line.strip())
        if match:
            imports.append(match.group(1))
        else:
            body_lines.append(line.rstrip())
    return imports, "\n".join(body_lines).strip()


def _infer_imports(code_body: str) -> set[str]:
    """Infer likely framework imports from snippet usage."""
    imports: set[str] = set()
    if re.search(r"\b(Date|URLSession|Data|DispatchQueue|OperationQueue|Timer)\b", code_body):
        imports.add("Foundation")
    if re.search(r"\b(BGTaskScheduler|BGTaskRequest|BGAppRefreshTask|BGProcessingTask)\b", code_body):
        imports.add("BackgroundTasks")
    if re.search(r"\b(View|Text|VStack|HStack|NavigationStack|@State|@ObservedObject|@Environment)\b", code_body):
        imports.add("SwiftUI")
    if re.search(r"\b(UIView|UIViewController|UIApplication|UINavigationController)\b", code_body):
        imports.add("UIKit")
    return imports


def _has_comment_line(code_block: str) -> bool:
    """Check whether snippet already contains at least one comment."""
    return any(line.strip().startswith("//") for line in code_block.splitlines())


def _ensure_snippet_comment(code_block: str) -> str:
    """Add one concise comment when the snippet has no explanatory comment."""
    cleaned_lines = [line.rstrip() for line in code_block.splitlines() if line.strip()]
    if not cleaned_lines:
        return ""
    if _has_comment_line("\n".join(cleaned_lines)):
        return "\n".join(cleaned_lines).strip()

    comment_line = "// Core idea: keep this implementation explicit and lightweight."
    insert_after = -1
    for idx, line in enumerate(cleaned_lines):
        if SWIFT_IMPORT_RE.match(line.strip()):
            insert_after = idx
        else:
            break
    cleaned_lines.insert(insert_after + 1, comment_line)
    return "\n".join(cleaned_lines).strip()


def _has_balanced_delimiters(code_text: str) -> bool:
    """Fast syntax sanity check when compiler tooling is unavailable."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = {value: key for key, value in pairs.items()}
    stack: list[str] = []
    for ch in code_text:
        if ch in pairs:
            stack.append(ch)
        elif ch in closers:
            if not stack or stack[-1] != closers[ch]:
                return False
            stack.pop()
    return not stack


def _build_typecheck_source(code_block: str, wrap_in_function: bool) -> str:
    """Build a compilable Swift source unit around the snippet."""
    explicit_imports, code_body = _extract_imports_and_body(code_block)
    if not code_body:
        return ""

    inferred_imports = _infer_imports(code_body)
    ordered_imports = sorted(dict.fromkeys([*explicit_imports, *sorted(inferred_imports)]))
    imports_block = "\n".join(f"import {module_name}" for module_name in ordered_imports)

    if wrap_in_function:
        wrapped_body = "func __linkedinSnippetWrapper() {\n"
        wrapped_body += textwrap.indent(code_body, "    ")
        wrapped_body += "\n}\n"
    else:
        wrapped_body = code_body + "\n"

    if not imports_block:
        return wrapped_body
    return f"{imports_block}\n\n{wrapped_body}"


def _swift_parse_only(swiftc_bin: str, source: str) -> bool:
    """Validate snippet syntax via parser when full typecheck is unavailable."""
    with tempfile.TemporaryDirectory() as temp_dir:
        source_path = Path(temp_dir) / "LinkedInSnippet.swift"
        source_path.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [
                swiftc_bin,
                "-frontend",
                "-swift-version",
                SWIFT_COMPILER_LANGUAGE_MODE,
                "-parse",
                str(source_path),
            ],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        if result.returncode != 0 and UNSUPPORTED_SWIFT_VERSION_PATTERN.search(result.stderr):
            result = subprocess.run(
                [swiftc_bin, "-frontend", "-parse", str(source_path)],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
    return result.returncode == 0


def _swift_ios_typechecks(swiftc_bin: str, source: str) -> bool:
    """Typecheck snippet against iOS simulator SDK when Xcode tooling is available."""
    xcrun_bin = shutil.which("xcrun")
    if not xcrun_bin:
        return _swift_parse_only(swiftc_bin, source)

    sdk_result = subprocess.run(
        [xcrun_bin, "--sdk", "iphonesimulator", "--show-sdk-path"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    sdk_path = sdk_result.stdout.strip()
    if sdk_result.returncode != 0 or not sdk_path:
        return _swift_parse_only(swiftc_bin, source)

    with tempfile.TemporaryDirectory() as temp_dir:
        source_path = Path(temp_dir) / "LinkedInSnippet.swift"
        module_cache_path = Path(temp_dir) / "ModuleCache"
        source_path.write_text(source, encoding="utf-8")

        result = subprocess.run(
            [
                swiftc_bin,
                "-swift-version",
                SWIFT_COMPILER_LANGUAGE_MODE,
                "-typecheck",
                "-target",
                IOS_SIMULATOR_TARGET,
                "-sdk",
                sdk_path,
                "-module-cache-path",
                str(module_cache_path),
                str(source_path),
            ],
            capture_output=True,
            text=True,
            timeout=18,
            check=False,
        )
        if result.returncode != 0 and UNSUPPORTED_SWIFT_VERSION_PATTERN.search(result.stderr):
            result = subprocess.run(
                [
                    swiftc_bin,
                    "-typecheck",
                    "-target",
                    IOS_SIMULATOR_TARGET,
                    "-sdk",
                    sdk_path,
                    "-module-cache-path",
                    str(module_cache_path),
                    str(source_path),
                ],
                capture_output=True,
                text=True,
                timeout=18,
                check=False,
            )
    return result.returncode == 0


def _snippet_is_compilable(code_block: str) -> bool:
    """Validate that snippet is syntactically sound and typechecks when possible."""
    if not code_block:
        return False
    if not _has_balanced_delimiters(code_block):
        return False

    swiftc_bin = shutil.which("swiftc")
    if not swiftc_bin:
        return True

    direct_source = _build_typecheck_source(code_block, wrap_in_function=False)
    if direct_source and _swift_ios_typechecks(swiftc_bin, direct_source):
        return True

    wrapped_source = _build_typecheck_source(code_block, wrap_in_function=True)
    if wrapped_source and _swift_ios_typechecks(swiftc_bin, wrapped_source):
        return True

    return False


def _prepare_snippet(code_block: str, max_lines: int = 6) -> str:
    """Trim, comment, and validate a snippet before including it in a post."""
    trimmed = _trim_code_block(code_block, max_lines=max_lines)
    commented = _ensure_snippet_comment(trimmed)
    if not commented:
        return ""
    if not _snippet_is_compilable(commented):
        return ""
    return commented


def _truncate_body(text: str, max_length: int) -> str:
    """Trim body text cleanly without cutting words/sentences abruptly."""
    if max_length <= 0:
        return ""
    if len(text) <= max_length:
        return text

    candidate = text[:max_length].rstrip()
    # Prefer sentence/paragraph boundaries when possible.
    boundary = max(
        candidate.rfind("\n\n"),
        candidate.rfind(". "),
        candidate.rfind("! "),
        candidate.rfind("? "),
    )
    if boundary > int(max_length * 0.55):
        candidate = candidate[: boundary + 1].rstrip()
    else:
        # Fallback to nearest word boundary.
        word_boundary = candidate.rfind(" ")
        if word_boundary > int(max_length * 0.55):
            candidate = candidate[:word_boundary].rstrip()

    return candidate + "..."


def _ensure_post_constraints(post: str, code_example: str, snippet_mode: str) -> str:
    """Normalize post formatting and enforce hashtag/emoji/snippet constraints."""
    normalized_mode = _normalize_snippet_mode(snippet_mode)
    cleaned = post.strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Ensure at least one emoji exists for social readability.
    if not re.search(r"[\U0001F300-\U0001FAFF]", cleaned):
        cleaned = f"🚀 {cleaned}"

    without_code, raw_code_block = _extract_first_code_block(cleaned)
    code_block = ""

    if normalized_mode != "never":
        if raw_code_block:
            code_block = _prepare_snippet(raw_code_block, max_lines=6)
        if not code_block and normalized_mode == "always":
            code_block = _prepare_snippet(code_example, max_lines=6)

    hashtags = re.findall(r"#\w+", without_code)
    unique_hashtags: list[str] = []
    for tag in hashtags:
        normalized = tag.lower()
        if normalized not in {h.lower() for h in unique_hashtags}:
            unique_hashtags.append(tag)

    if len(unique_hashtags) < 5:
        for tag in DEFAULT_HASHTAGS:
            if tag.lower() not in {h.lower() for h in unique_hashtags}:
                unique_hashtags.append(tag)
            if len(unique_hashtags) >= 5:
                break

    unique_hashtags = unique_hashtags[:8]

    # Remove hashtag lines and rebuild a clean single hashtag line at the end.
    no_hashtag_lines = [
        line
        for line in without_code.splitlines()
        if not re.fullmatch(r"(?:#\w+\s*)+", line.strip())
    ]
    body = "\n".join(no_hashtag_lines).strip()
    hashtag_line = " ".join(unique_hashtags)

    snippet_part = "```swift\n" + code_block + "\n```" if code_block else ""
    tail_parts = [part for part in [snippet_part, hashtag_line] if part.strip()]
    tail = "\n\n".join(tail_parts).strip()

    max_total_length = 1700
    separator_len = 2 if tail else 0
    max_body_length = max_total_length - len(tail) - separator_len

    if max_body_length < 280 and code_block:
        # If snippet makes post too long, shrink snippet further before trimming body.
        compact_code_block = _prepare_snippet(code_block, max_lines=4)
        if compact_code_block:
            code_block = compact_code_block
        snippet_part = "```swift\n" + code_block + "\n```" if code_block else ""
        tail_parts = [part for part in [snippet_part, hashtag_line] if part.strip()]
        tail = "\n\n".join(tail_parts).strip()
        max_body_length = max_total_length - len(tail) - (2 if tail else 0)

    safe_body = _truncate_body(body, max_body_length)
    parts = [safe_body]
    if tail:
        parts.append(tail)
    return "\n\n".join(part for part in parts if part.strip()).strip()


def _build_prompt(
    topic: str,
    article_body: str,
    code_example: str,
    snippet_mode: str,
    allowed_references: str,
) -> str:
    """Build prompt with optional code context."""
    return _load_prompt_template().format(
        topic=topic,
        allowed_references=allowed_references.strip() or "- None",
        article_body=article_body,
        code_example=code_example.strip() or "No code example provided.",
        snippet_requirement=_snippet_requirement_for_mode(snippet_mode),
        swift_language_version=SWIFT_LANGUAGE_VERSION,
        swift_language_mode=SWIFT_COMPILER_LANGUAGE_MODE,
    )


def _enforce_factual_grounding_post(
    client: OpenAI,
    topic: str,
    post: str,
    allowed_references: str,
    max_passes: int,
) -> str:
    """Rewrite LinkedIn post to suppress unsupported concrete claims."""
    if max_passes <= 0:
        return post.strip()

    current = post.strip()
    template = _load_factuality_template()
    for _ in range(max_passes):
        prompt = template.format(
            topic=topic,
            allowed_references=allowed_references.strip() or "- None",
            post=current,
        )
        response = client.responses.create(
            model=OPENAI_MODEL,
            max_output_tokens=700,
            input=prompt,
            **openai_generation_kwargs(min(OPENAI_TEMPERATURE, 0.3)),
        )
        revised = response.output_text.strip()
        if not revised:
            break
        current = revised

    return current.strip()


def generate_linkedin_post(
    topic: str,
    article_body: str,
    code_example: str = "",
    allowed_references: str = "",
    factual_passes: int = 0,
) -> str:
    """Generate a polished LinkedIn post with emojis and hashtags."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    snippet_mode = _normalize_snippet_mode(LINKEDIN_CODE_SNIPPET_MODE)
    prompt = _build_prompt(
        topic=topic,
        article_body=article_body,
        code_example=code_example,
        snippet_mode=snippet_mode,
        allowed_references=allowed_references,
    )

    response = client.responses.create(
        model=OPENAI_MODEL,
        max_output_tokens=700,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, 0.65)),
    )

    post = response.output_text.strip()
    if not post:
        raise RuntimeError("LinkedIn post generation returned empty output.")

    # Remove accidental markdown title formatting only.
    post = re.sub(r"^#\s+", "", post)

    constrained = _ensure_post_constraints(post, code_example=code_example, snippet_mode=snippet_mode)
    grounded = _enforce_factual_grounding_post(
        client=client,
        topic=topic,
        post=constrained,
        allowed_references=allowed_references,
        max_passes=max(0, factual_passes),
    )
    return _ensure_post_constraints(grounded, code_example=code_example, snippet_mode=snippet_mode)
