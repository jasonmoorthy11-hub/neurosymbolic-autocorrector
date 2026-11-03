"""Language detection using fast-langdetect (176 languages)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from fast_langdetect import detect as _ft_detect

    _HAS_FAST_LANGDETECT = True
except ImportError:
    _HAS_FAST_LANGDETECT = False


def detect_language(text: str) -> str:
    """Detect language from text. Returns ISO 639-1 code. Falls back to 'en'."""
    if not text or not text.strip():
        return "en"

    if not _HAS_FAST_LANGDETECT:
        return "en"

    try:
        result = _ft_detect(text)
        lang = result[0]["lang"] if isinstance(result, list) else result["lang"]
        score = result[0]["score"] if isinstance(result, list) else result["score"]
        if score < 0.3:
            return "en"
        return lang
    except Exception:
        logger.debug("Language detection failed, defaulting to English")
        return "en"
