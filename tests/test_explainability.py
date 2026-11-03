"""Tests for the explainability engine."""

from autocorrector.explainability.engine import ExplainabilityEngine
from autocorrector.models import (
    DetectedIntent,
    FactCheckResult,
    IntentType,
    PolicyViolation,
    Severity,
)


def _intent(it: IntentType = IntentType.FINANCIAL_COMPENSATION) -> DetectedIntent:
    return DetectedIntent(intent_type=it, confidence=0.95)


def _violation(
    rule_id: str = "CREDIT-001",
    severity: Severity = Severity.HIGH,
    source: str | None = "Credit_Policy_2024_v3.2",
) -> PolicyViolation:
    return PolicyViolation(
        rule_id=rule_id,
        rule_name="Credit Auth",
        description="Credits over $50 require 90+ days tenure",
        severity=severity,
        source_document=source,
        source_section="Section 4.1",
    )


def _fact_error(
    claim: str = "$5,000",
    correct: str = "$2,500",
    source: str | None = "Policy_Document_Standard_2024.pdf",
) -> FactCheckResult:
    return FactCheckResult(
        claim=claim,
        is_accurate=False,
        correct_value=correct,
        source_document=source,
        source_page=12,
        confidence=0.99,
    )


class TestBuildReport:
    def test_report_with_violations(self):
        report = ExplainabilityEngine.build_report(
            original_text="I can offer you a $200 credit.",
            corrected_text="I can offer you a $50 credit.",
            intent=_intent(),
            violations=[_violation()],
            fact_errors=[],
            confidence=0.92,
        )
        assert report.compliance_status == "corrected"
        assert "CREDIT-001" in report.rules_applied
        assert "Credit_Policy_2024_v3.2" in report.source_documents
        assert "Policy violation" in report.rationale
        assert report.confidence == 0.92

    def test_report_with_fact_errors(self):
        report = ExplainabilityEngine.build_report(
            original_text="Your dental coverage is $5,000.",
            corrected_text="Your dental coverage is $2,500.",
            intent=_intent(IntentType.POLICY_EXPLANATION),
            violations=[],
            fact_errors=[_fact_error()],
            confidence=0.92,
        )
        assert report.compliance_status == "corrected"
        assert report.rules_applied == []
        assert "Policy_Document_Standard_2024.pdf" in report.source_documents
        assert "Factual error" in report.rationale

    def test_report_with_both(self):
        report = ExplainabilityEngine.build_report(
            original_text="I can offer $200 credit. Dental is $5,000.",
            corrected_text="I can offer $50 credit. Dental is $2,500.",
            intent=_intent(),
            violations=[_violation()],
            fact_errors=[_fact_error()],
            confidence=0.92,
        )
        assert report.compliance_status == "corrected"
        assert "CREDIT-001" in report.rules_applied
        assert len(report.source_documents) == 2
        assert "Policy violation" in report.rationale
        assert "Factual error" in report.rationale

    def test_report_compliant(self):
        report = ExplainabilityEngine.build_report(
            original_text="Thank you for calling.",
            corrected_text="Thank you for calling.",
            intent=_intent(IntentType.GENERAL),
            violations=[],
            fact_errors=[],
            confidence=1.0,
        )
        assert report.compliance_status == "compliant"
        assert report.rules_applied == []
        assert report.source_documents == []
        assert report.rationale == "No issues found"

    def test_report_escalated(self):
        report = ExplainabilityEngine.build_report(
            original_text="I'll give you a $1,000 credit.",
            corrected_text="Let me connect you with a supervisor.",
            intent=_intent(),
            violations=[_violation(rule_id="CREDIT-002", severity=Severity.CRITICAL)],
            fact_errors=[],
            confidence=0.92,
        )
        assert report.compliance_status == "escalated"
        assert "CREDIT-002" in report.rules_applied

    def test_source_deduplication(self):
        """Sources should not be duplicated even if multiple violations share them."""
        v1 = _violation(rule_id="R1", source="Doc_A")
        v2 = _violation(rule_id="R2", source="Doc_A")
        report = ExplainabilityEngine.build_report(
            original_text="original",
            corrected_text="corrected",
            intent=_intent(),
            violations=[v1, v2],
            fact_errors=[],
            confidence=0.92,
        )
        assert report.source_documents == ["Doc_A"]

    def test_custom_correction_id(self):
        report = ExplainabilityEngine.build_report(
            original_text="a",
            corrected_text="b",
            intent=_intent(),
            violations=[],
            fact_errors=[],
            confidence=0.92,
            correction_id="custom-id-123",
        )
        assert report.correction_id == "custom-id-123"
