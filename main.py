"""CLI entrypoint for generating the weekly iOS Medium article."""

from __future__ import annotations

from workflows.weekly_pipeline import run_weekly_pipeline


def main() -> None:
    """Run the weekly pipeline and print output location."""
    output_path = run_weekly_pipeline()
    print(f"Article generated at: {output_path}")


if __name__ == "__main__":
    main()
