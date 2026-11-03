"""GraphRAG Retriever — semantic policy search using vector + Cypher hybrid queries.

Provides natural language policy lookup for the autocorrection pipeline.
Uses a vector similarity search over policy text combined with Cypher
graph traversal for structured relationship queries.

Uses OpenAI text-embedding-3-small + Neo4j native vector indexes for semantic
search, with keyword CONTAINS fallback when embeddings are unavailable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover
    AsyncOpenAI = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """A single result from a policy search."""

    content: str
    source: str = ""
    score: float = 0.0
    node_type: str = ""  # Rule, Coverage, Policy
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResponse:
    """Response from a retrieval query."""

    query: str
    results: list[RetrievalResult] = field(default_factory=list)
    total: int = 0

    @property
    def top_result(self) -> RetrievalResult | None:
        return self.results[0] if self.results else None


class PolicyRetriever:
    """Hybrid vector + keyword + graph retriever for policy knowledge.

    Supports two modes:
    1. **Neo4j mode** (production): Vector similarity search with keyword fallback
    2. **In-memory mode** (MVP): Searches the in-memory rule and fact stores

    Query types:
    - Rule lookup: "What is the credit limit for new accounts?"
    - Coverage lookup: "What is the dental coverage for Standard plan?"
    - Policy search: "Show me all refund rules"
    """

    def __init__(
        self,
        kg_store: Any,
        openai_api_key: str = "",
        embedding_model: str = "text-embedding-3-small",
        embedding_dimensions: int = 1536,
    ) -> None:
        self._kg_store = kg_store
        self._openai_api_key = openai_api_key
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions
        self._openai_client = None

    async def search(
        self,
        query: str,
        top_k: int = 5,
        node_types: list[str] | None = None,
    ) -> RetrievalResponse:
        """Search policy knowledge using the query.

        Args:
            query: Natural language query
            top_k: Maximum results to return
            node_types: Filter by node type (Rule, Coverage, Policy)
        """
        if self._kg_store.is_connected:
            results = await self._search_neo4j(query, top_k, node_types)
        else:
            results = self._search_inmemory(query, top_k, node_types)

        return RetrievalResponse(
            query=query,
            results=results[:top_k],
            total=len(results),
        )

    async def get_rule(self, rule_id: str) -> RetrievalResult | None:
        """Get a specific rule by ID."""
        if self._kg_store.is_connected:
            return await self._get_rule_neo4j(rule_id)
        return self._get_rule_inmemory(rule_id)

    async def get_coverage(self, plan_type: str, category: str) -> RetrievalResult | None:
        """Get coverage details for a plan and category."""
        if self._kg_store.is_connected:
            return await self._get_coverage_neo4j(plan_type, category)
        return self._get_coverage_inmemory(plan_type, category)

    async def get_rules_for_intent(self, intent_type: str) -> list[RetrievalResult]:
        """Get all active rules for an intent type."""
        if self._kg_store.is_connected:
            return await self._get_rules_for_intent_neo4j(intent_type)
        return self._get_rules_for_intent_inmemory(intent_type)

    # --- OpenAI Embedding ---

    def _get_openai_client(self):
        """Lazy init OpenAI client. Returns None if no API key or openai not installed."""
        if not self._openai_api_key or AsyncOpenAI is None:
            return None
        if self._openai_client is None:
            self._openai_client = AsyncOpenAI(api_key=self._openai_api_key)
        return self._openai_client

    async def _embed_query(self, text: str) -> list[float] | None:
        """Embed a search query. Returns None on any failure."""
        client = self._get_openai_client()
        if not client:
            return None
        try:
            resp = await client.embeddings.create(
                input=text,
                model=self._embedding_model,
                dimensions=self._embedding_dimensions,
            )
            return resp.data[0].embedding
        except Exception as e:
            logger.warning(f"Embedding failed, falling back to keyword search: {e}")
            return None

    # --- Neo4j Implementation ---

    async def _search_neo4j(
        self, query: str, top_k: int, node_types: list[str] | None
    ) -> list[RetrievalResult]:
        """Search Neo4j using vector similarity, with keyword fallback."""
        embedding = await self._embed_query(query)
        if embedding is not None:
            return await self._search_neo4j_vector(embedding, top_k, node_types)
        return await self._search_neo4j_keyword(query, top_k, node_types)

    async def _search_neo4j_vector(
        self, embedding: list[float], top_k: int, node_types: list[str] | None
    ) -> list[RetrievalResult]:
        """Vector similarity search using Neo4j vector indexes."""
        results = []
        driver = self._kg_store._driver

        async with driver.session() as session:
            if not node_types or "Rule" in node_types:
                result = await session.run(
                    "CALL db.index.vector.queryNodes('rule_embedding', $top_k, $embedding) "
                    "YIELD node AS r, score "
                    "MATCH (p:Policy)-[:HAS_RULE]->(r) "
                    "RETURN r.rule_id AS id, r.name AS name, r.description AS desc, "
                    "r.severity AS severity, p.name AS source, r.section AS section, score",
                    top_k=top_k,
                    embedding=embedding,
                )
                async for record in result:
                    data = record.data()
                    results.append(
                        RetrievalResult(
                            content=f"[{data['id']}] {data['desc']}",
                            source=f"{data['source']} {data.get('section', '')}",
                            score=data["score"],
                            node_type="Rule",
                            metadata=data,
                        )
                    )

            if not node_types or "Coverage" in node_types:
                result = await session.run(
                    "CALL db.index.vector.queryNodes('coverage_embedding', $top_k, $embedding) "
                    "YIELD node AS cov, score "
                    "MATCH (prod:Product)-[:HAS_COVERAGE]->(cov) "
                    "RETURN prod.name AS plan, cov.category AS category, "
                    "cov.limit AS coverage_limit, cov.source_document AS source_doc, "
                    "cov.source_page AS source_page, score",
                    top_k=top_k,
                    embedding=embedding,
                )
                async for record in result:
                    data = record.data()
                    results.append(
                        RetrievalResult(
                            content=(
                                f"{data['plan']} {data['category']}: ${data['coverage_limit']:,.0f}"
                            ),
                            source=(
                                f"{data.get('source_doc', '')} p.{data.get('source_page', '')}"
                            ),
                            score=data["score"],
                            node_type="Coverage",
                            metadata=data,
                        )
                    )

        return sorted(results, key=lambda r: r.score, reverse=True)

    async def _search_neo4j_keyword(
        self, query: str, top_k: int, node_types: list[str] | None
    ) -> list[RetrievalResult]:
        """Keyword fallback: full-text search using Cypher CONTAINS."""
        results = []
        keywords = _extract_keywords(query)
        driver = self._kg_store._driver

        async with driver.session() as session:
            if not node_types or "Rule" in node_types:
                for keyword in keywords:
                    result = await session.run(
                        """
                        MATCH (p:Policy)-[:HAS_RULE]->(r:Rule)
                        WHERE toLower(r.description) CONTAINS toLower($keyword)
                           OR toLower(r.name) CONTAINS toLower($keyword)
                           OR r.rule_id = toUpper($keyword)
                        RETURN r.rule_id AS id, r.name AS name, r.description AS desc,
                               r.severity AS severity, p.name AS source,
                               r.section AS section
                        LIMIT $limit
                        """,
                        keyword=keyword,
                        limit=top_k,
                    )
                    async for record in result:
                        data = record.data()
                        results.append(
                            RetrievalResult(
                                content=f"[{data['id']}] {data['desc']}",
                                source=f"{data['source']} {data.get('section', '')}",
                                score=1.0 if data["id"].upper() == keyword.upper() else 0.8,
                                node_type="Rule",
                                metadata=data,
                            )
                        )

            if not node_types or "Coverage" in node_types:
                for keyword in keywords:
                    result = await session.run(
                        """
                        MATCH (prod:Product)-[:HAS_COVERAGE]->(cov:Coverage)
                        WHERE toLower(cov.category) CONTAINS toLower($keyword)
                           OR toLower(prod.name) CONTAINS toLower($keyword)
                        RETURN prod.name AS plan, cov.category AS category,
                               cov.limit AS coverage_limit,
                               cov.source_document AS source_doc,
                               cov.source_page AS source_page
                        LIMIT $limit
                        """,
                        keyword=keyword,
                        limit=top_k,
                    )
                    async for record in result:
                        data = record.data()
                        results.append(
                            RetrievalResult(
                                content=(
                                    f"{data['plan']} {data['category']}"
                                    f": ${data['coverage_limit']:,.0f}"
                                ),
                                source=(
                                    f"{data.get('source_doc', '')} p.{data.get('source_page', '')}"
                                ),
                                score=0.9,
                                node_type="Coverage",
                                metadata=data,
                            )
                        )

        # Deduplicate by content
        seen = set()
        unique = []
        for r in results:
            if r.content not in seen:
                seen.add(r.content)
                unique.append(r)

        return sorted(unique, key=lambda r: r.score, reverse=True)

    async def _get_rule_neo4j(self, rule_id: str) -> RetrievalResult | None:
        """Get a rule by ID from Neo4j."""
        driver = self._kg_store._driver
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Policy)-[:HAS_RULE]->(r:Rule {rule_id: $rule_id})
                OPTIONAL MATCH (r)-[:HAS_CORRECTION]->(ct:CorrectionTemplate)
                RETURN r.rule_id AS id, r.name AS name, r.description AS desc,
                       r.severity AS severity, p.name AS source,
                       r.section AS section, ct.template AS correction
                """,
                rule_id=rule_id,
            )
            record = await result.single()
            if not record:
                return None
            data = record.data()
            return RetrievalResult(
                content=f"[{data['id']}] {data['desc']}",
                source=f"{data['source']} {data.get('section', '')}",
                score=1.0,
                node_type="Rule",
                metadata=data,
            )

    async def _get_coverage_neo4j(self, plan_type: str, category: str) -> RetrievalResult | None:
        """Get coverage details from Neo4j."""
        driver = self._kg_store._driver
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (prod:Product {name: $plan})-[:HAS_COVERAGE]->(cov:Coverage)
                WHERE toLower(cov.category) CONTAINS toLower($category)
                RETURN prod.name AS plan, cov.category AS category,
                       cov.limit AS coverage_limit,
                       cov.source_document AS source_doc,
                       cov.source_page AS source_page
                """,
                plan=plan_type,
                category=category,
            )
            record = await result.single()
            if not record:
                return None
            data = record.data()
            return RetrievalResult(
                content=f"{data['plan']} {data['category']}: ${data['coverage_limit']:,.0f}",
                source=f"{data.get('source_doc', '')} p.{data.get('source_page', '')}",
                score=1.0,
                node_type="Coverage",
                metadata=data,
            )

    async def _get_rules_for_intent_neo4j(self, intent_type: str) -> list[RetrievalResult]:
        """Get all rules for an intent type from Neo4j."""
        driver = self._kg_store._driver
        results = []
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Policy)-[:HAS_RULE]->(r:Rule)
                WHERE r.intent_type = $intent_type AND r.active = true
                RETURN r.rule_id AS id, r.name AS name, r.description AS desc,
                       r.severity AS severity, p.name AS source, r.section AS section
                ORDER BY r.severity DESC
                """,
                intent_type=intent_type,
            )
            async for record in result:
                data = record.data()
                results.append(
                    RetrievalResult(
                        content=f"[{data['id']}] {data['desc']}",
                        source=f"{data['source']} {data.get('section', '')}",
                        score=1.0,
                        node_type="Rule",
                        metadata=data,
                    )
                )
        return results

    # --- In-Memory Implementation ---

    def _search_inmemory(
        self, query: str, top_k: int, node_types: list[str] | None
    ) -> list[RetrievalResult]:
        """Search the in-memory stores."""
        results = []
        keywords = _extract_keywords(query.lower())

        if not node_types or "Rule" in node_types:
            for intent_type, rules in self._kg_store._policy_rules.items():
                for rule in rules:
                    score = _keyword_score(keywords, rule)
                    if score > 0:
                        results.append(
                            RetrievalResult(
                                content=f"[{rule['rule_id']}] {rule['description']}",
                                source=(
                                    f"{rule.get('source_document', '')} "
                                    f"{rule.get('source_section', '')}"
                                ),
                                score=score,
                                node_type="Rule",
                                metadata={
                                    "id": rule["rule_id"],
                                    "name": rule["rule_name"],
                                    "severity": rule["severity"],
                                    "intent_type": intent_type,
                                },
                            )
                        )

        if not node_types or "Coverage" in node_types:
            for plan_name, plan_data in self._kg_store._fact_store.items():
                for cov in plan_data.get("coverages", []):
                    if any(kw in cov["category"].lower() or kw in plan_name for kw in keywords):
                        results.append(
                            RetrievalResult(
                                content=(f"{plan_name} {cov['category']}: ${cov['limit']:,.0f}"),
                                source=(
                                    f"{cov.get('source_document', '')} "
                                    f"p.{cov.get('source_page', '')}"
                                ),
                                score=0.9,
                                node_type="Coverage",
                                metadata={
                                    "plan": plan_name,
                                    "category": cov["category"],
                                    "coverage_limit": cov["limit"],
                                },
                            )
                        )

        return sorted(results, key=lambda r: r.score, reverse=True)[:top_k]

    def _get_rule_inmemory(self, rule_id: str) -> RetrievalResult | None:
        """Get a rule by ID from in-memory store."""
        for _intent_type, rules in self._kg_store._policy_rules.items():
            for rule in rules:
                if rule["rule_id"] == rule_id:
                    return RetrievalResult(
                        content=f"[{rule['rule_id']}] {rule['description']}",
                        source=(
                            f"{rule.get('source_document', '')} {rule.get('source_section', '')}"
                        ),
                        score=1.0,
                        node_type="Rule",
                        metadata={
                            "id": rule["rule_id"],
                            "name": rule["rule_name"],
                            "severity": rule["severity"],
                        },
                    )
        return None

    def _get_coverage_inmemory(self, plan_type: str, category: str) -> RetrievalResult | None:
        """Get coverage from in-memory store."""
        plan_data = self._kg_store._fact_store.get(plan_type)
        if not plan_data:
            return None
        for cov in plan_data.get("coverages", []):
            if category.lower() in cov["category"].lower():
                return RetrievalResult(
                    content=f"{plan_type} {cov['category']}: ${cov['limit']:,.0f}",
                    source=f"{cov.get('source_document', '')} p.{cov.get('source_page', '')}",
                    score=1.0,
                    node_type="Coverage",
                    metadata={
                        "plan": plan_type,
                        "category": cov["category"],
                        "coverage_limit": cov["limit"],
                    },
                )
        return None

    def _get_rules_for_intent_inmemory(self, intent_type: str) -> list[RetrievalResult]:
        """Get rules for an intent type from in-memory store."""
        rules = self._kg_store._policy_rules.get(intent_type, [])
        return [
            RetrievalResult(
                content=f"[{r['rule_id']}] {r['description']}",
                source=f"{r.get('source_document', '')} {r.get('source_section', '')}",
                score=1.0,
                node_type="Rule",
                metadata={
                    "id": r["rule_id"],
                    "name": r["rule_name"],
                    "severity": r["severity"],
                },
            )
            for r in rules
        ]


def _extract_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from a query string."""
    # Remove common stop words
    stop_words = {
        "what",
        "is",
        "the",
        "a",
        "an",
        "for",
        "of",
        "in",
        "on",
        "to",
        "and",
        "or",
        "how",
        "does",
        "do",
        "can",
        "my",
        "me",
        "i",
        "show",
        "get",
        "find",
        "all",
        "are",
        "with",
        "that",
        "this",
    }
    words = re.findall(r"\b\w+\b", query.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 1]
    return keywords or words[:3]  # fallback to first words if all filtered


def _keyword_score(keywords: list[str], rule: dict[str, Any]) -> float:
    """Score how well keywords match a rule."""
    text = (
        f"{rule.get('rule_id', '')} {rule.get('rule_name', '')} {rule.get('description', '')}"
    ).lower()

    hits = sum(1 for kw in keywords if kw in text)
    if hits == 0:
        return 0.0
    return min(1.0, hits / len(keywords))
