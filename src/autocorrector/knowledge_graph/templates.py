"""Whole-message correction templates and the citation helpers shared by the tiers.

A whole-message correction (Neo4j template, LLM rewrite or the template tier)
replaces the entire reply, so its one edit cites every violation, highest
severity first, with sources joined by "; " (the span tier's convention).
"""

from __future__ import annotations

from typing import Any

from autocorrector.knowledge_graph.rewriter import RewriteResult
from autocorrector.models import CorrectionEdit, CorrectionTier

# Whole-message templates, one per correctable rule (the Neo4j seed mirrors these)
CORRECTION_TEMPLATES: dict[str, str] = {
    "CREDIT-001": (
        "I understand your frustration. Based on your account status, "
        "I can offer you a $50 credit today. For additional compensation, "
        "I'd be happy to escalate this to a supervisor who can review your case further."
    ),
    "CREDIT-002": (
        "I'd like to help resolve this for you. Let me connect you with a supervisor "
        "who has the authorization to review credits of this amount. They'll be able "
        "to assist you right away."
    ),
    "REFUND-001": (
        "I understand you'd like a refund. Let me check the details of your purchase "
        "and our current refund policy to find the best option for you."
    ),
    "PROMISE-001": (
        "I want to help resolve this for you. While I can't make guarantees about "
        "future policies, I can ensure your current concern is addressed and escalate "
        "for further review if needed."
    ),
    "SLA-001": (
        "I'll prioritize getting this resolved for you as quickly as possible. "
        "While I can't guarantee a specific timeline, I'll make sure your case "
        "receives prompt attention."
    ),
    "DISC-001": (
        "Let me look into that for you. I'll work on getting this resolved "
        "and follow up with the next steps."
    ),
}

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def by_severity(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Violations highest severity first; ties keep their order."""

    def rank(v: dict[str, Any]) -> int:
        severity = v.get("severity", "")
        return SEVERITY_RANK.get(str(getattr(severity, "value", severity)).lower(), 4)

    return sorted(violations, key=rank)


def cite_all(violations: list[dict[str, Any]]) -> dict[str, Any]:
    """One citation for a rewrite that addresses every violation (ids and sources joined)."""

    def joined(key: str) -> str | None:
        values = dict.fromkeys(v.get(key) for v in violations if v.get(key))
        return "; ".join(values) or None

    return {
        "rule_id": ", ".join(dict.fromkeys(v["rule_id"] for v in violations if v.get("rule_id"))),
        "source_document": joined("source_document"),
        "source_section": joined("source_section"),
    }


def whole_text_result(
    original_text: str, corrected: str, violation: dict[str, Any], tier: CorrectionTier
) -> RewriteResult:
    """A correction that replaces the whole message, as one edit citing ``violation``."""
    edit = CorrectionEdit(
        rule_id=violation.get("rule_id", ""),
        before=original_text,
        after=corrected,
        start=0,
        end=len(original_text),
        source_document=violation.get("source_document"),
        source_section=violation.get("source_section"),
        tier=tier,
    )
    return RewriteResult(text=corrected, edits=(edit,))
