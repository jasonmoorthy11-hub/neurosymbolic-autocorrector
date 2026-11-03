"""Explainability engine — builds structured correction reports."""

from __future__ import annotations

import uuid

from autocorrector.models import (
    DetectedIntent,
    ExplainabilityReport,
    FactCheckResult,
    PolicyViolation,
)


class ExplainabilityEngine:
    """Builds structured reports explaining why corrections were made."""

    @staticmethod
    def build_report(
        original_text: str,
        corrected_text: str,
        intent: DetectedIntent,
        violations: list[PolicyViolation],
        fact_errors: list[FactCheckResult],
        confidence: float,
        correction_id: str | None = None,
    ) -> ExplainabilityReport:
        """Build a structured explainability report for a correction.

        Args:
            original_text: The original agent output
            corrected_text: The corrected version
            intent: Detected intent classification
            violations: Policy violations found
            fact_errors: Factual errors found
            confidence: Correction confidence score
            correction_id: Optional ID (generated if not provided)

        Returns:
            ExplainabilityReport with rules, rationale, sources, and status
        """
        cid = correction_id or str(uuid.uuid4())
        was_modified = original_text != corrected_text

        rules_applied = [v.rule_id for v in violations]
        rationale = ExplainabilityEngine._build_rationale(violations, fact_errors)
        source_documents = ExplainabilityEngine._extract_sources(violations, fact_errors)

        if not was_modified:
            compliance_status = "compliant"
        elif any(v.severity.value == "critical" for v in violations):
            compliance_status = "escalated"
        else:
            compliance_status = "corrected"

        return ExplainabilityReport(
            correction_id=cid,
            rules_applied=rules_applied,
            rationale=rationale,
            source_documents=source_documents,
            confidence=confidence,
            compliance_status=compliance_status,
        )

    @staticmethod
    def _build_rationale(
        violations: list[PolicyViolation], fact_errors: list[FactCheckResult]
    ) -> str:
        """Build a human-readable rationale string."""
        parts: list[str] = []
        for v in violations:
            parts.append(
                f"Policy violation: {v.description} "
                f"(rule: {v.rule_id}, severity: {v.severity.value})"
            )
        for f in fact_errors:
            if not f.is_accurate:
                parts.append(f"Factual error: '{f.claim}' should be '{f.correct_value}'")
        return "; ".join(parts) if parts else "No issues found"

    @staticmethod
    def _extract_sources(
        violations: list[PolicyViolation], fact_errors: list[FactCheckResult]
    ) -> list[str]:
        """Extract unique source document references from violations and fact errors."""
        sources: list[str] = []
        seen: set[str] = set()

        for v in violations:
            if v.source_document and v.source_document not in seen:
                seen.add(v.source_document)
                sources.append(v.source_document)

        for f in fact_errors:
            if not f.is_accurate and f.source_document and f.source_document not in seen:
                seen.add(f.source_document)
                sources.append(f.source_document)

        return sources
