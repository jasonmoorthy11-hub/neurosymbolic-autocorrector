"""LLM-based translation using existing Anthropic client."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from autocorrector.llm_response import completed_text

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

_LANG_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "pt": "Portuguese",
    "de": "German",
    "it": "Italian",
    "nl": "Dutch",
    "ar": "Arabic",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "hi": "Hindi",
    "th": "Thai",
    "tr": "Turkish",
    "pl": "Polish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "el": "Greek",
    "he": "Hebrew",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "ms": "Malay",
    "tl": "Filipino",
    "ro": "Romanian",
    "cs": "Czech",
    "hu": "Hungarian",
    "uk": "Ukrainian",
    "bg": "Bulgarian",
    "hr": "Croatian",
    "sk": "Slovak",
    "ca": "Catalan",
}


async def translate_to_english(
    text: str,
    source_lang: str,
    client: AsyncAnthropic,
    model: str = "claude-sonnet-5-5",
) -> str:
    """Translate text to English. Returns original on failure (fail-open)."""
    if source_lang == "en":
        return text

    lang_name = _LANG_NAMES.get(source_lang, source_lang)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=8192,
            system=(
                f"Translate the following {lang_name} text to English. "
                "Return ONLY the translation, nothing else."
            ),
            messages=[{"role": "user", "content": text}],
            output_config={"effort": "low"},
        )
        translated = completed_text(response).strip()
        if not translated:
            logger.warning("Translation to English returned no text; using original text")
            return text
        return translated
    except Exception:
        logger.exception("Translation to English failed, using original text")
        return text


async def translate_from_english(
    text: str,
    target_lang: str,
    client: AsyncAnthropic,
    model: str = "claude-sonnet-5-5",
) -> str:
    """Translate English text to target language. Returns original on failure (fail-open)."""
    if target_lang == "en":
        return text

    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=8192,
            system=(
                f"Translate the following English text to {lang_name}. "
                "Return ONLY the translation, nothing else."
            ),
            messages=[{"role": "user", "content": text}],
            output_config={"effort": "low"},
        )
        translated = completed_text(response).strip()
        if not translated:
            logger.warning("Translation from English returned no text; using original text")
            return text
        return translated
    except Exception:
        logger.exception("Translation from English failed, using original text")
        return text
