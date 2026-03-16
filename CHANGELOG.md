# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

## [0.5.0] - 2026-03-16

### Removed
- **Cover image generation** (`agents/image_agent.py`) removed entirely. `GOOGLE_API_KEY`, `IMAGE_GENERATION_ENABLED`, `IMAGEN_MODEL`, and `OUTPUT_IMAGES_DIR` config vars dropped. `google-genai` and `pillow` removed from GitHub Actions install step. `outputs/images/` directory and all YAML frontmatter `cover_image` fields removed from the pipeline.

## [0.4.0] - 2026-03-16

### Added
- **Google Imagen 3 cover image generation** (`agents/image_agent.py`): each pipeline run now generates a 16:9 cover image based on the article topic and extracted technical keywords. Images are saved to `outputs/images/` and referenced via YAML frontmatter (`cover_image: images/...`) in the article markdown.
- `IMAGE_GENERATION_ENABLED`, `GOOGLE_API_KEY`, `IMAGEN_MODEL`, `OUTPUT_IMAGES_DIR` config vars. Image generation is skipped gracefully when `GOOGLE_API_KEY` is unset.
- `google-generativeai` added to the GitHub Actions install step; `GOOGLE_API_KEY` secret wired in.
- Generated images are copied to `ios-ai-articles/images/` in the publish step and committed alongside articles.

### Changed
- Pipeline schedule changed from Mon/Wed/Fri to **Mon/Thu** (`0 10 * * 1,4`).

## [0.3.4] - 2026-03-14

### Fixed
- `_safe_format` in `newsletter_agent.py` now uses plain `str.replace` per placeholder instead of `str.format(**kwargs)`. This eliminates `KeyError` crashes when the prompt template contains literal `{…}` patterns (inline code examples, prose, backtick snippets) that `str.format` misinterprets as format keys. No template escaping (`{{`/`}}`) is ever required now.

## [0.3.3] - 2026-03-14

### Fixed
- `_swift_parse_validate` and `_swift_compile_validate` in `code_agent.py` now catch `subprocess.TimeoutExpired` and return `(False, "[validation:...] swiftc timed out — snippet skipped.")` instead of propagating the exception. Previously a slow `swiftc` parse on GitHub Actions would crash the entire pipeline run.

## [0.3.2] - 2026-03-14

### Fixed
- `_pick_top_trends` in `newsletter_agent.py` now applies the same `_is_ios_relevant` keyword filter as `_pick_community_links`, preventing off-topic signals (hardware news, general programming) from appearing in the **Trend Signals** section.
- Added `_unescape_code_blocks()` post-processor: normalises `{{` → `{` and `}}` → `}` inside fenced code blocks after LLM generation, fixing unreadable/non-compilable Swift snippets caused by the model double-escaping curly braces.
- `_pick_best_snippet` now caps snippets at 25 lines (adds `// ... (truncated for newsletter)` marker), preventing 100+ line article snippets from flooding the newsletter format.
- Newsletter prompt: strengthened **This Week's Big Story** to require exactly 3 sentences (was routinely producing 2).
- Newsletter prompt: **Community Picks** now explicitly requires `**[Title](url)**` markdown hyperlinks; plain bold titles without links are rejected.
- Newsletter prompt: added explicit constraint that Swift braces must be single `{` `}` characters, never `{{` `}}`.

## [0.3.1] - 2026-03-14

### Fixed
- Newsletter `Community Picks` no longer includes off-topic items (hardware news, general programming) from HackerNews or Reddit. `_pick_community_links` in `newsletter_agent.py` now requires each candidate signal to contain at least one iOS/Apple keyword (`ios`, `swift`, `swiftui`, `xcode`, `apple`, `app store`, etc.) before it can appear in the section.
- `fetch_reddit_iosprogramming_trends` in `trend_scanner.py` now applies the same `_is_topic_related` keyword filter used by all other source fetchers, preventing off-topic r/iOSProgramming posts from entering the pipeline.
- Corrected `pyproject.toml` version — it was stuck at `0.2.0` and never bumped when v0.3.0 shipped.

## [0.3.0] - 2026-03-14

### Added
- Added `agents/newsletter_agent.py` — new `generate_newsletter()` function that assembles a weekly SwiftTribune-style developer newsletter from pipeline outputs. Selects top 5 trend signals (URL-bearing items ranked first), picks the best codegen snippet (prefers `direct` validation path), identifies 2–3 community picks from reddit/dev.to/hackernews/medium sources, calls the LLM with a structured prompt, and returns `{"markdown", "html", "issue_number"}`.
- Added `prompts/newsletter_prompt.txt` — newsletter prompt template with six sections: Opening hook, This Week's Big Story, Trend Signals, Swift Snippet of the Week, Community Picks, and Closing CTA. Placeholders: `{newsletter_name}`, `{issue_number}`, `{article_title}`, `{article_teaser}`, `{trend_signals_json}`, `{best_snippet}`, `{community_links_json}`, `{linkedin_post}`.
- Added `NEWSLETTER_ENABLED` (default `true`), `NEWSLETTER_NAME` (default `"iOS Dev Weekly"`), `NEWSLETTER_ISSUE_FILE` (default `outputs/newsletter/.issue_number`), and `OUTPUT_NEWSLETTER_DIR` to `config.py`.

### Changed
- `workflows/weekly_pipeline.py` — added `generate_newsletter` + `save_newsletter` steps after the LinkedIn step, writing `outputs/newsletter/YYYY-MM-DD-issue-N.md` and `.html`. Issue number is auto-incremented from a persistent counter file.
- `.github/workflows/weekly.yml` — publish step now copies `outputs/newsletter/` to `ios-ai-articles` content repo alongside articles, linkedin, and codegen outputs.
- `.gitignore` — added explicit `outputs/newsletter/` entry.

## [0.2.0] - 2026-03-13

### Changed
- Migrated generated outputs (`outputs/articles/`, `outputs/linkedin/`, `outputs/codegen/`) out of this repo and into a dedicated content repo (`saurabhdave/ios-ai-articles`).
- Added `outputs/` and `_content/` to `.gitignore`; all previously tracked output files removed from git history.
- Added "Publish articles to content repo" step to `.github/workflows/weekly.yml` — clones `ios-ai-articles` via `DEPLOY_TOKEN`, copies outputs, and commits/pushes with message `article: YYYY-MM-DD`.
- Added `ios-ai-articles/_config.yml` seed file with minimal Jekyll config (title, minima theme, permalink) for bootstrapping the content repo.

## [0.1.9] - 2026-03-13

### Added
- Added post-generation self-review step (`agents/review_agent.py`) that runs an LLM pass on the final article body and produces structured quality scores (`overall_quality`, `technical_depth`, `actionability`) with issues and strengths lists.
- Added `prompts/review_prompt.txt` for the iOS-specific article review rubric.
- Added persistent quality history (`outputs/quality_history.json`) — each pipeline run appends a record with layout score, code repair metadata, and LLM review scores for trend analysis across runs.
- Added `SELF_REVIEW_ENABLED` and `OUTPUT_QUALITY_HISTORY_PATH` config flags to control the new review and history features.
- Added `CLAUDE.md` with project guidance, architecture overview, and common commands.

### Changed
- Replaced word-set Jaccard similarity in topic deduplication with embedding-based cosine similarity (`text-embedding-3-small`) to catch semantic near-duplicates that share few words (e.g., "App Intents deep dive" vs "Mastering App Intents").
- Fixed closing section detection in `assess_medium_layout`: plain-text "Closing takeaway" near article end now correctly awards the score point and emits an advisory (promote to `##` heading) instead of falsely flagging the section as missing.
- `reinforce_medium_layout` now returns `tuple[str, LayoutAssessment]` so the final layout score is available to callers without re-running the scorer.

### Changed (previously Unreleased)
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
