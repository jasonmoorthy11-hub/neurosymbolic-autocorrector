"""Tests for ACE (Autonomous Correction Evolution) self-improvement loop."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from autocorrector.ace.curator import curate_strategy, on_approval
from autocorrector.ace.reflector import (
    REFLECTOR_SYSTEM_PROMPT,
    build_reflector_prompt,
    extract_lesson,
)
from autocorrector.audit.store import AuditStore

# --- Reflector Tests ---


class TestReflectorPrompt:
    def test_build_prompt_basic(self):
        prompt = build_reflector_prompt(
            original_text="I can offer $200 credit",
            corrected_text="I can offer $50 credit",
            violations=[{"rule_id": "CREDIT-001", "description": "Over threshold"}],
            rejection_reason="tone_wrong",
            reviewer_comment="Too abrupt",
        )
        assert "I can offer $200 credit" in prompt
        assert "I can offer $50 credit" in prompt
        assert "CREDIT-001" in prompt
        assert "tone_wrong" in prompt
        assert "Too abrupt" in prompt

    def test_build_prompt_no_optional_fields(self):
        prompt = build_reflector_prompt(
            original_text="text",
            corrected_text="fixed text",
            violations=[],
            rejection_reason=None,
            reviewer_comment=None,
        )
        assert "text" in prompt
        assert "REJECTION REASON" not in prompt
        assert "REVIEWER COMMENT" not in prompt


class TestReflectorExtract:
    @pytest.mark.asyncio
    async def test_extract_lesson_success(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="When correcting credits, maintain empathetic tone.")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        lesson = await extract_lesson(
            anthropic_client=mock_client,
            model="claude-sonnet-5-5",
            original_text="I can offer $200",
            corrected_text="I can offer $50",
            violations=[{"rule_id": "CREDIT-001", "description": "Over threshold"}],
            rejection_reason="tone_wrong",
        )
        assert lesson == "When correcting credits, maintain empathetic tone."
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == REFLECTOR_SYSTEM_PROMPT
        assert call_kwargs["max_tokens"] == 4096
        assert call_kwargs["output_config"] == {"effort": "medium"}

    @pytest.mark.asyncio
    async def test_extract_lesson_no_client(self):
        lesson = await extract_lesson(
            anthropic_client=None,
            model="claude-sonnet-5-5",
            original_text="text",
            corrected_text="fixed",
            violations=[],
        )
        assert lesson is None

    @pytest.mark.asyncio
    async def test_extract_lesson_api_failure(self):
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API error"))

        lesson = await extract_lesson(
            anthropic_client=mock_client,
            model="claude-sonnet-5-5",
            original_text="text",
            corrected_text="fixed",
            violations=[],
        )
        assert lesson is None

    @pytest.mark.asyncio
    async def test_extract_lesson_empty_response(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        lesson = await extract_lesson(
            anthropic_client=mock_client,
            model="claude-sonnet-5-5",
            original_text="text",
            corrected_text="fixed",
            violations=[],
        )
        assert lesson is None


# --- Curator Tests ---


@pytest.fixture
async def audit_store(tmp_path):
    store = AuditStore(db_path=str(tmp_path / "test_ace.db"))
    await store.connect()
    yield store
    await store.close()


class TestCurator:
    @pytest.mark.asyncio
    async def test_curate_new_strategy(self, audit_store):
        result = await curate_strategy(
            audit_store=audit_store,
            lesson="Always maintain empathetic tone when reducing credits.",
            rule_id="CREDIT-001",
            intent_type="financial_compensation",
            source_event_id="evt-1",
        )
        assert result["action"] == "created"
        assert result["strategy_id"]

        strategies = await audit_store.get_all_strategies()
        assert len(strategies) == 1
        assert strategies[0]["lesson"] == "Always maintain empathetic tone when reducing credits."

    @pytest.mark.asyncio
    async def test_curate_merges_similar(self, audit_store):
        await curate_strategy(
            audit_store=audit_store,
            lesson="Maintain empathetic tone when reducing credit amounts.",
            rule_id="CREDIT-001",
        )
        result = await curate_strategy(
            audit_store=audit_store,
            lesson="Maintain empathetic tone when reducing credit amounts for customers.",
            rule_id="CREDIT-001",
        )
        assert result["action"] == "merged"

        strategies = await audit_store.get_all_strategies()
        assert len(strategies) == 1

    @pytest.mark.asyncio
    async def test_curate_different_lessons(self, audit_store):
        await curate_strategy(audit_store=audit_store, lesson="Be empathetic with credits.")
        await curate_strategy(audit_store=audit_store, lesson="Always verify plan coverage amounts.")

        strategies = await audit_store.get_all_strategies()
        assert len(strategies) == 2

    @pytest.mark.asyncio
    async def test_on_approval_bumps_helpful(self, audit_store):
        await audit_store.add_strategy(
            strategy_id="s1",
            lesson="Be empathetic",
            intent_type="financial_compensation",
        )
        await on_approval(audit_store, "evt-1", intent_type="financial_compensation")

        strategies = await audit_store.get_top_strategies(intent_type="financial_compensation")
        assert strategies[0]["helpful_count"] >= 1


# --- Audit Store Strategy Methods ---


class TestAuditStoreStrategies:
    @pytest.mark.asyncio
    async def test_add_and_get(self, audit_store):
        await audit_store.add_strategy(
            strategy_id="s1",
            lesson="Test lesson",
            rule_id="CREDIT-001",
            intent_type="financial_compensation",
        )
        strategies = await audit_store.get_top_strategies()
        assert len(strategies) == 1
        assert strategies[0]["lesson"] == "Test lesson"
        assert strategies[0]["score"] == 0

    @pytest.mark.asyncio
    async def test_increment_helpful(self, audit_store):
        await audit_store.add_strategy(strategy_id="s1", lesson="Test")
        await audit_store.increment_strategy_helpful("s1")
        await audit_store.increment_strategy_helpful("s1")

        strategies = await audit_store.get_top_strategies()
        assert strategies[0]["helpful_count"] == 2
        assert strategies[0]["score"] == 2

    @pytest.mark.asyncio
    async def test_increment_harmful(self, audit_store):
        await audit_store.add_strategy(strategy_id="s1", lesson="Test")
        await audit_store.increment_strategy_harmful("s1")

        strategies = await audit_store.get_top_strategies()
        assert strategies[0]["harmful_count"] == 1
        assert strategies[0]["score"] == -1

    @pytest.mark.asyncio
    async def test_top_strategies_ordered_by_score(self, audit_store):
        await audit_store.add_strategy(strategy_id="s1", lesson="Bad lesson")
        await audit_store.add_strategy(strategy_id="s2", lesson="Good lesson")
        await audit_store.increment_strategy_harmful("s1")
        await audit_store.increment_strategy_helpful("s2")
        await audit_store.increment_strategy_helpful("s2")

        strategies = await audit_store.get_top_strategies()
        assert strategies[0]["strategy_id"] == "s2"
        assert strategies[1]["strategy_id"] == "s1"

    @pytest.mark.asyncio
    async def test_top_strategies_filtered_by_intent(self, audit_store):
        await audit_store.add_strategy(strategy_id="s1", lesson="Credit tip", intent_type="financial_compensation")
        await audit_store.add_strategy(strategy_id="s2", lesson="General tip", intent_type=None)
        await audit_store.add_strategy(strategy_id="s3", lesson="Troubleshooting tip", intent_type="troubleshooting")

        strategies = await audit_store.get_top_strategies(intent_type="financial_compensation")
        ids = [s["strategy_id"] for s in strategies]
        assert "s1" in ids
        assert "s2" in ids  # NULL intent matches any
        assert "s3" not in ids

    @pytest.mark.asyncio
    async def test_find_similar_strategy(self, audit_store):
        await audit_store.add_strategy(
            strategy_id="s1",
            lesson="Always maintain empathetic tone when correcting credit offers.",
            rule_id="CREDIT-001",
        )
        result = await audit_store.find_similar_strategy(
            "Always maintain empathetic tone when correcting credit offers to customers.",
            rule_id="CREDIT-001",
        )
        assert result is not None
        assert result["strategy_id"] == "s1"

    @pytest.mark.asyncio
    async def test_find_similar_no_match(self, audit_store):
        await audit_store.add_strategy(strategy_id="s1", lesson="Credit tone advice")
        result = await audit_store.find_similar_strategy(
            "Verify all coverage amounts against the knowledge graph before responding."
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_all_strategies(self, audit_store):
        await audit_store.add_strategy(strategy_id="s1", lesson="Lesson 1")
        await audit_store.add_strategy(strategy_id="s2", lesson="Lesson 2")
        strategies = await audit_store.get_all_strategies()
        assert len(strategies) == 2
        assert "created_at" in strategies[0]


# --- Strategy Injection into Correction Prompt ---


class TestStrategyInjection:
    def test_system_prompt_without_strategies(self):
        from autocorrector.knowledge_graph.store import KnowledgeGraphStore

        prompt = KnowledgeGraphStore._build_correction_system_prompt()
        assert "LEARNED STRATEGIES" not in prompt
        assert "compliance correction engine" in prompt

    def test_system_prompt_with_strategies(self):
        from autocorrector.knowledge_graph.store import KnowledgeGraphStore

        strategies = [
            {"lesson": "Be empathetic when reducing credits."},
            {"lesson": "Always offer escalation path."},
        ]
        prompt = KnowledgeGraphStore._build_correction_system_prompt(strategies=strategies)
        assert "LEARNED STRATEGIES" in prompt
        assert "Be empathetic when reducing credits." in prompt
        assert "Always offer escalation path." in prompt

    def test_system_prompt_limits_to_10(self):
        from autocorrector.knowledge_graph.store import KnowledgeGraphStore

        strategies = [{"lesson": f"Lesson {i}"} for i in range(15)]
        prompt = KnowledgeGraphStore._build_correction_system_prompt(strategies=strategies)
        assert "Lesson 0" in prompt
        assert "Lesson 9" in prompt
        assert "Lesson 10" not in prompt


class TestReflectorIncompleteResponse:
    @pytest.mark.asyncio
    async def test_refusal_returns_none(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.stop_reason = "refusal"
        mock_response.content = [MagicMock(text="should be discarded")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        lesson = await extract_lesson(
            anthropic_client=mock_client,
            model="claude-sonnet-5-5",
            original_text="a",
            corrected_text="b",
            violations=[],
            rejection_reason="tone_wrong",
        )
        assert lesson is None

    @pytest.mark.asyncio
    async def test_reads_lesson_after_thinking_block(self, caplog):
        from anthropic.types import Message, TextBlock, ThinkingBlock, Usage

        msg = Message(
            id="msg_t",
            type="message",
            role="assistant",
            model="claude-sonnet-5-5",
            content=[
                ThinkingBlock(type="thinking", thinking="", signature="sig"),
                TextBlock(type="text", text="Keep the apology before the policy line."),
            ],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=1, output_tokens=1),
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=msg)

        lesson = await extract_lesson(
            anthropic_client=mock_client,
            model="claude-sonnet-5-5",
            original_text="a",
            corrected_text="b",
            violations=[],
            rejection_reason="tone_wrong",
        )
        assert lesson == "Keep the apology before the policy line."
        assert "failed" not in caplog.text.lower()
