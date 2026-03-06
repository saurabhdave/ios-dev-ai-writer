"""Code agent: generate practical Swift/SwiftUI code examples for a topic."""

from __future__ import annotations

from pathlib import Path

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMPERATURE

PROMPT_PATH = Path("prompts/code_prompt.txt")


def _load_prompt_template() -> str:
    """Load the code generation prompt template."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def generate_code(topic: str) -> str:
    """Generate a practical Swift/SwiftUI snippet tied to the topic."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = _load_prompt_template().format(topic=topic)

    # Generate code only so it can be inserted into a fenced Swift block.
    response = client.responses.create(
        model=OPENAI_MODEL,
        temperature=OPENAI_TEMPERATURE,
        max_output_tokens=1200,
        input=prompt,
    )

    code = response.output_text.strip()
    # Remove markdown code fences if the model includes them accidentally.
    if code.startswith("```"):
        lines = code.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            code = "\n".join(lines[1:-1]).strip()
    if not code:
        raise RuntimeError("Code generation returned empty output.")

    return code
