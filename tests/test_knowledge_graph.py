"""Tests for the knowledge graph store (in-memory mode)."""

import pytest


class TestPolicyCompliance:
    @pytest.mark.asyncio
    async def test_credit_over_threshold_new_account(self, connected_kg_store):
        """Canonical scenario 1: $200 credit with 45-day tenure triggers violation."""
        violations = await connected_kg_store.check_policy_compliance(
            text="I can offer you a $200 credit for the inconvenience.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 45},
        )
        assert len(violations) == 1
        assert violations[0].rule_id == "CREDIT-001"

    @pytest.mark.asyncio
    async def test_credit_under_threshold_ok(self, connected_kg_store):
        """Small credit under $50 should not trigger CREDIT-001."""
        violations = await connected_kg_store.check_policy_compliance(
            text="I can offer you a $25 credit for the inconvenience.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 45},
        )
        credit_001 = [v for v in violations if v.rule_id == "CREDIT-001"]
        assert len(credit_001) == 0

    @pytest.mark.asyncio
    async def test_credit_with_sufficient_tenure(self, connected_kg_store):
        """Credit over $50 but with 90+ day tenure should not trigger CREDIT-001."""
        violations = await connected_kg_store.check_policy_compliance(
            text="I can offer you a $200 credit for the inconvenience.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 120},
        )
        credit_001 = [v for v in violations if v.rule_id == "CREDIT-001"]
        assert len(credit_001) == 0

    @pytest.mark.asyncio
    async def test_excessive_credit_triggers_critical(self, connected_kg_store):
        """Credit over $500 triggers critical CREDIT-002 regardless of tenure."""
        violations = await connected_kg_store.check_policy_compliance(
            text="I can offer you a $750 credit for the inconvenience.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 200},
        )
        rule_ids = [v.rule_id for v in violations]
        assert "CREDIT-002" in rule_ids

    @pytest.mark.asyncio
    async def test_no_violations_for_unrelated_intent(self, connected_kg_store):
        """Text about troubleshooting should not trigger financial rules."""
        violations = await connected_kg_store.check_policy_compliance(
            text="Please try restarting your router.",
            intent_type="troubleshooting",
            customer_context={"account_tenure_days": 45},
        )
        assert len(violations) == 0


class TestFactVerification:
    @pytest.mark.asyncio
    async def test_dental_coverage_overstatement(self, connected_kg_store):
        """Canonical scenario 2: $5,000 dental claim on Standard plan (actual $2,500)."""
        errors = await connected_kg_store.verify_facts(
            text="Your plan covers up to $5,000 in emergency dental work.",
            customer_context={"plan_type": "standard_2024"},
        )
        inaccurate = [e for e in errors if not e.is_accurate]
        assert len(inaccurate) == 1
        assert "$2,500" in inaccurate[0].correct_value

    @pytest.mark.asyncio
    async def test_correct_coverage_passes(self, connected_kg_store):
        """Correct $2,500 dental claim on Standard plan should pass."""
        errors = await connected_kg_store.verify_facts(
            text="Your plan covers up to $2,500 in emergency dental work.",
            customer_context={"plan_type": "standard_2024"},
        )
        inaccurate = [e for e in errors if not e.is_accurate]
        assert len(inaccurate) == 0

    @pytest.mark.asyncio
    async def test_no_fact_check_without_plan(self, connected_kg_store):
        """Without plan context, fact checking should return empty."""
        errors = await connected_kg_store.verify_facts(
            text="Your plan covers up to $5,000 in emergency dental work.",
            customer_context={},
        )
        assert len(errors) == 0


class TestCorrectionGeneration:
    @pytest.mark.asyncio
    async def test_credit_correction_generated(self, connected_kg_store):
        """Correction for CREDIT-001 should suggest $50 and escalation."""
        corrected = await connected_kg_store.generate_compliant_alternative(
            original_text="I can offer you a $200 credit for the inconvenience.",
            violations=[
                {
                    "rule_id": "CREDIT-001",
                    "rule_name": "Credit Authorization Threshold",
                    "description": "Credits over $50 require 90+ days tenure",
                    "severity": "high",
                }
            ],
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 45},
        )
        assert "$50" in corrected
        assert "supervisor" in corrected.lower() or "escalate" in corrected.lower()

    @pytest.mark.asyncio
    async def test_compliant_text_unchanged(self, connected_kg_store):
        """Text with no matching violations should remain unchanged."""
        original = "Thank you for calling. How can I help you?"
        corrected = await connected_kg_store.generate_compliant_alternative(
            original_text=original,
            violations=[],
            intent_type="general",
            customer_context={},
        )
        assert corrected == original
