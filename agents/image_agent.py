"""Image generation agent: produces a cover image for each article using Google Gemini/Imagen."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import config
from utils.observability import get_logger

LOGGER = get_logger(__name__)

# Technical nouns to strip from keyword extraction (too generic to be useful in prompts).
_STOP_WORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "in", "of", "to", "for", "with", "on", "at",
        "by", "from", "is", "are", "was", "were", "be", "been", "being", "have",
        "has", "had", "do", "does", "did", "will", "would", "could", "should",
        "may", "might", "must", "that", "this", "these", "those", "it", "its",
        "your", "our", "their", "we", "you", "they", "he", "she", "which",
        "what", "how", "when", "where", "why", "all", "each", "every", "some",
        "any", "more", "most", "other", "into", "through", "during", "before",
        "after", "above", "below", "between", "out", "off", "over", "under",
        "then", "once", "here", "there", "while", "although", "because", "since",
        "until", "whether", "about", "against", "also", "so", "but", "if",
        "use", "using", "used", "new", "make", "can", "just", "not", "no",
        "than", "too", "very", "as", "up", "get", "let", "set",
    }
)


def _extract_keywords(article_body: str, max_keywords: int = 5) -> list[str]:
    """Extract technical nouns from article body for prompt enrichment."""
    # Prefer capitalised technical terms (e.g. SwiftUI, Observable, Concurrency)
    capitalised = re.findall(r"\b[A-Z][a-zA-Z]{3,}\b", article_body)
    seen: dict[str, int] = {}
    for word in capitalised:
        if word.lower() not in _STOP_WORDS:
            seen[word] = seen.get(word, 0) + 1

    # Sort by frequency, deduplicate, and return top N
    ranked = sorted(seen.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in ranked[:max_keywords]]


def _build_prompt(topic: str, article_body: str) -> str:
    """Build a descriptive image generation prompt."""
    keywords = _extract_keywords(article_body)
    keyword_phrase = ", ".join(keywords) if keywords else "Swift programming"
    return (
        f"Abstract minimalist cover illustration for an Apple iOS developer blog post titled '{topic}'. "
        f"Concepts: {keyword_phrase}. "
        "Modern flat design with clean geometric shapes, Apple-inspired color palette "
        "(white, light silver, vibrant blue, subtle gradients). "
        "No text, no typography, no letters, no words. "
        "Professional tech blog aesthetic, 16:9 landscape orientation."
    )


def generate_cover_image(
    topic: str,
    article_body: str,
    slug: str,
    date_str: str,
) -> Path | None:
    """Generate a cover image for the article and save it to outputs/images/.

    Returns the saved Path on success, or None if image generation is skipped or fails.
    """
    try:
        from google import genai  # noqa: PLC0415
        from google.genai import types as genai_types  # noqa: PLC0415
    except ImportError:
        LOGGER.warning("google-genai not installed — skipping cover image generation")
        return None

    if not config.GOOGLE_API_KEY:
        LOGGER.warning("GOOGLE_API_KEY not set — skipping cover image generation")
        return None

    config.OUTPUT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.OUTPUT_IMAGES_DIR / f"{date_str}-{slug}.png"

    prompt = _build_prompt(topic, article_body)
    LOGGER.info("Generating cover image for topic=%r model=%s", topic, config.IMAGEN_MODEL)

    try:
        import io  # noqa: PLC0415
        from PIL import Image as PilImage  # noqa: PLC0415

        client = genai.Client(api_key=config.GOOGLE_API_KEY)

        image_bytes: bytes | None = None
        last_exc: Exception | None = None

        for attempt in range(1, 4):  # up to 3 attempts
            try:
                if config.IMAGEN_MODEL.startswith("imagen-"):
                    # Dedicated Imagen model — use generate_images endpoint
                    response = client.models.generate_images(
                        model=config.IMAGEN_MODEL,
                        prompt=prompt,
                        config=genai_types.GenerateImagesConfig(
                            number_of_images=1,
                            aspect_ratio="16:9",
                            safety_filter_level="BLOCK_LOW_AND_ABOVE",
                            person_generation="DONT_ALLOW",
                        ),
                    )
                    if not response.generated_images:
                        LOGGER.warning("Imagen returned no images — skipping cover image")
                        return None
                    image_bytes = response.generated_images[0].image.image_bytes
                else:
                    # Gemini multimodal model — pure image request, no tools/grounding
                    response = client.models.generate_content(
                        model=config.IMAGEN_MODEL,
                        contents=prompt,
                        config=genai_types.GenerateContentConfig(
                            response_modalities=["IMAGE", "TEXT"],
                            tools=[],  # disable grounding/tools for a clean image request
                        ),
                    )
                    for part in response.candidates[0].content.parts:
                        if part.inline_data is not None:
                            image_bytes = part.inline_data.data
                            break
                    if image_bytes is None:
                        LOGGER.warning("Gemini image model returned no image parts — skipping cover image")
                        return None
                break  # success — exit retry loop

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                error_str = str(exc)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    retry_delay = 5 * attempt  # 5s, 10s, 15s
                    LOGGER.warning(
                        "Cover image rate-limited (attempt %d/3) — retrying in %ds: %s",
                        attempt, retry_delay, error_str[:120],
                    )
                    time.sleep(retry_delay)
                else:
                    break  # non-retryable error

        if image_bytes is None:
            LOGGER.warning("Cover image generation failed: %s — continuing without image", last_exc)
            return None

        PilImage.open(io.BytesIO(image_bytes)).save(str(out_path))
        LOGGER.info("Cover image saved to %s", out_path)
        return out_path

    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Cover image generation failed: %s — continuing without image", exc)
        return None
