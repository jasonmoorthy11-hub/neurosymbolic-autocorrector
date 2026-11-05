"""Neo4j Knowledge Graph Schema — node types, relationships, constraints, and seed data.

Schema Design:
    Policy ─HAS_RULE─> Rule ─HAS_CONDITION─> Condition
                         │
                         └─HAS_CORRECTION─> CorrectionTemplate

    Product ─HAS_COVERAGE─> Coverage

Nodes:
    Policy      — A policy document (Credit_Policy_2024_v3.2, etc.)
    Rule        — A specific compliance rule within a policy
    Condition   — A prerequisite that must be met for a rule to trigger
    CorrectionTemplate — A template for generating corrected text
    Product     — A product/plan (Standard_2024, Premium_2024)
    Coverage    — A coverage item within a product (emergency dental, etc.)

Relationships:
    (Policy)-[:HAS_RULE]->(Rule)
    (Rule)-[:HAS_CONDITION]->(Condition)
    (Rule)-[:HAS_CORRECTION]->(CorrectionTemplate)
    (Product)-[:HAS_COVERAGE]->(Coverage)
"""

from __future__ import annotations

import logging
from typing import Any

from autocorrector.knowledge_graph.store import CORRECTION_TEMPLATES, KnowledgeGraphStore

logger = logging.getLogger(__name__)


# --- Constraints & Indexes ---

CONSTRAINTS = [
    "CREATE CONSTRAINT rule_id IF NOT EXISTS FOR (r:Rule) REQUIRE r.rule_id IS UNIQUE",
    "CREATE CONSTRAINT policy_name IF NOT EXISTS FOR (p:Policy) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT product_name IF NOT EXISTS FOR (p:Product) REQUIRE p.name IS UNIQUE",
]

INDEXES = [
    "CREATE INDEX rule_intent IF NOT EXISTS FOR (r:Rule) ON (r.intent_type)",
    "CREATE INDEX rule_active IF NOT EXISTS FOR (r:Rule) ON (r.active)",
    "CREATE INDEX coverage_category IF NOT EXISTS FOR (c:Coverage) ON (c.category)",
]


# --- Seed Data ---

POLICIES: list[dict[str, Any]] = [
    {
        "name": "Credit_Policy_2024_v3.2",
        "version": "3.2",
        "effective_date": "2024-01-15",
        "category": "financial",
    },
    {
        "name": "Refund_Policy_2024_v2.1",
        "version": "2.1",
        "effective_date": "2024-03-01",
        "category": "financial",
    },
    {
        "name": "Escalation_Policy_2024_v1.0",
        "version": "1.0",
        "effective_date": "2024-01-01",
        "category": "escalation",
    },
    {
        "name": "Agent_Conduct_Policy_2024_v1.0",
        "version": "1.0",
        "effective_date": "2024-01-01",
        "category": "conduct",
    },
    {
        "name": "SLA_Policy_2024_v1.0",
        "version": "1.0",
        "effective_date": "2024-01-01",
        "category": "service",
    },
]


def _rules_from_defaults() -> list[dict[str, Any]]:
    """The in-memory rules in seed shape: one source of truth for both paths.

    ``intent_type`` is the in-memory domain (``_universal`` for rules that apply
    under every intent); the compliance query reads every active rule and only
    orders by it.
    """
    rules = []
    for domain, domain_rules in KnowledgeGraphStore._load_default_rules().items():
        for rule in domain_rules:
            rules.append(
                {
                    "rule_id": rule["rule_id"],
                    "name": rule["rule_name"],
                    "description": rule["description"],
                    "intent_type": domain,
                    "severity": rule["severity"],
                    "section": rule["source_section"],
                    "active": True,
                    "amount_threshold": rule.get("amount_threshold"),
                    "keywords": list(rule["keywords"]),
                    "policy": rule["source_document"],
                    "conditions": [dict(c) for c in rule["conditions"]],
                    "correction_template": CORRECTION_TEMPLATES.get(rule["rule_id"]),
                }
            )
    return rules


def _products_from_defaults() -> list[dict[str, Any]]:
    """The in-memory coverage facts in seed shape."""
    return [
        {
            "name": key,
            "display_name": plan["plan_name"],
            "coverages": [dict(cov) for cov in plan["coverages"]],
        }
        for key, plan in KnowledgeGraphStore._load_default_facts().items()
    ]


RULES: list[dict[str, Any]] = _rules_from_defaults()

PRODUCTS: list[dict[str, Any]] = _products_from_defaults()


# --- Schema Operations ---


async def create_constraints_and_indexes(driver) -> None:
    """Create uniqueness constraints and indexes."""
    async with driver.session() as session:
        for constraint in CONSTRAINTS:
            try:
                await session.run(constraint)
            except Exception as e:
                logger.debug(f"Constraint may already exist: {e}")

        for index in INDEXES:
            try:
                await session.run(index)
            except Exception as e:
                logger.debug(f"Index may already exist: {e}")

    logger.info(f"Created {len(CONSTRAINTS)} constraints and {len(INDEXES)} indexes")


async def seed_policies(driver) -> None:
    """Create Policy nodes."""
    async with driver.session() as session:
        for policy in POLICIES:
            await session.run(
                """
                MERGE (p:Policy {name: $name})
                SET p.version = $version,
                    p.effective_date = $effective_date,
                    p.category = $category
                """,
                **policy,
            )
    logger.info(f"Seeded {len(POLICIES)} policies")


async def seed_rules(driver) -> None:
    """Create Rule nodes with Conditions, CorrectionTemplates, and link to Policies."""
    async with driver.session() as session:
        for rule in RULES:
            # Create Rule node and link to Policy
            await session.run(
                """
                MATCH (p:Policy {name: $policy})
                MERGE (r:Rule {rule_id: $rule_id})
                SET r.name = $name,
                    r.description = $description,
                    r.intent_type = $intent_type,
                    r.severity = $severity,
                    r.section = $section,
                    r.active = $active,
                    r.amount_threshold = $amount_threshold,
                    r.keywords = $keywords
                MERGE (p)-[:HAS_RULE]->(r)
                """,
                rule_id=rule["rule_id"],
                name=rule["name"],
                description=rule["description"],
                intent_type=rule["intent_type"],
                severity=rule["severity"],
                section=rule["section"],
                active=rule["active"],
                amount_threshold=rule["amount_threshold"],
                keywords=rule["keywords"],
                policy=rule["policy"],
            )

            # Create Condition nodes
            for i, cond in enumerate(rule["conditions"]):
                await session.run(
                    """
                    MATCH (r:Rule {rule_id: $rule_id})
                    MERGE (c:Condition {rule_id: $rule_id, index: $index})
                    SET c.field = $field,
                        c.operator = $operator,
                        c.value = $value
                    MERGE (r)-[:HAS_CONDITION]->(c)
                    """,
                    rule_id=rule["rule_id"],
                    index=i,
                    field=cond["field"],
                    operator=cond["operator"],
                    value=cond["value"],
                )

            # Create CorrectionTemplate
            if rule.get("correction_template"):
                await session.run(
                    """
                    MATCH (r:Rule {rule_id: $rule_id})
                    MERGE (ct:CorrectionTemplate {rule_id: $rule_id})
                    SET ct.template = $template
                    MERGE (r)-[:HAS_CORRECTION]->(ct)
                    """,
                    rule_id=rule["rule_id"],
                    template=rule["correction_template"],
                )

    logger.info(f"Seeded {len(RULES)} rules with conditions and correction templates")


async def seed_products(driver) -> None:
    """Create Product and Coverage nodes."""
    async with driver.session() as session:
        for product in PRODUCTS:
            await session.run(
                """
                MERGE (p:Product {name: $name})
                SET p.display_name = $display_name
                """,
                name=product["name"],
                display_name=product["display_name"],
            )

            for cov in product["coverages"]:
                await session.run(
                    """
                    MATCH (p:Product {name: $product_name})
                    MERGE (c:Coverage {product: $product_name, category: $category})
                    SET c.limit = $limit,
                        c.source_document = $source_document,
                        c.source_page = $source_page
                    MERGE (p)-[:HAS_COVERAGE]->(c)
                    """,
                    product_name=product["name"],
                    category=cov["category"],
                    limit=cov["limit"],
                    source_document=cov["source_document"],
                    source_page=cov["source_page"],
                )

    logger.info(f"Seeded {len(PRODUCTS)} products with coverages")


async def seed_all(driver) -> None:
    """Run full schema setup: constraints, indexes, and all seed data."""
    await create_constraints_and_indexes(driver)
    await seed_policies(driver)
    await seed_rules(driver)
    await seed_products(driver)
    logger.info("Schema seeding complete")


async def create_vector_indexes(driver, dimensions: int = 1536) -> None:
    """Create vector indexes on Rule and Coverage nodes for semantic search."""
    async with driver.session() as session:
        for label, prop in [("Rule", "rule_embedding"), ("Coverage", "coverage_embedding")]:
            try:
                await session.run(
                    f"CREATE VECTOR INDEX {prop} IF NOT EXISTS "
                    f"FOR (n:{label}) ON (n.embedding) "
                    "OPTIONS {indexConfig: {"
                    "`vector.dimensions`: $dimensions, "
                    "`vector.similarity_function`: 'cosine'"
                    "}}",
                    dimensions=dimensions,
                )
            except Exception as e:
                logger.debug(f"Vector index {prop} may already exist: {e}")

    logger.info("Vector indexes created (or already exist)")


async def generate_embeddings(
    driver,
    openai_api_key: str,
    model: str = "text-embedding-3-small",
    dimensions: int = 1536,
) -> int:
    """Generate and store embeddings for Rule and Coverage nodes missing them."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=openai_api_key)
    embedded_count = 0

    async with driver.session() as session:
        # Embed Rules
        result = await session.run(
            "MATCH (r:Rule) WHERE r.embedding IS NULL "
            "RETURN r.rule_id AS id, r.name AS name, r.description AS desc"
        )
        rules = [record.data() async for record in result]

        for rule in rules:
            text = f"{rule['name']}: {rule['desc']}"
            resp = await client.embeddings.create(input=text, model=model, dimensions=dimensions)
            embedding = resp.data[0].embedding
            await session.run(
                "MATCH (r:Rule {rule_id: $id}) SET r.embedding = $embedding",
                id=rule["id"],
                embedding=embedding,
            )
            embedded_count += 1

        # Embed Coverages
        result = await session.run(
            "MATCH (prod:Product)-[:HAS_COVERAGE]->(c:Coverage) "
            "WHERE c.embedding IS NULL "
            "RETURN prod.display_name AS display_name, c.category AS category, "
            "c.limit AS limit, c.product AS product_name"
        )
        coverages = [record.data() async for record in result]

        for cov in coverages:
            text = f"{cov['display_name']} {cov['category']} coverage: ${cov['limit']}"
            resp = await client.embeddings.create(input=text, model=model, dimensions=dimensions)
            embedding = resp.data[0].embedding
            await session.run(
                "MATCH (c:Coverage {product: $product_name, category: $category}) "
                "SET c.embedding = $embedding",
                product_name=cov["product_name"],
                category=cov["category"],
                embedding=embedding,
            )
            embedded_count += 1

    logger.info(f"Generated embeddings for {embedded_count} nodes")
    return embedded_count


async def clear_all(driver) -> None:
    """Delete all nodes and relationships. Use with caution."""
    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
    logger.info("All data cleared")


async def get_stats(driver) -> dict[str, int]:
    """Get counts of all node types."""
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (n)
            RETURN labels(n)[0] AS label, count(n) AS count
            ORDER BY label
            """
        )
        stats = {record["label"]: record["count"] async for record in result}
    return stats
