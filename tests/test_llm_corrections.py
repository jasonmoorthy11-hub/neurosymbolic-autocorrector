"""Tests for LLM-powered corrections (NSA-40)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autocorrector.knowledge_graph.store import KnowledgeGraphStore

# --- Fixtures ---


@pytest.fixture
def store():
    """KG store with no API key (template fallback path)."""
    return KnowledgeGraphStore()


@pytest.fixture
def store_with_key():
    """KG store with an API key configured."""
    return KnowledgeGraphStore(anthropic_api_key="test-key-123")


@pytest.fixture
def store_custom_model():
    """KG store with custom model override."""
    return KnowledgeGraphStore(
        anthropic_api_key="test-key-123",
        correction_llm="claude-haiku-4-5-20251001",
    )


# --- Prompt Tests ---


class TestLLMCorrectionPrompts:
    def test_system_prompt_contains_key_instructions(self):
        prompt = KnowledgeGraphStore._build_correction_system_prompt()
        assert "compliance" in prompt.lower()
        assert "corrected text" in prompt.lower()
        assert "fabricat" in prompt.lower()

    def test_user_prompt_includes_violations(self):
        violations = [
            {
                "rule_id": "CREDIT-001",
                "severity": "high",
                "description": "Credits over $50 require 90+ days tenure",
                "source_document": "Credit_Policy_2024_v3.2",
            }
        ]
        prompt = KnowledgeGraphStore._build_correction_user_prompt(
            original_text="I can offer you a $200 credit.",
            violations=violations,
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30},
        )
        assert "CREDIT-001" in prompt
        assert "high" in prompt
        assert "$200 credit" in prompt
        assert "financial_compensation" in prompt
        assert "account_tenure_days" in prompt
        assert "Credit_Policy_2024_v3.2" in prompt

    def test_user_prompt_handles_empty_violations(self):
        prompt = KnowledgeGraphStore._build_correction_user_prompt(
            original_text="Hello, how can I help?",
            violations=[],
            intent_type="general",
            customer_context={},
        )
        assert "Hello, how can I help?" in prompt
        assert "POLICY VIOLATIONS" not in prompt

    def test_user_prompt_numbers_multiple_violations(self):
        violations = [
            {"rule_id": "CREDIT-001", "severity": "high", "description": "v1"},
            {"rule_id": "CREDIT-002", "severity": "critical", "description": "v2"},
        ]
        prompt = KnowledgeGraphStore._build_correction_user_prompt(
            original_text="text",
            violations=violations,
            intent_type="financial_compensation",
            customer_context={},
        )
        assert "1. [CREDIT-001]" in prompt
        assert "2. [CREDIT-002]" in prompt


# --- Fallback Chain Tests ---


class TestLLMCorrectionFallback:
    @pytest.mark.asyncio
    async def test_llm_called_when_neo4j_unavailable(self, store_with_key):
        """LLM tier should be attempted when Neo4j is not connected."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="I can offer you a $50 credit.")]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        store_with_key._anthropic_client = mock_client

        result = await store_with_key.generate_compliant_alternative(
            original_text="I can offer you a $200 credit.",
            violations=[{"rule_id": "CREDIT-001", "severity": "high", "description": "test"}],
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30},
        )
        assert result == "I can offer you a $50 credit."
        mock_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_template_on_api_error(self, store_with_key):
        """When LLM API fails, should fall back to template correction."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
        store_with_key._anthropic_client = mock_client

        result = await store_with_key.generate_compliant_alternative(
            original_text="I can offer you a $200 credit.",
            violations=[{"rule_id": "CREDIT-001", "severity": "high", "description": "test"}],
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30},
        )
        # Should fall through to template correction for CREDIT-001
        assert "$50" in result
        assert "supervisor" in result.lower() or "escalate" in result.lower()

    @pytest.mark.asyncio
    async def test_falls_back_when_no_api_key(self, store):
        """Without API key, LLM is skipped entirely and template is used."""
        result = await store.generate_compliant_alternative(
            original_text="I can offer you a $200 credit.",
            violations=[{"rule_id": "CREDIT-001", "severity": "high", "description": "test"}],
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30},
        )
        # Template correction for CREDIT-001
        assert "$50" in result

    @pytest.mark.asyncio
    async def test_cached_on_second_call(self, store_with_key):
        """Second call with same args should return cached result without calling LLM."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Corrected text from LLM")]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        store_with_key._anthropic_client = mock_client

        args = {
            "original_text": "Test text",
            "violations": [{"rule_id": "X-001", "severity": "high", "description": "test"}],
            "intent_type": "general",
            "customer_context": {},
        }

        result1 = await store_with_key.generate_compliant_alternative(**args)
        result2 = await store_with_key.generate_compliant_alternative(**args)

        assert result1 == result2 == "Corrected text from LLM"
        # Only called once — second call used cache
        assert mock_client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_llm_response_falls_back(self, store_with_key):
        """Empty LLM response should fall back to template."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="")]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        store_with_key._anthropic_client = mock_client

        result = await store_with_key.generate_compliant_alternative(
            original_text="I can offer you a $200 credit.",
            violations=[{"rule_id": "CREDIT-001", "severity": "high", "description": "test"}],
            intent_type="financial_compensation",
            customer_context={},
        )
        # Should fall to template
        assert "$50" in result


# --- Client Initialization Tests ---


class TestLLMClientInitialization:
    def test_no_client_without_key(self, store):
        """No API key means client should be None."""
        client = store._get_anthropic_client()
        assert client is None

    @patch("autocorrector.knowledge_graph.store.anthropic", create=True)
    def test_client_created_with_key(self, mock_anthropic_mod, store_with_key):
        """Client should be created when API key is provided."""

        mock_client_instance = MagicMock()
        mock_anthropic = MagicMock()
        mock_anthropic.AsyncAnthropic.return_value = mock_client_instance

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            store_with_key._anthropic_client = None  # reset
            client = store_with_key._get_anthropic_client()
            assert client is mock_client_instance
            mock_anthropic.AsyncAnthropic.assert_called_once_with(api_key="test-key-123")

    def test_client_reused(self, store_with_key):
        """Client should be reused after first initialization."""
        mock_client = MagicMock()
        store_with_key._anthropic_client = mock_client
        assert store_with_key._get_anthropic_client() is mock_client

    def test_model_default(self, store_with_key):
        """Default model should be set."""
        from autocorrector.knowledge_graph.store import DEFAULT_CORRECTION_LLM

        assert store_with_key._correction_llm == DEFAULT_CORRECTION_LLM

    def test_model_override(self, store_custom_model):
        """Custom model should override the default."""
        assert store_custom_model._correction_llm == "claude-haiku-4-5-20251001"
