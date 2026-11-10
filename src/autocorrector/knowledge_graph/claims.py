"""Claim extraction: which dollar amount does each coverage-category mention claim?

Every ``$`` amount in a clause is assigned to the category mention nearest to
it, so "$40 specialist copay and $2,500 emergency dental" checks $40 against
the copay and $2,500 against dental, rather than every amount against every
category. A gap that crosses a connector ("and", a comma) loses to one that
doesn't, so "$2,500 for emergency dental and $10,000 for emergency room"
pairs each amount with the category that follows it.

Amounts that name some other kind of money ("a $45 copay", "the bill was
$1,200") are not coverage claims; where that is ambiguous, an amount counts
only with limit language ("up to", "capped", "maximum", "annual").
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence

from autocorrector.knowledge_graph.keywords import keyword_pattern
from autocorrector.models import FactCheckResult

AMOUNT_PATTERN = re.compile(r"\$(\d+(?:,\d{3})*(?:\.\d{2})?)")

# A sentence ends at .!? before an uppercase letter, amount or digit, or at a line break
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])[ \t]+(?=[\"'(\[]?[A-Z$\d])|[ \t]*\n\s*")
_ABBREVIATION = re.compile(
    r"(?:^|[\s(])(?:e\.g|i\.e|etc|approx|vs|incl|est|no|dr|mr|mrs|ms|st|jr|sr)\.$",
    re.IGNORECASE,
)
_CLAUSE_BREAK = re.compile(r";\s*")
_CONNECTOR_WORD = re.compile(r"\b(?:and|but|or|while|whereas|plus)\b", re.IGNORECASE)

# Language that marks an amount as a limit rather than a charge or payout
_LIMIT_CUE = re.compile(
    r"\b(?:up\s+to|capped|limited|limit|maximum|max|cap|annual|yearly|per\s+year|a\s+year)\b",
    re.IGNORECASE,
)
_SPEND_LIMIT_CUE = re.compile(r"\b(?:up\s+to|covered|capped|max(?:imum)?)\b", re.IGNORECASE)
# A sentence that may refer back to the previous one ("It pays up to $5,000.");
# it only claims when it also carries limit language
_ANAPHOR = re.compile(
    r"\s*[\"'(]?(?:(?:it|this|that)\b"
    r"|the\s+(?:annual\s+|yearly\s+)?(?:limit|maximum|max|cap|benefit|coverage)\s+(?:is|of)\b)",
    re.IGNORECASE,
)

# Money words that are not coverage limits; an amount typed by one is not a coverage claim
_MONEY_WORDS = (
    r"co-?pay|credit|deposit|fee|refund|bill|charge|deductible|balance"
    r"|payment|discount|rebate|penalty|invoice|reimbursement|check"
)
# "premium" only types an amount it follows ("$120 premium"): before one it is a plan name
_TYPED_AFTER = re.compile(r"[ \t]+(" + _MONEY_WORDS + r"|premium)s?\b", re.IGNORECASE)
# "fee of $30", "the bill was $1,200", "charges for your visit were $1,200"
# (up to 4 words between, never across a connector: "no deductible and covers up to $5,000")
_TYPED_BEFORE = re.compile(
    r"\b(" + _MONEY_WORDS + r")s?\b"
    r"(?:[ \t]+(?!(?:and|but|or|while|whereas|plus|so)\b)[\w'’-]+){0,4}?[ \t]+$",
    re.IGNORECASE,
)
_TYPED_WINDOW = 60
# Amounts used or left of a benefit are usage, not the limit ("used $480", "$2,020 remaining")
_USAGE_BEFORE = re.compile(
    r"\b(used|spent|paid|leaves|left)[ \t]+(?:only[ \t]+|about[ \t]+)?$", re.IGNORECASE
)
_USAGE_AFTER = re.compile(r"[ \t]+(remaining|left)\b", re.IGNORECASE)
# Money words that, right after a category, describe spending under it ("dental charges")
_SPEND_WORDS = frozenset({"charge", "bill", "payment", "fee", "invoice"})


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) offsets of each sentence or line in ``text``."""
    spans = []
    start = 0
    for brk in _SENTENCE_BREAK.finditer(text):
        if "\n" in brk.group():
            if brk.start() > 0 and text[brk.start() - 1] == ":":
                continue  # "Emergency dental:\n$5,000" is one statement
        elif _ABBREVIATION.search(text, max(0, brk.start() - 8), brk.start()):
            continue
        spans.append((start, brk.start()))
        start = brk.end()
    spans.append((start, len(text)))
    return spans


def _clause_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans = []
    for brk in _CLAUSE_BREAK.finditer(text, start, end):
        spans.append((start, brk.start()))
        start = brk.end()
    spans.append((start, end))
    return spans


def assign_amounts(
    text: str, categories: Mapping[str, Iterable[str]]
) -> dict[str, list[re.Match[str]]]:
    """Map each category to the ``$`` amount matches it claims, in text order.

    ``categories`` maps a category name to the phrases that mention it
    (matched as whole words, see ``keywords.keyword_pattern``). Each amount is claimed by
    at most one category: the nearest mention in its clause, preferring a gap
    without a connector word, then one without a comma, then the shorter gap,
    then a mention before the amount. Amounts typed as other money are skipped
    (see ``_typed_elsewhere``). A sentence with no mention that starts "It",
    "This", "The annual maximum is"... and carries limit language claims its
    untyped amounts for the previous sentence's single category.

    Runs in O(n log n): only the nearest mention on each side of an amount
    can win, and connector checks use precomputed positions.
    """
    lower = text.lower()
    patterns = {cat: [keyword_pattern(v) for v in variants] for cat, variants in categories.items()}
    gaps = _GapIndex(text)
    claims: dict[str, list[re.Match[str]]] = {cat: [] for cat in categories}
    previous_category: str | None = None

    for s_start, s_end in sentence_spans(text):
        sentence_categories: set[str] = set()
        sentence_amounts: list[re.Match[str]] = []
        for c_start, c_end in _clause_spans(text, s_start, s_end):
            mentions = [
                (m.start(), m.end(), cat)
                for cat, cat_patterns in patterns.items()
                for pattern in cat_patterns
                for m in pattern.finditer(lower, c_start, c_end)
            ]
            amounts = list(AMOUNT_PATTERN.finditer(text, c_start, c_end))
            sentence_categories.update(cat for _, _, cat in mentions)
            sentence_amounts.extend(amounts)
            if not mentions:
                continue
            by_start = sorted(mentions)
            starts = [m[0] for m in by_start]
            by_end = sorted(mentions, key=lambda m: (m[1], m[0], m[2]))
            ends = [m[1] for m in by_end]
            for amount in amounts:
                candidates = []
                i = bisect_right(ends, amount.start()) - 1
                while i >= 0 and (not candidates or ends[i] == candidates[0][1]):
                    candidates.append(by_end[i])
                    i -= 1
                after = []
                j = bisect_left(starts, amount.end())
                while j < len(by_start) and (not after or starts[j] == after[0][0]):
                    after.append(by_start[j])
                    j += 1
                candidates += after
                m_start, m_end, cat = min(candidates, key=lambda m, a=amount: gaps.rank(m, a))
                if not _typed_elsewhere(text, lower, amount, m_start, m_end):
                    claims[cat].append(amount)

        refers_back = (
            previous_category
            and not sentence_categories
            and _ANAPHOR.match(text, s_start)
            and _LIMIT_CUE.search(text, s_start, s_end)
        )
        if refers_back:
            claims[previous_category].extend(
                a for a in sentence_amounts if _money_noun(text, a) is None
            )

        single = len(sentence_categories) == 1 and not sentence_amounts
        previous_category = next(iter(sentence_categories)) if single else None

    return {cat: sorted(found, key=lambda m: m.start()) for cat, found in claims.items()}


def nearest_plan(
    text: str, amount: re.Match[str], plans: Mapping[str, Iterable[str]]
) -> str | None:
    """The plan whose mention sits nearest to ``amount`` in its sentence, if any.

    ``plans`` maps a plan key to the phrases that name it ("premium plan").
    A mention claims an amount only when the gap between them crosses no
    connector word or comma ("The Premium plan covers up to $5,000, while
    your plan covers up to $2,500" leaves $2,500 to whatever "your plan"
    maps to); among those, the shorter gap wins, then a mention before the
    amount. No such mention means ``None``: the amount is about whichever
    plan the conversation is already about.
    """
    lower = text.lower()
    s_start, s_end = next(
        ((s, e) for s, e in sentence_spans(text) if s <= amount.start() < e),
        (0, len(text)),
    )
    gaps = _GapIndex(text)
    best: tuple[tuple, str] | None = None
    for key, variants in plans.items():
        for variant in variants:
            # Plan names match from a word start ("premium" also finds "premiums")
            pattern = re.compile(r"\b" + re.escape(variant.lower()))
            for m in pattern.finditer(lower, s_start, s_end):
                if m.start() < amount.end() and m.end() > amount.start():
                    continue
                crosses_connector, crosses_comma, *_ = gaps.rank((m.start(), m.end(), key), amount)
                if crosses_connector or crosses_comma:
                    continue
                rank = (gaps.rank((m.start(), m.end(), key), amount), key)
                if best is None or rank < best:
                    best = rank
    return best[1] if best else None


class _GapIndex:
    """Answers "does text[lo:hi] contain a connector word / a comma?" by bisection."""

    def __init__(self, text: str):
        self._connectors = [m.start() for m in _CONNECTOR_WORD.finditer(text)]
        self._commas = [i for i, ch in enumerate(text) if ch == ","]

    @staticmethod
    def _any(positions: list[int], lo: int, hi: int) -> bool:
        return bisect_left(positions, lo) < bisect_left(positions, hi)

    def rank(self, mention: tuple[int, int, str], amount: re.Match[str]) -> tuple:
        m_start, m_end, _ = mention
        if amount.start() >= m_end:
            lo, hi, amount_before = m_end, amount.start(), 0
        else:
            lo, hi, amount_before = amount.end(), m_start, 1
        return (
            self._any(self._connectors, lo, hi),
            self._any(self._commas, lo, hi),
            hi - lo,
            amount_before,
        )


def _stem(word: str) -> str:
    return word.replace("-", "").removesuffix("s")


def _money_noun(text: str, amount: re.Match[str]) -> tuple[str, int, int] | None:
    """The non-coverage money word typing this amount, with its offsets, if any.

    "$45 copay", "fee of $30" and "the bill was $1,200" are typed;
    "$5,000 in emergency dental" is not.
    """
    after = _TYPED_AFTER.match(text, amount.end())
    if after:
        return after.group(1).lower(), after.start(1), after.end(1)
    window_start = max(0, amount.start() - _TYPED_WINDOW)
    before = _TYPED_BEFORE.search(text, window_start, amount.start())
    if before:
        return before.group(1).lower(), before.start(1), before.end(1)
    usage = _USAGE_AFTER.match(text, amount.end()) or _USAGE_BEFORE.search(
        text, window_start, amount.start()
    )
    if usage:
        return "usage", usage.start(1), usage.end(1)
    return None


def _typed_elsewhere(
    text: str, lower: str, amount: re.Match[str], m_start: int, m_end: int
) -> bool:
    """True when a money word types the amount as something other than this category.

    "a $45 copay" next to a dental mention is typed elsewhere, and so is an
    amount before a mention that a money word follows ("a $700 emergency
    room credit", "$650 toward your emergency dental bill"). The word does
    not count when the mention names it ("specialist copay is $60"), or when
    it is a spend word right after the mention followed by limit language
    ("emergency dental charges of up to $5,000"). "Your emergency room bill
    was $1,200" stays typed: it is a bill, not the limit.
    """
    typed = _money_noun(text, amount)
    if typed is None and amount.end() <= m_start:
        after = _TYPED_AFTER.match(text, m_end)
        if after:
            typed = after.group(1).lower(), after.start(1), after.end(1)
    if typed is None:
        return False
    noun, noun_start, noun_end = typed
    if _stem(noun) in _stem(lower[m_start:m_end]):
        return False
    follows_mention = noun_start >= m_end and not text[m_end:noun_start].strip()
    limit_language = noun_end <= amount.start() and _SPEND_LIMIT_CUE.search(
        text, noun_end, amount.start()
    )
    return not (follows_mention and _stem(noun) in _SPEND_WORDS and limit_language)


def fact_replacements(
    text: str, fact_errors: Sequence[FactCheckResult]
) -> list[tuple[int, int, FactCheckResult]]:
    """Locate each wrong claim in ``text`` as ``(start, end, error)``, sorted by start.

    Uses the claim's span when it still points at the claim; otherwise
    matches whole-amount occurrences of the claim ("$50" never matches "$500").
    Overlapping spans keep the later-starting (then longer) one.
    """
    found: dict[tuple[int, int], FactCheckResult] = {}
    for error in fact_errors:
        if not (error.claim and error.correct_value):
            continue
        start, end = error.claim_start, error.claim_end
        if start is not None and end is not None and text[start:end] == error.claim:
            found[(start, end)] = error
            continue
        for m in re.finditer(re.escape(error.claim) + r"(?!\d|,\d|\.\d)", text):
            found.setdefault((m.start(), m.end()), error)

    kept: list[tuple[int, int, FactCheckResult]] = []
    next_start = len(text) + 1
    for (start, end), error in sorted(found.items(), key=lambda item: item[0], reverse=True):
        if end > next_start:  # overlaps a later span already kept
            continue
        kept.append((start, end, error))
        next_start = start
    return kept[::-1]


def apply_fact_corrections(text: str, fact_errors: Sequence[FactCheckResult]) -> str:
    """Replace each wrong claim with its correct value (spans from ``fact_replacements``)."""
    corrected = text
    for start, end, error in reversed(fact_replacements(text, fact_errors)):
        corrected = corrected[:start] + error.correct_value + corrected[end:]
    return corrected
