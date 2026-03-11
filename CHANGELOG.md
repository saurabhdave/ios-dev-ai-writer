# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [0.1.5] - 2026-03-11

### Added
- Added strict code generation observability artifacts under `outputs/codegen/` with generation path (`direct`, `repaired`, `omitted`) and repair-attempt diagnostics.
- Added configurable code generation failure policy via `CODEGEN_FAILURE_MODE` (`omit` or `error`).

### Changed
- Updated LinkedIn post generation to make code snippets optional by policy, and to keep snippets only when they are validated and comment-annotated.
- Removed generic fallback code snippets from article generation to avoid publishing irrelevant examples.
- Strengthened article reference filtering with topic/domain quality scoring to reduce low-signal sources.
- Updated automation and docs to include new LinkedIn/codegen controls and generated artifact folders.

## [0.1.4] - 2026-03-10

### Added
- Added LinkedIn post generation agent to produce professional promotional posts with emojis and hashtags.
- Added dedicated LinkedIn prompt template and output artifacts under `outputs/linkedin/`.
- Integrated LinkedIn post generation into weekly pipeline with configurable toggle (`LINKEDIN_POST_ENABLED`).
- Updated GitHub Actions weekly commit step to include `outputs/linkedin/` files.

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
