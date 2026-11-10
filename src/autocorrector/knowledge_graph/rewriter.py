"""Span-level deterministic rewrites: change only the sentences a rule flags.

Each rule has a fixed, policy-grounded rewrite of the offending sentence
(CREDIT-001 caps the amount, REFUND/PROMISE/SLA replace the commitment,
DISC-001 drops the disclosure). Every change is returned as a
``CorrectionEdit`` that cites its rules and source sections, so a reviewer can
see exactly what changed and why. A rule with no editable span (an amount in
words, a critical rule that must escalate) is reported as unresolved and the
caller falls back to a whole-message template.

When several rules flag the same sentence, their fixes are composed into one
replacement (see ``_compose``) so no fix undoes another.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from autocorrector.knowledge_graph.claims import (
    assign_amounts,
    fact_replacements,
    sentence_spans,
)
from autocorrector.knowledge_graph.keywords import mentions_keyword
from autocorrector.knowledge_graph.rules import REFUND_RULE_ID, grants_refund
from autocorrector.models import CorrectionEdit, FactCheckResult

CREDIT_RULE_ID = "CREDIT-001"
# "an $80 credit" capped at $50 reads "a $50 credit"
_ARTICLE_AN = re.compile(r"\b(an) $", re.IGNORECASE)
CREDIT_FOLLOW_UP = " If you need more than that, I can ask a supervisor to review it."

# Replacement for the first offending sentence; later offending sentences are dropped.
# Wording avoids every trigger keyword of every rule so the rewrite revalidates clean.
SENTENCE_REPLACEMENTS = {
    "REFUND-001": (
        "This purchase is past our 30-day window, but let me check what options are available."
    ),
    "PROMISE-001": "I can't make a permanent commitment, but I'll make sure this is handled now.",
    "SLA-001": "I'll prioritize this and keep you updated on progress.",
}
DROPPED_RULES = frozenset({"DISC-001"})
FACT_RULE_ID = "FACT-CHECK"
# Most specific first: a sentence that is both a timeline guarantee and a promise
# gets the SLA wording; PROMISE-001's generic wording is the last resort.
_PLAN_ORDER = ("CREDIT-001", "REFUND-001", "SLA-001", "PROMISE-001", "DISC-001")
# Claim-assignment key for a rule's own keywords (never a coverage category name)
_OFFER_KEY = "\0offer"


@dataclass(frozen=True)
class RewriteResult:
    """Rewritten text, the edits that produced it, and rules left for the template tier."""

    text: str
    edits: tuple[CorrectionEdit, ...] = ()
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Fix:
    """One rule's fix for one sentence."""

    violation: Mapping[str, Any]
    replacement: str  # "" drops the sentence
    standalone: str | None = None  # credit only: the offer as its own sentence

    @property
    def rule_id(self) -> str:
        return self.violation.get("rule_id", "")


def rewrite(
    text: str,
    violations: Sequence[Mapping[str, Any]],
    rules: Mapping[str, Mapping[str, Any]],
    coverage: Mapping[str, Sequence[str]] | None = None,
) -> RewriteResult:
    """Rewrite only the sentences that violate a rule.

    ``violations`` are ``PolicyViolation`` dumps; ``rules`` maps rule_id to the
    rule definition (``keywords``, ``amount_threshold``); ``coverage`` maps each
    coverage category to its mentions, so coverage amounts are never taken for
    offers. The caller revalidates the result.
    """
    spans = sentence_spans(text)
    fixes: dict[int, list[_Fix]] = {}
    unresolved: list[str] = []

    for violation in sorted(violations, key=_priority):
        rule_id = violation.get("rule_id", "")
        plan = _plan(rule_id, rules.get(rule_id, {}), violation, text, spans, coverage or {})
        if plan is None:
            unresolved.append(rule_id)
            continue
        for index, fix in plan.items():
            fixes.setdefault(index, []).append(fix)

    if not fixes:
        return RewriteResult(text=text, unresolved=tuple(unresolved))

    rewritten = {index: _compose(entries) for index, entries in fixes.items()}
    if not any(rewritten.get(i, text[s:e]).strip() for i, (s, e) in enumerate(spans)):
        # Dropping every sentence would leave nothing to send
        dropped = {f.rule_id for entries in fixes.values() for f in entries} & DROPPED_RULES
        return RewriteResult(text=text, unresolved=(*unresolved, *sorted(dropped)))

    pieces: list[str] = []
    edits: list[CorrectionEdit] = []
    for index, (start, end) in enumerate(spans):
        sentence = text[start:end]
        content = sentence.rstrip()
        if index in rewritten:
            replacement = rewritten[index]
            edits.append(_edit(fixes[index], content, replacement, start))
            if not replacement:
                continue
            sentence = replacement + sentence[len(content) :]
        separator = text[spans[index - 1][1] : start] if pieces else ""
        pieces.append(separator + sentence)

    return RewriteResult(text="".join(pieces), edits=tuple(edits), unresolved=tuple(unresolved))


def _priority(violation: Mapping[str, Any]) -> int:
    rule_id = violation.get("rule_id", "")
    return _PLAN_ORDER.index(rule_id) if rule_id in _PLAN_ORDER else len(_PLAN_ORDER)


def _compose(entries: Sequence[_Fix]) -> str:
    """Combine every rule's fix for one sentence (entries are in ``_PLAN_ORDER``).

    The first whole-sentence wording wins (it is written to satisfy every rule);
    a drop gives way to a wording. A credit cap edits the sentence in place only
    when nothing else must change it; otherwise the capped offer is restated as
    its own sentence before the other fix, so neither fix undoes the other.
    """
    credit = next((f for f in entries if f.standalone is not None), None)
    others = [f for f in entries if f.standalone is None]
    if not others:
        return credit.replacement if credit else ""
    wording = next((f.replacement for f in others if f.replacement), "")
    if credit is None:
        return wording
    return f"{credit.standalone} {wording}" if wording else credit.standalone


def _edit(entries: Sequence[_Fix], before: str, after: str, start: int) -> CorrectionEdit:
    def joined(key: str) -> str | None:
        values = dict.fromkeys(f.violation.get(key) for f in entries if f.violation.get(key))
        return "; ".join(values) or None

    return CorrectionEdit(
        rule_id=", ".join(dict.fromkeys(f.rule_id for f in entries)),
        before=before,
        after=after,
        start=start,
        end=start + len(before),
        source_document=joined("source_document"),
        source_section=joined("source_section"),
        tier="span",
    )


def _plan(
    rule_id: str,
    rule: Mapping[str, Any],
    violation: Mapping[str, Any],
    text: str,
    spans: list[tuple[int, int]],
    coverage: Mapping[str, Sequence[str]],
) -> dict[int, _Fix] | None:
    """Map sentence index -> this rule's fix, or None if no span fits."""
    keywords = [kw.lower() for kw in rule.get("keywords", [])]
    if rule_id == CREDIT_RULE_ID:
        return _plan_credit(rule, violation, text, spans, keywords, coverage)

    targets = [
        i for i, (start, end) in enumerate(spans) if mentions_keyword(text[start:end], keywords)
    ]
    if rule_id == REFUND_RULE_ID:
        # Rewrite the sentences that grant the refund; a status report or a question
        # next to them stays ("Your earlier refund is still pending.")
        targets = [i for i in targets if grants_refund(text[slice(*spans[i])], keywords)]
    if not targets:
        return None
    if rule_id in SENTENCE_REPLACEMENTS:
        plan = {i: _Fix(violation, "") for i in targets}
        plan[targets[0]] = _Fix(violation, SENTENCE_REPLACEMENTS[rule_id])
        return plan
    if rule_id in DROPPED_RULES:
        return {i: _Fix(violation, "") for i in targets}
    return None


def _plan_credit(
    rule: Mapping[str, Any],
    violation: Mapping[str, Any],
    text: str,
    spans: list[tuple[int, int]],
    keywords: list[str],
    coverage: Mapping[str, Sequence[str]],
) -> dict[int, _Fix] | None:
    """Cap each over-threshold amount offered next to a credit keyword.

    Amounts are assigned jointly with the coverage categories, so "a $100
    credit and your emergency dental coverage is $5,000" caps only the $100.
    """
    threshold = float(rule.get("amount_threshold", 50))
    cap = f"${threshold:,.0f}"
    standalone = f"I can offer you a {cap} credit." + CREDIT_FOLLOW_UP
    offered = assign_amounts(text, {**coverage, _OFFER_KEY: keywords})[_OFFER_KEY]
    over = [m for m in offered if float(m.group(1).replace(",", "")) > threshold]
    if not over:
        return None

    plan: dict[int, _Fix] = {}
    for index, (start, end) in enumerate(spans):
        in_sentence = [m for m in over if start <= m.start() < end]
        if not in_sentence:
            continue
        if len(in_sentence) > 1:  # "$80 or a $150 credit": restate a single offer
            plan[index] = _Fix(violation, standalone, standalone)
            continue
        content = text[start:end].rstrip()
        (m,) = in_sentence
        before = _ARTICLE_AN.sub(lambda a: a.group(1)[0] + " ", content[: m.start() - start])
        capped = before + cap + content[m.end() - start :]
        if not capped.endswith((".", "!", "?")):
            capped += "."
        plan[index] = _Fix(violation, capped + CREDIT_FOLLOW_UP, standalone)
    return plan


def fact_edits(text: str, fact_errors: Sequence[FactCheckResult]) -> RewriteResult:
    """Replace each wrong coverage amount, recording one edit per replacement."""
    replacements = fact_replacements(text, fact_errors)
    corrected = text
    for start, end, error in reversed(replacements):
        corrected = corrected[:start] + (error.correct_value or "") + corrected[end:]
    edits = [
        CorrectionEdit(
            rule_id=FACT_RULE_ID,
            before=text[start:end],
            after=error.correct_value or "",
            start=start,
            end=end,
            source_document=error.source_document,
            source_section=f"p. {error.source_page}" if error.source_page else None,
            tier="span",
        )
        for start, end, error in replacements
    ]
    return RewriteResult(text=corrected, edits=tuple(edits))
