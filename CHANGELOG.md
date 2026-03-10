# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [0.1.3] - 2026-03-10

### Changed
- Added explicit priority topic interests for AI, AI agents, AI automation, agentic AI/workflows, and generative AI.
- Added configurable topic composition mode (`TOPIC_MODE`) to avoid always combining iOS + AI in one title.
- Broadened trend scanning with dedicated source coverage for `x.com`, `dev.to`, and `medium.com`.
- Added direct RSS ingestion for `dev.to` and `medium.com` tags, alongside scoped web/social queries.
- Updated default trend source set to include `social` and `platforms` scanners.

## [0.1.2] - 2026-03-10

### Changed
- Strengthened topic generation with anti-repetition checks and stricter publication-style title limits.
- Added quality editor pass (`editor_agent`) for clearer, more professional Medium-ready article bodies.
- Tightened prompts for article/outline/code quality and reduced filler content.
- Added safeguards to strip unapproved URLs from article body and keep references controlled.
- Improved trend quality filtering to reduce noisy/non-iOS/low-signal source items.
- Updated pipeline formatting to produce cleaner Medium-style output sections.

## [0.1.1] - 2026-03-07

### Changed
- Added release and versioning baseline files (`VERSION`, `pyproject.toml`, `.python-version`).
- Added release automation via `.github/workflows/release.yml`.
- Updated README with repo metadata, release process, and badge/version alignment.
- Pinned dependency ranges in `requirements.txt`.

## [0.1.0] - 2026-03-07

### Added
- Initial AI writer pipeline for weekly iOS Medium-style content generation.
- Modular agents for topic, outline, article, and Swift/SwiftUI code generation.
- Automatic trend discovery from HackerNews, Reddit, Apple feeds, WWDC, and viral web proxies.
- Custom trend extension via `scanners/custom_trends.json`.
- Weekly GitHub Actions automation for content generation and commits.
- Trend snapshot persistence under `outputs/trends/`.
