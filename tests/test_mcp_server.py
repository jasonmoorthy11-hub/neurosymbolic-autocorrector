"""Tests for the MCP server tools, resources, and helpers."""

import json
from unittest.mock import MagicMock

import pytest

from autocorrector.explainability.engine import ExplainabilityEngine
from autocorrector.mcp_server.server import (
    get_active_policies,
    get_compliance_rules,
    get_correction_metrics,
)
from autocorrector.models import (
    FactCheckResult,
    PolicyViolation,
    Severity,
)

# --- Resource Tests ---


class TestComplianceRulesResource:
    @pytest.mark.asyncio
    async def test_financial_compensation_domain(self):
        result = await get_compliance_rules("financial_compensation")
        data = json.loads(result)
        assert data["domain"] == "financial_compensation"
        assert len(data["rules"]) == 2
        rule_ids = [r["id"] for r in data["rules"]]
        assert "CREDIT-001" in rule_ids
        assert "CREDIT-002" in rule_ids

    @pytest.mark.asyncio
    async def test_policy_explanation_domain(self):
        result = await get_compliance_rules("policy_explanation")
        data = json.loads(result)
        assert data["domain"] == "policy_explanation"
        assert len(data["rules"]) == 2

    @pytest.mark.asyncio
    async def test_unknown_domain_returns_empty(self):
        result = await get_compliance_rules("nonexistent_domain")
        data = json.loads(result)
        assert data["domain"] == "nonexistent_domain"
        assert data["rules"] == []

    @pytest.mark.asyncio
    async def test_rules_from_kg_store(self, connected_kg_store):
        """When KG store is available, rules should come from it."""

        mock_ctx = MagicMock()
        mock_ctx.request_context.lifespan_context = {"kg": connected_kg_store}
        result = await get_compliance_rules("financial_compensation", ctx=mock_ctx)
        data = json.loads(result)
        assert data["domain"] == "financial_compensation"
        assert len(data["rules"]) >= 2
        rule_ids = [r["id"] for r in data["rules"]]
        assert "CREDIT-001" in rule_ids


class TestCorrectionMetricsResource:
    @pytest.mark.asyncio
    async def test_returns_zero_counters(self):
        result = await get_correction_metrics()
        data = json.loads(result)
        assert data["total_validations"] == 0
        assert data["total_corrections"] == 0
        assert data["average_confidence"] == 0.0
        assert data["average_latency_ms"] == 0.0


class TestActivePoliciesResource:
    def test_returns_structure(self):
        result = get_active_policies()
        data = json.loads(result)
        assert "policies" in data
        assert "last_updated" in data
        assert data["total_rules"] == 0


# --- Helper Tests ---


class TestBuildRationale:
    def test_empty_inputs(self):
        rationale = ExplainabilityEngine._build_rationale([], [])
        assert rationale == "No issues found"

    def test_with_violations(self):
        violations = [
            PolicyViolation(
                rule_id="CREDIT-001",
                rule_name="Credit Auth",
                description="Credits over $50",
                severity=Severity.HIGH,
            )
        ]
        rationale = ExplainabilityEngine._build_rationale(violations, [])
        assert "CREDIT-001" in rationale
        assert "Credits over $50" in rationale

    def test_with_fact_errors(self):
        facts = [
            FactCheckResult(
                claim="$5,000",
                is_accurate=False,
                correct_value="$2,500",
                confidence=0.99,
            )
        ]
        rationale = ExplainabilityEngine._build_rationale([], facts)
        assert "$5,000" in rationale
        assert "$2,500" in rationale

    def test_accurate_facts_ignored(self):
        facts = [FactCheckResult(claim="$2,500", is_accurate=True, confidence=0.99)]
        rationale = ExplainabilityEngine._build_rationale([], facts)
        assert rationale == "No issues found"

    def test_combined_violations_and_facts(self):
        violations = [
            PolicyViolation(
                rule_id="R1", rule_name="Rule 1", description="Desc 1", severity=Severity.LOW
            )
        ]
        facts = [FactCheckResult(claim="X", is_accurate=False, correct_value="Y", confidence=0.9)]
        rationale = ExplainabilityEngine._build_rationale(violations, facts)
        assert "R1" in rationale
        assert "X" in rationale
        assert "Y" in rationale


class TestRunValidationLanguageGuard:
    async def test_run_validation_non_english_escalates(self):
        from autocorrector.knowledge_graph.store import KnowledgeGraphStore
        from autocorrector.mcp_server.server import _run_validation
        from autocorrector.models import (
            CorrectionAction,
            CustomerContext,
            DetectedIntent,
            IntentType,
        )

        result = await _run_validation(
            "Puedo ofrecerle un crédito de $200 en su cuenta hoy mismo.",
            DetectedIntent(intent_type=IntentType.GENERAL, confidence=0.5),
            CustomerContext(account_tenure_days=30),
            KnowledgeGraphStore(),
        )
        assert result.action == CorrectionAction.ESCALATE
        assert result.is_compliant is False

    async def test_run_validation_short_english_allowed(self):
        from autocorrector.knowledge_graph.store import KnowledgeGraphStore
        from autocorrector.mcp_server.server import _run_validation
        from autocorrector.models import (
            CorrectionAction,
            CustomerContext,
            DetectedIntent,
            IntentType,
        )

        result = await _run_validation(
            "OK.",
            DetectedIntent(intent_type=IntentType.GENERAL, confidence=0.5),
            CustomerContext(account_tenure_days=30),
            KnowledgeGraphStore(),
        )
        assert result.action == CorrectionAction.ALLOW

    async def test_validate_action_reports_translation_reason(self):
        from unittest.mock import AsyncMock

        from autocorrector.intent_classifier.classifier import IntentClassifier
        from autocorrector.knowledge_graph.store import KnowledgeGraphStore
        from autocorrector.mcp_server.server import validate_action

        ctx = MagicMock()
        ctx.request_context.lifespan_context = {
            "kg": KnowledgeGraphStore(),
            "classifier": IntentClassifier(),
            "audit_store": None,
        }
        ctx.info = AsyncMock()
        ctx.warning = AsyncMock()

        result = await validate_action(
            text="Puedo ofrecerle un crédito de $200 en su cuenta hoy mismo.",
            customer_context={"account_tenure_days": 30},
            ctx=ctx,
        )
        assert result["action"] == "escalate"
        assert result["detected_language"] == "es"
        assert result["reason"] == "translation-unavailable"

