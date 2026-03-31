"""LinkedIn agent: generate a professional promotional post from article content.

Design principles
-----------------
- All constants (paths, timeouts, thresholds, token limits) are typed ``Final``.
- Swift compiler invocation is consolidated in ``_run_swiftc`` — one call site,
  uniform ``(returncode, stderr)`` return, no subprocess exceptions in callers.
- ``_build_typecheck_source`` is the only place that understands how to wrap a
  snippet into a compilable Swift compilation unit.
- Post constraint enforcement is a pure transformation: same input → same output.
- Every public function documents what it raises and why.
- ``OPENAI_API_KEY`` check removed — key validation belongs in
  ``create_openai_client()``.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Final

from config import (
    LINKEDIN_CODE_SNIPPET_MODE,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    SWIFT_COMPILER_LANGUAGE_MODE,
    SWIFT_LANGUAGE_VERSION,
    openai_generation_kwargs,
)
from utils.observability import get_logger, log_event
from utils.openai_logging import create_openai_client, response_output_text, responses_create_logged

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROMPT_PATH: Final[Path] = Path("prompts/linkedin_prompt.txt")
FACTUALITY_PROMPT_PATH: Final[Path] = Path("prompts/linkedin_factuality_prompt.txt")

MAX_POST_TOKENS: Final[int] = 700
FACTUALITY_MAX_TOKENS: Final[int] = 700

GENERATION_TEMPERATURE: Final[float] = 0.40
FACTUALITY_TEMPERATURE: Final[float] = 0.30

# LinkedIn character budget and safety margins.
MAX_TOTAL_POST_LENGTH: Final[int] = 1_700
MIN_BODY_LENGTH: Final[int] = 280   # Below this, drop snippet to reclaim space.

# Snippet line limits.
SNIPPET_MAX_LINES: Final[int] = 6
SNIPPET_COMPACT_LINES: Final[int] = 4

# Hashtag policy.
DEFAULT_HASHTAG_MIN: Final[int] = 3
DEFAULT_HASHTAG_MAX: Final[int] = 5

# Subprocess timeouts (seconds).
SDK_QUERY_TIMEOUT: Final[int] = 10
TYPECHECK_TIMEOUT: Final[int] = 18
PARSE_TIMEOUT: Final[int] = 12

IOS_SIMULATOR_TARGET: Final[str] = "arm64-apple-ios18.0-simulator"

VALID_SNIPPET_MODES: Final[frozenset[str]] = frozenset({"auto", "always", "never"})

SNIPPET_REQUIREMENT_BY_MODE: Final[dict[str, str]] = {
    "auto": (
        "Code snippet is optional. Include one short Swift snippet (3–8 lines) only when it "
        "materially improves clarity; skip code for conceptual posts."
    ),
    "always": (
        "Include one small Swift snippet (3–8 lines) in a single ```swift fenced block. "
        "Keep it simple, educational, and syntactically valid."
    ),
    "never": "Do not include any code snippet in this post.",
}

DEFAULT_HASHTAGS: Final[list[str]] = [
    "#iOS",
    "#Swift",
    "#SwiftUI",
    "#iOSArchitecture",
    "#SoftwareArchitecture",
    "#MobileDevelopment",
    "#SoftwareEngineering",
    "#TechLeadership",
]

LOGGER = get_logger("pipeline.linkedin")

# ---------------------------------------------------------------------------
# Regex patterns — compiled once
# ---------------------------------------------------------------------------

_SWIFT_IMPORT_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$"
)
_HASHTAG_RE: Final[re.Pattern[str]] = re.compile(r"#\w+")
_HASHTAG_LINE_RE: Final[re.Pattern[str]] = re.compile(r"^(?:#\w+\s*)+$")
_CODE_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"```(?:\w+)?\n([\s\S]*?)```")
_TITLE_H1_RE: Final[re.Pattern[str]] = re.compile(r"^#\s+")
_EXTRA_BLANK_LINES_RE: Final[re.Pattern[str]] = re.compile(r"\n{3,}")
_HSPACE_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]{2,}")
_UNSUPPORTED_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"invalid value '[^']+' in '-swift-version"
)

# Brace pairs used for delimiter balance checking.
_BRACE_PAIRS: Final[dict[str, str]] = {"(": ")", "[": "]", "{": "}"}
_BRACE_CLOSERS: Final[dict[str, str]] = {v: k for k, v in _BRACE_PAIRS.items()}

# Swift code-line heuristic prefixes.
_CODE_LINE_PREFIXES: Final[tuple[str, ...]] = (
    "final class ",
    "class ",
    "struct ",
    "enum ",
    "func ",
    "init(",
    "let ",
    "var ",
    "@MainActor",
)
_CODE_LINE_TOKENS: Final[tuple[str, ...]] = (
    "class", "struct", "func", "init", "let ", "var ",
)

# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def _load_template(path: Path) -> str:
    """Read and return a prompt template.

    Raises
    ------
    FileNotFoundError
        When the template file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt template not found at '{path}'. "
            "Verify the path constant or the process working directory."
        )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Snippet mode helpers
# ---------------------------------------------------------------------------


def _resolve_snippet_mode(raw: str) -> str:
    """Return a valid snippet policy, defaulting to 'auto' on invalid input."""
    normalized = raw.strip().lower()
    return normalized if normalized in VALID_SNIPPET_MODES else "auto"


def _snippet_requirement(mode: str) -> str:
    return SNIPPET_REQUIREMENT_BY_MODE[_resolve_snippet_mode(mode)]


# ---------------------------------------------------------------------------
# Swift compiler helpers
# ---------------------------------------------------------------------------


def _run_command(command: list[str], timeout: int) -> tuple[int, str, str]:
    """Execute a subprocess command and return ``(returncode, stdout, stderr)``.

    Returns ``(-1, "", error_message)`` on timeout or OSError so callers
    always receive a uniform tuple rather than an exception.
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"command timed out after {timeout}s"
    except OSError as exc:
        return -1, "", f"command OSError: {exc}"


def _run_swiftc(command: list[str], timeout: int) -> tuple[int, str]:
    """Execute a swiftc command and return ``(returncode, stderr)``."""
    rc, _stdout, stderr = _run_command(command, timeout)
    return rc, stderr


def _query_ios_sdk_path(xcrun: str) -> str:
    """Return the iOS simulator SDK path from ``xcrun``, or ``""`` on failure."""
    rc, stdout, _stderr = _run_command(
        [xcrun, "--sdk", "iphonesimulator", "--show-sdk-path"],
        timeout=SDK_QUERY_TIMEOUT,
    )
    return stdout if rc == 0 else ""


def _swift_version_args() -> list[str]:
    mode = SWIFT_COMPILER_LANGUAGE_MODE.strip()
    return ["-swift-version", mode] if mode else []


def _extract_imports_and_body(code_block: str) -> tuple[list[str], str]:
    """Split explicit `import` statements from the snippet body."""
    imports: list[str] = []
    body_lines: list[str] = []
    for line in code_block.splitlines():
        m = _SWIFT_IMPORT_RE.match(line.strip())
        if m:
            imports.append(m.group(1))
        else:
            body_lines.append(line.rstrip())
    return imports, "\n".join(body_lines).strip()


def _infer_imports(code_body: str) -> set[str]:
    """Infer likely framework imports from symbol usage in the snippet."""
    imports: set[str] = set()
    checks: list[tuple[str, str]] = [
        (r"\b(Date|URLSession|Data|DispatchQueue|OperationQueue|Timer)\b", "Foundation"),
        (r"\b(BGTaskScheduler|BGTaskRequest|BGAppRefreshTask|BGProcessingTask)\b", "BackgroundTasks"),
        (r"\b(View|Text|VStack|HStack|NavigationStack|@State|@ObservedObject|@Environment)\b", "SwiftUI"),
        (r"\b(UIView|UIViewController|UIApplication|UINavigationController)\b", "UIKit"),
    ]
    for pattern, framework in checks:
        if re.search(pattern, code_body):
            imports.add(framework)
    return imports


def _build_typecheck_source(code_block: str, *, wrap_in_function: bool) -> str:
    """Construct a compilable Swift source unit around the snippet.

    Merges explicit and inferred imports; optionally wraps the body in a
    top-level function to allow expression-level snippets to typecheck.
    """
    explicit_imports, code_body = _extract_imports_and_body(code_block)
    if not code_body:
        return ""

    all_imports = sorted(dict.fromkeys([*explicit_imports, *sorted(_infer_imports(code_body))]))
    imports_block = "\n".join(f"import {name}" for name in all_imports)

    if wrap_in_function:
        body = "func __linkedinSnippetWrapper() {\n"
        body += textwrap.indent(code_body, "    ")
        body += "\n}\n"
    else:
        body = code_body + "\n"

    return f"{imports_block}\n\n{body}" if imports_block else body


def _parse_only(swiftc: str, source: str) -> bool:
    """Validate syntax via parser when full typecheck is unavailable."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "LinkedInSnippet.swift"
        path.write_text(source, encoding="utf-8")

        rc, stderr = _run_swiftc(
            [swiftc, "-frontend", *_swift_version_args(), "-parse", str(path)],
            timeout=PARSE_TIMEOUT,
        )
        if rc != 0 and _UNSUPPORTED_VERSION_RE.search(stderr):
            rc, _ = _run_swiftc(
                [swiftc, "-frontend", "-parse", str(path)],
                timeout=PARSE_TIMEOUT,
            )
    return rc == 0


def _ios_typecheck(swiftc: str, source: str) -> bool:
    """Typecheck snippet against the iOS simulator SDK when available.

    Falls back to parse-only validation when xcrun or the SDK is absent.
    """
    xcrun = shutil.which("xcrun")
    if not xcrun:
        return _parse_only(swiftc, source)

    sdk_path = _query_ios_sdk_path(xcrun)
    if not sdk_path:
        return _parse_only(swiftc, source)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "LinkedInSnippet.swift"
        module_cache = Path(tmp) / "ModuleCache"
        path.write_text(source, encoding="utf-8")

        cmd = [
            swiftc,
            *_swift_version_args(),
            "-typecheck",
            "-target", IOS_SIMULATOR_TARGET,
            "-sdk", sdk_path,
            "-module-cache-path", str(module_cache),
            str(path),
        ]
        rc, stderr = _run_swiftc(cmd, timeout=TYPECHECK_TIMEOUT)
        if rc != 0 and _UNSUPPORTED_VERSION_RE.search(stderr):
            cmd_no_ver = [
                swiftc,
                "-typecheck",
                "-target", IOS_SIMULATOR_TARGET,
                "-sdk", sdk_path,
                "-module-cache-path", str(module_cache),
                str(path),
            ]
            rc, _ = _run_swiftc(cmd_no_ver, timeout=TYPECHECK_TIMEOUT)

    return rc == 0


def _has_balanced_delimiters(code: str) -> bool:
    """Return True when all bracket/brace/paren pairs are balanced."""
    stack: list[str] = []
    for ch in code:
        if ch in _BRACE_PAIRS:
            stack.append(ch)
        elif ch in _BRACE_CLOSERS:
            if not stack or stack[-1] != _BRACE_CLOSERS[ch]:
                return False
            stack.pop()
    return not stack


def _snippet_is_compilable(code_block: str) -> bool:
    """Return True when the snippet passes delimiter and compiler checks."""
    if not code_block or not _has_balanced_delimiters(code_block):
        return False

    swiftc = shutil.which("swiftc")
    if not swiftc:
        return True   # No toolchain available — assume valid and proceed.

    for wrap in (False, True):
        source = _build_typecheck_source(code_block, wrap_in_function=wrap)
        if source and _ios_typecheck(swiftc, source):
            return True
    return False


# ---------------------------------------------------------------------------
# Snippet preparation
# ---------------------------------------------------------------------------


def _trim_code_block(code_block: str, max_lines: int) -> str:
    """Return a concise, syntactically complete slice of the snippet."""
    if not code_block:
        return ""

    lines = [line.rstrip() for line in code_block.splitlines() if line.strip()]
    selected: list[str] = []
    brace_balance = 0

    for line in lines:
        selected.append(line)
        brace_balance += line.count("{") - line.count("}")
        if len(selected) >= 4 and brace_balance <= 0 and line.strip().endswith("}"):
            break
        if len(selected) >= max_lines:
            break

    # Append minimal closing braces to avoid leaving open blocks.
    while brace_balance > 0 and len(selected) < max_lines + 2:
        selected.append("}")
        brace_balance -= 1

    return "\n".join(selected).strip()


def _has_comment(code_block: str) -> bool:
    return any(line.strip().startswith("//") for line in code_block.splitlines())


def _ensure_snippet_comment(code_block: str) -> str:
    """Add one explanatory comment when the snippet has none."""
    lines = [line.rstrip() for line in code_block.splitlines() if line.strip()]
    if not lines or _has_comment("\n".join(lines)):
        return "\n".join(lines).strip()

    comment = "// Core idea: keep this implementation explicit and lightweight."
    # Insert after the last import line so the comment sits before the logic.
    insert_after = -1
    for idx, line in enumerate(lines):
        if _SWIFT_IMPORT_RE.match(line.strip()):
            insert_after = idx
        else:
            break
    lines.insert(insert_after + 1, comment)
    return "\n".join(lines).strip()


def _prepare_snippet(code_block: str, max_lines: int) -> str:
    """Trim, comment, and compile-check a snippet; return empty string on failure."""
    trimmed = _trim_code_block(code_block, max_lines=max_lines)
    commented = _ensure_snippet_comment(trimmed)
    if not commented or not _snippet_is_compilable(commented):
        return ""
    return commented


# ---------------------------------------------------------------------------
# Post body helpers
# ---------------------------------------------------------------------------


def _extract_first_code_block(text: str) -> tuple[str, str]:
    """Return ``(text_without_block, first_code_block)``."""
    match = _CODE_FENCE_RE.search(text)
    if not match:
        return text, ""
    code = match.group(1).strip()
    without = (text[: match.start()] + text[match.end():]).strip()
    return without, code


def _truncate_body(text: str, max_length: int) -> str:
    """Trim body text at a sentence or paragraph boundary when possible."""
    if max_length <= 0:
        return ""
    if len(text) <= max_length:
        return text

    candidate = text[:max_length].rstrip()
    floor = int(max_length * 0.55)
    boundary = max(
        candidate.rfind("\n\n"),
        candidate.rfind(". "),
        candidate.rfind("! "),
        candidate.rfind("? "),
    )
    if boundary > floor:
        return candidate[: boundary + 1].rstrip() + "..."

    word_boundary = candidate.rfind(" ")
    if word_boundary > floor:
        return candidate[:word_boundary].rstrip() + "..."

    return candidate + "..."


def _looks_like_swift_line(line: str) -> bool:
    """Heuristic check for stray unfenced Swift lines in social copy."""
    stripped = line.strip()
    if not stripped:
        return False
    if stripped in {"{", "}"}:
        return True
    if stripped.lower().startswith("swift snippet"):
        return True
    if stripped.startswith(_CODE_LINE_PREFIXES):
        return True
    if ("{" in stripped or "}" in stripped) and any(t in stripped for t in _CODE_LINE_TOKENS):
        return True
    return False


def _deduplicate_hashtags(tags: list[str]) -> list[str]:
    """Return a list with case-insensitive deduplication preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            result.append(tag)
    return result


def _enforce_post_constraints(post: str, code_example: str, snippet_mode: str) -> str:
    """Normalise formatting and enforce hashtag, snippet, and length constraints.

    This is a pure transformation — same inputs always produce the same output.
    """
    mode = _resolve_snippet_mode(snippet_mode)
    cleaned = _EXTRA_BLANK_LINES_RE.sub("\n\n", post.strip())

    without_code, raw_code = _extract_first_code_block(cleaned)
    code_block = ""

    if mode != "never":
        if raw_code:
            code_block = _prepare_snippet(raw_code, max_lines=SNIPPET_MAX_LINES)
        if not code_block and mode == "always":
            code_block = _prepare_snippet(code_example, max_lines=SNIPPET_MAX_LINES)

    # Collect and deduplicate hashtags; pad to minimum from defaults.
    found_tags = _deduplicate_hashtags(_HASHTAG_RE.findall(without_code))
    for default_tag in DEFAULT_HASHTAGS:
        if len(found_tags) >= DEFAULT_HASHTAG_MAX:
            break
        if default_tag.lower() not in {t.lower() for t in found_tags}:
            found_tags.append(default_tag)
    found_tags = found_tags[:DEFAULT_HASHTAG_MAX]

    # Strip hashtag-only lines and inline hashtags from prose body.
    body_lines = [
        line for line in without_code.splitlines()
        if not _HASHTAG_LINE_RE.fullmatch(line.strip())
    ]
    body = _HASHTAG_RE.sub("", "\n".join(body_lines)).strip()
    body = _HSPACE_RE.sub(" ", body).strip()

    # Remove stray code lines when not explicitly required.
    if mode != "always" and not code_block:
        body = "\n".join(
            line for line in body.splitlines()
            if not _looks_like_swift_line(line)
        ).strip()

    hashtag_line = " ".join(found_tags)
    snippet_block = f"```swift\n{code_block}\n```" if code_block else ""
    tail_parts = [p for p in [snippet_block, hashtag_line] if p.strip()]
    tail = "\n\n".join(tail_parts).strip()

    separator = 2 if tail else 0
    max_body = MAX_TOTAL_POST_LENGTH - len(tail) - separator

    # If body budget is too tight, shrink snippet and recalculate.
    if max_body < MIN_BODY_LENGTH and code_block:
        code_block = _prepare_snippet(code_block, max_lines=SNIPPET_COMPACT_LINES)
        snippet_block = f"```swift\n{code_block}\n```" if code_block else ""
        tail_parts = [p for p in [snippet_block, hashtag_line] if p.strip()]
        tail = "\n\n".join(tail_parts).strip()
        max_body = MAX_TOTAL_POST_LENGTH - len(tail) - (2 if tail else 0)

    parts = [_truncate_body(body, max_body)]
    if tail:
        parts.append(tail)
    return "\n\n".join(p for p in parts if p.strip()).strip()


# ---------------------------------------------------------------------------
# Factual grounding
# ---------------------------------------------------------------------------


def _enforce_factual_grounding(
    client: object,
    topic: str,
    post: str,
    allowed_references: str,
    max_passes: int,
) -> str:
    """Rewrite the post to suppress unsupported concrete claims."""
    if max_passes <= 0:
        return post.strip()

    template = _load_template(FACTUALITY_PROMPT_PATH)
    current = post.strip()

    for pass_num in range(1, max_passes + 1):
        prompt = (
            template
            .replace("{topic}", topic)
            .replace("{allowed_references}", allowed_references.strip() or "- None")
            .replace("{post}", current)
        )
        log_event(
            LOGGER,
            "linkedin_factuality_pass_started",
            level=logging.INFO,
            topic=topic,
            pass_num=pass_num,
        )
        response = responses_create_logged(
            client,
            agent_name="linkedin_agent",
            operation="enforce_factual_grounding_post",
            model=OPENAI_MODEL,
            max_output_tokens=FACTUALITY_MAX_TOKENS,
            input=prompt,
            **openai_generation_kwargs(min(OPENAI_TEMPERATURE, FACTUALITY_TEMPERATURE)),
        )
        revised = response_output_text(response).strip()
        if not revised:
            log_event(
                LOGGER,
                "linkedin_factuality_pass_empty",
                level=logging.WARNING,
                topic=topic,
                pass_num=pass_num,
            )
            break
        current = revised

    return current.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_linkedin_post(
    topic: str,
    article_body: str,
    code_example: str = "",
    allowed_references: str = "",
    factual_passes: int = 0,
) -> str:
    """Generate a polished LinkedIn post for a senior engineering audience.

    Parameters
    ----------
    topic:
        Article topic string used to drive generation.
    article_body:
        Full article markdown body for context.
    code_example:
        Optional Swift snippet from the code agent.
    allowed_references:
        Verified source URLs/titles the post may reference.
    factual_passes:
        Number of factuality-grounding passes to run (0 = skip).

    Returns
    -------
    str
        Constraint-normalised LinkedIn post text.

    Raises
    ------
    FileNotFoundError
        When a required prompt template file is missing.
    RuntimeError
        When the initial generation returns empty output.
    """
    client = create_openai_client()
    snippet_mode = _resolve_snippet_mode(LINKEDIN_CODE_SNIPPET_MODE)

    prompt = _load_template(PROMPT_PATH).format(
        topic=topic,
        allowed_references=allowed_references.strip() or "- None",
        article_body=article_body,
        code_example=code_example.strip() or "No code example provided.",
        snippet_requirement=_snippet_requirement(snippet_mode),
        swift_language_version=SWIFT_LANGUAGE_VERSION,
        swift_language_mode=SWIFT_COMPILER_LANGUAGE_MODE,
    )

    response = responses_create_logged(
        client,
        agent_name="linkedin_agent",
        operation="generate_linkedin_post",
        model=OPENAI_MODEL,
        max_output_tokens=MAX_POST_TOKENS,
        input=prompt,
        **openai_generation_kwargs(min(OPENAI_TEMPERATURE, GENERATION_TEMPERATURE)),
    )

    post = response_output_text(response).strip()
    if not post:
        raise RuntimeError(
            f"LinkedIn post generation returned empty output for topic={topic!r}."
        )

    # Remove accidental H1 title the model occasionally prepends.
    post = _TITLE_H1_RE.sub("", post, count=1).strip()

    log_event(
        LOGGER,
        "linkedin_post_generated",
        level=logging.INFO,
        topic=topic,
        snippet_mode=snippet_mode,
        raw_chars=len(post),
    )

    constrained = _enforce_post_constraints(post, code_example, snippet_mode)

    if factual_passes > 0:
        grounded = _enforce_factual_grounding(
            client=client,
            topic=topic,
            post=constrained,
            allowed_references=allowed_references,
            max_passes=factual_passes,
        )
        constrained = _enforce_post_constraints(grounded, code_example, snippet_mode)

    log_event(
        LOGGER,
        "linkedin_post_finalised",
        level=logging.INFO,
        topic=topic,
        final_chars=len(constrained),
    )
    return constrained
