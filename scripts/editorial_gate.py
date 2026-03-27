#!/usr/bin/env python3
"""
scripts/editorial_gate.py  (ios-dev-ai-writer)
Pre-push editorial gate — runs after content generation, before syncing to
ios-ai-articles.  Failed articles are moved to quarantine/ so they are never
published but are preserved for debugging.

Checks:
  1. No validated code  — codegen path == "omitted"
  2. Banned deprecated Swift APIs in code blocks
  3. New article title duplicates an already-published article title
     (Jaccard > 0.5 on filtered tokens, compared against existing-articles-dir)
  4. Orphaned newsletter — Big Story title not in newly generated articles

Usage (called from weekly.yml):
  python scripts/editorial_gate.py \
    --articles-dir    articles \
    --linkedin-dir    linkedin \
    --codegen-dir     codegen \
    --newsletter-dir  newsletter \
    --quarantine-dir  quarantine \
    --existing-articles-dir content_repo/articles

Exit codes:
  0 — all content passed (or quarantine was empty)
  1 — one or more files quarantined (pipeline should continue; only valid
      content will be synced)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ARTICLE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")
NEWSLETTER_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}-.+)\.(md|html)$")

BANNED_APIS: list[str] = [
    "@Published",
    "@ObservableObject",
    "os_signpost(",
]

JACCARD_THRESHOLD = 0.50

# Common boilerplate words in iOS/Swift article titles — not discriminating
# for topic identity, filtered before Jaccard comparison.
TITLE_STOPWORDS: frozenset[str] = frozenset({
    "migrate", "migrating", "migration",
    "swift", "swiftui", "ios",
    "to", "from", "for", "with", "using", "and", "the", "a", "an", "in",
    "on", "of", "at",
    "patterns", "pattern",
    "apps", "app",
    "production",
})

BIG_STORY_SECTION = "### This Week's Big Story"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def quarantine(path: str, quarantine_dir: str, moved: list[str]) -> None:
    """Move a file to quarantine_dir, creating it if needed."""
    if not os.path.exists(path):
        return
    os.makedirs(quarantine_dir, exist_ok=True)
    dest = os.path.join(quarantine_dir, os.path.basename(path))
    shutil.move(path, dest)
    moved.append(f"{path} → {dest}")


def get_h1(filepath: str) -> str | None:
    try:
        with open(filepath, encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if s.startswith("# "):
                    return s[2:].strip()
    except OSError:
        pass
    return None


def extract_swift_blocks(content: str) -> list[str]:
    return re.findall(r"```swift\n(.*?)```", content, re.DOTALL)


def tokenize(title: str) -> set[str]:
    raw = {t for t in re.split(r"[^a-z0-9]+", title.lower()) if t}
    return raw - TITLE_STOPWORDS


def jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def slug_from_filename(filename: str) -> str | None:
    m = ARTICLE_PATTERN.match(filename)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def read_codegen_path_field(slug: str, codegen_dir: str) -> str:
    path = os.path.join(codegen_dir, f"{slug}-codegen.json")
    if not os.path.exists(path):
        return "missing"
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("path", "missing")
    except (OSError, json.JSONDecodeError):
        return "missing"


def quarantine_article_set(
    slug: str,
    articles_dir: str,
    linkedin_dir: str,
    codegen_dir: str,
    quarantine_dir: str,
    moved: list[str],
) -> None:
    quarantine(os.path.join(articles_dir, f"{slug}.md"), quarantine_dir, moved)
    quarantine(os.path.join(linkedin_dir, f"{slug}-linkedin.md"), quarantine_dir, moved)
    quarantine(os.path.join(codegen_dir, f"{slug}-codegen.json"), quarantine_dir, moved)


def extract_big_story_title(newsletter_md: str) -> str | None:
    try:
        with open(newsletter_md, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return None
    idx = content.find(BIG_STORY_SECTION)
    if idx == -1:
        return None
    m = re.search(r"\*\*([^*\n]+)\*\*", content[idx + len(BIG_STORY_SECTION):])
    return m.group(1).strip() if m else None


def collect_h1s(directory: str) -> set[str]:
    """Collect all article H1 titles from a directory."""
    titles: set[str] = set()
    if not os.path.isdir(directory):
        return titles
    for filename in os.listdir(directory):
        if not ARTICLE_PATTERN.match(filename):
            continue
        h1 = get_h1(os.path.join(directory, filename))
        if h1:
            titles.add(h1.strip())
    return titles


# ---------------------------------------------------------------------------
# Check 1 — No validated code
# ---------------------------------------------------------------------------

def check_no_validated_code(
    slugs: list[str],
    articles_dir: str,
    linkedin_dir: str,
    codegen_dir: str,
    quarantine_dir: str,
    blocked: dict[str, list[str]],
    moved: list[str],
) -> None:
    for slug in slugs:
        if read_codegen_path_field(slug, codegen_dir) == "omitted":
            blocked.setdefault(slug, []).append(
                "codegen path == 'omitted' (no validated Swift code)"
            )
            quarantine_article_set(
                slug, articles_dir, linkedin_dir, codegen_dir, quarantine_dir, moved
            )


# ---------------------------------------------------------------------------
# Check 2 — Banned deprecated APIs
# ---------------------------------------------------------------------------

def check_banned_apis(
    slugs: list[str],
    articles_dir: str,
    linkedin_dir: str,
    codegen_dir: str,
    quarantine_dir: str,
    blocked: dict[str, list[str]],
    moved: list[str],
) -> None:
    for slug in slugs:
        path = os.path.join(articles_dir, f"{slug}.md")
        if not os.path.exists(path):
            continue
        try:
            content = open(path, encoding="utf-8").read()
        except OSError:
            continue
        combined = "\n".join(extract_swift_blocks(content))
        hits = [api for api in BANNED_APIS if api in combined]
        if hits:
            blocked.setdefault(slug, []).append(
                f"banned deprecated API(s) in Swift code: {', '.join(hits)}"
            )
            quarantine_article_set(
                slug, articles_dir, linkedin_dir, codegen_dir, quarantine_dir, moved
            )


# ---------------------------------------------------------------------------
# Check 3 — Duplicate vs already-published articles
# ---------------------------------------------------------------------------

def check_duplicate_vs_existing(
    slugs: list[str],
    articles_dir: str,
    linkedin_dir: str,
    codegen_dir: str,
    quarantine_dir: str,
    existing_articles_dir: str,
    blocked: dict[str, list[str]],
    moved: list[str],
) -> None:
    """
    Compare each new article's H1 against already-published article H1s.
    If Jaccard > threshold, quarantine the new article (don't publish duplicate).
    """
    existing_h1s: list[tuple[str, set[str]]] = []
    for filename in os.listdir(existing_articles_dir) if os.path.isdir(existing_articles_dir) else []:
        if not ARTICLE_PATTERN.match(filename):
            continue
        h1 = get_h1(os.path.join(existing_articles_dir, filename))
        if h1:
            existing_h1s.append((h1, tokenize(h1)))

    for slug in slugs:
        path = os.path.join(articles_dir, f"{slug}.md")
        if not os.path.exists(path):
            continue
        new_h1 = get_h1(path)
        if not new_h1:
            continue
        new_tokens = tokenize(new_h1)
        for existing_title, existing_tokens in existing_h1s:
            score = jaccard(new_tokens, existing_tokens)
            if score > JACCARD_THRESHOLD:
                blocked.setdefault(slug, []).append(
                    f"near-duplicate of already-published '{existing_title}' "
                    f"(Jaccard={score:.2f})"
                )
                quarantine_article_set(
                    slug, articles_dir, linkedin_dir, codegen_dir, quarantine_dir, moved
                )
                break  # one match is enough to quarantine


# ---------------------------------------------------------------------------
# Check 4 — Orphaned newsletter
# ---------------------------------------------------------------------------

def check_orphaned_newsletter(
    articles_dir: str,
    newsletter_dir: str,
    quarantine_dir: str,
    blocked_newsletters: dict[str, list[str]],
    moved: list[str],
) -> None:
    """
    Remove newsletter pairs whose Big Story title doesn't match any
    article in the newly generated articles_dir.
    """
    new_h1s = collect_h1s(articles_dir)

    basename_map: dict[str, list[str]] = {}
    for filename in os.listdir(newsletter_dir) if os.path.isdir(newsletter_dir) else []:
        m = NEWSLETTER_PATTERN.match(filename)
        if m:
            basename_map.setdefault(m.group(1), []).append(filename)

    for base, files in sorted(basename_map.items()):
        md_file = next((f for f in files if f.endswith(".md")), None)
        if not md_file:
            continue
        big_story = extract_big_story_title(os.path.join(newsletter_dir, md_file))
        if big_story is None:
            reason = "Big Story title could not be parsed"
        elif big_story not in new_h1s:
            reason = f"Big Story '{big_story}' has no matching new article H1"
        else:
            continue
        blocked_newsletters.setdefault(base, []).append(reason)
        for f in sorted(files):
            quarantine(os.path.join(newsletter_dir, f), quarantine_dir, moved)


# ---------------------------------------------------------------------------
# CLI + main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Pre-push editorial gate for ios-dev-ai-writer."
    )
    p.add_argument("--articles-dir", default="articles")
    p.add_argument("--linkedin-dir", default="linkedin")
    p.add_argument("--codegen-dir", default="codegen")
    p.add_argument("--newsletter-dir", default="newsletter")
    p.add_argument("--quarantine-dir", default="quarantine")
    p.add_argument(
        "--existing-articles-dir",
        default="content_repo/articles",
        help="Path to already-published articles for duplicate detection.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    print("=== Editorial Gate (pre-push) ===\n")

    slugs: list[str] = sorted(
        filter(None, (slug_from_filename(f) for f in os.listdir(args.articles_dir)))
    ) if os.path.isdir(args.articles_dir) else []

    blocked: dict[str, list[str]] = {}
    blocked_newsletters: dict[str, list[str]] = {}
    moved: list[str] = []

    check_no_validated_code(
        slugs, args.articles_dir, args.linkedin_dir,
        args.codegen_dir, args.quarantine_dir, blocked, moved,
    )
    check_banned_apis(
        slugs, args.articles_dir, args.linkedin_dir,
        args.codegen_dir, args.quarantine_dir, blocked, moved,
    )
    check_duplicate_vs_existing(
        slugs, args.articles_dir, args.linkedin_dir,
        args.codegen_dir, args.quarantine_dir,
        args.existing_articles_dir, blocked, moved,
    )
    check_orphaned_newsletter(
        args.articles_dir, args.newsletter_dir,
        args.quarantine_dir, blocked_newsletters, moved,
    )

    # ---- Summary --------------------------------------------------------
    if not blocked and not blocked_newsletters:
        print("All generated content passed the editorial gate.")
        return 0

    if blocked:
        print(f"QUARANTINED ARTICLES ({len(blocked)}):")
        for slug, reasons in blocked.items():
            print(f"  {slug}")
            for r in reasons:
                print(f"    reason: {r}")

    if blocked_newsletters:
        print(f"\nQUARANTINED NEWSLETTERS ({len(blocked_newsletters)}):")
        for base, reasons in blocked_newsletters.items():
            print(f"  {base}")
            for r in reasons:
                print(f"    reason: {r}")

    print(f"\nMoved {len(moved)} file(s) to quarantine:")
    for m in moved:
        print(f"  {m}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
