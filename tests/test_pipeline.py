"""Tests for the LangGraph orchestration pipeline."""

import pytest

from autocorrector.audit.store import AuditStore
from autocorrector.models import CorrectionAction, IntentType, Severity
from autocorrector.orchestrator.pipeline import run_autocorrection


class TestPipelineAllow:
    """Test that compliant outputs are allowed through."""

    @pytest.mark.asyncio
    async def test_compliant_output_allowed(self):
        result = await run_autocorrection(
            text="I can help you with that question about your account.",
            customer_context={"customer_id": "C-1234", "account_tenure_days": 120},
        )
        assert result.action_taken == CorrectionAction.ALLOW
        assert result.was_modified is False
        assert result.corrected_text == result.original_text

    @pytest.mark.asyncio
    async def test_small_credit_allowed(self):
        result = await run_autocorrection(
            text="I can offer you a $25 credit for the inconvenience.",
            customer_context={
                "customer_id": "C-1234",
                "plan_type": "standard_2024",
                "account_tenure_days": 120,
            },
        )
        assert result.action_taken == CorrectionAction.ALLOW
        assert result.was_modified is False


class TestPipelineCorrect:
    """Test that non-compliant outputs are corrected."""

    @pytest.mark.asyncio
    async def test_unauthorized_credit_corrected(self):
        """Canonical scenario 1: Agent offers $75 credit to new customer."""
        result = await run_autocorrection(
            text="I'll go ahead and issue you a $75 credit right now.",
            customer_context={
                "customer_id": "C-5678",
                "plan_type": "standard_2024",
                "account_tenure_days": 30,
            },
        )
        assert result.action_taken == CorrectionAction.CORRECT
        assert result.was_modified is True
        assert "$50" in result.corrected_text
        assert (
            "escalate" in result.corrected_text.lower()
            or "supervisor" in result.corrected_text.lower()
        )

    @pytest.mark.asyncio
    async def test_factual_error_corrected(self):
        """Canonical scenario 2: Agent states wrong coverage amount."""
        result = await run_autocorrection(
            text="Your Standard plan covers up to $5,000 in emergency dental procedures.",
            customer_context={
                "customer_id": "C-9012",
                "plan_type": "standard_2024",
                "account_tenure_days": 365,
            },
        )
        assert result.action_taken == CorrectionAction.CORRECT
        assert result.was_modified is True
        assert "$2,500" in result.corrected_text

    async def test_pipeline_fact_fix_leaves_correct_duplicate_amount(self):
        """Only the wrong $5,000 (dental) is fixed; the correct Rx $5,000 stays."""
        result = await run_autocorrection(
            text="Your $5,000 prescription drug limit and $5,000 emergency dental limit stay.",
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 365},
        )
        assert result.corrected_text == (
            "Your $5,000 prescription drug limit and $2,500 emergency dental limit stay."
        )

    @pytest.mark.asyncio
    async def test_correction_preserves_intent(self):
        result = await run_autocorrection(
            text="I'll go ahead and issue you a $75 credit right now.",
            customer_context={
                "customer_id": "C-5678",
                "account_tenure_days": 30,
            },
        )
        assert result.intent.intent_type == IntentType.FINANCIAL_COMPENSATION


class TestPipelineEscalate:
    """Test that critical violations trigger escalation."""

    @pytest.mark.asyncio
    async def test_excessive_credit_escalated(self):
        """Credits over $500 must be escalated to supervisor."""
        result = await run_autocorrection(
            text="I'll issue you a $750 credit to make up for everything.",
            customer_context={
                "customer_id": "C-3456",
                "plan_type": "standard_2024",
                "account_tenure_days": 200,
            },
        )
        assert result.action_taken == CorrectionAction.ESCALATE
        assert result.validation.severity == Severity.CRITICAL

    @pytest.mark.asyncio
    async def test_escalation_does_not_deliver_text(self):
        result = await run_autocorrection(
            text="I'll issue you a $750 credit to make up for everything.",
            customer_context={
                "customer_id": "C-3456",
                "account_tenure_days": 200,
            },
        )
        assert result.action_taken == CorrectionAction.ESCALATE


class TestPipelineFailClosed:
    """A validation check that raises escalates (human in the loop), never allows."""

    _TEXT = "I'll go ahead and issue you a $75 credit right now."
    _CTX = {"customer_id": "C-5678", "plan_type": "standard_2024", "account_tenure_days": 30}

    async def _run_with_raising(self, monkeypatch, method: str):
        from autocorrector.knowledge_graph.store import KnowledgeGraphStore

        async def _boom(self, *args, **kwargs):
            raise RuntimeError("store down")

        monkeypatch.setattr(KnowledgeGraphStore, method, _boom)
        return await run_autocorrection(text=self._TEXT, customer_context=self._CTX)

    async def test_policy_check_exception_escalates(self, monkeypatch):
        result = await self._run_with_raising(monkeypatch, "check_policy_compliance")
        assert result.action_taken == CorrectionAction.ESCALATE
        assert result.validation.action == CorrectionAction.ESCALATE
        assert result.validation.is_compliant is False
        assert "validation-unavailable" in result.guardrails_applied
        assert result.edits == []
        assert result.corrected_text == self._TEXT

    async def test_fact_check_exception_escalates(self, monkeypatch):
        result = await self._run_with_raising(monkeypatch, "verify_facts")
        assert result.action_taken == CorrectionAction.ESCALATE
        assert "validation-unavailable" in result.guardrails_applied
        assert result.edits == []

    async def test_fact_check_exception_keeps_policy_violations(self, monkeypatch):
        """The check that did run still reports what it found."""
        result = await self._run_with_raising(monkeypatch, "verify_facts")
        assert len(result.validation.violations) > 0

    async def test_revalidation_exception_escalates(self, monkeypatch):
        """A raise on the second (post-correction) pass still escalates; the
        reported validation stays the first pass, by design."""
        from autocorrector.knowledge_graph.store import KnowledgeGraphStore

        original = KnowledgeGraphStore.check_policy_compliance
        calls = {"n": 0}

        async def _second_call_raises(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("store down")
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(KnowledgeGraphStore, "check_policy_compliance", _second_call_raises)
        result = await run_autocorrection(text=self._TEXT, customer_context=self._CTX)
        assert calls["n"] >= 2
        assert result.action_taken == CorrectionAction.ESCALATE
        assert result.edits == []
        assert "validation-unavailable" in result.guardrails_applied
        assert result.validation.action == CorrectionAction.CORRECT

    async def test_translation_and_validation_failures_both_tagged(self, monkeypatch):
        from autocorrector.knowledge_graph.store import KnowledgeGraphStore

        async def _boom(self, *args, **kwargs):
            raise RuntimeError("store down")

        monkeypatch.setattr(KnowledgeGraphStore, "verify_facts", _boom)
        result = await run_autocorrection(
            text="Je peux vous offrir un crédit de 200 $ sur votre compte aujourd'hui.",
            customer_context=self._CTX,
        )
        assert result.action_taken == CorrectionAction.ESCALATE
        assert "translation-unavailable" in result.guardrails_applied
        assert "validation-unavailable" in result.guardrails_applied

    async def test_healthy_checks_do_not_flag_validation(self):
        result = await run_autocorrection(text=self._TEXT, customer_context=self._CTX)
        assert "validation-unavailable" not in result.guardrails_applied
        assert result.action_taken == CorrectionAction.CORRECT


class TestPipelineMetadata:
    """Test pipeline metadata and tracking."""

    @pytest.mark.asyncio
    async def test_intent_classification(self):
        result = await run_autocorrection(
            text="I can process your refund right away. Let me initiate the refund for your purchase.",
            customer_context={"customer_id": "C-1234"},
        )
        assert result.intent.intent_type == IntentType.REFUND_PROCESSING
        assert result.intent.confidence >= 0.7

    @pytest.mark.asyncio
    async def test_validation_details_present(self):
        result = await run_autocorrection(
            text="I'll go ahead and issue you a $75 credit right now.",
            customer_context={"customer_id": "C-5678", "account_tenure_days": 30},
        )
        assert result.validation is not None
        assert len(result.validation.violations) > 0

    @pytest.mark.asyncio
    async def test_confidence_score(self):
        result = await run_autocorrection(
            text="Your Standard plan covers up to $5,000 in emergency dental procedures.",
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 365},
        )
        assert result.confidence < 1.0  # Modified = lower confidence


class TestPipelineACEStrategies:
    """Test ACE strategy injection into the correction pipeline."""

    @pytest.mark.asyncio
    async def test_audit_store_passed_to_pipeline(self, tmp_path):
        """Pipeline should accept and use an audit store for ACE strategies."""
        audit = AuditStore(db_path=str(tmp_path / "test_ace_pipeline.db"))
        await audit.connect()
        await audit.add_strategy(
            strategy_id="s1",
            lesson="When reducing credits, always acknowledge the inconvenience first.",
            intent_type="financial_compensation",
        )

        result = await run_autocorrection(
            text="I'll go ahead and issue you a $75 credit right now.",
            customer_context={"account_tenure_days": 30},
            audit_store=audit,
        )
        assert result.action_taken == CorrectionAction.CORRECT
        assert result.was_modified is True
        await audit.close()

    @pytest.mark.asyncio
    async def test_pipeline_works_without_audit_store(self):
        """Pipeline should still work when no audit store is provided (strategies=None)."""
        result = await run_autocorrection(
            text="I'll go ahead and issue you a $75 credit right now.",
            customer_context={"account_tenure_days": 30},
        )
        assert result.action_taken == CorrectionAction.CORRECT
        assert result.was_modified is True
