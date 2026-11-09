"""Tests for the experimental policy retriever."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autocorrector.knowledge_graph.retriever import (
    PolicyRetriever,
    RetrievalResponse,
    RetrievalResult,
    _extract_keywords,
    _keyword_score,
)
from autocorrector.knowledge_graph.store import KnowledgeGraphStore

# --- Keyword Extraction Tests ---


class TestKeywordExtraction:
    """Test keyword extraction and scoring utilities."""

    def test_extracts_meaningful_words(self):
        keywords = _extract_keywords("What is the credit limit for new accounts?")
        assert "credit" in keywords
        assert "limit" in keywords
        assert "accounts" in keywords

    def test_removes_stop_words(self):
        keywords = _extract_keywords("What is the refund policy for all returns?")
        assert "what" not in keywords
        assert "is" not in keywords
        assert "the" not in keywords
        assert "for" not in keywords
        assert "all" not in keywords

    def test_fallback_when_all_stop_words(self):
        keywords = _extract_keywords("what is the")
        assert len(keywords) > 0  # falls back to first words

    def test_lowercases_keywords(self):
        keywords = _extract_keywords("CREDIT REFUND POLICY")
        assert all(kw == kw.lower() for kw in keywords)

    def test_keyword_score_full_match(self):
        rule = {
            "rule_id": "CREDIT-001",
            "rule_name": "Credit Authorization",
            "description": "Credits over $50 require tenure",
        }
        score = _keyword_score(["credit", "authorization"], rule)
        assert score == 1.0

    def test_keyword_score_partial_match(self):
        rule = {
            "rule_id": "CREDIT-001",
            "rule_name": "Credit Authorization",
            "description": "Credits over $50",
        }
        score = _keyword_score(["credit", "refund"], rule)
        assert score == 0.5

    def test_keyword_score_no_match(self):
        rule = {
            "rule_id": "CREDIT-001",
            "rule_name": "Credit Authorization",
            "description": "Credits over $50",
        }
        score = _keyword_score(["dental", "coverage"], rule)
        assert score == 0.0


# --- Retrieval Result Tests ---


class TestRetrievalResult:
    """Test result dataclasses."""

    def test_retrieval_result_fields(self):
        result = RetrievalResult(
            content="[CREDIT-001] Credits over $50",
            source="Credit Policy Section 4.1",
            score=0.9,
            node_type="Rule",
            metadata={"id": "CREDIT-001"},
        )
        assert result.content == "[CREDIT-001] Credits over $50"
        assert result.node_type == "Rule"
        assert result.score == 0.9

    def test_retrieval_response_top_result(self):
        results = [
            RetrievalResult(content="first", score=0.9),
            RetrievalResult(content="second", score=0.5),
        ]
        response = RetrievalResponse(query="test", results=results, total=2)
        assert response.top_result.content == "first"

    def test_retrieval_response_empty(self):
        response = RetrievalResponse(query="test", results=[], total=0)
        assert response.top_result is None


# --- In-Memory Retriever Tests ---


class TestInMemoryRetriever:
    """Test the in-memory search backend (MVP mode)."""

    @pytest.fixture
    def retriever(self) -> PolicyRetriever:
        store = KnowledgeGraphStore()
        return PolicyRetriever(store)

    async def test_search_finds_credit_rules(self, retriever):
        response = await retriever.search("credit authorization threshold")
        assert response.results
        assert any("CREDIT" in r.content for r in response.results)

    async def test_search_finds_refund_rules(self, retriever):
        response = await retriever.search("refund policy window")
        assert response.results
        assert any("REFUND" in r.content for r in response.results)

    async def test_search_respects_top_k(self, retriever):
        response = await retriever.search("credit", top_k=1)
        assert len(response.results) <= 1

    async def test_search_filters_by_node_type(self, retriever):
        response = await retriever.search("credit", node_types=["Rule"])
        assert all(r.node_type == "Rule" for r in response.results)

    async def test_search_returns_scores(self, retriever):
        response = await retriever.search("credit authorization")
        for result in response.results:
            assert result.score > 0

    async def test_search_sorted_by_score(self, retriever):
        response = await retriever.search("credit authorization")
        scores = [r.score for r in response.results]
        assert scores == sorted(scores, reverse=True)

    async def test_search_no_results(self, retriever):
        response = await retriever.search("xyzzy frobnicator")
        assert response.total == 0
        assert response.top_result is None

    async def test_get_rule_by_id(self, retriever):
        result = await retriever.get_rule("CREDIT-001")
        assert result is not None
        assert "CREDIT-001" in result.content
        assert result.node_type == "Rule"
        assert result.score == 1.0

    async def test_get_rule_not_found(self, retriever):
        result = await retriever.get_rule("NONEXISTENT-999")
        assert result is None

    async def test_get_coverage(self, retriever):
        result = await retriever.get_coverage("standard_2024", "dental")
        assert result is not None
        assert "dental" in result.content.lower()
        assert result.node_type == "Coverage"

    async def test_get_coverage_not_found(self, retriever):
        result = await retriever.get_coverage("nonexistent_plan", "dental")
        assert result is None

    async def test_get_rules_for_intent(self, retriever):
        results = await retriever.get_rules_for_intent("financial_compensation")
        assert len(results) > 0
        assert all(r.node_type == "Rule" for r in results)

    async def test_get_rules_for_unknown_intent(self, retriever):
        results = await retriever.get_rules_for_intent("nonexistent_intent")
        assert len(results) == 0


# --- Vector Search Tests ---


class TestVectorSearch:
    """Test vector embedding search with mocked OpenAI and Neo4j."""

    def test_no_api_key_returns_no_client(self):
        store = KnowledgeGraphStore()
        retriever = PolicyRetriever(store)
        assert retriever._get_openai_client() is None

    def test_api_key_creates_client(self):
        store = KnowledgeGraphStore()
        retriever = PolicyRetriever(store, openai_api_key="sk-test-key")
        with patch("autocorrector.knowledge_graph.retriever.AsyncOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = retriever._get_openai_client()
            assert client is not None
            mock_cls.assert_called_once_with(api_key="sk-test-key")

    async def test_embed_query_calls_openai(self):
        store = KnowledgeGraphStore()
        retriever = PolicyRetriever(store, openai_api_key="sk-test-key")

        mock_embedding = [0.1] * 1536
        mock_resp = MagicMock()
        mock_resp.data = [MagicMock(embedding=mock_embedding)]

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_resp)
        retriever._openai_client = mock_client

        result = await retriever._embed_query("dental coverage")
        assert result == mock_embedding
        mock_client.embeddings.create.assert_called_once_with(
            input="dental coverage",
            model="text-embedding-3-small",
            dimensions=1536,
        )

    async def test_embed_query_returns_none_on_failure(self):
        store = KnowledgeGraphStore()
        retriever = PolicyRetriever(store, openai_api_key="sk-test-key")

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(side_effect=Exception("API error"))
        retriever._openai_client = mock_client

        result = await retriever._embed_query("dental coverage")
        assert result is None

    async def test_embed_query_returns_none_without_key(self):
        store = KnowledgeGraphStore()
        retriever = PolicyRetriever(store)
        result = await retriever._embed_query("dental coverage")
        assert result is None

    async def test_search_falls_back_to_keyword_on_embed_failure(self):
        store = KnowledgeGraphStore()
        retriever = PolicyRetriever(store, openai_api_key="sk-test-key")

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(side_effect=Exception("fail"))
        retriever._openai_client = mock_client

        # In-memory mode (not connected) — should still search via keywords
        response = await retriever.search("credit authorization")
        assert isinstance(response, RetrievalResponse)
        assert any("CREDIT" in r.content for r in response.results)

    async def test_no_api_key_uses_keyword_search(self):
        store = KnowledgeGraphStore()
        retriever = PolicyRetriever(store)

        response = await retriever.search("credit authorization")
        assert isinstance(response, RetrievalResponse)
        assert any("CREDIT" in r.content for r in response.results)

    async def test_vector_search_returns_scored_results(self):
        """Verify vector path returns results with cosine similarity scores."""
        store = KnowledgeGraphStore()
        store._connected = True
        retriever = PolicyRetriever(store, openai_api_key="sk-test-key")

        mock_embedding = [0.1] * 1536
        mock_resp = MagicMock()
        mock_resp.data = [MagicMock(embedding=mock_embedding)]

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_resp)
        retriever._openai_client = mock_client

        # Mock Neo4j driver session
        mock_record = MagicMock()
        mock_record.data.return_value = {
            "id": "CREDIT-001",
            "name": "Credit Authorization",
            "desc": "Credits over $50 require tenure",
            "severity": "high",
            "source": "Credit_Policy_2024_v3.2",
            "section": "Section 4.1",
            "score": 0.95,
        }

        mock_result = MagicMock()
        mock_result.__aiter__ = lambda self: self
        mock_result.__anext__ = AsyncMock(side_effect=[mock_record, StopAsyncIteration])

        # Empty result for coverage query
        mock_empty = MagicMock()
        mock_empty.__aiter__ = lambda self: self
        mock_empty.__anext__ = AsyncMock(side_effect=StopAsyncIteration)

        mock_session = AsyncMock()
        mock_session.run = AsyncMock(side_effect=[mock_result, mock_empty])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        store._driver = mock_driver

        response = await retriever.search("credit limit policy")
        assert len(response.results) == 1
        assert response.results[0].score == 0.95
        assert response.results[0].node_type == "Rule"
        assert "CREDIT-001" in response.results[0].content
