"""Tests for the MCP server tools, resources, and helpers."""

import json
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

from autocorrector.explainability.engine import ExplainabilityEngine
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.mcp_server.server import (
    get_active_policies,
    get_compliance_rules,
    get_correction_metrics,
    mcp,
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
    async def test_domain_without_rules_returns_none_not_invented_ones(self):
        # Coverage facts are checked by the fact verifier; no rule lives in this
        # domain, and the resource must not make any up (it once returned
        # hardcoded FACT-001/FACT-002 entries that nothing evaluates)
        result = await get_compliance_rules("policy_explanation")
        data = json.loads(result)
        assert data == {"domain": "policy_explanation", "rules": []}

    @pytest.mark.asyncio
    async def test_unknown_domain_returns_empty(self):
        result = await get_compliance_rules("nonexistent_domain")
        data = json.loads(result)
        assert data["domain"] == "nonexistent_domain"
        assert data["rules"] == []


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
    async def test_active_policies_lists_the_stores_documents(self):
        data = json.loads(await get_active_policies())
        assert [p["name"] for p in data["policies"]] == [
            "Agent_Conduct_Policy_2024_v1.0",
            "Credit_Policy_2024_v3.2",
            "Escalation_Policy_2024_v1.0",
            "Refund_Policy_2024_v2.1",
            "SLA_Policy_2024_v1.0",
        ]
        assert data["policies"][1]["rules"] == ["CREDIT-001", "CREDIT-002"]
        assert data["total_rules"] == 8
        assert data["total_facts"] == 6
        assert data["store"] == "memory"


@asynccontextmanager
async def _mcp_session(tmp_path, monkeypatch):
    """A real client session against the server object, lifespan included."""
    from mcp.shared.memory import create_connected_server_and_client_session

    monkeypatch.setenv("AUTOCORRECTOR_AUDIT_DB_PATH", str(tmp_path / "mcp_audit.db"))
    monkeypatch.setenv("AUTOCORRECTOR_NEO4J_URI", "")
    async with create_connected_server_and_client_session(mcp) as session:
        yield session


class TestResourcesOverASession:
    """Calling the resource functions directly cannot show whether a client can
    read them: `metrics://corrections/summary` was registered as a parameterless
    template and every read failed with "Unknown resource"."""

    async def test_resources_are_listed_as_two_static_and_one_template(self, tmp_path, monkeypatch):
        async with _mcp_session(tmp_path, monkeypatch) as session:
            static = sorted(str(r.uri) for r in (await session.list_resources()).resources)
            templates = [
                t.uriTemplate for t in (await session.list_resource_templates()).resourceTemplates
            ]
        assert static == ["metrics://corrections/summary", "policies://active"]
        assert templates == ["rules://compliance/{domain}"]

    async def test_resource_metrics_reflects_a_logged_event(self, tmp_path, monkeypatch):
        async with _mcp_session(tmp_path, monkeypatch) as session:
            await session.call_tool(
                "log_correction_event",
                {
                    "original_text": "I can offer you a $200 credit.",
                    "corrected_text": "I can offer you a $50 credit.",
                    "was_modified": True,
                    "action_taken": "correct",
                    "intent_type": "financial_compensation",
                },
            )
            result = await session.read_resource("metrics://corrections/summary")
        data = json.loads(result.contents[0].text)
        assert data["total_validations"] == 1
        assert data["total_corrections"] == 1

    async def test_resource_policies_reads_the_running_store(
        self, tmp_path, monkeypatch, sentinel_store
    ):
        async with _mcp_session(tmp_path, monkeypatch) as session:
            result = await session.read_resource("policies://active")
        data = json.loads(result.contents[0].text)
        assert {"name": "Sentinel_Policy_v0", "rules": ["SENTINEL-001"]} in data["policies"]
        assert data["total_rules"] == 9

    async def test_resource_rules_reads_the_running_store(
        self, tmp_path, monkeypatch, sentinel_store
    ):
        async with _mcp_session(tmp_path, monkeypatch) as session:
            result = await session.read_resource("rules://compliance/sentinel_domain")
        data = json.loads(result.contents[0].text)
        assert data["rules"] == [
            {
                "id": "SENTINEL-001",
                "name": "Sentinel",
                "description": "Only the running server's store has this rule",
                "severity": "low",
                "source_document": "Sentinel_Policy_v0",
                "source_section": "Section 9.9",
            }
        ]

    async def test_resources_read_over_streamable_http(self, tmp_path, monkeypatch):
        """The default transport: stateless JSON over HTTP, one lifespan per request."""
        from httpx import ASGITransport, AsyncClient

        monkeypatch.setenv("AUTOCORRECTOR_AUDIT_DB_PATH", str(tmp_path / "mcp_http.db"))
        monkeypatch.setenv("AUTOCORRECTOR_NEO4J_URI", "")
        app = mcp.streamable_http_app()
        headers = {"Accept": "application/json, text/event-stream"}

        async def rpc(client, request_id, method, params):
            body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            response = await client.post("/mcp", json=body, headers=headers)
            assert response.status_code == 200, response.text
            return response.json()["result"]

        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as c,
        ):
            await rpc(
                c,
                1,
                "tools/call",
                {
                    "name": "log_correction_event",
                    "arguments": {
                        "original_text": "I can offer you a $200 credit.",
                        "corrected_text": "I can offer you a $50 credit.",
                        "was_modified": True,
                        "action_taken": "correct",
                    },
                },
            )
            metrics = await rpc(c, 2, "resources/read", {"uri": "metrics://corrections/summary"})
            policies = await rpc(c, 3, "resources/read", {"uri": "policies://active"})
        assert json.loads(metrics["contents"][0]["text"])["total_validations"] == 1
        assert json.loads(policies["contents"][0]["text"])["total_rules"] == 8


@pytest.fixture
def sentinel_store(monkeypatch):
    """Make the server's lifespan build a store no fallback could produce, so a
    resource that silently used a fresh default store would fail the test."""
    store = KnowledgeGraphStore()
    store._policy_rules["sentinel_domain"] = [
        {
            "rule_id": "SENTINEL-001",
            "rule_name": "Sentinel",
            "description": "Only the running server's store has this rule",
            "severity": "low",
            "source_document": "Sentinel_Policy_v0",
            "source_section": "Section 9.9",
            "keywords": [],
            "conditions": [],
        }
    ]
    monkeypatch.setattr(
        KnowledgeGraphStore, "from_settings", classmethod(lambda cls, s=None: store)
    )
    return store


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


class TestMcpToolsPiiRail:
    """Every MCP tool runs the PII rail: no raw PII leaves or is stored."""

    EMAIL_TEXT = (
        "I'll go ahead and issue you a $75 credit right now; confirm at jane.doe@example.com."
    )

    @staticmethod
    def _ctx(audit_store=None):
        from unittest.mock import AsyncMock

        from autocorrector.intent_classifier.classifier import IntentClassifier
        from autocorrector.knowledge_graph.store import KnowledgeGraphStore

        ctx = MagicMock()
        ctx.request_context.lifespan_context = {
            "kg": KnowledgeGraphStore(),
            "classifier": IntentClassifier(),
            "audit_store": audit_store,
        }
        ctx.info = AsyncMock()
        ctx.warning = AsyncMock()
        return ctx

    async def test_validate_action_redacts_and_reports_pii(self):
        from autocorrector.mcp_server.server import validate_action

        result = await validate_action(
            text=self.EMAIL_TEXT, customer_context={"account_tenure_days": 30}, ctx=self._ctx()
        )
        assert result["pii_detected"] is True
        assert result["pii_types"] == ["email"]
        assert "jane.doe@example.com" not in result["redacted_text"]
        assert "[EMAIL REDACTED]" in result["redacted_text"]
        # the policy check still ran on the redacted text
        assert result["action"] == "correct"

    async def test_validate_action_clean_text_reports_no_pii(self):
        from autocorrector.mcp_server.server import validate_action

        result = await validate_action(
            text="I can help with that.", customer_context={}, ctx=self._ctx()
        )
        assert result["pii_detected"] is False
        assert result["pii_types"] == []
        assert result["redacted_text"] == "I can help with that."

    async def test_get_correction_never_returns_raw_pii(self):
        from autocorrector.mcp_server.server import get_correction

        violation = {
            "rule_id": "CREDIT-001",
            "rule_name": "Credit Authorization Threshold",
            "description": "Credits >$50 require 90+ days account tenure",
            "severity": "high",
        }
        result = await get_correction(
            text=self.EMAIL_TEXT,
            violations=[violation],
            customer_context={"account_tenure_days": 30},
            intent_type="financial_compensation",
            ctx=self._ctx(),
        )
        assert "jane.doe@example.com" not in result["original_text"]
        assert "jane.doe@example.com" not in result["corrected_text"]
        assert result["pii_detected"] is True
        assert result["pii_types"] == ["email"]
        assert result["was_modified"] is True

    async def test_log_correction_event_stores_redacted_text(self, tmp_path):
        from autocorrector.audit.store import AuditStore
        from autocorrector.mcp_server.server import log_correction_event

        audit = AuditStore(db_path=str(tmp_path / "mcp_pii.db"))
        await audit.connect()
        try:
            result = await log_correction_event(
                original_text="Your SSN 123-45-6789 is on file.",
                corrected_text="Your SSN 123-45-6789 is on file.",
                was_modified=False,
                action_taken="allow",
                ctx=self._ctx(audit),
            )
            event = await audit.get_event_by_id(result["event_id"])
        finally:
            await audit.close()
        assert event is not None
        assert "123-45-6789" not in event.original_text
        assert "123-45-6789" not in event.corrected_text
        assert "[SSN REDACTED]" in event.original_text

    async def test_log_correction_event_keeps_redaction_tokens(self, tmp_path):
        """Text already redacted by validate_action/get_correction is stored byte-identical."""
        from autocorrector.audit.store import AuditStore
        from autocorrector.guardrails.validators import _PII_PATTERNS
        from autocorrector.mcp_server.server import log_correction_event

        redacted = " ".join(token for _, token in _PII_PATTERNS.values())
        audit = AuditStore(db_path=str(tmp_path / "mcp_tokens.db"))
        await audit.connect()
        try:
            result = await log_correction_event(
                original_text=redacted,
                corrected_text=redacted,
                was_modified=False,
                action_taken="allow",
                ctx=self._ctx(audit),
            )
            event = await audit.get_event_by_id(result["event_id"])
        finally:
            await audit.close()
        assert event.original_text == redacted
        assert event.corrected_text == redacted
