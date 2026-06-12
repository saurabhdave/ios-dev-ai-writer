#!/usr/bin/env python3
"""Trigger several sequential pipeline runs to publish multiple articles.

A thin orchestrator over the existing ``workflow_dispatch`` interface — it
changes nothing about the pipeline or CI design. Each topic becomes one
manual run with ``multi_per_day=true`` so same-date articles accumulate in
the content repo instead of replacing each other.

Usage:
    python scripts/publish_burst.py "Topic One" "Topic Two" ...
    python scripts/publish_burst.py --topics-file topics.txt
    python scripts/publish_burst.py --replace "Single replacement topic"

Requires the GitHub CLI (``gh``) authenticated against the repo. Runs are
strictly sequential: parallel runs would race on the state commit and the
content-repo push.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

REPO = "saurabhdave/ios-dev-ai-writer"
WORKFLOW = "weekly.yml"

TITLE_MAX_CHARS = 60
TITLE_MAX_WORDS = 10

#: Seconds to wait for the dispatched run to appear in the run list.
DISPATCH_SETTLE_SECONDS = 8

#: Hard per-run timeout (the pipeline normally finishes in ~5 minutes).
RUN_TIMEOUT_SECONDS = 20 * 60


def _gh(*args: str, timeout: int | None = None) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout.strip()


def _latest_run_id() -> int:
    payload = _gh(
        "run", "list",
        "--repo", REPO,
        "--workflow", WORKFLOW,
        "--limit", "1",
        "--json", "databaseId",
    )
    return json.loads(payload)[0]["databaseId"]


def _run_one(topic: str, multi_per_day: bool) -> bool:
    print(f"\n▶ Triggering: {topic!r} (multi_per_day={str(multi_per_day).lower()})")
    _gh(
        "workflow", "run", WORKFLOW,
        "--repo", REPO,
        "-f", f"forced_topic={topic}",
        "-f", f"multi_per_day={str(multi_per_day).lower()}",
    )
    time.sleep(DISPATCH_SETTLE_SECONDS)
    run_id = _latest_run_id()
    print(f"  run: https://github.com/{REPO}/actions/runs/{run_id}")
    try:
        subprocess.run(
            ["gh", "run", "watch", str(run_id), "--repo", REPO, "--exit-status"],
            stdout=subprocess.DEVNULL,
            timeout=RUN_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.CalledProcessError:
        print(f"  ✗ FAILED — see https://github.com/{REPO}/actions/runs/{run_id}")
        return False
    except subprocess.TimeoutExpired:
        print(f"  ✗ TIMED OUT after {RUN_TIMEOUT_SECONDS}s — check the run page")
        return False
    print("  ✓ completed")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topics", nargs="*", help="Forced topics, one run each.")
    parser.add_argument(
        "--topics-file",
        help="File with one topic per line (blank lines and # comments skipped).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Use the default same-date replacement instead of accumulating.",
    )
    args = parser.parse_args()

    topics = list(args.topics)
    if args.topics_file:
        with open(args.topics_file, encoding="utf-8") as fh:
            topics += [
                line.strip() for line in fh
                if line.strip() and not line.lstrip().startswith("#")
            ]
    if not topics:
        parser.error("No topics given (positional args or --topics-file).")

    for topic in topics:
        if len(topic) > TITLE_MAX_CHARS or len(topic.split()) > TITLE_MAX_WORDS:
            print(
                f"✗ Topic exceeds title limits ({TITLE_MAX_CHARS} chars / "
                f"{TITLE_MAX_WORDS} words): {topic!r}"
            )
            return 2

    print(f"Burst: {len(topics)} sequential run(s) on {REPO}")
    completed = 0
    for topic in topics:
        if not _run_one(topic, multi_per_day=not args.replace):
            print(f"\nAborting burst: {completed}/{len(topics)} runs completed.")
            return 1
        completed += 1

    print(f"\n✓ Burst complete: {completed}/{len(topics)} articles published.")
    print("Content repo: https://github.com/saurabhdave/ios-ai-articles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
