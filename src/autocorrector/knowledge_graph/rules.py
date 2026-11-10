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
