"""The PII rail's patterns and span functions. No third-party imports.

``guardrails/validators.py`` wraps these in a Guardrails AI validator; the MCP
server uses them directly when guardrails-ai is not installed. Both give the
same text: ``redact_spans(text, find_pii_spans(text))`` replays the validator.
"""

from __future__ import annotations

import re

# Street addresses: a number, a name, a street type; the longest run wins ("45
# Shady Lane Drive"). "way", "road", "place", "court", "drive" and "circle" are
# also everyday nouns, so a number is not a house number when what follows it,
# all in lower case, is function words and then one of those nouns ("72 is on its
# way", "911 in the first place"), or a unit of time or count on the way to one
# ("3 business days each way", "5 years down the road"). Neither applies when
# the street goes on to another street type ("23 first place drive") or a
# "City, ST 12345" tail follows.
_STREET_TYPE = (
    r"(?:St(?:reet)?|Ave(?:nue)?|Blvd|Boulevard|Dr(?:ive)?|Ln|Lane|Rd|Road|Way|Ct|Court"
    r"|Pl(?:ace)?|Cir(?:cle)?)"
)
_CITY_TAIL = r"\s*,\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s*,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?"
_STREET_GOES_ON = rf"(?:\s+(?:[A-Z][a-z]+\s+){{0,4}}{_STREET_TYPE}\b|\.?{_CITY_TAIL})"
_EVERYDAY_NOUN = rf"(?-i:way|road|place|court|drive|circle)\b(?!{_STREET_GOES_ON})"
_FUNCTION_WORD = (
    r"(?-i:is|are|was|were|be|been|on|in|at|of|to|for|from|by|with|its|it|the|a|an|this|that"
    r"|your|my|our|their|his|her|all|each|every|either|any|some|more|other|and|or|but|not|no"
    r"|will|can|per|as|out|up|down|back|along|under|over|first|next|last|same)"
)
_UNIT_WORD = r"(?-i:days?|weeks?|months?|years?|hours?|minutes?|times?|items?|orders?)"
_LOWER_WORD = r"(?-i:[a-z]+)"
_NOT_A_HOUSE_NUMBER = (
    rf"(?!(?:{_FUNCTION_WORD}\s+)+{_EVERYDAY_NOUN})"
    rf"(?!(?:{_LOWER_WORD}\s+){{0,3}}{_UNIT_WORD}\s+(?:{_LOWER_WORD}\s+){{0,4}}{_EVERYDAY_NOUN})"
)

# Common PII patterns — ordered so prefix-based patterns run before general numeric ones
_PII_PATTERNS = {
    "ssn": (
        r"\b\d{3}[-.\s]\d{2}[-.\s]\d{4}\b",
        "[SSN REDACTED]",
    ),
    "credit_card": (
        r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        "[CARD REDACTED]",
    ),
    "email": (
        # The lookbehind starts a match only at the start of a run of local-part
        # characters. With a bare \b, a long dotted run with no "@" was rescanned
        # from every word start inside it (quadratic). A local part with non-ASCII
        # letters is still matched only from its last ASCII run ("josé.garcia@...").
        r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[EMAIL REDACTED]",
    ),
    "date_of_birth": (
        # The date may trail its cue by a few words ("date of birth you gave us, 04/17/1986")
        # but not cross into the next sentence
        r"\b(?:DOB|date of birth|birth ?date|born on)\b[^\d\n.;!?]{0,40}?"
        r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b",
        "[DOB REDACTED]",
    ),
    "passport": (
        # The lookahead keeps a redaction token from matching as the number on a second pass
        r"\b(?:passport\s*(?:number|no\.?|#)?[:\s]*)(?!REDACTED\])[A-Z0-9]{6,9}\b",
        "[PASSPORT REDACTED]",
    ),
    "bank_account": (
        r"\b(?:account\s*(?:number|no\.?|#)|acct\.?\s*#?)[:\s]*\d{8,17}\b",
        "[BANK ACCOUNT REDACTED]",
    ),
    "routing_number": (
        r"\b(?:routing\s*(?:number|no\.?|#)|ABA)[:\s]*\d{9}\b",
        "[ROUTING NUMBER REDACTED]",
    ),
    "drivers_license": (
        r"\b(?:driver'?s?\s*license\s*(?:number|no\.?|#)?|DL\s*#?)[:\s]*"
        r"(?!REDACTED\])[A-Z0-9]{5,15}\b",
        "[DL REDACTED]",
    ),
    "address": (
        rf"\b\d{{1,5}}\s+{_NOT_A_HOUSE_NUMBER}"
        rf"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+{_STREET_TYPE}\.?(?:{_CITY_TAIL})?\b",
        "[ADDRESS REDACTED]",
    ),
    "phone": (
        r"(?<!\w)(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "[PHONE REDACTED]",
    ),
}
PII_TYPES = tuple(_PII_PATTERNS)


def detect_pii_types(text: str) -> list[str]:
    """PII types present in ``text``, in pattern order (what PIIDetectionValidator redacts)."""
    return [
        pii_type
        for pii_type, (pattern, _) in _PII_PATTERNS.items()
        if re.search(pattern, text, re.IGNORECASE)
    ]


def find_pii_spans(text: str) -> list[tuple[str, int, int]]:
    """(type, start, end) of each span PIIDetectionValidator redacts, sorted by start.

    Replays the validator exactly: patterns run in order, each only if it matches
    the original text, each substituting on the already-redacted text. Positions
    are mapped back to ``text``; a match that swallows earlier redaction tokens
    absorbs those spans. ``redact_spans(text, find_pii_spans(text))`` therefore
    equals the validator's output.
    """
    current = text
    # origin[i]: index in ``text`` of current[i], or -(claim id + 1) for token chars
    origin = list(range(len(text)))
    claims: dict[int, tuple[str, int, int]] = {}
    next_id = 0
    for pii_type, (pattern, token) in _PII_PATTERNS.items():
        if not re.search(pattern, text, re.IGNORECASE):
            continue
        pieces: list[str] = []
        new_origin: list[int] = []
        pos = 0
        for m in re.finditer(pattern, current, re.IGNORECASE):
            starts, ends = [], []
            for o in origin[m.start() : m.end()]:
                if o >= 0:
                    starts.append(o)
                    ends.append(o + 1)
                elif -o - 1 in claims:
                    _, s, e = claims.pop(-o - 1)
                    starts.append(s)
                    ends.append(e)
            claim_id = next_id
            next_id += 1
            claims[claim_id] = (pii_type, min(starts), max(ends))
            pieces += [current[pos : m.start()], token]
            new_origin += origin[pos : m.start()] + [-claim_id - 1] * len(token)
            pos = m.end()
        pieces.append(current[pos:])
        new_origin += origin[pos:]
        current, origin = "".join(pieces), new_origin
    return sorted(claims.values(), key=lambda span: span[1])


def redact_spans(text: str, spans: list[tuple[str, int, int]]) -> str:
    """Replace each (type, start, end) span with that type's redaction token."""
    out, pos = [], 0
    for pii_type, start, end in sorted(spans, key=lambda span: span[1]):
        out.append(text[pos:start])
        out.append(_PII_PATTERNS[pii_type][1])
        pos = end
    out.append(text[pos:])
    return "".join(out)


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Redacted text and the PII types found, as the validator's guard gives them."""
    spans = find_pii_spans(text)
    if not spans:
        return text, []
    return redact_spans(text, spans), detect_pii_types(text)
