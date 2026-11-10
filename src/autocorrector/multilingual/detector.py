"""Language detection using fast-langdetect (176 languages)."""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

try:
    from fast_langdetect import detect as _ft_detect

    _HAS_FAST_LANGDETECT = True
except ImportError:
    _HAS_FAST_LANGDETECT = False


# Below this many words the detector is unreliable ("OK." -> pl at 0.99, a
# five-character order code -> zh at 0.99), and a non-English result escalates the
# output. Short plain-ASCII text ("OK.", "Plan B", "Merci", "Z7KQ2", "ok 2.75") is
# treated as English unless it carries an amount: a "$", or a number standing on its
# own next to two or more words ("Credito de 750 aplicado."). Short text with
# accented letters ("Crédito aplicado.") is always detected.
MIN_WORDS_FOR_DETECTION = 4

# Scripts written without spaces (CJK ideographs, kana, hangul): one character ~ one word.
_UNSPACED_CHAR = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_LETTER = re.compile(r"[^\W\d_]")
# A number on its own ("750", "2.75", "50%"), not a digit inside a code or username
_STANDALONE_NUMBER = re.compile(r"(?<![\w-])\d[\d.,]*%?(?![\w-])")
# Typographic punctuation that English text carries; nothing else non-ASCII is excused
_TYPOGRAPHIC = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
)

# Some of the languages the detector names that are not written in Latin letters
# (the list is partial; a label missing from it is simply not excused). Plain-ASCII
# text cannot be in one of them, whatever the score. Serbian is left out on purpose:
# it is also written in Latin letters.
# fmt: off
_NON_LATIN_SCRIPT = frozenset({
    "am", "ar", "be", "bg", "bn", "el", "fa", "gu", "he", "hi", "hy", "ja", "ka", "kk", "km",
    "kn", "ko", "ky", "lo", "mk", "ml", "mn", "mr", "my", "ne", "pa", "ps", "ru", "si", "ta",
    "te", "tg", "th", "uk", "ur", "yi", "zh",
})
# fmt: on

# Common English words that are not everyday words in Spanish, French, Portuguese,
# German, Italian or Dutch. Typo-ridden English ("what els can i do" -> ca at 0.99)
# still carries them. Words those languages share with English are left out ("a",
# "no", "me", "in", "is", "we", "help", "let", "will", "has", "not", "for", "check",
# "yes"); "sorry", "thank" and "thanks" are borrowed by several of them, which is
# why one marker alone decides nothing.
# fmt: off
_ENGLISH_MARKERS = frozenset({
    "about", "alright", "and", "any", "anything", "are", "can", "could", "did", "does", "from",
    "get", "got", "great", "have", "here", "how", "just", "know", "may", "might", "need", "new",
    "our", "please", "should", "sorry", "thank", "thanks", "that", "the", "their", "there",
    "these", "they", "this", "those", "today", "what", "when", "where", "which", "who", "why",
    "with", "would", "you", "your",
})
# fmt: on
# Plain-ASCII text is English when it has at least this many different marker words
# and they make up at least this share of its words.
MIN_ENGLISH_MARKERS = 2
ENGLISH_MARKER_SHARE = 0.2


def _word_count(text: str) -> int:
    unspaced = len(_UNSPACED_CHAR.findall(text))
    spaced = len(re.findall(r"[^\W\d_]+", _UNSPACED_CHAR.sub(" ", text)))
    return spaced + unspaced


def _plain_ascii(text: str) -> bool:
    """True for ASCII text, allowing curly quotes, dashes, an ellipsis and a hard space.

    The text is composed first (NFC), so an accent typed as a combining mark still
    counts as an accented letter.
    """
    return unicodedata.normalize("NFC", text).translate(_TYPOGRAPHIC).isascii()


def _carries_amount(text: str, words: int) -> bool:
    return "$" in text or (words >= 2 and bool(_STANDALONE_NUMBER.search(text)))


def _too_short_to_detect(text: str) -> bool:
    words = _word_count(text)
    return (
        words < MIN_WORDS_FOR_DETECTION and _plain_ascii(text) and not _carries_amount(text, words)
    )


def _reads_as_english(text: str) -> bool:
    words = re.findall(r"[a-z]+", text.lower())
    markers = [word for word in words if word in _ENGLISH_MARKERS]
    return (
        len(set(markers)) >= MIN_ENGLISH_MARKERS
        and len(markers) / len(words) >= ENGLISH_MARKER_SHARE
    )


def detect_language(text: str) -> str:
    """Detect language from text. Returns ISO 639-1 code. Falls back to 'en'."""
    if not text or not _LETTER.search(text):
        return "en"  # nothing to read: empty, or digits and symbols only

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
        if (
            lang != "en"
            and _plain_ascii(text)
            and (lang in _NON_LATIN_SCRIPT or _reads_as_english(text))
        ):
            return "en"
        return lang
    except Exception:
        logger.debug("Language detection failed, defaulting to English")
        return "en"
