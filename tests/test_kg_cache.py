"""Tests for KG store TTL caching."""

import pytest

from autocorrector.knowledge_graph.store import KnowledgeGraphStore


@pytest.fixture
async def store():
    s = KnowledgeGraphStore()
    await s.connect()
    yield s
    await s.close()


class TestCacheCompliance:
    @pytest.mark.asyncio
    async def test_cache_hit(self, store):
        """Second call with same args should return cached result."""
        args = {
            "text": "I can offer you a $200 credit.",
            "intent_type": "financial_compensation",
            "customer_context": {"account_tenure_days": 45},
        }
        result1 = await store.check_policy_compliance(**args)
        result2 = await store.check_policy_compliance(**args)
        assert result1 == result2
        # Verify cache was populated
        assert len(store._rule_cache) == 1

    @pytest.mark.asyncio
    async def test_cache_miss_different_args(self, store):
        """Different args should produce different cache entries."""
        await store.check_policy_compliance(
            text="I can offer you a $200 credit.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 45},
        )
        await store.check_policy_compliance(
            text="I can offer you a $200 credit.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 120},
        )
        assert len(store._rule_cache) == 2


class TestCacheFacts:
    @pytest.mark.asyncio
    async def test_fact_cache_hit(self, store):
        """Fact verification should be cached."""
        args = {
            "text": "Your dental coverage is $5,000.",
            "customer_context": {"plan_type": "standard_2024"},
        }
        result1 = await store.verify_facts(**args)
        result2 = await store.verify_facts(**args)
        assert result1 == result2
        assert len(store._fact_cache) == 1


class TestCacheCorrection:
    @pytest.mark.asyncio
    async def test_correction_cache_hit(self, store):
        """Correction generation should be cached."""
        args = {
            "original_text": "I can offer you a $200 credit.",
            "violations": [{"rule_id": "CREDIT-001"}],
            "intent_type": "financial_compensation",
            "customer_context": {"account_tenure_days": 45},
        }
        result1 = await store.generate_compliant_alternative(**args)
        result2 = await store.generate_compliant_alternative(**args)
        assert result1 == result2
        assert len(store._correction_cache) == 1


class TestCacheClear:
    @pytest.mark.asyncio
    async def test_clear_cache(self, store):
        """clear_cache() should empty all caches."""
        await store.check_policy_compliance(
            text="$200 credit",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 45},
        )
        await store.verify_facts(
            text="$5,000 dental",
            customer_context={"plan_type": "standard_2024"},
        )
        assert len(store._rule_cache) > 0
        assert len(store._fact_cache) > 0

        store.clear_cache()
        assert len(store._rule_cache) == 0
        assert len(store._fact_cache) == 0
        assert len(store._correction_cache) == 0
