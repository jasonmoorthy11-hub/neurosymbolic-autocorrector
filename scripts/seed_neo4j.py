#!/usr/bin/env python3
"""Seed the Neo4j knowledge graph with policy data.

Usage:
    python scripts/seed_neo4j.py              # Seed using .env config
    python scripts/seed_neo4j.py --clear      # Clear all data first, then seed
    python scripts/seed_neo4j.py --stats      # Show node counts only
    python scripts/seed_neo4j.py --verify     # Seed then run verification queries

Requires NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD env vars (or .env file).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Neo4j knowledge graph")
    parser.add_argument("--clear", action="store_true", help="Clear all data before seeding")
    parser.add_argument("--stats", action="store_true", help="Show node counts only")
    parser.add_argument(
        "--verify", action="store_true", help="Run verification queries after seeding"
    )
    parser.add_argument(
        "--embed", action="store_true", help="Generate vector embeddings (requires OPENAI_API_KEY)"
    )
    args = parser.parse_args()

    # Load config from env / .env
    from dotenv import load_dotenv

    load_dotenv()

    uri = os.getenv("AUTOCORRECTOR_NEO4J_URI") or os.getenv("NEO4J_URI")
    user = os.getenv("AUTOCORRECTOR_NEO4J_USER") or os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("AUTOCORRECTOR_NEO4J_PASSWORD") or os.getenv("NEO4J_PASSWORD")

    if not uri or not password:
        print("ERROR: Neo4j connection details not found.")
        print()
        print("Set these environment variables (or add to .env):")
        print("  AUTOCORRECTOR_NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io")
        print("  AUTOCORRECTOR_NEO4J_USER=neo4j")
        print("  AUTOCORRECTOR_NEO4J_PASSWORD=your-password")
        sys.exit(1)

    from neo4j import AsyncGraphDatabase

    print(f"Connecting to Neo4j at {uri}...")
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    try:
        await driver.verify_connectivity()
        print("Connected successfully!")
    except Exception as e:
        print(f"ERROR: Connection failed: {e}")
        sys.exit(1)

    from autocorrector.knowledge_graph.schema import (
        clear_all,
        create_vector_indexes,
        generate_embeddings,
        get_stats,
        seed_all,
    )

    try:
        if args.stats:
            stats = await get_stats(driver)
            print_stats(stats)
            return

        if args.clear:
            print("\nClearing all data...")
            await clear_all(driver)
            print("All data cleared.")

        print("\nSeeding knowledge graph...")
        await seed_all(driver)
        print("Seeding complete!")

        # Always show stats after seeding
        stats = await get_stats(driver)
        print_stats(stats)

        if args.embed:
            openai_key = os.getenv("AUTOCORRECTOR_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
            if not openai_key:
                print("\nERROR: --embed requires OPENAI_API_KEY or AUTOCORRECTOR_OPENAI_API_KEY")
                sys.exit(1)
            print("\nCreating vector indexes...")
            await create_vector_indexes(driver)
            print("Generating embeddings...")
            count = await generate_embeddings(driver, openai_key)
            print(f"Embedded {count} nodes.")

        if args.verify:
            await run_verification(driver)

    finally:
        await driver.close()
        print("\nConnection closed.")


def print_stats(stats: dict[str, int]) -> None:
    """Print node count summary."""
    print("\n--- Knowledge Graph Stats ---")
    total = 0
    for label, count in sorted(stats.items()):
        print(f"  {label}: {count}")
        total += count
    print(f"  TOTAL: {total}")


async def run_verification(driver) -> None:
    """Run verification queries to confirm graph structure."""
    print("\n--- Verification Queries ---")

    async with driver.session() as session:
        # 1. Check policy → rule relationships
        result = await session.run(
            """
            MATCH (p:Policy)-[:HAS_RULE]->(r:Rule)
            RETURN p.name AS policy, collect(r.rule_id) AS rules
            """
        )
        print("\n1. Policy → Rules:")
        async for record in result:
            print(f"   {record['policy']}: {record['rules']}")

        # 2. Check rules with conditions
        result = await session.run(
            """
            MATCH (r:Rule)-[:HAS_CONDITION]->(c:Condition)
            RETURN r.rule_id AS rule, c.field AS field, c.operator AS op, c.value AS value
            """
        )
        print("\n2. Rules with Conditions:")
        async for record in result:
            print(f"   {record['rule']}: {record['field']} {record['op']} {record['value']}")

        # 3. Check correction templates
        result = await session.run(
            """
            MATCH (r:Rule)-[:HAS_CORRECTION]->(ct:CorrectionTemplate)
            RETURN r.rule_id AS rule, left(ct.template, 60) AS template_preview
            """
        )
        print("\n3. Correction Templates:")
        async for record in result:
            print(f'   {record["rule"]}: "{record["template_preview"]}..."')

        # 4. Check products and coverages
        result = await session.run(
            """
            MATCH (p:Product)-[:HAS_COVERAGE]->(c:Coverage)
            RETURN p.display_name AS product, c.category AS category, c.limit AS limit
            ORDER BY p.name, c.category
            """
        )
        print("\n4. Product Coverages:")
        async for record in result:
            print(f"   {record['product']}: {record['category']} = ${record['limit']:,}")

        # 5. Test a sample compliance query (same as store.py uses)
        result = await session.run(
            """
            MATCH (p:Policy)-[:HAS_RULE]->(r:Rule)
            WHERE r.intent_type = 'financial_compensation' AND r.active = true
            OPTIONAL MATCH (r)-[:HAS_CONDITION]->(c:Condition)
            RETURN r.rule_id AS rule_id, r.name AS name,
                   r.amount_threshold AS threshold, r.keywords AS keywords,
                   collect(CASE WHEN c IS NOT NULL
                     THEN {field: c.field, operator: c.operator, value: c.value}
                   END) AS conditions
            """
        )
        print("\n5. Sample Compliance Query (financial_compensation rules):")
        async for record in result:
            print(
                f"   {record['rule_id']}: threshold=${record['threshold']}, "
                f"keywords={record['keywords']}, conditions={record['conditions']}"
            )

    print("\nAll verification queries passed!")


if __name__ == "__main__":
    asyncio.run(main())
