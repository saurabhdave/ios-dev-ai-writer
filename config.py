"""Central configuration for the ios-dev-ai-writer project."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from a local .env file (if present).
load_dotenv()

# OpenAI credentials and model settings.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.7"))

# Output directory for generated markdown articles.
OUTPUT_ARTICLES_DIR = Path("outputs/articles")
OUTPUT_TRENDS_DIR = Path("outputs/trends")

# Trend discovery configuration.
TREND_DISCOVERY_ENABLED = os.getenv("TREND_DISCOVERY_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TREND_MAX_ITEMS_PER_SOURCE = int(os.getenv("TREND_MAX_ITEMS_PER_SOURCE", "10"))
TREND_HTTP_TIMEOUT_SECONDS = int(os.getenv("TREND_HTTP_TIMEOUT_SECONDS", "12"))
REDDIT_USER_AGENT = os.getenv(
    "REDDIT_USER_AGENT",
    "ios-dev-ai-writer/1.0 (weekly trend scanner)",
)
TREND_SOURCES = tuple(
    source.strip().lower()
    for source in os.getenv(
        "TREND_SOURCES",
        "hackernews,reddit,apple,wwdc,viral,social,platforms,custom",
    ).split(",")
    if source.strip()
)
CUSTOM_TRENDS_FILE = Path(os.getenv("CUSTOM_TRENDS_FILE", "scanners/custom_trends.json"))

# Content quality controls.
EDITOR_PASS_ENABLED = os.getenv("EDITOR_PASS_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

TOPIC_INTERESTS = [
    item.strip()
    for item in os.getenv(
        "TOPIC_INTERESTS",
        "AI,AI Agents,AI Automation,Agentic AI,Agentic workflows,Generative AI",
    ).split(",")
    if item.strip()
]

# Topic composition policy:
# - balanced: alternate across iOS-only, AI-only, and hybrid based on recent history
# - ios_only: keep topics focused on Apple platform engineering without AI requirement
# - ai_only: keep topics focused on AI/agentic/generative themes (can still be app-dev relevant)
# - hybrid: combine Apple platform + AI themes in one topic
TOPIC_MODE = os.getenv("TOPIC_MODE", "balanced").strip().lower()
