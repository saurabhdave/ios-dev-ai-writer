"""Structured logging helpers for pipeline observability."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
from typing import Iterator

RUN_ID_CONTEXT: ContextVar[str] = ContextVar("pipeline_run_id", default="")
WORKFLOW_CONTEXT: ContextVar[str] = ContextVar("pipeline_workflow", default="")
DEFAULT_LOG_LEVEL = "INFO"


def _normalize_log_level(level_name: str) -> int:
    """Return a supported stdlib logging level."""
    candidate = level_name.strip().upper()
    return getattr(logging, candidate, logging.INFO)


class JsonLogFormatter(logging.Formatter):
    """Render each log record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }

        run_id = RUN_ID_CONTEXT.get()
        workflow = WORKFLOW_CONTEXT.get()
        if run_id:
            payload["run_id"] = run_id
        if workflow:
            payload["workflow"] = workflow

        event_data = getattr(record, "event_data", None)
        if isinstance(event_data, dict):
            payload.update(event_data)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_structured_logging() -> None:
    """Configure root logging for line-oriented structured logs."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(
        level=_normalize_log_level(os.getenv("PIPELINE_LOG_LEVEL", DEFAULT_LOG_LEVEL)),
        handlers=[handler],
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)


def set_run_context(run_id: str, workflow: str) -> tuple[object, object]:
    """Attach run-scoped context to structured logs."""
    return RUN_ID_CONTEXT.set(run_id), WORKFLOW_CONTEXT.set(workflow)


def reset_run_context(tokens: tuple[object, object]) -> None:
    """Restore previous run-scoped logging context."""
    run_token, workflow_token = tokens
    RUN_ID_CONTEXT.reset(run_token)
    WORKFLOW_CONTEXT.reset(workflow_token)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Emit a structured event with flat key/value fields."""
    logger.log(level, event, extra={"event": event, "event_data": fields})


@contextmanager
def timed_step(
    logger: logging.Logger,
    step: str,
    **fields: object,
) -> Iterator[dict[str, object]]:
    """Time a pipeline step and log started/completed/failed events."""
    step_fields: dict[str, object] = {"step": step, **fields}
    start = time.perf_counter()
    log_event(logger, "pipeline_step_started", **step_fields)

    try:
        yield step_fields
    except Exception as exc:
        log_event(
            logger,
            "pipeline_step_failed",
            level=logging.ERROR,
            elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
            error_type=type(exc).__name__,
            error=str(exc),
            **step_fields,
        )
        raise

    log_event(
        logger,
        "pipeline_step_completed",
        elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
        **step_fields,
    )
