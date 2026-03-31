# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

## [1.6.7] - 2026-03-31

### Fixed
- **Empty Responses API text handling** (`utils/openai_logging.py`, `agents/article_agent.py`, `agents/code_agent.py`, `agents/editor_agent.py`, `agents/linkedin_agent.py`, `agents/newsletter_agent.py`, `agents/outline_agent.py`): Added a shared `response_output_text()` helper and routed agents through it so `output_text=None` no longer crashes the pipeline with `AttributeError`. Empty model responses now fall through to each agent's intended empty-output handling path.
- **SDK-backed Swift validation and newsletter counter persistence** (`agents/code_agent.py`, `agents/linkedin_agent.py`, `agents/newsletter_agent.py`): Restored real iOS simulator SDK path capture by reading `xcrun --show-sdk-path` from `stdout`, which re-enables full typechecking instead of silently dropping to parse-only validation. Newsletter issue numbers are now persisted only after generation succeeds, so transient generation failures no longer burn an issue number.

## [1.6.6] - 2026-03-30

### Added
- **Author context injection** (`scanners/author_context.json`, `agents/topic_agent.py`, `agents/article_agent.py`, `prompts/article_prompt.txt`): Articles can now be grounded in real production experience. `scanners/author_context.json` stores first-person experience bullets keyed by topic family (`concurrency`, `swiftui_rendering`, `architecture`, `testing`, `performance`, `migration`). `load_author_context()` in `topic_agent.py` keyword-matches the chosen topic to a family and returns formatted bullets. `article_agent.py` injects these into the article prompt via a new `{author_context}` placeholder at the top of `article_prompt.txt`. When no context matches (missing file, unmatched topic, empty family), the prompt falls back to a generic senior-engineer voice — no pipeline change.

## [1.6.5] - 2026-03-30

### Added
- **Exponential backoff retry on all OpenAI API calls** (`utils/openai_logging.py`): All calls to `client.responses.create()` and `client.embeddings.create()` are now wrapped with tenacity retry logic. Retries on `RateLimitError`, `APITimeoutError`, `APIConnectionError`, and `InternalServerError` with exponential backoff (2–30s, 4 attempts). A `WARNING` log line is emitted before each sleep via `before_sleep_log`. After all attempts are exhausted the original exception is re-raised.
- **`tenacity>=8.2.0` dependency** (`pyproject.toml`): Added as a direct dependency to support retry logic.
- **Failure run summary** (`workflows/weekly_pipeline.py`): When the pipeline exits via an unhandled exception, `outputs/run_summary.json` is written with `"quarantine_triggered": true` and `"failure_reason"` set to the exception message. The write is guarded so it never suppresses the original exception.

## [1.6.4] - 2026-03-30

### Fixed
- **Near-duplicate topic detection** (`agents/topic_agent.py`): `_is_repetitive` now uses `normalise_title()` instead of `_word_set()`. `normalise_title()` strips a broader stopword set and de-gerunds long tokens (e.g. `profiling→profil`, `rendering→render`) so that verb-form variants of the same concept — such as "Profile SwiftUI Rendering with Instruments" vs "Profiling SwiftUI Rendering in Instruments" — are correctly detected as duplicates at the Jaccard step.

### Added
- **`TOPIC_SIMILARITY_THRESHOLD` env var** (`config.py`): The semantic cosine-similarity rejection threshold (previously hard-coded at 0.72 in `topic_agent.py`) is now configurable via `TOPIC_SIMILARITY_THRESHOLD`. Default remains `0.72`. Documented in README configuration table.

## [1.6.3] - 2026-03-30

### Added
- **Reference quality rules in article prompt** (`prompts/article_prompt.txt`): New `REFERENCE RULES — strictly enforced` section requires references to link to specific documentation pages, not homepages or top-level paths. Rules cover banned/required URL patterns, Swift Evolution proposal citation format (`SE-XXXX`), WWDC session citation format, a 5-reference maximum, and enforcement that every listed reference must appear in the article prose.
- **`validate_references` in article agent** (`agents/article_agent.py`): Post-generation check that scans `allowed_references` for homepage-level URLs matching `developer.apple.com/documentation/<word>`, `swift.org/documentation`, or `swift.org/blog`. Emits `reference_homepage_warning` log events at `WARNING` level per offending URL. Never blocks pipeline execution.

## [1.6.2] - 2026-03-30

### Fixed
- **CI syntax error in run summary step** (`.github/workflows/weekly.yml`): Backslash escapes inside f-string expressions (`'\u2014'`) cause a `SyntaxError` on Python < 3.12. Extracted the em dash to a variable (`dash = "\u2014"`) defined before the f-strings, and replaced `dict.get(key, '\u2014')` with `dict.get(key) or dash`.

## [1.6.1] - 2026-03-29

### Added
- **GitHub Actions step summary** (`.github/workflows/weekly.yml`, `workflows/weekly_pipeline.py`): After each `python main.py` run, the pipeline writes `outputs/run_summary.json` (topic, word count, codegen path, repair attempts, editor pass count, config flags). A new "Write run summary" CI step reads this file and appends a formatted markdown table to `$GITHUB_STEP_SUMMARY`, providing a human-readable audit trail per run without digging into logs.
- **`editor_pass_count` tracking** (`workflows/weekly_pipeline.py`): Integer counter incremented after each LLM-backed editing pass (polish, factual grounding, layout reinforcement, review-triggered repair) and surfaced in `run_summary.json`.

## [1.6.0] - 2026-03-28

### Added
- **Voice post-processing pass** (`agents/editor_agent.py`, `prompts/voice_prompt.txt`): New `apply_voice_pass()` runs after `polish_article()` and rewrites prose to remove detectable AI writing patterns — "Choose X / Choose Z" constructs, hedge phrases (`where possible`, `where feasible`, etc.), passive recommendations (`it is recommended that`, `you may want to`), and vague performance claims. Preserves all API names, code blocks, section headers, and the five-section article structure.
- **`VOICE_PASS_ENABLED` config var** (`config.py`): Toggle for the voice pass. Default: `true`. Set `VOICE_PASS_ENABLED=false` to skip.
- **`prompts/voice_prompt.txt`** (new): Five-rule rewrite prompt with BAD/GOOD examples per rule, instructs the model to output the full article with no preamble.

## [1.5.2] - 2026-03-27

### Fixed
- **`quality_history.json` never committed on CI** (`.gitignore`, `.github/workflows/weekly.yml`): `outputs/` was gitignored with a trailing-slash pattern, which prevents child-path negations — so `git status --porcelain outputs/quality_history.json` always returned empty and the file was silently skipped in the CI commit step. Changed to `outputs/*` + `!outputs/quality_history.json` so the file is trackable. Also added `git add -f` in the CI step as a belt-and-suspenders guard.
- **Static fallback titles caused silent duplicate emission** (`agents/topic_agent.py`): Removed `_FALLBACK_TITLES` list and `_fallback_topic_title()`. If all 5 generation attempts are rejected, `generate_topic` now raises `RuntimeError` with a clear message pointing to the `violations` log field. A failed CI run is observable and fixable; a silently published duplicate is not.

## [1.5.1] - 2026-03-27

### Fixed
- **Topic deduplication broken on CI** (`workflows/weekly_pipeline.py`, `.github/workflows/weekly.yml`): `_load_recent_titles()` was reading from `outputs/articles/*.md`, which in GitHub Actions contains only the current run's article (all previous articles are pushed to the content repo and never committed back). The topic agent therefore received a history of 1 title instead of the full history, making all 5 deduplication checks ineffective and allowing repeated topics.
  - `_load_recent_titles()` now reads from `outputs/quality_history.json` first (all-time history, committed to source repo), then falls back to local article markdown files for dev/manual runs.
  - GitHub Actions workflow now also commits `outputs/quality_history.json` back to the source repo after each run, so the full topic history grows across runs and is available on the next CI checkout.

## [1.5.0] - 2026-03-26

### Added
- **Review-triggered repair loop** (`workflows/weekly_pipeline.py`, `agents/editor_agent.py`, `prompts/review_repair_prompt.txt`): Articles that score below `REVIEW_REPAIR_MIN_SCORE` (default 7) on any quality dimension (overall, technical_depth, actionability) and have reviewer-identified issues now automatically receive a targeted `repair_from_review()` editor pass before publishing. New `review_repair_triggered` field written to `outputs/quality_history.json` per run.
- **`REVIEW_REPAIR_ENABLED` / `REVIEW_REPAIR_MIN_SCORE` config vars** (`config.py`): Toggle and threshold for the review-repair loop. Defaults: enabled, score threshold 7.
- **`repair_from_review()` function** (`agents/editor_agent.py`): Calls the LLM with a focused issue-list prompt (`review_repair_prompt.txt`). Fixes only the listed issues; preserves unchanged sections, tone, and length. Uses `.replace()` substitution (immune to Swift brace KeyError).
- **`prompts/review_repair_prompt.txt`** (new): Targeted repair prompt with typed fix rules per issue category (try!, unnamed implementations, undefined jargon, weak hooks) and a strict preserve-unchanged constraint.
- **Theme cluster saturation guard** (`agents/topic_agent.py`): Hard code-level dedup — detects when 2+ recent articles cover the same theme cluster (Swift concurrency/async-await, UIKit migration, SwiftUI performance profiling) and rejects new topic candidates in that cluster. `THEME_CLUSTER_SATURATION_LIMIT = 2`.
- **`_cluster_match()`, `_is_theme_cluster_saturated()`, `_theme_concentration_summary()`** (`agents/topic_agent.py`): New helpers powering the cluster guard and the `{theme_warnings}` injection into the topic prompt.
- **Community Picks post-processor** (`agents/newsletter_agent.py`): `_repair_community_picks()` deterministically strips bold-only (no hyperlink) picks after LLM generation. Falls back to "More community links next issue." when fewer than 2 hyperlinked picks survive.

### Changed
- **Semantic similarity threshold lowered: 0.80 → 0.72** (`agents/topic_agent.py`): Catches "same concept, different angle" near-duplicates (e.g., concurrency basics vs. concurrency migration) that previously passed the cosine check.
- **Recent-titles display limit: 15 → 24** (`agents/topic_agent.py`): Gives the topic agent broader dedup context when history is deep.
- **`prompts/topic_prompt.txt`**: Added `{theme_warnings}` block between recent titles and TASK sections; saturated theme avoidance promoted to conflict-resolution priority 2.
- **`try!` prohibition added to `prompts/article_prompt.txt` and `prompts/editor_prompt.txt`**: `try!` and unguarded force-unwrap forbidden in all production code snippets and prose; allowed only inside `// ❌ Before` legacy blocks.
- **Named-pattern demonstration rule** (`prompts/article_prompt.txt`, `prompts/editor_prompt.txt`): Prose that names a concrete implementation (Router, NavigationBridge, bounded semaphore) must either show it in a code snippet or describe it in enough detail that no code is needed.
- **Jargon definition rule** (`prompts/article_prompt.txt`, `prompts/editor_prompt.txt`): Any non-Apple-API specialized term (e.g., "blue-green wiring", "circuit breaker") must be defined in one sentence on first use.
- **`prompts/review_prompt.txt`**: Added 3 new issue categories to the checklist — `try!`/force-unwrap in production context, named pattern not demonstrated, and specialized jargon without inline definition.
- **`prompts/newsletter_prompt.txt`**: Added fallback instruction for empty `{best_snippet}` — replace "Swift Snippet of the Week" with "Worth Watching This Week" (1–2 sentences on top trend signal).
- **Backtick repair regex generalized** (`utils/article_repair.py`): `_MALFORMED_BACKTICK_RE` changed from `r'\`(with)\`(\w+)\`\`'` to `r'\`(\w+)\`(\w+)\`\`'` — now catches any prefix word, not just "with" (e.g., `` `withThrowing`TaskGroup`` `` → `` `withThrowingTaskGroup` ``).
- **Reader-visible pipeline placeholder replaced** (`workflows/weekly_pipeline.py`): "_No validated code snippet was generated this run._" → "_A code example for this topic is not included in this edition._"
- **Removed duplicate `TOPIC_INTERESTS` entry** (`config.py`): "Swift async await patterns" removed (covered by "Structured Concurrency"); list reduced 20 → 19 entries.

## [1.1.0] - 2026-03-18

### Added
- **Deterministic article post-processor** (`utils/article_repair.py`): New pure-Python module that runs on every pipeline execution after `medium_layout_reinforcement`, before `sanitize_article`. `repair_malformed_backticks()` regex-fixes the common `` `with`Word`` `` split-backtick pattern (e.g. `` `with`TaskGroup`` `` → `` `withTaskGroup` ``) with zero LLM cost. `audit_missing_version_callouts()` logs a structured warning when a tracked API (`withTaskGroup`, `@Observable`, `AsyncSequence`, etc.) appears without its deployment target. Both results are emitted as structured pipeline log events (`backtick_fixes_applied`, `version_callouts_missing`).
- **`deterministic_repair` pipeline step** (`workflows/weekly_pipeline.py`): Calls `utils/article_repair.repair_article()` between layout reinforcement and sanitize. Fix and warning counts are recorded in step metadata.

### Fixed
- **Python 3.9 compatibility: `dataclass(slots=True)`** (`agents/code_agent.py`, `agents/editor_agent.py`, `agents/review_agent.py`, `agents/topic_agent.py`, `scanners/trend_scanner.py`): `slots=True` requires Python 3.10+. Removed the kwarg from all five dataclasses so the pipeline runs on the project's Python 3.9 venv.
- **`str.format()` crash on Swift code in prompts and article bodies** (all agents): Prompt templates contain Swift code examples with literal `{` / `}` braces (closure syntax, JSON examples). Python's `str.format()` mis-parsed these as format placeholders, raising `KeyError` or `IndexError`. Replaced all `.format(topic=…, article=…, …)` calls in every agent with chained `.replace("{placeholder}", value)` calls, which perform literal substitution and are immune to brace content.
- **Literal `{}` in `prompts/code_prompt.txt`**: The brace-balance reminder line contained bare `{}` that broke the (now-removed) `str.format()` path. No longer needed with the `.replace()` fix; reverted to readable `{}`.

### Improved
- **Editor prompt: backtick corruption guard** (`prompts/editor_prompt.txt`): Added an explicit "Backtick Corruption Guard" rule listing the high-frequency malformed patterns the model must scan for and fix before returning output.
- **Editor prompt: mandatory per-API version callouts** (`prompts/editor_prompt.txt`): Expanded the "Version Statements" rule with a concrete table mapping each key API (`withTaskGroup`, `@Observable`, `AsyncStream`, etc.) to its minimum deployment target. Prevents the recurring "no version stated" review flag.
- **Editor prompt: opening hook specificity rule** (`prompts/editor_prompt.txt`): Added "Opening Hook Specificity" rule requiring the hook to name a concrete runtime symptom or failure mode rather than a generic technology statement. Includes a ✅/❌ example pair.

## [1.0.0] - 2026-03-16

### Fixed
- **Code gen line-length overflow** (`prompts/code_prompt.txt`): Added explicit 35-line maximum (ideal 15–25) to prevent the LLM from generating full ViewModel implementations that then fail brace-balance and Swift 6 data-race validation. Also added `@MainActor` guidance and a note to prefer flat structures in `TaskGroup` bodies.
- **Code repair loop: simplify on failure** (`agents/code_agent.py`): The repair prompt now instructs the model to simplify the snippet when it exceeds 35 lines or has deeply nested closures. Also added explicit `@MainActor` guidance for Swift 6 race-statement errors, so the repair model resolves sendability violations rather than reshuffling code.
- **Topic clustering: same-API migration dedup** (`agents/topic_agent.py`): Added `_shares_migration_target()` helper that detects when a candidate topic targets the same legacy API (completion handler, callback, delegate, KVO, NSNotification, URLSession, Combine, UIKit) as a recently published article, and rejects the candidate. Also lowered the semantic embedding similarity threshold from 0.88 → 0.80 to catch near-duplicate topics that previously passed the cosine check.
- **Newsletter missing snippet fallback** (`agents/newsletter_agent.py`): Added `_extract_article_code_block()` helper that extracts the first fenced code block from the article body. `_pick_best_snippet()` now uses this as a fallback when codegen produced no validated snippet, so the "Swift Snippet of the Week" section is never empty.

## [0.9.0] - 2026-03-16

### Added
- **Brace-balance diagnostic in code repair loop** (`agents/code_agent.py`): Added `_brace_balance_diagnostic()` helper that counts `{}`, `[]`, `()` open/close pairs and emits a human-readable message (e.g. "Unbalanced `{}`: 5 opens, 3 closes (2 extra opens)") when mismatched. Injected into the repair loop's combined diagnostics so the repair model has a precise, actionable target instead of cryptic swiftc parse errors.
- **Article-grounded code generation** (`agents/code_agent.py`, `workflows/weekly_pipeline.py`, `prompts/code_prompt.txt`): `generate_code_with_metadata` now accepts an `article_body` parameter. Added `_article_excerpt()` helper that extracts `### Implementation Pattern` subsections (or falls back to plain truncation at 1200 chars) and injects them into the code prompt as "Article context". Pipeline passes `polished_article` so the snippet illustrates the article's actual patterns rather than guessing from the title alone.

### Fixed
- **`@Bindable`-inside-`@Observable` repair reliability** (`agents/code_agent.py`, `prompts/code_prompt.txt`): The repair model was moving `@Bindable` around rather than removing it from the model. Fixed by:
  - Replacing the abstract one-line rule in the repair prompt with a concrete `WRONG`/`CORRECT` before-after example
  - Upgrading `_observation_style_diagnostics` to extract and name the specific offending properties (e.g. "Remove `@Bindable` from model properties (`rating`, `label`)...")
  - Adding a concrete `WRONG`/`CORRECT` example block directly in `code_prompt.txt`
- **`str.format()` crash on Swift code in article body** (`agents/code_agent.py`): Article prose containing `{` and `}` (Swift format strings, closure syntax) caused `KeyError` when injected via `.format()`. Fixed with a two-step substitution: `.format()` uses a sentinel `__ARTICLE_CTX__` for the article slot, then a plain `str.replace()` injects the raw excerpt after format has run.
- **Literal `{ }` in prompt template** (`prompts/code_prompt.txt`): The brace-balance reminder line contained `{ }` which Python's `str.format()` parsed as a format placeholder. Escaped to `{{ }}`.

## [0.8.0] - 2026-03-16

### Changed
- **Topic diversity: removed migration bias** (`agents/topic_agent.py`, `prompts/topic_prompt.txt`, `config.py`, `scanners/trend_scanner.py`): Articles were 100% migration-focused due to four compounding biases. Fixed by:
  - Removed explicit "Prefer migration-focused titles" instruction from `topic_prompt.txt`
  - Replaced `MIGRATION_INTEREST_DEFAULTS` auto-injection logic with a `TOPIC_FAMILIES` rotation system (8 families: architecture, performance, concurrency, SwiftUI features, tooling/debugging, frameworks/APIs, accessibility/design, migration)
  - Added `_sample_topic_family()` — history-aware weighted sampler that de-weights recently used families; migration family gets 0.5× weight multiplier
  - Rewrote `_filtered_interests()` to sample a topic family per run, supplemented by non-migration items from config
  - Diversified fallback candidates to one per family (migration is 1 of 8)
  - Removed 3 migration-specific entries from `TOPIC_INTERESTS` default in `config.py`; replaced with accessibility, Swift Testing, and SwiftUI animations
  - Replaced 3 migration-focused viral queries in `trend_scanner.py` with accessibility/testing/architecture queries; removed migration keywords from `SOCIAL_WEB_QUERY_SOURCES`

## [0.7.0] - 2026-03-16

### Added
- **WebSearch trend source** (`scanners/trend_scanner.py`): New `fetch_websearch_trends()` source runs targeted Google News RSS queries ("top 10 trending topics in iOS development", "top trending iOS Swift topics 2025", "most popular iOS development topics developers") and contributes up to `TREND_MAX_ITEMS_PER_SOURCE` signals tagged as `WebSearch`. Registered as the `websearch` key in `SOURCE_FETCHERS` and enabled by default in `TREND_SOURCES`.

## [0.6.0] - 2026-03-16

### Added
- **Deterministic Swift API backtick formatting** (`agents/article_agent.py`): `apply_swift_backticks()` post-processor regex-wraps ~25 known Swift API names (`withCheckedThrowingContinuation`, `@Observable`, `AsyncStream`, `URLSession.data(for:)`, etc.) in inline backticks. Skips fenced code blocks and never double-wraps already-formatted names. Applied in `_normalize_article()` and after every editor pass in `_render_model_response()`.
- **Static Apple/Swift doc reference seeding** (`workflows/weekly_pipeline.py`): `_APPLE_DOC_SEEDS` table (14 topic→URL mappings) and `_seed_reference_items(topic)` function inject relevant `developer.apple.com` and `swift.org` doc URLs into both agent grounding context and the published References section. Articles now always include at least 2–4 verified Apple documentation links.

### Changed
- `article_prompt.txt`: backtick rule promoted to standalone hard requirement with explicit wrong/right examples; no longer buried as a sub-bullet.
- `editor_prompt.txt`: added explicit backtick enforcement instruction to the polish pass.
- `_compose_markdown`: merges trend-sourced refs with seeded Apple docs (seeds fill gaps, max 10 total).
- `_references_for_prompt`: appends seed refs after trend refs so agents always have stable Apple doc anchors.
- `has_references` quality metric now returns `true` whenever topic-matched seeds exist.

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
