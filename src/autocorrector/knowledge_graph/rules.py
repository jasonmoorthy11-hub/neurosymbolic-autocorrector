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
from autocorrector.knowledge_graph.keywords import mentions_keyword
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

# The refund-window rule is about granting a refund, not about the word. A clause
# that mentions a refund keyword does not grant one when it is a refusal, a
# question put to the customer, a report on a refund already under way or a
# statement of policy, and the agent does not act in it. The real-turn audit found
# 238 of 531 REFUND-001 flags were of these kinds.
REFUND_RULE_ID = "REFUND-001"
# Refusals the message-level NON_OFFER_PATTERNS do not name
REFUND_REFUSAL = re.compile(
    r"\b(?:cannot|can\s?not|can't|won't|unable\s+to|not\s+(?:be\s+)?able\s+to)\s+"
    r"(?:\w+\s+){0,2}?(?:refund|process|accept)\b"
)
REFUND_NON_GRANT_PATTERNS = [
    # status of a refund that already exists
    r"\brefund(?:\s+status)?\s+(?:is|was|has\s+been)\s+(?:still\s+|currently\s+|already\s+)*"
    r"(?:in\s+progress|in\s+process|being\s+processed|processing|pending|complete|completed"
    r"|not\s+started|not\s+yet\s+started)\b",
    r"\b(?:status\s+of|waiting\s+(?:for|on)|you\s+initiated|you\s+requested|requesting)\s+"
    r"(?:your|the|that|this|a|an)\s+(?:\w+\s+)?refund\b",
    r"\brefund\s+(?:status|policy|window|request)\b",
    # policy and membership statements
    r"\bunlimited\s+refunds\b",
    r"\brefunds\s+(?:are|can\s+be|will\s+be)\s+(?:only\s+|just\s+|always\s+)*(?:available|issued)\b",
]
# The agent acting, or something happening now, anywhere in the sentence: then no
# clause of it is excused as a question, a status report or policy ("I've approved
# your refund request", "I'll refund you now, ok?", "Your refund is now complete",
# "Per our refund policy you're entitled to one, which I've issued")
REFUND_AGENT_ACTS = re.compile(
    r"\b(?:i|we)(?:'(?:ll|ve|m|d))?\s+"
    r"(?:(?:will|have|can|am|are|did|just|could|would|already|also|now|shall|gone\s+ahead\s+and"
    r"|go\s+ahead\s+and)\s+){0,3}"
    r"(?:refund|issu|approv|send|sent|start|process|request|put|giv|gave|offer|initiat|return"
    r"|submit|releas|push|complet|gone\s+ahead|go\s+ahead|do\b|did\b|done\b)\w*"
    r"|\b(?:approved|today|tonight|now|right\s+away|immediately|as\s+we\s+speak)\b"
)
# A question that takes the refund as settled, or proposes it, still grants it:
# "How would you like your refund, to your card or as store credit?"
REFUND_QUESTION_GRANTS = re.compile(
    r"\bhow\s+(?:would|do|did)\s+(?:you\s+)?(?:like|want|prefer)\b"
    r"|\bhow\s+about\b"
    r"|\b(?:shall|should|can|could|may)\s+(?:i|we)\s+(?:go\s+ahead|offer|issue|process|give|send|refund)\b"
    r"|\b(?:want|like)\s+(?:me|us)\s+to\s+(?:go\s+ahead|offer|issue|process|give|send|refund)\b"
    r"|\bwould\s+(?:you\s+accept|an?\s+(?:\w+\s+)?refund)\b"
    r"|\b(?:gift\s+card|paper\s+check|store\s+credit|refund\s+method|which\s+card|visa"
    r"|mastercard|original\s+payment)\b"
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
        r"\b(?:do|does|did|will|wo|ca|can)n?['\u2019]?t\s+last\s+forever\b"
        r"|\bnot\s+(?:last\s+)?forever\b"
        r"|\byou['\u2019]ll\s+never\s+miss\s+(?:a|an|another|any|out)\b",
        re.IGNORECASE,
    ),
    "SLA-001": re.compile(r"\bwill\s+definitely\s+be\s+able\s+to\b", re.IGNORECASE),
}

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
    return idioms.sub(lambda m: " " * len(m.group()), text)


def grants_refund(text: str, keywords: list[str]) -> bool:
    """True when some clause of ``text`` mentions a refund keyword and grants the refund.

    A clause does not grant one when it refuses. Otherwise it is excused only in
    a sentence where the agent does not act (``REFUND_AGENT_ACTS``), and only as
    a question to the customer that does not take the refund as settled, or as
    one of ``REFUND_NON_GRANT_PATTERNS``. Fails closed: any other mention grants.
    """
    chain: list[list[str]] = []
    normalised = _SPACES.sub(" ", text.lower().replace("\u2019", "'"))
    for match in _CLAUSE.finditer(normalised):
        chain.append(_CONJUNCTION.split(match.group(1)))
        ends = match.group(2)
        if ends and not ends.strip(",;"):
            continue  # a comma or semicolon: the sentence goes on
        asks = "?" in ends
        tagged = asks and len(chain) > 1 and " ".join(chain[-1]).strip() in _CONFIRMATION_TAGS
        parts = [
            (part, asks and (tagged or index == len(chain) - 1))
            for index, clause in enumerate(chain)
            for part in clause
            if not REFUND_REFUSAL.search(part)
        ]
        acts = any(REFUND_AGENT_ACTS.search(part) for part, _ in parts)
        for part, question in parts:
            if not mentions_keyword(part, keywords):
                continue
            if acts:
                return True
            if question:
                if REFUND_QUESTION_GRANTS.search(part):
                    return True
            elif not any(re.search(pat, part) for pat in REFUND_NON_GRANT_PATTERNS):
                return True
        chain = []
    return False


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

        # Step 1b': the refund-window rule needs a clause that grants the refund
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
                r"(?:dollar|credit(?![ \t]+cards?\b|-cards?\b)|compensation|adjustment)",
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
