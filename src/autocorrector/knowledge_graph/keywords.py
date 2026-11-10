"""Whole-word matching of rule keywords, shared by the rule evaluator and the rewriter.

A keyword matches itself and its regular inflections, never a longer word that
merely starts with it. Up to v1.5 the match was a word-start prefix, so "sue"
matched "suede" and "credit" matched "creditor".
"""

from __future__ import annotations

import re
from functools import lru_cache

# Keywords that are also given names. Written "Sue" the word is read as the verb
# unless the wording around it shows a person (see ``_is_name``): when in doubt the
# rule fires, because a missed legal threat costs more than a held reply.
_NAME_KEYWORDS = frozenset({"sue"})
# An object right after it makes it the verb, whatever comes before ("Thanks. Sue us then.")
_VERB_OBJECT = re.compile(r"\s+(?:us|me|you|them|him|her|it)\b", re.IGNORECASE)
# A greeting, a title or a verb that takes a person, directly before the name and in
# the same sentence ("Thanks, Sue.", "Ms. Sue", "I asked Sue")
_NAME_BEFORE = re.compile(
    r"\b(?:(?:hi|hello|hey|dear|thanks|thank you|sorry|regards|ok|okay|sure|welcome|miss|with"
    r"|ask|asked|give|gave|tell|told|from|for|by|i'm|i am|this is|name is),?|(?:ms|mrs)\.?)\s+$",
    re.IGNORECASE,
)
# A comma that does not continue the threat ("Sue, your order shipped", not "Sue, if
# you must"), or a verb the person is the subject of, directly after
_NAME_AFTER = re.compile(
    r",(?!\s*(?:if|then|and|but)\b)|\s+(?:will|can|was|from|here|speaking)\b",
    re.IGNORECASE,
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


@lru_cache(maxsize=4096)
def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """The compiled matcher for one rule keyword (see :func:`mentions_keyword`)."""
    kw = keyword.lower()
    phrase = " " in kw
    endings = "|".join(re.escape(s) for s in _suffixes(kw.rsplit(" ", 1)[-1], phrase))
    body = rf"{re.escape(kw)}(?:{endings})?"
    if not phrase and kw.endswith("e"):
        body += "|" + re.escape(kw[:-1]) + "ing"
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE)


def _is_name(text: str, keyword: str, match: re.Match[str]) -> bool:
    """True when a name keyword is written as a name and the wording shows a person."""
    name = keyword.capitalize()
    if match.group() == name + "'s":
        return True  # "Sue's replacement"
    if match.group() != name:
        return False  # "sue", "SUE", "Sued" and "Suing" are the verb
    if _VERB_OBJECT.match(text, match.end()):
        return False
    before = text[max(0, match.start() - 16) : match.start()]
    return bool(_NAME_AFTER.match(text, match.end()) or _NAME_BEFORE.search(before))


def mentions_keyword(text: str, keywords: list[str]) -> bool:
    """True when the text contains one of the keywords as a whole word or phrase.

    "sue" matches "sued" and "suing" but not "suede" or "issue"; "credit"
    matches "credited" but not "creditor"; "always will" does not match
    "always willing". Case is ignored, except that a keyword which is also a
    given name is skipped where the text addresses or names a person
    ("Thanks, Sue.", "Sue will call you back").
    """
    for keyword in keywords:
        is_name_keyword = keyword.lower() in _NAME_KEYWORDS
        for match in keyword_pattern(keyword).finditer(text):
            if not (is_name_keyword and _is_name(text, keyword, match)):
                return True
    return False
