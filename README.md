# ios-dev-ai-writer ✍️📱

![Python](https://img.shields.io/badge/python-3.11-blue)
![Version](https://img.shields.io/badge/version-0.2.0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

## 🚀 About
`ios-dev-ai-writer` is an open-source Python agent pipeline that generates weekly Medium-style Apple-platform engineering articles.
It discovers trends, creates a topic, builds an outline, writes the article body, generates Swift/SwiftUI code, creates a LinkedIn promo post, and saves output automatically.

## ✨ Features
- Automatic iOS trend discovery from:
  - HackerNews
  - Reddit `r/iOSProgramming`
  - Apple Developer docs/news release feeds
  - WWDC videos feed
  - Broader web/social sources including `x.com`, `dev.to`, and `medium.com` (query + RSS coverage)
- Priority topic interests for upcoming posts:
  - Swift async/await, Structured Concurrency, Swift 6.3 Macros
  - iOS performance architecture, and boilerplate reduction patterns
  - Apple-platform APIs like App Intents, Apple Intelligence APIs, and WidgetKit
- Apple ecosystem programming-only topic generation (no AI-first topic modes)
- Trend-grounded topic generation using OpenAI
- Structured Medium article outline generation
- Professional Medium-style article generation (~900-1200 words)
- Built-in editor pass for quality, tone, and readability
- Reinforcement-style layout repair loop for Medium formatting consistency
- URL-safety guardrails (body text strips unverified links)
- Semantic anti-repetition topic deduplication using embedding-based cosine similarity (`text-embedding-3-small`) to catch near-duplicate topics that share few lexical tokens
- Post-generation self-review agent scores each article on overall quality, technical depth, and actionability via a dedicated LLM pass
- Persistent quality history (`outputs/quality_history.json`) accumulating per-run layout scores, code repair counts, and review scores for trend analysis across runs
- Practical Swift/SwiftUI code generation
- Swift 6 Observation-first code generation (`@Observable` preferred over legacy wrappers)
- Swift version targeting for generated snippets (default: Swift 6.2.4, compiler mode 6)
- Senior, architecture-focused LinkedIn post generation with claim guardrails
- Trust-first reference publication from vetted technical domains
- Code generation observability metadata (`direct|repaired|omitted` path + repair attempts)
- Snippet-safe code validation mode with Swift-book-guided typo/unknown-symbol repairs
- Structured JSON logging for local runs and GitHub Actions (`agent_name`, token usage, timing, step status)
- Output saved to:
  - `outputs/articles/{date}-{slug}.md`
  - `outputs/trends/{timestamp}-trend-signals.json`
  - `outputs/linkedin/{date}-{slug}-linkedin.md`
  - `outputs/codegen/{date}-{slug}-codegen.json`
  - `outputs/quality_history.json` (append-only quality record per run)
- GitHub Actions automation 3 days/week (Monday, Wednesday, Friday at 10:00 UTC)

## 🧱 Project Structure
```text
ios-dev-ai-writer/
├── agents/
│   ├── topic_agent.py
│   ├── outline_agent.py
│   ├── article_agent.py
│   ├── editor_agent.py
│   ├── code_agent.py
│   ├── linkedin_agent.py
│   └── review_agent.py
├── scanners/
│   ├── trend_scanner.py
│   └── custom_trends.json
├── workflows/
│   └── weekly_pipeline.py
├── prompts/
│   ├── topic_prompt.txt
│   ├── outline_prompt.txt
│   ├── article_prompt.txt
│   ├── article_factuality_prompt.txt
│   ├── editor_prompt.txt
│   ├── layout_repair_prompt.txt
│   ├── code_prompt.txt
│   ├── linkedin_prompt.txt
│   ├── linkedin_factuality_prompt.txt
│   └── review_prompt.txt
├── outputs/
│   ├── articles/
│   ├── trends/
│   ├── linkedin/
│   ├── codegen/
│   └── quality_history.json
├── .github/workflows/
│   ├── weekly.yml
│   └── release.yml
├── CLAUDE.md
├── VERSION
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml
├── config.py
├── main.py
└── README.md
```

## 🧭 Architecture Diagram
```mermaid
flowchart TD
    A[GitHub Actions Scheduled Trigger 3x per week] --> B[main.py]
    B --> C[workflows/weekly_pipeline.py]
    C --> S[scanners/trend_scanner.py]
    S --> S1[HackerNews]
    S --> S2[Reddit r/iOSProgramming]
    S --> S3[Apple Docs/News]
    S --> S4[WWDC Feed]
    S --> S5[Viral iOS Web/Social]
    S --> S6[Custom Sources JSON]
    C --> D[topic_agent.generate_topic]
    C --> E[outline_agent.generate_outline]
    C --> F[article_agent.generate_article]
    C --> G[editor_agent.polish_article]
    C --> H[code_agent.generate_code]
    C --> L[linkedin_agent.generate_linkedin_post]
    C --> R[review_agent.review_article]
    D --> API[OpenAI API]
    E --> API
    F --> API
    G --> API
    H --> API
    L --> API
    R --> API
    C --> I[Markdown Composer]
    I --> J[outputs/articles/date-slug.md]
    C --> K[outputs/trends/timestamp-trend-signals.json]
    C --> M[outputs/linkedin/date-slug-linkedin.md]
    C --> N[outputs/codegen/date-slug-codegen.json]
    R --> O[outputs/quality_history.json]
```

## ⚙️ Setup
1. Clone the repository.
2. Create and activate a Python 3.11 virtual environment.
3. Install dependencies:
```bash
pip install -e .
```
4. Configure environment variables (or `.env`):
```bash
export OPENAI_API_KEY="your_api_key"
export OPENAI_MODEL="gpt-5-mini"                                  # optional
export OPENAI_TEMPERATURE="0.7"                                   # optional
export OPENAI_REASONING_EFFORT="low"                              # optional: minimal|low|medium|high (used for gpt-5*)
export TREND_DISCOVERY_ENABLED="true"                             # optional
export TREND_MAX_ITEMS_PER_SOURCE="10"                            # optional
export TREND_HTTP_TIMEOUT_SECONDS="12"                            # optional
export REDDIT_USER_AGENT="ios-dev-ai-writer/1.0"                  # optional
export TREND_SOURCES="hackernews,reddit,apple,wwdc,viral,social,platforms,custom"  # optional
export CUSTOM_TRENDS_FILE="scanners/custom_trends.json"           # optional
export EDITOR_PASS_ENABLED="true"                                  # optional
export MEDIUM_LAYOUT_REINFORCEMENT_ENABLED="true"                  # optional
export MEDIUM_LAYOUT_MAX_REPAIR_PASSES="2"                         # optional
export MEDIUM_LAYOUT_MIN_SCORE="8"                                 # optional
export FACT_GROUNDING_ENABLED="true"                               # optional
export FACT_GROUNDING_MAX_PASSES="1"                               # optional
export TOPIC_INTERESTS="Swift async await patterns,Structured Concurrency,SwiftUI architecture,iOS performance improvements,Xcode tips and debugging workflows,UIKit interoperability,SwiftData persistence,App Intents,Apple Intelligence APIs,WidgetKit,verified Swift tips and tricks,verified SwiftUI modifiers,Swift 6.3 Macros,Reducing Boilerplate in Real Projects,visionOS development,Swift 6 migration and strict concurrency,Deprecated Apple API migration playbooks,Legacy UIKit patterns to modern SwiftUI"  # optional
export TOPIC_MODE="ios_only"                                       # optional; normalized to ios_only
export LINKEDIN_POST_ENABLED="true"                                # optional
export LINKEDIN_CODE_SNIPPET_MODE="auto"                           # optional: auto|always|never
export SWIFT_LANGUAGE_VERSION="6.2.4"                              # optional
export SWIFT_COMPILER_LANGUAGE_MODE="6"                            # optional; maps to swiftc -swift-version
export CODEGEN_FAILURE_MODE="omit"                                 # optional: omit|error
export CODEGEN_VALIDATION_MODE="snippet"                           # optional: snippet|compile|none
export SELF_REVIEW_ENABLED="true"                                  # optional: run LLM self-review after generation
export OUTPUT_QUALITY_HISTORY_PATH="outputs/quality_history.json"  # optional: path for per-run quality metrics
export PIPELINE_LOG_LEVEL="INFO"                                   # optional: DEBUG|INFO|WARNING|ERROR
```

## ▶️ Run Locally
```bash
python main.py
```

The CLI now emits structured JSON log lines to stdout so GitHub Actions logs show pipeline steps, agent calls, token usage, and elapsed time.

Generated outputs:
- `outputs/articles/YYYY-MM-DD-your-topic-slug.md`
- `outputs/trends/YYYY-MM-DDTHH-MM-SSZ-trend-signals.json`
- `outputs/linkedin/YYYY-MM-DD-your-topic-slug-linkedin.md`
- `outputs/codegen/YYYY-MM-DD-your-topic-slug-codegen.json`
- `outputs/quality_history.json` (appended each run)

## 🔌 Add New Trend Sources (Recommended)
Use a config-first workflow:
1. Add/edit entries in `scanners/custom_trends.json`.
2. Keep `TREND_SOURCES` in `.env` to enable/disable source groups.
3. Only add Python fetcher code when a source needs custom API/auth logic.

LinkedIn query example:
```json
{
  "name": "LinkedIn iOS Posts",
  "query": "site:linkedin.com/posts iOS SwiftUI"
}
```

## 🏷️ Versioning
- Current version: `0.1.9` (see `VERSION`)
- Versioning scheme: Semantic Versioning (`MAJOR.MINOR.PATCH`)
- Release notes source: `CHANGELOG.md`

### Release process
1. Update `VERSION`, `CHANGELOG.md`, and `pyproject.toml` version field.
2. Commit changes.
3. Create and push a version tag:
```bash
git tag v0.1.9
git push origin v0.1.9
```
4. GitHub Action `.github/workflows/release.yml` creates a GitHub Release automatically.

## 🤖 GitHub Automation
The workflow `.github/workflows/weekly.yml` runs every Monday, Wednesday, and Friday at 10:00 UTC.

Workflow steps:
1. Checkout repository
2. Set up Python 3.11
3. Install dependencies from `pyproject.toml`
4. Run `python main.py`
5. Commit and push generated content from:
   - `outputs/articles/`
   - `outputs/trends/`
   - `outputs/linkedin/`
   - `outputs/codegen/`
   - `outputs/quality_history.json`

Required repository secret:
- `OPENAI_API_KEY`

## 📄 License
MIT License. See `LICENSE`.
