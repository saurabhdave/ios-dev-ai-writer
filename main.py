"""CLI entrypoint for generating the weekly iOS Medium article."""

from __future__ import annotations

import logging

from workflows.weekly_pipeline import run_weekly_pipeline
from utils.observability import configure_structured_logging, get_logger, log_event

LOGGER = get_logger("pipeline.cli")


def main() -> None:
    """Run the weekly pipeline and print output location."""
    configure_structured_logging()
    log_event(LOGGER, "cli_started")
    try:
        output_path = run_weekly_pipeline()
    except Exception as exc:
        log_event(
            LOGGER,
            "cli_failed",
            level=logging.ERROR,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise

    log_event(LOGGER, "cli_completed", output_path=str(output_path))
    print(f"Article generated at: {output_path}")


if __name__ == "__main__":
    main()
