# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

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
