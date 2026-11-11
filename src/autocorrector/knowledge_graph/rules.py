"""Policy rule evaluation shared by the in-memory and Neo4j store paths.

Rules arrive in the in-memory shape (``rule_id``, ``rule_name``, ``description``,
``severity``, ``source_document``, ``source_section``, ``keywords``,
``conditions`` and an optional ``amount_threshold``); the Neo4j path reshapes its
records to match, so one evaluator serves both.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from autocorrector.knowledge_graph.claims import AMOUNT_PATTERN, assign_amounts, sentence_spans
from autocorrector.knowledge_graph.keywords import NOT_BEFORE, mentions_keyword
from autocorrector.models import PolicyViolation, Severity

# Claim-assignment key for a rule's own keywords (never a coverage category name)
OFFER_KEY = "\0offer"
# A coverage statement joins the category to its amount with linking words only
COVERAGE_LINK = (
    r"\s+(?:coverage\s+|limit\s+|benefit\s+|maximum\s+)?(?:is|are|covers|of|pays)"
    r"\s+(?:up\s+to\s+)?$"
)
# ...and ends there, apart from limit wording ("$2,500 per year.")
COVERAGE_TAIL = re.compile(
    r"\s*(?:(?:per|a|each)\s+(?:calendar\s+)?(?:year|visit|month)|annually|yearly)?\s*[.!]?\s*$"
)
# Moving money to the customer makes an amount an offer, whatever its value
TRANSFER_VERB = re.compile(
    r"\b(?:put|putting|apply|applying|add|adding|give|giving|cover|covering"
    r"|send|sending|issue|issuing|toward|towards)\b",
    re.IGNORECASE,
)

# Patterns indicating the agent is NOT actively making an offer
NON_OFFER_PATTERNS = [
    r"(?:unable|cannot|can'?t|won'?t|wouldn'?t|couldn'?t)\s+(?:to\s+)?(?:offer|provide|give|apply|issue)",
    r"\bcompetitor",
    r"(?:already|previously)\s+(?:applied|issued|given|credited|processed)",
    r"(?:was|were|has been|had been)\s+(?:applied|issued|given|credited)",
    r"(?:if\s+(?:you were|approved|eligible))|(?:could\s+be\s+eligible)",
    r"(?:policy|policies|credits?)\s+(?:allows?|states?|limits?|requires?)",
    r"(?:refunds?|returns?)\s+(?:are|is)\s+(?:not|no longer)\s+(?:possible|available)",
    r"(?:not|isn'?t|no longer)\s+(?:possible|eligible)",
]

# The refund-window rule is about granting a refund, not about the word. A reply
# that mentions a refund keyword is excused only when every part of every sentence
# is a refusal, a question that neither proposes the refund nor takes it as
# settled, or a neutral remark (a greeting, thanks, an apology, looking something
# up). Anything else fires, whatever it says, so wording nobody thought of errs
# toward the supervisor. The first form of this carve-out worked the other way
# round (it excused listed status and policy wordings unless the agent was seen
# to act), and each of four reviews found grants it let through: "You'd like a
# refund? Sure thing.", "I can't refund the shipping fee, just the $80 for the
# item itself.", "Your refund request has been paid out."
REFUND_RULE_ID = "REFUND-001"
# Refusals the message-level NON_OFFER_PATTERNS do not name
REFUND_REFUSAL = re.compile(
    r"\b(?:cannot|can\s?not|can't|won't|unable\s+to|not\s+(?:be\s+)?able\s+to)\s+"
    r"(?:\w+\s+){0,2}?(?:refund|process|accept)\b"
)
# ...unless the negative is an idiom for eagerness ("I can't wait to refund you",
# "I won't hesitate to refund this", "There's no reason I can't refund it") or
# refuses only a part ("I can't refund more than the $80 you paid")
REFUND_EAGER = re.compile(
    r"\bwait\s+to\b|\bhesitate\s+to\b|\bno\s+reason\b"
    r"|\b(?:more|other|faster|sooner)\s+than\b|\bexcept\b|\bonly\b|\banything\s+but\b"
)
# A question excused although it mentions a refund: it asks what the customer
# wants or did. "Would you like ...", "Do you want ..." and "Why not ..." are
# offers, so they are not here.
REFUND_QUESTION_ECHO = re.compile(
    r"\byou(?:'d|\s+would)\s+like\b"
    r"|(?<!\bdo\s)(?<!\bdid\s)\byou\s+(?:want|wanted|need|needed)\b(?!\s+(?:me|us)\b)"
    r"|\b(?:you(?:'re|\s+are)|are\s+you|were\s+you)\s+"
    r"(?:after|asking|requesting|looking|hoping|calling|seeking|wanting)\b"
    r"|\bis\s+(?:this|that|it)\s+(?:about|regarding)\b"
    r"|\bhave\s+you\b"
    r"|\bwhy\s+(?:do|did|are|would)\s+you\b"
)
# A question that takes the refund as settled, or proposes it, grants it: "How
# should we send your refund, to your card or as store credit?", "Where should
# the refund go?". Checked on every question, with or without a refund keyword.
REFUND_QUESTION_GRANTS = re.compile(
    r"\bhow\s+(?:would|do|did|should)\s+(?:you\s+|we\s+|i\s+)?(?:like|want|prefer|send)\b"
    r"|\bhow\s+about\b|\bwhere\b"
    r"|\b(?:shall|should|can|could|may)\s+(?:i|we)\s+(?:go\s+ahead|offer|issue|process|give|send|refund)\b"
    r"|\b(?:want|like)\s+(?:me|us)\s+to\b"
    r"|\bwould\s+(?:you\s+accept|an?\s+(?:\w+\s+)?refund)\b"
    r"|\b(?:refund|money)\s+(?:go|going|back\s+to)\b"
    r"|\b(?:gift\s+card|paper\s+check|cheque|store\s+credit|refund\s+method|cards?|visa"
    r"|mastercard|amex|paypal|venmo|bank|account|wallet|original\s+payment)\b"
)
# The neutral remarks, matched against a whole part and nothing more
_TROUBLE = r"(?:that|this|the\s+(?:trouble|wait|delay|inconvenience|confusion|mix-up))"
REFUND_NEUTRAL = re.compile(
    r"(?:oh\s+|ah\s+|so\s+|well\s+)?(?:"
    r"(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening))(?:\s+there|\s+[a-z]+)?"
    r"|(?:thanks|thank\s+you)(?:\s+(?:so|very)\s+much)?(?:\s+for\s+(?:your\s+patience|waiting"
    r"|holding|reaching\s+out|contacting\s+us|getting\s+in\s+touch|your\s+message|that))?"
    r"|(?:(?:i|we)(?:'m|'re|\s+am|\s+are)\s+)?(?:(?:so|very|really|truly)\s+)?sorry"
    rf"(?:\s+(?:about|for)\s+{_TROUBLE})?"
    rf"|(?:my|our)\s+apologies|(?:i|we)\s+apologi[sz]e(?:\s+(?:about|for)\s+{_TROUBLE})?"
    r"|unfortunately|i\s+understand|one\s+moment(?:\s+please)?|just\s+a\s+moment|please\s+hold"
    r"|bear\s+with\s+me"
    r")"
)
# Looking something up is not doing something ("I'll check the status of your
# refund"), unless the part goes on to report an outcome
REFUND_LOOKING = re.compile(
    r"(?:let\s+me|allow\s+me\s+to|(?:i|we)(?:'ll|\s+will|\s+can))(?:\s+just|\s+quickly)?\s+"
    r"(?:check|look\s+(?:into|up|at|for)|pull\s+up|review|take\s+a\s+look"
    r"|see\s+(?:what|if|whether|where|how))\b"
)
REFUND_OUTCOME = re.compile(
    r"\b(?:approved|granted|refunded|issued|sent|processed|credited|paid|done|released)\b"
)
# Clauses end at punctuation or a line break; a conjunction splits one into parts
_CLAUSE = re.compile(r"([^,;.!?\n]+)([,;.!?]*)")
_CONJUNCTION = re.compile(r" (?:but|so|and|because|which|while|although|then) ")
_SPACES = re.compile(r"[ \t]+")
# "..., correct?" makes the clauses before it a question too; any other last clause
# ("..., is there anything else?", "..., ok?") is a question of its own
_CONFIRMATION_TAGS = frozenset({"correct", "right", "is that right", "is that correct"})

# Wordings that contain a rule keyword without being what the rule is about: a
# thing that does not last forever, a sale the customer will not miss, being able
# to do something (no timeline). They are blanked before that rule's keywords are
# matched; the same keyword anywhere else in the reply still fires.
RULE_IDIOMS = {
    "PROMISE-001": re.compile(
        r"\b(?:do|does|did|will|wo|ca|can)n?['’]?t\s+last\s+forever\b"
        r"|\byou['’]ll\s+never\s+miss\s+(?:a|an|another|any)\s+(?:\w+\s+)?"
        r"(?:sale|deal|event|offer|promotion|promo|launch|drop|announcement)s?\b",
        re.IGNORECASE,
    ),
    "SLA-001": re.compile(r"\bwill\s+definitely\s+be\s+able\s+to\b", re.IGNORECASE),
}
# ...and only in a sentence that names no time: "By Friday we will definitely be
# able to deliver it" and "... refund you this afternoon" still fire
IDIOM_UNLESS = {
    "SLA-001": re.compile(
        r"\d|\b(?:by|within|today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday"
        r"|saturday|sunday|seconds?|minutes?|hours?|days?|weeks?|months?|years?|morning"
        r"|afternoon|evening|noon|midnight|weekend|next|soon|shortly|immediately|now"
        r"|right\s+away|no\s+time)\b",
        re.IGNORECASE,
    ),
}
_SENTENCE = re.compile(r"[^.!?\n]+[.!?\n]*|[.!?\n]+")

# Patterns indicating hedged/non-guarantee timeline references (skip SLA-001)
NON_GUARANTEE_PATTERNS = [
    r"\bshould\b",
    r"\btypically\b",
    r"\busually\b",
    r"\bmay\b",
    r"\bmight\b",
    r"\bcould\b",
    r"\bhappy to have\b",
    r"\bestimate[ds]?\b",
    r"\bapproximately\b",
    r"\bgenerally\b",
    r"\bnormally\b",
    r"\bcan'?t guarantee\b",
    r"\bno guarantee\b",
]

# Word-to-number mapping for amounts expressed in words
WORD_AMOUNTS = {
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "seventy five": 75,
    "seventy-five": 75,
    "one hundred": 100,
    "two hundred": 200,
    "three hundred": 300,
    "four hundred": 400,
    "five hundred": 500,
    "one thousand": 1000,
}


def offer_amounts(
    text: str,
    keywords: list[str],
    limits: dict[str, float],
    mentions: dict[str, list[str]],
) -> list[str]:
    """The ``$`` amounts a threshold rule with these keywords may count.

    ``limits`` maps each coverage category of the customer's plan to its limit
    and ``mentions`` each known category to the phrases that name it.

    Fails closed: only a verified coverage statement is left out. That is
    an amount that claim assignment gives to a coverage category, equal to
    that category's limit on the customer's plan, in a sentence that reads
    as a statement of coverage: the mention right before the amount,
    joined only by linking words ("Your emergency dental is $2,500."), no
    transfer verb, and none of the rule's keywords. Every other amount
    counts, including "a $600 emergency dental credit" and "...with a
    credit. I'll put $2,500 toward your emergency dental."
    """
    if not limits:
        return [m.group(1) for m in AMOUNT_PATTERN.finditer(text)]

    lower = text.lower()
    claimed = assign_amounts(text, {**mentions, OFFER_KEY: keywords})
    statement_sentences = [
        (start, end)
        for start, end in sentence_spans(text)
        if not mentions_keyword(text[start:end], keywords)
        and not TRANSFER_VERB.search(text, start, end)
    ]

    def is_statement(m: re.Match[str], cat: str) -> bool:
        if float(m.group(1).replace(",", "")) != limits[cat]:
            return False
        sentence = next(((s, e) for s, e in statement_sentences if s <= m.start() < e), None)
        if sentence is None:
            return False
        start, end = sentence
        names = "|".join(re.escape(v.lower()) for v in mentions[cat])
        before = re.search(rf"\b(?:{names}){COVERAGE_LINK}", lower[start : m.start()])
        return before is not None and COVERAGE_TAIL.match(lower, m.end(), end) is not None

    verified = {
        m.span()
        for cat, found in claimed.items()
        if cat in limits
        for m in found
        if is_statement(m, cat)
    }
    return [m.group(1) for m in AMOUNT_PATTERN.finditer(text) if m.span() not in verified]


def without_idioms(rule_id: str, text: str) -> str:
    """``text`` with the rule's idioms (``RULE_IDIOMS``) blanked, offsets unchanged."""
    idioms = RULE_IDIOMS.get(rule_id)
    if idioms is None:
        return text

    def blank(sentence: str) -> str:
        return idioms.sub(lambda m: " " * len(m.group()), sentence)

    unless = IDIOM_UNLESS.get(rule_id)
    if unless is None:
        return blank(text)
    pieces = []
    for sentence in _SENTENCE.findall(text):
        blanked = blank(sentence)
        pieces.append(sentence if unless.search(blanked) else blanked)
    return "".join(pieces)


def _refund_part_excused(part: str, question: bool, keywords: list[str]) -> bool:
    """True when one part of a sentence neither grants a refund nor could."""
    bare = part.strip(" \t-*>\"'()")
    if not any(char.isalnum() for char in bare):
        return True
    if question:
        if REFUND_QUESTION_GRANTS.search(part):
            return False
        return not mentions_keyword(part, keywords) or bool(REFUND_QUESTION_ECHO.search(part))
    if REFUND_REFUSAL.search(part):
        return not REFUND_EAGER.search(part)
    if REFUND_NEUTRAL.fullmatch(bare):
        return True
    return bool(REFUND_LOOKING.match(bare)) and not REFUND_OUTCOME.search(bare)


def grants_refund(text: str, keywords: list[str]) -> bool:
    """True when ``text`` mentions a refund keyword and is not excused as a whole.

    A reply is excused only when every part of every sentence is a refusal, a
    question that neither proposes the refund nor takes it as settled, or a
    neutral remark (see ``_refund_part_excused``). Fails closed: a status
    report, a statement of policy and any wording not listed here all grant.
    """
    normalised = _SPACES.sub(" ", text.lower().replace("’", "'"))
    parts: list[tuple[str, bool]] = []  # (part, in a question)
    chain: list[list[str]] = []

    def close(asks: bool) -> None:
        tagged = asks and len(chain) > 1 and " ".join(chain[-1]).strip() in _CONFIRMATION_TAGS
        for index, clause in enumerate(chain):
            question = asks and (tagged or index == len(chain) - 1)
            parts.extend((part, question) for part in clause)
        chain.clear()

    for match in _CLAUSE.finditer(normalised):
        chain.append(_CONJUNCTION.split(match.group(1)))
        ends = match.group(2)
        if not ends or ends.strip(",;"):
            close("?" in ends)  # the sentence ends here (or at a line break)
    close(False)  # a reply that stops on a comma

    if not any(mentions_keyword(part, keywords) for part, _ in parts):
        return False
    return not all(_refund_part_excused(part, question, keywords) for part, question in parts)


def violations_for(
    rules: list[dict[str, Any]],
    text: str,
    customer_context: dict[str, Any],
    amounts_for: Callable[[list[str]], list[str]],
) -> list[PolicyViolation]:
    """Evaluate in-memory-shaped rules against the text (both store paths).

    A rule triggers only when ALL of these are true:
    1. At least one keyword matches (and text is not a non-offer context for credit rules)
    2. The extracted dollar amount exceeds the amount_threshold (if defined)
    3. All conditions are met (e.g., tenure < 90 days)
    If a rule has no conditions, only keyword + amount_threshold are checked.
    ``amounts_for(keywords)`` returns the ``$`` amounts a threshold rule with
    those keywords may count (see :func:`offer_amounts`).
    """
    violations = []
    text_lower = text.lower()

    # Detect non-offer contexts (agent describing, not offering)
    is_non_offer = any(re.search(pat, text_lower) for pat in NON_OFFER_PATTERNS)

    # Detect hedged/non-guarantee timeline references
    is_hedged = any(re.search(pat, text_lower) for pat in NON_GUARANTEE_PATTERNS)

    for rule in rules:
        # Step 1: Check keyword triggers
        if not mentions_keyword(
            without_idioms(rule.get("rule_id", ""), text), rule.get("keywords", [])
        ):
            continue

        # Step 1b: Skip credit and refund rules if non-offer context detected
        if is_non_offer and rule.get("rule_id", "").startswith(("CREDIT", "REFUND")):
            continue

        # Step 1b': the refund-window rule is excused only as a whole reply
        if rule.get("rule_id") == REFUND_RULE_ID and not grants_refund(
            text, rule.get("keywords", [])
        ):
            continue

        # Step 1c: Skip SLA rules if hedged/non-guarantee language detected
        if is_hedged and rule.get("rule_id", "").startswith("SLA"):
            continue

        # Step 2: Check amount threshold (if defined)
        if "amount_threshold" in rule:
            # Parse $X amounts (verified coverage statements excluded)
            amounts = amounts_for(rule.get("keywords", []))
            # Parse bare "N dollar(s)" or "N credit" without $ prefix
            bare = re.findall(
                r"(?:^|\s)(\d+(?:,\d{3})*(?:\.\d{2})?)\s+"
                rf"(?:dollar|credit(?!{NOT_BEFORE['credit']})|compensation|adjustment)",
                text_lower,
            )
            amounts.extend(bare)

            parsed = [float(a.replace(",", "")) for a in amounts]

            # Check word amounts
            for word, val in WORD_AMOUNTS.items():
                if word in text_lower:
                    parsed.append(val)

            amount_exceeded = any(a > rule["amount_threshold"] for a in parsed)

            # Check sum of multiple amounts (e.g., $30 + $40 = $70)
            # Only for lower thresholds — avoids mixing credit and non-credit amounts
            if not amount_exceeded and len(parsed) >= 2 and rule["amount_threshold"] < 500:
                amount_exceeded = sum(parsed) > rule["amount_threshold"]

            if not amount_exceeded:
                continue

        # Step 3: Check all conditions (must ALL be true)
        conditions = rule.get("conditions", [])
        if conditions:
            all_conditions_met = True
            for condition in conditions:
                field = condition["field"]
                operator = condition["operator"]
                threshold = condition["value"]
                actual_value = customer_context.get(field)

                # Missing context defaults to 0 (conservative: unknown = new/recent)
                if actual_value is None:
                    actual_value = 0

                if (
                    operator == "lt"
                    and not (actual_value < threshold)
                    or operator == "gt"
                    and not (actual_value > threshold)
                    or operator == "eq"
                    and actual_value != threshold
                ):
                    all_conditions_met = False
                    break

            if not all_conditions_met:
                continue

        # All checks passed — this is a violation
        violations.append(
            PolicyViolation(
                rule_id=rule["rule_id"],
                rule_name=rule["rule_name"],
                description=rule["description"],
                severity=Severity(rule["severity"]),
                source_document=rule.get("source_document"),
                source_section=rule.get("source_section"),
            )
        )

    return violations
