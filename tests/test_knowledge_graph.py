"""Tests for the knowledge graph store (in-memory mode)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from autocorrector.knowledge_graph.store import KnowledgeGraphStore


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


def _neo4j_store(records: list[dict]) -> KnowledgeGraphStore:
    """A store that behaves as connected, with one Neo4j query returning ``records``."""
    mock_records = []
    for data in records:
        rec = MagicMock()
        rec.data.return_value = data
        mock_records.append(rec)

    mock_result = MagicMock()
    mock_result.__aiter__ = lambda self: self
    mock_result.__anext__ = AsyncMock(side_effect=[*mock_records, StopAsyncIteration])

    mock_session = AsyncMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    store = KnowledgeGraphStore()
    store._connected = True
    store._driver = MagicMock()
    store._driver.session.return_value = mock_session
    return store


_STANDARD_COVERAGES = [
    {
        "category": cov["category"],
        "coverage_limit": cov["limit"],
        "source_doc": cov["source_document"],
        "source_page": cov["source_page"],
    }
    for cov in KnowledgeGraphStore._load_default_facts()["standard_2024"]["coverages"]
]

_UNRELATED_AMOUNTS = "Your $40 specialist copay and $2,500 emergency dental limit are unchanged."
_DENTAL_OVERSTATED = "Your Standard plan covers up to $5,000 in emergency dental procedures."


class TestFactCheckAdjacency:
    async def test_fact_check_ignores_unrelated_amounts(self, connected_kg_store):
        """Each amount is checked only against the category it sits next to."""
        errors = await connected_kg_store.verify_facts(
            text=_UNRELATED_AMOUNTS, customer_context={"plan_type": "standard_2024"}
        )
        assert errors == []

    async def test_fact_check_still_flags_adjacent_overstatement(self, connected_kg_store):
        errors = await connected_kg_store.verify_facts(
            text=_DENTAL_OVERSTATED, customer_context={"plan_type": "standard_2024"}
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$5,000", "$2,500")]

    async def test_fact_check_ignores_amounts_in_other_sentences(self, connected_kg_store):
        errors = await connected_kg_store.verify_facts(
            text="Your emergency dental limit is unchanged. I can offer you a $200 credit.",
            customer_context={"plan_type": "standard_2024"},
        )
        assert errors == []

    async def test_fact_check_two_categories_one_sentence(self, connected_kg_store):
        errors = await connected_kg_store.verify_facts(
            text="Under your plan, emergency dental is $5,000 and emergency room is $10,000.",
            customer_context={"plan_type": "standard_2024"},
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$5,000", "$2,500")]

    async def test_fact_check_neo4j_ignores_unrelated_amounts(self):
        store = _neo4j_store(_STANDARD_COVERAGES)
        errors = await store.verify_facts(
            text=_UNRELATED_AMOUNTS, customer_context={"plan_type": "Standard 2024"}
        )
        assert errors == []

    async def test_fact_check_reports_claim_span(self, connected_kg_store):
        text = "Your $5,000 prescription drug limit and $5,000 emergency dental limit stay."
        errors = await connected_kg_store.verify_facts(
            text=text, customer_context={"plan_type": "standard_2024"}
        )
        (error,) = errors
        assert (error.claim, error.correct_value) == ("$5,000", "$2,500")
        start = text.rindex("$5,000")
        assert (error.claim_start, error.claim_end) == (start, start + len("$5,000"))

    async def test_fact_check_neo4j_uses_aliases(self):
        store = _neo4j_store(_STANDARD_COVERAGES)
        errors = await store.verify_facts(
            text="Your specialist copay is $60.", customer_context={"plan_type": "Standard 2024"}
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$60", "$40")]

    @pytest.mark.parametrize(
        "text",
        [
            "Emergency dental on Premium is $10,000.",
            "Emergency dental under your Premium is $10,000.",
        ],
    )
    async def test_fact_check_premium_plan_name_is_not_a_money_word(self, connected_kg_store, text):
        errors = await connected_kg_store.verify_facts(
            text=text, customer_context={"plan_type": "premium_2024"}
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$10,000", "$5,000")]

    async def test_fact_check_neo4j_flags_adjacent_overstatement(self):
        store = _neo4j_store(_STANDARD_COVERAGES)
        errors = await store.verify_facts(
            text=_DENTAL_OVERSTATED, customer_context={"plan_type": "Standard 2024"}
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$5,000", "$2,500")]


class TestMissingContextParity:
    async def test_missing_context_same_in_neo4j_and_memory(self):
        """A missing tenure field counts as 0 days in both paths (D-213)."""
        text = "I can offer you a $200 credit for the inconvenience."
        credit_rule = {
            "rule_id": "CREDIT-001",
            "name": "Credit Authorization Threshold",
            "description": "Credits over $50 require 90+ days tenure",
            "severity": "high",
            "source_document": "Credit_Policy_2024_v3.2",
            "source_section": "Section 4.1",
            "amount_threshold": 50,
            "keywords": ["credit"],
            "conditions": [{"field": "account_tenure_days", "operator": "lt", "value": 90}],
        }
        neo4j_violations = await _neo4j_store([credit_rule]).check_policy_compliance(
            text=text, intent_type="financial_compensation", customer_context={}
        )
        memory_violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="financial_compensation", customer_context={}
        )
        assert [v.rule_id for v in memory_violations] == ["CREDIT-001"]
        assert [v.rule_id for v in neo4j_violations] == ["CREDIT-001"]
