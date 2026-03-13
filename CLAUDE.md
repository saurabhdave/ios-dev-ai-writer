# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

`ios-dev-ai-writer` is a Python pipeline that auto-generates weekly Medium-style articles about Apple platform engineering. It runs on a GitHub Actions schedule (Mon/Wed/Fri at 10:00 UTC), discovers iOS/Swift trends from multiple sources, then passes them through a multi-stage LLM pipeline to produce articles, Swift code examples, and LinkedIn posts — all committed back to the repo.

## Commands

```bash
# Install dependencies (editable mode)
pip install -e .

# Run the full pipeline locally
python main.py

# Environment variables required
cp .env.example .env  # set OPENAI_API_KEY at minimum
```

There is no test suite or linter configured.

## Architecture

### Data Flow

```
main.py → weekly_pipeline.py
  → TrendScanner (8 sources: HackerNews, Reddit, Apple Docs, WWDC, etc.)
  → topic_agent → outline_agent → article_agent
  → editor_agent (polish → factual grounding → Medium layout reinforcement loop)
  → code_agent (Swift/SwiftUI snippet with repair loop)
  → linkedin_agent
  → Save to outputs/{articles,trends,codegen,linkedin}/
```

### Key Files

| File | Role |
|------|------|
| [workflows/weekly_pipeline.py](workflows/weekly_pipeline.py) | Main orchestrator — all pipeline stages wired here |
| [config.py](config.py) | All configuration via env vars with defaults |
| [scanners/trend_scanner.py](scanners/trend_scanner.py) | Multi-source trend aggregation → `TrendSignal` dataclass |
| [agents/editor_agent.py](agents/editor_agent.py) | Polish, layout reinforcement loop, factual grounding |
| [agents/code_agent.py](agents/code_agent.py) | Swift codegen with validation/repair loop |
| [agents/linkedin_agent.py](agents/linkedin_agent.py) | LinkedIn post generation |
| [utils/observability.py](utils/observability.py) | Structured JSON logging, timed steps, pipeline events |
| [utils/openai_logging.py](utils/openai_logging.py) | OpenAI client init and token usage tracking |

### Content Constraints (enforced by agents)

- **Apple-platform only** — topics filtered to iOS/Swift/SwiftUI/Xcode; AI-first topics explicitly excluded
- **Swift 6 patterns** — `@Observable` preferred over `@Published`; Swift 6.2.4 + compiler mode 6 by default
- **Title limits** — 60 chars / 10 words max; fallback titles generated on violation
- **Article length** — 900–1200 words
- **Code snippets** — 3–8 lines for articles
- **References** — trust-first model: low-signal domains stripped before publication; unverified URLs sanitized from article body

### Configuration

All settings in [config.py](config.py) are driven by environment variables. Important ones:

```
OPENAI_API_KEY          # Required
OPENAI_MODEL            # Default: gpt-5-mini
OPENAI_REASONING_EFFORT # minimal|low|medium|high (for GPT-5 models)

CODEGEN_VALIDATION_MODE # snippet|compile|none (default: snippet)
CODEGEN_FAILURE_MODE    # omit|error (default: omit — publishes without code on failure)

LINKEDIN_POST_ENABLED   # Default: true
LINKEDIN_CODE_SNIPPET_MODE # auto|always|never

TREND_SOURCES           # Comma-separated source names
FACT_GROUNDING_ENABLED  # Default: true
MEDIUM_LAYOUT_REINFORCEMENT_ENABLED # Default: true
MEDIUM_LAYOUT_MAX_REPAIR_PASSES     # Default: 2
```

### Output Artifacts

- `outputs/articles/{date}-{slug}.md` — Final Medium markdown
- `outputs/trends/{timestamp}-trend-signals.json` — Trend snapshot
- `outputs/linkedin/{date}-{slug}-linkedin.md` — LinkedIn post
- `outputs/codegen/{date}-{slug}-codegen.json` — Code generation metadata and repair diagnostics

### GitHub Actions

- [.github/workflows/weekly.yml](.github/workflows/weekly.yml) — Runs pipeline 3×/week, auto-commits outputs to main
- [.github/workflows/release.yml](.github/workflows/release.yml) — Creates GitHub Release on `v*` tag push
