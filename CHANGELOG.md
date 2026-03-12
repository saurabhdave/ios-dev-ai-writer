# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

### Changed
- Consolidated Python dependency installation on `pyproject.toml` and removed the duplicate `requirements.txt` source of truth.

## [0.1.8] - 2026-03-12

### Changed
- Simplified topic generation to Apple-platform programming only (Swift/SwiftUI/UIKit/Xcode scope) and removed AI-only/hybrid title routing.
- Updated topic prompt constraints and fallback titles to explicitly block AI-first framing in generated topics.
- Replaced AI-centric default topic interests and trend queries with Apple ecosystem engineering interests.
- Tightened trend filtering and custom trend sources to prioritize Apple-platform development signals.
- Added stronger migration/deprecation topic bias (legacy/deprecated Apple patterns to modern Swift 6+ approaches) across topic interests and trend queries.
- Expanded topic preferences to include async/await, Structured Concurrency, performance tuning, App Intents/Apple Intelligence APIs, Xcode tips, and verified SwiftUI modifier guidance.
- Added new prioritized topic themes for `Swift 6.3 Macros` and `Reducing Boilerplate in Real Projects`.
- Strengthened prompt quality for senior, practical, architecture-level article and LinkedIn outputs.
- Enforced Swift 6 Observation-first code examples (`@Observable`) and added style validation to prevent legacy `ObservableObject`/`@Published` patterns in generated snippets.
- Strengthened trustworthiness by prioritizing trusted technical domains for published references and tightening verifiability language in article/LinkedIn prompts.

## [0.1.7] - 2026-03-12

### Changed
- Switched automation and documented defaults to use OpenAI model `gpt-5-mini`.
- Updated OpenAI request handling for GPT-5 compatibility by omitting unsupported `temperature` and supplying configurable reasoning effort (`OPENAI_REASONING_EFFORT`).
- Updated `.github/workflows/weekly.yml` schedule from once weekly to three runs per week (Monday, Wednesday, Friday at 10:00 UTC).
- Refreshed README model/scheduling/version-tag examples and release references for consistency.
- Aligned package metadata version in `pyproject.toml` with `VERSION` for release management consistency.

## [0.1.6] - 2026-03-11

### Added
- Added `SwiftLee` (`https://www.avanderlee.com/feed/`) to custom trend RSS sources.
- Added factual grounding passes for article and LinkedIn generation to rewrite unsupported/hypothetical claims conservatively.
- Added Swift targeting controls via `SWIFT_LANGUAGE_VERSION` and `SWIFT_COMPILER_LANGUAGE_MODE`.
- Added Swift target metadata fields (`swift_language_version`, `swift_language_mode`) in codegen artifacts.

### Changed
- Elevated `avanderlee.com` into high-quality reference scoring for citation selection.
- Enforced topic mode compliance (`ios_only`, `ai_only`, `hybrid`) with balanced selection and mode-safe fallbacks.
- Strengthened Medium layout constraints and rubric checks (intro hook requirement, scannable lists, pull-quote, and heading hierarchy discipline).
- Updated code/LinkedIn prompts and validators to target Swift `6.2.4` semantics while gracefully falling back on older local `swiftc` toolchains.
- Updated README env configuration and version/release examples.

## [0.1.5] - 2026-03-11

### Added
- Added strict code generation observability artifacts under `outputs/codegen/` with generation path (`direct`, `repaired`, `omitted`) and repair-attempt diagnostics.
- Added configurable code generation failure policy via `CODEGEN_FAILURE_MODE` (`omit` or `error`).

### Changed
- Updated LinkedIn post generation to make code snippets optional by policy, and to keep snippets only when they are validated and comment-annotated.
- Removed generic fallback code snippets from article generation to avoid publishing irrelevant examples.
- Switched code snippet validation defaults to snippet-safe parsing (`CODEGEN_VALIDATION_MODE=snippet`) so article examples are not over-rejected by strict compile-only checks.
- Added Swift-book-guided repair constraints plus advisory unknown-symbol diagnostics to reduce typo/unknown API usage in published snippets.
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
