#!/usr/bin/env python3
"""List dictionary words that a rule keyword matches without being that keyword.

Until v1.5 rule keywords matched at a word start, so "sue" also matched "suede"
and "credit" matched "creditor". This script runs every keyword of every default
rule over a word list and prints two lists:

- collisions: words that match and are not the keyword plus a regular ending.
  With the whole-word matcher this list is empty, and ``--check`` fails if it
  is not.
- look-alikes: dictionary words that are the keyword plus an ending the matcher
  accepts. Most are real inflections ("credits"); some are other words ("us" +
  "ed" is "used"), which no suffix rule can tell apart, so read this list by
  hand when adding a keyword.

A word list cannot find a keyword that is also a name ("Sue"): those are listed
by hand in ``autocorrector.knowledge_graph.keywords``.

    python3 scripts/audit_keywords.py                  # /usr/share/dict/words
    python3 scripts/audit_keywords.py --words my.txt   # one word per line
    python3 scripts/audit_keywords.py --check          # exit 1 on any collision
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from autocorrector.knowledge_graph.defaults import load_default_rules
from autocorrector.knowledge_graph.keywords import mentions_keyword

DEFAULT_WORDS = Path("/usr/share/dict/words")
ENDINGS = ("s", "es", "d", "ed", "ing", "'s")


def inflections(word: str) -> set[str]:
    """The word plus every regular ending ("sue" -> sues, sued, suing)."""
    forms = {word + ending for ending in ENDINGS}
    if word.endswith("e"):
        forms.add(word[:-1] + "ing")
    return forms


def matches(keyword: str, words: list[str]) -> tuple[list[str], list[str]]:
    """(collisions, look-alikes) among the words the keyword matches.

    For a phrase only the last word is open-ended ("offer you" once matched
    "offer young"), so each candidate replaces the last word of the phrase.
    """
    *head, last = keyword.lower().split(" ")
    prefix = " ".join(head)
    regular = inflections(last)
    collisions, lookalikes = [], []
    for word in words:
        lower = word.lower()
        if lower == last or not lower.startswith(last):
            continue
        if mentions_keyword(f"{prefix} {lower}".strip(), [keyword]):
            (lookalikes if lower in regular else collisions).append(word)
    return collisions, lookalikes


def audit(words: list[str]) -> dict[tuple[str, str], tuple[list[str], list[str]]]:
    """(rule id, keyword) -> (collisions, look-alikes), for every default rule keyword."""
    report = {}
    for rules in load_default_rules().values():
        for rule in rules:
            for keyword in rule["keywords"]:
                found = matches(keyword, words)
                if any(found):
                    report[(rule["rule_id"], keyword)] = found
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--words", type=Path, default=DEFAULT_WORDS, help="word list file")
    parser.add_argument("--check", action="store_true", help="exit 1 if any collision is found")
    args = parser.parse_args()

    if not args.words.is_file():
        print(f"word list not found: {args.words}", file=sys.stderr)
        return 2
    words = sorted({w.strip() for w in args.words.read_text().splitlines() if w.strip()})

    report = audit(words)
    total = 0
    for (rule_id, keyword), (collisions, lookalikes) in sorted(report.items()):
        print(f"{rule_id}  {keyword!r}")
        if collisions:
            print("    COLLISIONS: " + ", ".join(collisions))
        if lookalikes:
            print("    look-alikes: " + ", ".join(lookalikes))
        total += len(collisions)
    print(f"\n{total} collisions ({len(words)} words checked)")
    return 1 if args.check and total else 0


if __name__ == "__main__":
    sys.exit(main())
