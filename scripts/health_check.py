"""scripts/health_check.py — flag pipeline-health regressions after a run.

Run after the pipeline finishes (and after ``scripts/update_readme.py``) so the
quality history on disk is the just-completed run. Exits non-zero when one or
more thresholds trip, with a markdown issue body in ``outputs/health_regression.md``
that the CI step uploads via ``gh issue create --body-file``.

Thresholds (override via env vars):
  - HEALTH_MIN_CODEGEN_SUCCESS_PCT (default 70) — last RECENT_RUNS_WINDOW runs
  - HEALTH_MAX_ZERO_COVERAGE_FAMILIES (default 4) — last FAMILY_WINDOW picks
  - HEALTH_MIN_AVG_REVIEW (default 7.5) — last RECENT_RUNS_WINDOW runs

Exit codes:
  0 — healthy or not enough data yet (CI step is a no-op)
  1 — at least one threshold tripped; ``outputs/health_regression.md`` is ready
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
QUALITY_HISTORY_PATH: Final[Path] = ROOT / "outputs" / "quality_history.json"
QUALITY_HISTORY_META_PATH: Final[Path] = ROOT / "outputs" / "quality_history_meta.json"
FAMILY_PICKS_PATH: Final[Path] = ROOT / "memory" / "family_picks.json"
REGRESSION_BODY_PATH: Final[Path] = ROOT / "outputs" / "health_regression.md"

# Windows mirror scripts/update_readme.py and agents/topic_agent.py so the
# regression check uses the same horizons the dashboard reports against.
RECENT_RUNS_WINDOW: Final[int] = 10
FAMILY_WINDOW: Final[int] = 8

# Threshold defaults; env vars override so the bar can be tuned without a code change.
MIN_CODEGEN_SUCCESS_PCT: Final[int] = int(os.environ.get("HEALTH_MIN_CODEGEN_SUCCESS_PCT", "70"))
MAX_ZERO_COVERAGE_FAMILIES: Final[int] = int(os.environ.get("HEALTH_MAX_ZERO_COVERAGE_FAMILIES", "4"))
MIN_AVG_REVIEW: Final[float] = float(os.environ.get("HEALTH_MIN_AVG_REVIEW", "7.5"))

FAMILIES: Final[list[str]] = [
    "architecture",
    "performance",
    "concurrency",
    "swiftui_features",
    "tooling_debugging",
    "frameworks_apis",
    "accessibility_design",
    "migration",
]


def _load(path: Path, default):
    """Return JSON contents of *path* or *default* on any read/parse failure."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _lifetime_total_runs(history: list, meta: object) -> int:
    """Return the lifetime run count from the meta sidecar, or len(history).

    quality_history.json is capped at a trailing window, so len(history) stops
    being the lifetime total once trimming starts; quality_history_meta.json
    persists the cumulative count. A lifetime total can never be smaller than
    the window on disk, so an implausible meta value falls back to len(history).
    """
    if isinstance(meta, dict):
        total = meta.get("total_runs")
        if isinstance(total, int) and total >= len(history):
            return total
    return len(history)


def _avg_review_score(window: list) -> float | None:
    """Average review_overall across *window*, ignoring unscored entries.

    Returns None when no entry carries a numeric score (e.g. self-review was
    disabled for the whole window) so callers can skip the review threshold
    instead of averaging zeros into a false regression.
    """
    scores = [
        float(e["review_overall"])
        for e in window
        if isinstance(e.get("review_overall"), (int, float))
        and not isinstance(e.get("review_overall"), bool)
    ]
    if not scores:
        return None
    return sum(scores) / len(scores)


def main() -> int:
    history = _load(QUALITY_HISTORY_PATH, [])
    if not isinstance(history, list):
        print("quality_history.json is not a list; skipping health check.")
        return 0

    fam_payload = _load(FAMILY_PICKS_PATH, {"picks": []})
    picks = fam_payload.get("picks", []) if isinstance(fam_payload, dict) else []
    picks = [p for p in picks if isinstance(p, str)]

    if len(history) < RECENT_RUNS_WINDOW:
        print(
            f"Only {len(history)} runs in quality_history.json — need "
            f"{RECENT_RUNS_WINDOW} for a stable health check. Skipping."
        )
        return 0

    window = history[-RECENT_RUNS_WINDOW:]
    fam_window = picks[-FAMILY_WINDOW:]

    code_paths = Counter(e.get("code_path", "unknown") for e in window)
    direct = code_paths.get("direct", 0)
    repaired = code_paths.get("repaired", 0)
    omitted = code_paths.get("omitted", 0)
    success_pct = round(((direct + repaired) / len(window)) * 100)

    avg_review = _avg_review_score(window)

    fam_counts = Counter(fam_window)
    zero_cov = [f for f in FAMILIES if fam_counts.get(f, 0) == 0]

    breaches: list[str] = []
    if success_pct < MIN_CODEGEN_SUCCESS_PCT:
        breaches.append(
            f"- **Codegen success {success_pct}%** (threshold {MIN_CODEGEN_SUCCESS_PCT}%) — "
            f"{direct} direct, {repaired} repaired, {omitted} omitted in last {len(window)} runs"
        )
    if len(zero_cov) > MAX_ZERO_COVERAGE_FAMILIES:
        breaches.append(
            f"- **Zero-coverage families: {len(zero_cov)} of {len(FAMILIES)}** "
            f"(threshold ≤{MAX_ZERO_COVERAGE_FAMILIES}) — missing: `{', '.join(zero_cov)}`"
        )
    if avg_review is not None and avg_review < MIN_AVG_REVIEW:
        breaches.append(
            f"- **Avg review score {avg_review:.1f}/10** (threshold {MIN_AVG_REVIEW}) "
            f"over last {len(window)} runs"
        )

    avg_review_label = f"{avg_review:.1f}/10" if avg_review is not None else "n/a (no scored runs)"

    if not breaches:
        print(
            f"Pipeline health OK — codegen success {success_pct}%, "
            f"avg review {avg_review_label}, "
            f"{len(zero_cov)} zero-coverage families."
        )
        return 0

    total_runs = _lifetime_total_runs(history, _load(QUALITY_HISTORY_META_PATH, None))
    date_range = f"{history[0].get('date', '?')} → {history[-1].get('date', '?')}"
    rotation = " → ".join(reversed(fam_window)) if fam_window else "_(no picks yet)_"

    body = (
        "## Pipeline health regression detected\n\n"
        "The post-run health check tripped one or more thresholds:\n\n"
        + "\n".join(breaches)
        + "\n\n"
        "**Context:**\n"
        f"- Total runs: {total_runs} (window on disk: {len(history)}, {date_range})\n"
        f"- Last {len(window)} runs — codegen: {direct} direct, {repaired} repaired, {omitted} omitted\n"
        f"- Avg review: {avg_review_label}\n"
        f"- Recent family rotation (newest first): {rotation}\n\n"
        "**Where to dig:**\n"
        "- `outputs/quality_history.json` — full run-level metrics\n"
        "- `outputs/codegen/*.json` — `diagnostics_excerpt` for omitted/repaired runs\n"
        "- `agents/topic_agent.py` — `_sample_topic_family` if rotation is stuck\n\n"
        "Auto-created by `scripts/health_check.py` via `.github/workflows/weekly.yml`.\n"
    )

    REGRESSION_BODY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGRESSION_BODY_PATH.write_text(body, encoding="utf-8")
    print(body)
    return 1


if __name__ == "__main__":
    sys.exit(main())
