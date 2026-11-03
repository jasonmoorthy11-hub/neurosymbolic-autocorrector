"""Integration tests for Neo4j knowledge graph.

These tests require a running Neo4j instance. They are skipped automatically
when Neo4j is not available (no URI configured or connection fails).

To run: Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD env vars, then:
    pytest tests/test_neo4j_integration.py -v

The graph is seeded once per module (using a module-level flag).
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

load_dotenv()

# Skip entire module if no Neo4j URI configured
NEO4J_URI = os.getenv("AUTOCORRECTOR_NEO4J_URI") or os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("AUTOCORRECTOR_NEO4J_USER") or os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("AUTOCORRECTOR_NEO4J_PASSWORD") or os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("AUTOCORRECTOR_NEO4J_DATABASE") or os.getenv("NEO4J_DATABASE", "neo4j")

pytestmark = pytest.mark.skipif(
    not NEO4J_URI or not NEO4J_PASSWORD,
    reason="Neo4j not configured (set AUTOCORRECTOR_NEO4J_URI and AUTOCORRECTOR_NEO4J_PASSWORD)",
)

_seeded = False


async def _ensure_seeded(driver):
    """Seed the graph once per test run."""
    global _seeded
    if not _seeded:
        from autocorrector.knowledge_graph.schema import clear_all, seed_all

        await clear_all(driver)
        await seed_all(driver)
        _seeded = True


@pytest.fixture
async def neo4j_driver():
    """Create a Neo4j driver, ensure graph is seeded, and close after test."""
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD), database=NEO4J_DATABASE
    )

    try:
        await driver.verify_connectivity()
    except Exception as e:
        pytest.skip(f"Neo4j connection failed: {e}")

    await _ensure_seeded(driver)

    yield driver

    await driver.close()


@pytest.fixture
async def kg_store():
    """Create a KnowledgeGraphStore connected to the test Neo4j instance."""
    from autocorrector.knowledge_graph.store import KnowledgeGraphStore

    store = KnowledgeGraphStore(
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    await store.connect()
    assert store._connected, "KG store should be connected to Neo4j"

    yield store

    await store.close()


# --- Schema Tests ---


class TestSchema:
    """Test that schema was seeded correctly."""

    async def test_node_counts(self, neo4j_driver):
        from autocorrector.knowledge_graph.schema import get_stats

        stats = await get_stats(neo4j_driver)
        assert stats.get("Policy", 0) == 2  # Credit_Policy, Refund_Policy
        assert stats.get("Rule", 0) == 3  # CREDIT-001, CREDIT-002, REFUND-001
        assert stats.get("Condition", 0) == 1  # CREDIT-001 has 1 condition
        assert stats.get("CorrectionTemplate", 0) == 3  # One per rule
        assert stats.get("Product", 0) == 2  # standard_2024, premium_2024
        assert stats.get("Coverage", 0) == 5  # 3 standard + 2 premium

    async def test_policy_rule_relationships(self, neo4j_driver):
        async with neo4j_driver.session() as session:
            result = await session.run(
                "MATCH (p:Policy)-[:HAS_RULE]->(r:Rule) RETURN p.name AS policy, r.rule_id AS rule"
            )
            rels = [(r["policy"], r["rule"]) async for r in result]

        assert ("Credit_Policy_2024_v3.2", "CREDIT-001") in rels
        assert ("Credit_Policy_2024_v3.2", "CREDIT-002") in rels
        assert ("Refund_Policy_2024_v2.1", "REFUND-001") in rels

    async def test_product_coverage_relationships(self, neo4j_driver):
        async with neo4j_driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Product)-[:HAS_COVERAGE]->(c:Coverage)
                RETURN p.name AS product, c.category AS category, c.limit AS limit
                ORDER BY p.name, c.category
                """
            )
            coverages = [r.data() async for r in result]

        # Standard plan has 3 coverages
        standard = [c for c in coverages if c["product"] == "standard_2024"]
        assert len(standard) == 3
        dental = next(c for c in standard if c["category"] == "emergency dental")
        assert dental["limit"] == 2500

        # Premium plan has 2 coverages
        premium = [c for c in coverages if c["product"] == "premium_2024"]
        assert len(premium) == 2
        dental_p = next(c for c in premium if c["category"] == "emergency dental")
        assert dental_p["limit"] == 5000


# --- Compliance Tests (via KG Store) ---


class TestComplianceNeo4j:
    """Test policy compliance checking against Neo4j."""

    async def test_credit_over_threshold_new_account(self, kg_store):
        """CREDIT-001: $75 credit for a 30-day-old account should violate."""
        violations = await kg_store.check_policy_compliance(
            text="I can offer you a $75 credit for your trouble.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30},
        )
        rule_ids = [v.rule_id for v in violations]
        assert "CREDIT-001" in rule_ids

    async def test_credit_under_threshold_ok(self, kg_store):
        """$30 credit should not trigger CREDIT-001 (threshold is $50)."""
        violations = await kg_store.check_policy_compliance(
            text="I can offer you a $30 credit.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30},
        )
        assert len(violations) == 0

    async def test_credit_with_sufficient_tenure(self, kg_store):
        """$75 credit with 120-day tenure should not trigger CREDIT-001."""
        violations = await kg_store.check_policy_compliance(
            text="I can offer you a $75 credit for your trouble.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 120},
        )
        rule_ids = [v.rule_id for v in violations]
        assert "CREDIT-001" not in rule_ids

    async def test_excessive_credit_triggers_critical(self, kg_store):
        """$750 credit should trigger CREDIT-002 (critical)."""
        violations = await kg_store.check_policy_compliance(
            text="I'll apply a $750 credit to your account right away.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 120},
        )
        rule_ids = [v.rule_id for v in violations]
        assert "CREDIT-002" in rule_ids

    async def test_no_violations_for_unrelated_intent(self, kg_store):
        """Troubleshooting intent should not trigger credit rules."""
        violations = await kg_store.check_policy_compliance(
            text="I can offer you a $75 credit for your trouble.",
            intent_type="troubleshooting",
            customer_context={"account_tenure_days": 30},
        )
        assert len(violations) == 0


# --- Fact Verification Tests (via KG Store) ---


class TestFactsNeo4j:
    """Test fact verification against Neo4j."""

    async def test_dental_coverage_overstatement(self, kg_store):
        """Standard plan dental is $2,500, not $5,000."""
        results = await kg_store.verify_facts(
            text="Your emergency dental coverage is $5,000 under the Standard plan.",
            customer_context={"plan_type": "standard_2024"},
        )
        assert len(results) > 0
        assert results[0].is_accurate is False
        assert results[0].correct_value == "$2,500"

    async def test_correct_coverage_passes(self, kg_store):
        """Correct dental coverage amount should pass."""
        results = await kg_store.verify_facts(
            text="Your emergency dental coverage is $2,500 under the Standard plan.",
            customer_context={"plan_type": "standard_2024"},
        )
        assert len(results) == 0

    async def test_no_fact_check_without_plan(self, kg_store):
        """No plan type = no fact checks."""
        results = await kg_store.verify_facts(
            text="Your emergency dental coverage is $5,000.",
            customer_context={},
        )
        assert len(results) == 0


# --- Correction Template Tests (via KG Store) ---


class TestCorrectionNeo4j:
    """Test correction generation from Neo4j templates."""

    async def test_credit_correction_from_neo4j(self, kg_store):
        """Correction for CREDIT-001 should come from Neo4j template."""
        correction = await kg_store.generate_compliant_alternative(
            original_text="I can offer you a $200 credit right now.",
            violations=[{"rule_id": "CREDIT-001"}],
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30},
        )
        assert "$50" in correction
        assert "supervisor" in correction.lower() or "escalate" in correction.lower()

    async def test_critical_correction_from_neo4j(self, kg_store):
        """Correction for CREDIT-002 should reference supervisor."""
        correction = await kg_store.generate_compliant_alternative(
            original_text="I'll give you a $750 credit.",
            violations=[{"rule_id": "CREDIT-002"}],
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 120},
        )
        assert "supervisor" in correction.lower()

    async def test_idempotent_seeding(self, neo4j_driver):
        """Running seed_all twice should not duplicate data."""
        from autocorrector.knowledge_graph.schema import get_stats, seed_all

        await seed_all(neo4j_driver)  # Second seed
        stats = await get_stats(neo4j_driver)
        assert stats.get("Rule", 0) == 3  # Still 3, not 6
        assert stats.get("Policy", 0) == 2
