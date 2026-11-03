"""Tests for shared data models."""

import pytest
from pydantic import ValidationError

from autocorrector.models import (
    CorrectionAction,
    CorrectionEvent,
    CorrectionResult,
    CustomerContext,
    DetectedIntent,
    FactCheckResult,
    IntentType,
    PolicyViolation,
    Severity,
    ValidationResult,
)


class TestEnums:
    def test_severity_values(self):
        assert Severity.LOW.value == "low"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.HIGH.value == "high"
        assert Severity.CRITICAL.value == "critical"

    def test_severity_from_string(self):
        assert Severity("low") == Severity.LOW
        assert Severity("critical") == Severity.CRITICAL

    def test_correction_action_values(self):
        assert CorrectionAction.ALLOW.value == "allow"
        assert CorrectionAction.WARN.value == "warn"
        assert CorrectionAction.CORRECT.value == "correct"
        assert CorrectionAction.BLOCK.value == "block"
        assert CorrectionAction.ESCALATE.value == "escalate"

    def test_intent_type_values(self):
        assert len(IntentType) == 9
        assert IntentType.FINANCIAL_COMPENSATION.value == "financial_compensation"
        assert IntentType.GENERAL.value == "general"

    def test_invalid_severity_raises(self):
        with pytest.raises(ValueError):
            Severity("invalid")


class TestDetectedIntent:
    def test_valid_intent(self):
        intent = DetectedIntent(intent_type=IntentType.GENERAL, confidence=0.85)
        assert intent.intent_type == IntentType.GENERAL
        assert intent.confidence == 0.85
        assert intent.extracted_entities == {}

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            DetectedIntent(intent_type=IntentType.GENERAL, confidence=-0.1)
        with pytest.raises(ValidationError):
            DetectedIntent(intent_type=IntentType.GENERAL, confidence=1.1)

    def test_boundary_confidence(self):
        i0 = DetectedIntent(intent_type=IntentType.GENERAL, confidence=0.0)
        assert i0.confidence == 0.0
        i1 = DetectedIntent(intent_type=IntentType.GENERAL, confidence=1.0)
        assert i1.confidence == 1.0


class TestPolicyViolation:
    def test_roundtrip_serialization(self):
        v = PolicyViolation(
            rule_id="CREDIT-001",
            rule_name="Credit Auth",
            description="Credits over $50",
            severity=Severity.HIGH,
            source_document="Policy.pdf",
            source_section="4.1",
        )
        data = v.model_dump()
        v2 = PolicyViolation(**data)
        assert v2.rule_id == v.rule_id
        assert v2.severity == Severity.HIGH

    def test_optional_source_fields(self):
        v = PolicyViolation(
            rule_id="R1",
            rule_name="Rule",
            description="Desc",
            severity=Severity.LOW,
        )
        assert v.source_document is None
        assert v.source_section is None


class TestFactCheckResult:
    def test_accurate_fact(self):
        f = FactCheckResult(claim="$2,500", is_accurate=True, confidence=0.99)
        assert f.is_accurate
        assert f.correct_value is None

    def test_inaccurate_fact(self):
        f = FactCheckResult(
            claim="$5,000",
            is_accurate=False,
            correct_value="$2,500",
            source_document="Policy.pdf",
            source_page=12,
            confidence=0.99,
        )
        assert not f.is_accurate
        assert f.correct_value == "$2,500"

    def test_confidence_validation(self):
        with pytest.raises(ValidationError):
            FactCheckResult(claim="x", is_accurate=True, confidence=2.0)


class TestValidationResult:
    def test_compliant_result(self):
        v = ValidationResult(
            is_compliant=True,
            action=CorrectionAction.ALLOW,
            severity=Severity.LOW,
        )
        assert v.violations == []
        assert v.fact_errors == []

    def test_non_compliant_result(self):
        violation = PolicyViolation(
            rule_id="R1", rule_name="R", description="D", severity=Severity.HIGH
        )
        v = ValidationResult(
            is_compliant=False,
            action=CorrectionAction.CORRECT,
            violations=[violation],
            severity=Severity.HIGH,
        )
        assert len(v.violations) == 1


class TestCustomerContext:
    def test_empty_context(self):
        ctx = CustomerContext()
        assert ctx.customer_id is None
        assert ctx.plan_type is None
        assert ctx.interaction_history == []

    def test_full_context(self):
        ctx = CustomerContext(
            customer_id="C-123",
            plan_type="standard_2024",
            account_tenure_days=90,
            account_tier="gold",
        )
        assert ctx.account_tenure_days == 90

    def test_from_dict(self):
        ctx = CustomerContext(**{"customer_id": "C-1", "account_tenure_days": 30})
        assert ctx.customer_id == "C-1"


class TestCorrectionResult:
    def test_serialization_roundtrip(self):
        result = CorrectionResult(
            original_text="bad text",
            corrected_text="good text",
            was_modified=True,
            action_taken=CorrectionAction.CORRECT,
            confidence=0.92,
            intent=DetectedIntent(intent_type=IntentType.GENERAL, confidence=0.9),
            validation=ValidationResult(
                is_compliant=False,
                action=CorrectionAction.CORRECT,
                severity=Severity.HIGH,
            ),
        )
        data = result.model_dump(mode="json")
        assert data["action_taken"] == "correct"
        assert data["confidence"] == 0.92


class TestCorrectionEvent:
    def test_event_has_timestamp(self):
        event = CorrectionEvent(
            event_id="e1",
            original_text="text",
            corrected_text="text",
            was_modified=False,
            action_taken=CorrectionAction.ALLOW,
            intent=DetectedIntent(intent_type=IntentType.GENERAL, confidence=1.0),
            confidence=1.0,
        )
        assert event.timestamp is not None
        assert event.agent_id is None
