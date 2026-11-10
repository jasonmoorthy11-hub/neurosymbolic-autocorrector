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
# that mentions a refund keyword does not grant one when it is a question put to
# the customer, a report on a refund already under way, a statement of policy or a
# refusal. The real-turn audit found 238 of 531 REFUND-001 flags were of these kinds.
REFUND_RULE_ID = "REFUND-001"
REFUND_NON_GRANT_PATTERNS = [
    # status of a refund that already exists
    r"\brefund(?:\s+status)?\s+(?:is|was|has\s+been)\s+(?:still\s+|currently\s+|now\s+|already\s+)*"
    r"(?:in\s+progress|in\s+process|being\s+processed|processing|pending|complete|completed"
    r"|not\s+started|not\s+yet\s+started)\b",
    r"\b(?:status\s+of|waiting\s+(?:for|on)|you\s+initiated|you\s+requested|requesting)\s+"
    r"(?:your|the|that|this|a|an)\s+(?:\w+\s+)?refund\b",
    r"\brefund\s+(?:status|policy|window|request)\b",
    # policy and membership statements
    r"\bunlimited\s+refunds\b",
    r"\brefunds\s+(?:are|can\s+be|will\s+be)\s+(?:only\s+|just\s+|always\s+)*(?:available|issued)\b",
    # refusals the message-level patterns above do not name
    r"\b(?:cannot|can\s?not|can'?t|won'?t|unable\s+to|not\s+(?:be\s+)?able\s+to)\s+"
    r"(?:\w+\s+){0,2}?(?:refund|process|accept)\b",
]
# A question that takes the refund as settled, or offers one, still grants it:
# "How would you like your refund, to your card or as store credit?"
REFUND_QUESTION_GRANTS = re.compile(
    r"\bhow\s+(?:would|do|did)\s+(?:you\s+)?(?:like|want|prefer)\b"
    r"|\b(?:can|could|may|shall|should)\s+(?:i|we)\s+(?:offer|issue|process|give|send|refund)\b"
    r"|\bwould\s+you\s+(?:like\s+(?:me|us)\s+to\s+(?:offer|issue|process|give|send|refund)|accept)\b"
    r"|\b(?:gift\s+card|paper\s+check|store\s+credit|refund\s+method|which\s+card)\b",
    re.IGNORECASE,
)
_CLAUSE = re.compile(r"[^,;.!?\n]+[,;.!?]*")
# "..., correct?" turns the whole sentence into a question; a longer last clause
# ("..., is there anything else I can help with?") is a question of its own
_TAG_QUESTION_WORDS = 3

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


def grants_refund(text: str, keywords: list[str]) -> bool:
    """True when some clause of ``text`` mentions a refund keyword and grants the refund.

    A clause does not grant one when it is a question to the customer (unless the
    question takes the refund as settled or offers it), or matches one of
    ``REFUND_NON_GRANT_PATTERNS``. Fails closed: any other mention grants.
    """
    for start, end in sentence_spans(text):
        sentence = text[start:end].rstrip(" \t>*\"')")
        clauses = [m.group() for m in _CLAUSE.finditer(sentence)]
        if not clauses:
            continue
        asks = sentence.endswith("?")
        tag = asks and len(clauses) > 1 and len(clauses[-1].split()) <= _TAG_QUESTION_WORDS
        for index, clause in enumerate(clauses):
            if not mentions_keyword(clause, keywords):
                continue
            lower = clause.lower()
            question = asks and (tag or index == len(clauses) - 1)
            if question and not REFUND_QUESTION_GRANTS.search(lower):
                continue
            if any(re.search(pat, lower) for pat in REFUND_NON_GRANT_PATTERNS):
                continue
            return True
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
        if not mentions_keyword(text, rule.get("keywords", [])):
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
                r"(?:^|\s)(\d+(?:,\d{3})*(?:\.\d{2})?)\s+(?:dollar|credit|compensation|adjustment)",
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
