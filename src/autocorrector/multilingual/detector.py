"""Language detection using fast-langdetect (176 languages)."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

try:
    from fast_langdetect import detect as _ft_detect

    _HAS_FAST_LANGDETECT = True
except ImportError:
    _HAS_FAST_LANGDETECT = False


# Below this many words the detector is unreliable ("OK." -> pl at 0.99), and a
# non-English result escalates the output. Short plain-ASCII text with no digits or
# currency ("OK.", "Plan B", "Merci") is treated as English; short text carrying an
# amount or non-ASCII letters ("Crédito de $750 aplicado.") is still detected.
MIN_WORDS_FOR_DETECTION = 4

# Scripts written without spaces (CJK ideographs, kana, hangul): one character ~ one word.
_UNSPACED_CHAR = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_AMOUNT_OR_NUMBER = re.compile(r"[\d$€£¥]")


def _word_count(text: str) -> int:
    unspaced = len(_UNSPACED_CHAR.findall(text))
    spaced = len(re.findall(r"[^\W\d_]+", _UNSPACED_CHAR.sub(" ", text)))
    return spaced + unspaced


def _too_short_to_detect(text: str) -> bool:
    return (
        _word_count(text) < MIN_WORDS_FOR_DETECTION
        and text.isascii()
        and not _AMOUNT_OR_NUMBER.search(text)
    )


def detect_language(text: str) -> str:
    """Detect language from text. Returns ISO 639-1 code. Falls back to 'en'."""
    if not text or not text.strip():
        return "en"

    if _too_short_to_detect(text):
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
