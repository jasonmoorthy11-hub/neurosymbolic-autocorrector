"""Tests for Guardrails AI validators and pipeline integration."""

import pytest

from autocorrector.guardrails.telemetry import build_guard, build_pii_guard
from autocorrector.guardrails.validators import (
    ComplianceCheckValidator,
    FactualAccuracyValidator,
)
from autocorrector.orchestrator.pipeline import run_autocorrection

# --- PII Detection Tests ---


class TestPIIDetection:
    """Test PII detection and redaction."""

    def test_ssn_detected_and_redacted(self):
        guard = build_pii_guard()
        result = guard.validate("Your SSN is 123-45-6789.")
        assert result.validation_passed is True  # fixed = passes
        assert "[SSN REDACTED]" in result.validated_output
        assert "123-45-6789" not in result.validated_output

    def test_credit_card_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Card number: 4532-1234-5678-9012")
        assert "[CARD REDACTED]" in result.validated_output
        assert "4532" not in result.validated_output

    def test_email_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Contact me at john.doe@example.com")
        assert "[EMAIL REDACTED]" in result.validated_output
        assert "john.doe@example.com" not in result.validated_output

    def test_phone_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Call us at (555) 123-4567")
        assert "[PHONE REDACTED]" in result.validated_output
        assert "555" not in result.validated_output

    def test_multiple_pii_types(self):
        guard = build_pii_guard()
        result = guard.validate("SSN: 123-45-6789, email: test@test.com, phone: 555-123-4567")
        assert "[SSN REDACTED]" in result.validated_output
        assert "[EMAIL REDACTED]" in result.validated_output
        assert "[PHONE REDACTED]" in result.validated_output

    def test_no_pii_passes(self):
        guard = build_pii_guard()
        result = guard.validate("I can help you with your account.")
        assert result.validation_passed is True
        assert result.validated_output == "I can help you with your account."

    def test_selective_pii_types(self):
        """Test that we can check only specific PII types."""
        guard = build_pii_guard(pii_types=["ssn"])
        result = guard.validate("SSN: 123-45-6789, email: test@test.com")
        assert "[SSN REDACTED]" in result.validated_output
        # Email should NOT be redacted since we only check SSN
        assert "test@test.com" in result.validated_output

    def test_email_regex_does_not_match_pipe(self):
        """Verify email regex [A-Za-z] does not match literal pipe character."""
        guard = build_pii_guard(pii_types=["email"])
        # Pipe character should not be treated as part of TLD
        result = guard.validate("Contact user@example.com for help.")
        assert "[EMAIL REDACTED]" in result.validated_output
        # Ensure a non-email with pipe is NOT matched
        result2 = guard.validate("Use cat|grep to filter output.")
        assert result2.validated_output == "Use cat|grep to filter output."

    def test_phone_with_parentheses(self):
        """Phone numbers starting with ( should be detected."""
        guard = build_pii_guard(pii_types=["phone"])
        result = guard.validate("Call (800) 555-1234 for support.")
        assert "[PHONE REDACTED]" in result.validated_output
        assert "(800)" not in result.validated_output

    def test_phone_with_plus1(self):
        """Phone numbers with +1 prefix should be detected."""
        guard = build_pii_guard(pii_types=["phone"])
        result = guard.validate("Reach us at +1-800-555-1234.")
        assert "[PHONE REDACTED]" in result.validated_output

    def test_dob_detected(self):
        """Date of birth patterns should be detected and redacted."""
        guard = build_pii_guard()
        result = guard.validate("DOB: 01/15/1990 is on file.")
        assert "[DOB REDACTED]" in result.validated_output

    def test_ssn_dot_format(self):
        guard = build_pii_guard()
        result = guard.validate("Your SSN is 412.55.7890 on file.")
        assert "[SSN REDACTED]" in result.validated_output
        assert "412.55.7890" not in result.validated_output

    def test_ssn_space_format(self):
        guard = build_pii_guard()
        result = guard.validate("SSN: 412 55 7890 confirmed.")
        assert "[SSN REDACTED]" in result.validated_output
        assert "412 55 7890" not in result.validated_output

    def test_passport_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Your passport number: AB1234567 is on file.")
        assert "[PASSPORT REDACTED]" in result.validated_output
        assert "AB1234567" not in result.validated_output

    def test_passport_with_hash_prefix(self):
        guard = build_pii_guard()
        result = guard.validate("Passport # C9876543 verified.")
        assert "[PASSPORT REDACTED]" in result.validated_output

    def test_bank_account_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Your account number: 12345678901 is on file.")
        assert "[BANK ACCOUNT REDACTED]" in result.validated_output
        assert "12345678901" not in result.validated_output

    def test_bank_account_acct_prefix(self):
        guard = build_pii_guard()
        result = guard.validate("Acct# 9876543210 has been updated.")
        assert "[BANK ACCOUNT REDACTED]" in result.validated_output

    def test_routing_number_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Your routing number: 021000021 is for Chase.")
        assert "[ROUTING NUMBER REDACTED]" in result.validated_output
        assert "021000021" not in result.validated_output

    def test_routing_aba_prefix(self):
        guard = build_pii_guard()
        result = guard.validate("ABA: 026009593 on your account.")
        assert "[ROUTING NUMBER REDACTED]" in result.validated_output

    def test_drivers_license_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Your driver's license number: D1234567 is verified.")
        assert "[DL REDACTED]" in result.validated_output
        assert "D1234567" not in result.validated_output

    def test_drivers_license_dl_prefix(self):
        guard = build_pii_guard()
        result = guard.validate("DL# S12345678 on file.")
        assert "[DL REDACTED]" in result.validated_output

    def test_address_detected(self):
        guard = build_pii_guard()
        result = guard.validate("We have your address as 123 Main St, Springfield, IL 62704.")
        assert "[ADDRESS REDACTED]" in result.validated_output
        assert "123 Main St" not in result.validated_output

    def test_address_boulevard(self):
        guard = build_pii_guard()
        result = guard.validate("Shipping to 456 Oak Boulevard.")
        assert "[ADDRESS REDACTED]" in result.validated_output

    def test_no_false_positive_on_normal_numbers(self):
        """Normal account references shouldn't trigger bank account detection."""
        guard = build_pii_guard()
        result = guard.validate("Your order #12345 has shipped. Reference case 67890.")
        assert result.validated_output == "Your order #12345 has shipped. Reference case 67890."


# --- Compliance Check Tests ---


class TestComplianceCheck:
    """Test policy compliance validation."""

    def test_credit_violation_detected(self, kg_store):
        guard = build_guard(ComplianceCheckValidator, on_fail="fix")
        result = guard.validate(
            "I can offer you a $200 credit for the inconvenience.",
            metadata={
                "kg_store": kg_store,
                "intent_type": "financial_compensation",
                "customer_context": {"account_tenure_days": 30},
            },
        )
        assert "$50" in result.validated_output
        assert (
            "supervisor" in result.validated_output.lower()
            or "escalate" in result.validated_output.lower()
        )

    def test_compliant_credit_passes(self, kg_store):
        guard = build_guard(ComplianceCheckValidator, on_fail="fix")
        result = guard.validate(
            "I can offer you a $25 credit for the inconvenience.",
            metadata={
                "kg_store": kg_store,
                "intent_type": "financial_compensation",
                "customer_context": {"account_tenure_days": 120},
            },
        )
        assert result.validated_output == "I can offer you a $25 credit for the inconvenience."

    def test_critical_credit_detected(self, kg_store):
        guard = build_guard(ComplianceCheckValidator, on_fail="fix")
        result = guard.validate(
            "I'll apply a $750 credit right now.",
            metadata={
                "kg_store": kg_store,
                "intent_type": "financial_compensation",
                "customer_context": {"account_tenure_days": 200},
            },
        )
        # Should be corrected to escalation text
        assert "supervisor" in result.validated_output.lower()


# --- Factual Accuracy Tests ---


class TestFactualAccuracy:
    """Test fact verification validation."""

    def test_wrong_coverage_corrected(self, kg_store):
        guard = build_guard(FactualAccuracyValidator, on_fail="fix")
        result = guard.validate(
            "Your plan covers up to $5,000 in emergency dental work.",
            metadata={
                "kg_store": kg_store,
                "customer_context": {"plan_type": "standard_2024"},
            },
        )
        assert "$2,500" in result.validated_output
        assert "$5,000" not in result.validated_output

    def test_correct_coverage_passes(self, kg_store):
        guard = build_guard(FactualAccuracyValidator, on_fail="fix")
        result = guard.validate(
            "Your plan covers up to $2,500 in emergency dental work.",
            metadata={
                "kg_store": kg_store,
                "customer_context": {"plan_type": "standard_2024"},
            },
        )
        assert result.validated_output == "Your plan covers up to $2,500 in emergency dental work."

    def test_no_plan_type_skips_check(self, kg_store):
        guard = build_guard(FactualAccuracyValidator, on_fail="fix")
        result = guard.validate(
            "Your plan covers up to $5,000 in emergency dental work.",
            metadata={
                "kg_store": kg_store,
                "customer_context": {},
            },
        )
        # No plan_type = no fact check = passes through
        assert result.validated_output == "Your plan covers up to $5,000 in emergency dental work."


# --- Pipeline Integration Tests ---


class TestPipelineGuardrails:
    """Test that guardrails are wired into the LangGraph pipeline."""

    @pytest.mark.asyncio
    async def test_pii_redacted_in_pipeline(self):
        """PII in agent output should be redacted before validation."""
        result = await run_autocorrection(
            text="Your SSN 123-45-6789 is on file. I can help you with your account.",
            customer_context={"customer_id": "C-1234", "account_tenure_days": 120},
        )
        assert result.pii_detected is True
        assert "pii-detection" in result.guardrails_applied
        assert "123-45-6789" not in result.corrected_text
        assert "[SSN REDACTED]" in result.corrected_text

    @pytest.mark.asyncio
    async def test_pii_plus_compliance(self):
        """PII redaction + policy correction should both apply."""
        result = await run_autocorrection(
            text="I'll give you a $200 credit. Your email is john@test.com.",
            customer_context={
                "customer_id": "C-1234",
                "account_tenure_days": 30,
            },
        )
        assert result.pii_detected is True
        assert "john@test.com" not in result.corrected_text

    @pytest.mark.asyncio
    async def test_no_pii_no_guardrails_flag(self):
        """Clean text should not trigger PII detection."""
        result = await run_autocorrection(
            text="I can help you with that question about your account.",
            customer_context={"customer_id": "C-1234", "account_tenure_days": 120},
        )
        assert result.pii_detected is False
        assert result.guardrails_applied == []

    @pytest.mark.asyncio
    async def test_credit_card_redacted_in_pipeline(self):
        result = await run_autocorrection(
            text="Your card 4532-1234-5678-9012 has been charged.",
            customer_context={"customer_id": "C-1234"},
        )
        assert result.pii_detected is True
        assert "[CARD REDACTED]" in result.corrected_text
