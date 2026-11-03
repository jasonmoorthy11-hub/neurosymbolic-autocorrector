"""End-to-end tests for the canonical scenarios.

These tests verify the complete pipeline: intent classification -> policy check ->
fact verification -> correction generation, matching the canonical
credit-policy and coverage-fact scenarios.
"""

import pytest

from autocorrector.intent_classifier.classifier import IntentClassifier
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import IntentType, Severity


@pytest.fixture
async def pipeline():
    """Set up the complete pipeline for scenario testing."""
    kg = KnowledgeGraphStore()
    await kg.connect()
    classifier = IntentClassifier()
    yield kg, classifier
    await kg.close()


class TestScenario1CreditApproval:
    """Scenario 1: Credit Approval (Financial Exposure Control)

    Agent says: "I can offer you a $200 credit for the inconvenience."
    System detects: Intent = Financial compensation
    Rule check: Account tenure = 45 days, Policy requires 90+ days for credits > $50
    Expected: Auto-correct to $50 credit with escalation offer
    """

    @pytest.mark.asyncio
    async def test_full_scenario(self, pipeline):
        kg, classifier = pipeline
        agent_output = "I can offer you a $200 credit for the inconvenience."
        customer = {"account_tenure_days": 45, "customer_id": "CUST-001"}

        # Step 1: Intent classification
        intent = classifier.classify(agent_output)
        assert intent.intent_type == IntentType.FINANCIAL_COMPENSATION
        assert intent.confidence >= 0.8
        assert intent.extracted_entities.get("amount") == "200"

        # Step 2: Policy compliance
        violations = await kg.check_policy_compliance(
            text=agent_output,
            intent_type=intent.intent_type.value,
            customer_context=customer,
        )
        assert len(violations) >= 1
        assert violations[0].rule_id == "CREDIT-001"
        assert violations[0].severity == Severity.HIGH
        assert "Credit_Policy_2024_v3.2" in violations[0].source_document

        # Step 3: Generate correction
        corrected = await kg.generate_compliant_alternative(
            original_text=agent_output,
            violations=[v.model_dump() for v in violations],
            intent_type=intent.intent_type.value,
            customer_context=customer,
        )
        assert corrected != agent_output
        assert "$50" in corrected
        assert "supervisor" in corrected.lower() or "escalate" in corrected.lower()


class TestScenario2PolicyExplanation:
    """Scenario 2: Policy Explanation (Hallucination Prevention)

    Agent says: "Your plan covers up to $5,000 in emergency dental work."
    System detects: Intent = Coverage info request
    Fact check: Standard plan limit = $2,500 (not $5,000)
    Expected: Correct to $2,500 with upsell suggestion
    """

    @pytest.mark.asyncio
    async def test_full_scenario(self, pipeline):
        kg, classifier = pipeline
        agent_output = "Your plan covers up to $5,000 in emergency dental work."
        customer = {"plan_type": "standard_2024", "customer_id": "CUST-002"}

        # Step 1: Intent classification
        intent = classifier.classify(agent_output)
        assert intent.intent_type == IntentType.POLICY_EXPLANATION
        assert intent.confidence >= 0.7

        # Step 2: Fact verification
        fact_errors = await kg.verify_facts(
            text=agent_output,
            customer_context=customer,
        )
        inaccurate = [f for f in fact_errors if not f.is_accurate]
        assert len(inaccurate) >= 1
        assert any("2,500" in (f.correct_value or "") for f in inaccurate)
        assert any(
            "Policy_Document_Standard_2024.pdf" in (f.source_document or "") for f in inaccurate
        )


class TestScenario3CompliantOutput:
    """Verify that a compliant agent output passes through without modification."""

    @pytest.mark.asyncio
    async def test_compliant_output_passes(self, pipeline):
        kg, classifier = pipeline
        agent_output = "I understand your concern. Let me look into this for you right away."
        customer = {"account_tenure_days": 200, "plan_type": "premium_2024"}

        # Intent should be general
        intent = classifier.classify(agent_output)

        # No policy violations
        violations = await kg.check_policy_compliance(
            text=agent_output,
            intent_type=intent.intent_type.value,
            customer_context=customer,
        )
        assert len(violations) == 0

        # No fact errors
        fact_errors = await kg.verify_facts(text=agent_output, customer_context=customer)
        inaccurate = [f for f in fact_errors if not f.is_accurate]
        assert len(inaccurate) == 0


class TestScenario4CriticalViolation:
    """Verify that excessive credit amounts trigger critical severity / escalation."""

    @pytest.mark.asyncio
    async def test_excessive_credit_escalation(self, pipeline):
        kg, classifier = pipeline
        agent_output = "I'll apply a $1,000 credit to your account immediately."
        customer = {"account_tenure_days": 30}

        intent = classifier.classify(agent_output)
        assert intent.intent_type == IntentType.FINANCIAL_COMPENSATION

        violations = await kg.check_policy_compliance(
            text=agent_output,
            intent_type=intent.intent_type.value,
            customer_context=customer,
        )

        # Should trigger both CREDIT-001 (tenure) and CREDIT-002 (amount)
        rule_ids = [v.rule_id for v in violations]
        assert "CREDIT-002" in rule_ids

        # CREDIT-002 should be critical severity
        critical = [v for v in violations if v.rule_id == "CREDIT-002"]
        assert critical[0].severity == Severity.CRITICAL
