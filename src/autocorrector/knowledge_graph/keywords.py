"""Whole-word matching of rule keywords, shared by the rule evaluator and the rewriter.

A keyword matches itself and its regular inflections, never a longer word that
merely starts with it. Up to v1.5 the match was a word-start prefix, so "sue"
matched "suede" and "credit" matched "creditor".
"""

from __future__ import annotations

import re
from functools import lru_cache

# Keywords that are also given names. Written "Sue", the word is a name only in a
# direct address: a greeting right before it and punctuation (or the end) right
# after it ("Thanks, Sue.", "Hi Sue, ..."). Everything else is read as the verb,
# so "Sue will call you back" still fires: a held reply costs less than a missed
# legal threat, and no wider rule survived review without missing real threats.
_NAME_KEYWORDS = frozenset({"sue"})
_GREETING_BEFORE = re.compile(
    r"(?<!\w)(?:hi|hello|hey|dear|thanks|thank you|regards"
    r"|good (?:morning|afternoon|evening)|ms\.?|mrs\.?|miss),?\s+$",
    re.IGNORECASE,
)
_ADDRESS_ENDS = re.compile(r"\s*(?:[,.!?]|$)")

# A keyword that is the first word of a compound naming something else: "credit
# card" is a means of payment, not a credit. The bare keyword does not match there
# ("$40 back to your credit card" offers no credit), nor in "credit-card" or "credit
# or debit card", and only on the same line. Its inflections always match
# ("credited card ending 4417"), and so does the verb ("I'll credit card ending
# 4417 with $90", "to credit cards ending ..."). "a $75 credit on your credit card
# statement" still matches at the first "credit".
NOT_BEFORE = {
    "credit": r"(?:/debit|[ \t\u00a0]+or[ \t\u00a0]+debit)?(?:[ \t\u00a0]+|-)cards?(?!\w)"
}
# Words that make the keyword a verb where it stands before such a compound
_VERB_CUES = (
    "to",
    "'ll",
    "\u2019ll",
    "will",
    "can",
    "could",
    "would",
    "should",
    "must",
    "did",
    "I",
    "we",
    "and",
    "me",
    "just",
    "let's",
    "please",
    "then",
    "also",
    "now",
)


def _suffixes(word: str, phrase: bool) -> list[str]:
    """Regular inflection endings for a keyword's last word.

    A single word takes noun and verb endings ("credit": credits, credited,
    crediting); a phrase names one wording, so it takes a plural only
    ("internal tools", never "always willing"). "-es" follows a sibilant and
    "-d" a final "e", so "car" does not match "card" or "cares".
    """
    plural = ["es"] if word.endswith(("s", "x", "z", "ch", "sh")) else ["s"]
    if phrase:
        return plural
    past = ["d"] if word.endswith("e") else ["ed"]
    return ["'s", *plural, *past, "ing"]


def _after_cue(cue: str, kw: str) -> str:
    """Lookbehind for ``kw`` right after a whole-word verb cue ("I", not "Citi")."""
    boundary = "" if cue[0] in "'\u2019" else r"(?<!\w)"
    return rf"(?<={boundary}{re.escape(cue)} {re.escape(kw)})"


@lru_cache(maxsize=4096)
def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """The compiled matcher for one rule keyword (see :func:`mentions_keyword`)."""
    kw = keyword.lower()
    phrase = " " in kw
    endings = "|".join(re.escape(s) for s in _suffixes(kw.rsplit(" ", 1)[-1], phrase))
    body = rf"{re.escape(kw)}(?:{endings})?"
    if not phrase and kw.endswith("e"):
        body += "|" + re.escape(kw[:-1]) + "ing"
    if kw in NOT_BEFORE:
        as_verb = "|".join(_after_cue(cue, kw) for cue in _VERB_CUES)
        bare = rf"{re.escape(kw)}(?:(?!{NOT_BEFORE[kw]})|{as_verb})"
        body = body.replace(re.escape(kw), bare, 1)
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE)


def _is_name(text: str, keyword: str, match: re.Match[str]) -> bool:
    """True when a name keyword is written as a name inside a direct address."""
    if match.group() != keyword.capitalize():
        return False  # "sue", "SUE", "Sued", "Suing" and "Sue's" are never skipped
    before = text[max(0, match.start() - 24) : match.start()]
    return bool(_GREETING_BEFORE.search(before) and _ADDRESS_ENDS.match(text, match.end()))


def mentions_keyword(text: str, keywords: list[str]) -> bool:
    """True when the text contains one of the keywords as a whole word or phrase.

    "sue" matches "sued" and "suing" but not "suede" or "issue"; "credit"
    matches "credited" but not "creditor"; "always will" does not match
    "always willing"; "credit" does not match in "credit card". Case is ignored,
    except that a keyword which is also a given name is skipped where the text
    greets a person by it ("Thanks, Sue.").
    """
    for keyword in keywords:
        is_name_keyword = keyword.lower() in _NAME_KEYWORDS
        for match in keyword_pattern(keyword).finditer(text):
            if not (is_name_keyword and _is_name(text, keyword, match)):
                return True
    return False
