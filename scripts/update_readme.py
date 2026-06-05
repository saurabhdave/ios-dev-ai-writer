"""scripts/update_readme.py — regenerate the Pipeline Health section in README.md.

Reads:
  - outputs/quality_history.json — historical run records (always present, committed)
  - memory/family_picks.json     — chronological topic-family rotation

Writes:
  - README.md (replaces content between PIPELINE_HEALTH_START and PIPELINE_HEALTH_END)

Idempotent. Run locally with `python scripts/update_readme.py` or as a CI step
after the weekly pipeline finishes. If the markers are missing the script exits
non-zero so misconfiguration surfaces in CI rather than silently doing nothing.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
QUALITY_HISTORY_PATH: Final[Path] = ROOT / "outputs" / "quality_history.json"
FAMILY_PICKS_PATH: Final[Path] = ROOT / "memory" / "family_picks.json"
README_PATH: Final[Path] = ROOT / "README.md"

START_MARKER: Final[str] = "<!-- PIPELINE_HEALTH_START -->"
END_MARKER: Final[str] = "<!-- PIPELINE_HEALTH_END -->"

# Window sizes mirror those in agents/topic_agent.py so the README reflects
# the same horizons the pipeline itself uses.
RECENT_RUNS_WINDOW: Final[int] = 10
RECENT_FAMILY_WINDOW: Final[int] = 8

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

_NON_IOS_PLATFORM_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(watchos|visionos|macos|ipados|realitykit|arkit|widgetkit|"
    r"appkit|carplay|tvos|mac\s*catalyst)\b",
    re.IGNORECASE,
)

# Topic family classifier — order matters; first match wins. Keep in sync
# with the artifact dashboard at outputs/ios-dev-ai-writer-gaps.html.
# All stems use \w* suffixes — a bare \bconcurrenc\b only matches "concurrenc"
# itself, not "concurrency", which previously sent ~27% of titles to 'other'.
_FAMILY_RULES: Final[list[tuple[str, re.Pattern[str]]]] = [
    ("migration",            re.compile(r"\b(migrat\w*|deprecat\w*|legacy|combine to|urlsession to)\b", re.I)),
    ("frameworks_apis",      re.compile(r"\b(app intent\w*|appintent\w*|apple intelligence|widgetkit|macro\w*|foundation model\w*|realitykit|arkit)\b", re.I)),
    ("accessibility_design", re.compile(r"\b(accessibilit\w*|voiceover|dynamic type|dark mode|color scheme|haptic\w*)\b", re.I)),
    ("concurrency",          re.compile(r"\b(async|await|actor\w*|task group|concurren\w*|combine|continuation\w*)\b", re.I)),
    ("performance",          re.compile(r"\b(profil\w*|instrument\w*|memory leak|memory graph|launch time|signpost\w*|ossignposter|time profiler|hang detection|on-device)\b", re.I)),
    ("tooling_debugging",    re.compile(r"\b(xcode build|swift package\w*|preview\w*|build time|explicit (swift )?module\w*|swift testing|swift module\w*)\b", re.I)),
    ("architecture",         re.compile(r"\b(architectur\w*|dependency injection|modular|navigationstack|composable navig|mvvm)\b", re.I)),
    ("swiftui_features",     re.compile(r"\b(swiftui|layout protocol|modifier\w*|environmentkey|phaseanimator|keyframe\w*|view builder)\b", re.I)),
]


def _classify_family(topic: str) -> str:
    """Return the topic-family bucket for *topic*, or 'other'."""
    for family, regex in _FAMILY_RULES:
        if regex.search(topic or ""):
            return family
    return "other"


def _load_json(path: Path, default):
    """Return the JSON contents of *path* or *default* on any read/parse failure."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def render_pipeline_health() -> str:
    """Return the full markdown block including START/END markers."""
    history = _load_json(QUALITY_HISTORY_PATH, [])
    family_payload = _load_json(FAMILY_PICKS_PATH, {"picks": []})
    family_picks = family_payload.get("picks", []) if isinstance(family_payload, dict) else []

    if not history:
        return (
            f"{START_MARKER}\n"
            "## Pipeline Health\n\n"
            "_No `outputs/quality_history.json` yet — the first pipeline run will populate this section._\n"
            f"{END_MARKER}"
        )

    runs_window = history[-RECENT_RUNS_WINDOW:]
    family_window = family_picks[-RECENT_FAMILY_WINDOW:]
    platform_window = history[-RECENT_FAMILY_WINDOW:]

    code_paths = Counter(e.get("code_path", "unknown") for e in runs_window)
    direct = code_paths.get("direct", 0)
    repaired = code_paths.get("repaired", 0)
    omitted = code_paths.get("omitted", 0)
    success_pct = round(((direct + repaired) / len(runs_window)) * 100) if runs_window else 0
    avg_review = (
        sum(float(e.get("review_overall", 0) or 0) for e in runs_window) / len(runs_window)
        if runs_window
        else 0.0
    )

    cross_platform = sum(
        1 for e in platform_window if _NON_IOS_PLATFORM_RE.search(e.get("topic", ""))
    )

    family_counts = Counter(family_window)
    zero_coverage = [f for f in FAMILIES if family_counts.get(f, 0) == 0]

    date_range = f"{history[0].get('date', '?')} → {history[-1].get('date', '?')}"
    rotation_str = (
        " → ".join(reversed(family_window)) if family_window else "_(no picks recorded yet)_"
    )

    lines = [
        START_MARKER,
        "## Pipeline Health",
        "",
        f"_Auto-generated from `outputs/quality_history.json` and `memory/family_picks.json` "
        f"by `scripts/update_readme.py`._",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total runs | {len(history)} ({date_range}) |",
        f"| Codegen success (last {len(runs_window)}) | "
        f"{success_pct}% — {direct} direct, {repaired} repaired, {omitted} omitted |",
        f"| Avg review score (last {len(runs_window)}) | {avg_review:.1f} / 10 |",
        f"| Cross-platform topics (last {len(platform_window)}) | "
        f"{cross_platform} of {len(platform_window)} mention non-iOS Apple platforms |",
        f"| Zero-coverage families (last {len(family_window)}) | "
        f"{len(zero_coverage)} of {len(FAMILIES)}"
        + (f" — `{', '.join(zero_coverage)}`" if zero_coverage else " — all covered")
        + " |",
        "",
        f"**Recent topic-family rotation (newest first):** {rotation_str}",
        "",
        END_MARKER,
    ]
    return "\n".join(lines)


def update_readme() -> int:
    """Replace the PIPELINE_HEALTH block in README.md. Returns shell exit code."""
    if not README_PATH.exists():
        print(f"README.md not found at {README_PATH}", file=sys.stderr)
        return 1

    new_block = render_pipeline_health()
    readme = README_PATH.read_text(encoding="utf-8")

    block_re = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        re.DOTALL,
    )
    if not block_re.search(readme):
        print(
            f"PIPELINE_HEALTH markers not found in README.md. "
            f"Add the following pair where you want the section to appear:\n"
            f"  {START_MARKER}\n  {END_MARKER}",
            file=sys.stderr,
        )
        return 1

    new_readme = block_re.sub(new_block, readme)

    if new_readme == readme:
        print("README.md already up to date.")
        return 0

    README_PATH.write_text(new_readme, encoding="utf-8")
    print("README.md updated with fresh pipeline health metrics.")
    return 0


if __name__ == "__main__":
    sys.exit(update_readme())
