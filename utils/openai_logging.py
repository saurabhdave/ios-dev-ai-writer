"""Shared OpenAI client helpers with request-level observability."""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping

import openai
from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from config import OPENAI_API_KEY
from utils.observability import get_logger, log_event

LLM_LOGGER = get_logger("pipeline.llm")

_RETRYABLE_ERRORS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)

_retry_kwargs = dict(
    retry=retry_if_exception_type(_RETRYABLE_ERRORS),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    before_sleep=before_sleep_log(LLM_LOGGER, logging.WARNING),
    reraise=True,
)


@retry(**_retry_kwargs)
def _call_responses_api(client: OpenAI, **kwargs: Any) -> Any:
    return client.responses.create(**kwargs)


@retry(**_retry_kwargs)
def embeddings_create_with_retry(client: OpenAI, **kwargs: Any) -> Any:
    return client.embeddings.create(**kwargs)


def create_openai_client() -> OpenAI:
    """Return an authenticated OpenAI client or raise a clear error."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _usage_field(value: Any, field: str) -> int | None:
    """Read token usage fields from SDK model objects or dict-like values."""
    if value is None:
        return None
    if isinstance(value, dict):
        raw = value.get(field)
    else:
        raw = getattr(value, field, None)
    return raw if isinstance(raw, int) else None


def _extract_usage(response: Any) -> dict[str, int | None]:
    """Normalize token usage fields across SDK response shapes."""
    usage = getattr(response, "usage", None)
    output_details = None if usage is None else getattr(usage, "output_tokens_details", None)
    if isinstance(usage, dict):
        output_details = usage.get("output_tokens_details")

    return {
        "input_tokens": _usage_field(usage, "input_tokens"),
        "output_tokens": _usage_field(usage, "output_tokens"),
        "total_tokens": _usage_field(usage, "total_tokens"),
        "reasoning_tokens": _usage_field(output_details, "reasoning_tokens"),
    }


def response_output_text(response: Any) -> str:
    """Return the response text payload as a plain string.

    Some SDK responses may expose ``output_text=None`` when no text content was
    produced. Callers use this helper so empty-output handling remains explicit
    and consistent instead of crashing on ``None.strip()``.
    """
    text = getattr(response, "output_text", "")
    return text if isinstance(text, str) else ""


def responses_create_logged(
    client: OpenAI,
    *,
    agent_name: str,
    operation: str,
    model: str,
    max_output_tokens: int,
    input: str,
    log_fields: Mapping[str, object] | None = None,
    **kwargs: Any,
) -> Any:
    """Call the Responses API and emit structured timing/token logs."""
    base_fields = {
        "agent_name": agent_name,
        "operation": operation,
        "model": model,
        "max_output_tokens": max_output_tokens,
        "input_chars": len(input),
    }
    if log_fields:
        base_fields.update(dict(log_fields))

    start = time.perf_counter()
    try:
        response = _call_responses_api(
            client,
            model=model,
            max_output_tokens=max_output_tokens,
            input=input,
            **kwargs,
        )
    except Exception as exc:
        log_event(
            LLM_LOGGER,
            "llm_call_failed",
            level=logging.ERROR,
            elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
            error_type=type(exc).__name__,
            error=str(exc),
            **base_fields,
        )
        raise

    usage_fields = _extract_usage(response)
    log_event(
        LLM_LOGGER,
        "llm_call_completed",
        elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
        response_id=getattr(response, "id", ""),
        **base_fields,
        **usage_fields,
    )
    return response
