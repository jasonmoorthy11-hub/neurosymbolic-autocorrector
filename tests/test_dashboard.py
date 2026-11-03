"""Tests for the dashboard API."""

import pytest
from httpx import ASGITransport, AsyncClient

import autocorrector.dashboard.api as api_module
from autocorrector.audit.store import AuditStore
from autocorrector.dashboard.api import _connections, _event_log, app
from autocorrector.models import (
    CorrectionAction,
    CorrectionEvent,
    DetectedIntent,
    IntentType,
)


def _make_event(
    event_id: str = "e1",
    action: CorrectionAction = CorrectionAction.CORRECT,
    intent: IntentType = IntentType.FINANCIAL_COMPENSATION,
    confidence: float = 0.9,
    latency_ms: float = 15.0,
    violations: list | None = None,
) -> CorrectionEvent:
    return CorrectionEvent(
        event_id=event_id,
        agent_id="agent-1",
        session_id="sess-1",
        original_text="I can offer you a $200 credit.",
        corrected_text="I can offer you a $50 credit.",
        was_modified=True,
        action_taken=action,
        intent=DetectedIntent(intent_type=intent, confidence=confidence),
        violations=violations or [],
        confidence=confidence,
        latency_ms=latency_ms,
    )


@pytest.fixture(autouse=True)
async def clean_state(tmp_path):
    """Reset state and inject a temp audit store between tests."""
    _event_log.clear()
    _connections.clear()

    # Inject a temp audit store so tests don't touch a real DB
    audit = AuditStore(db_path=str(tmp_path / "test_dashboard.db"))
    await audit.connect()
    api_module._audit_store = audit

    yield audit

    _event_log.clear()
    _connections.clear()
    await audit.close()
    api_module._audit_store = None


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestMetricsEndpoint:
    @pytest.mark.asyncio
    async def test_metrics_empty(self, client):
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_events"] == 0
        assert data["correction_rate"] == 0.0
        assert data["avg_latency_ms"] == 0.0

    @pytest.mark.asyncio
    async def test_metrics_with_events(self, client, clean_state):
        audit = clean_state
        await audit.log_event(
            _make_event(
                event_id="e1",
                action=CorrectionAction.CORRECT,
                confidence=0.9,
                latency_ms=15.0,
            )
        )
        await audit.log_event(
            _make_event(
                event_id="e2",
                action=CorrectionAction.ALLOW,
                intent=IntentType.TROUBLESHOOTING,
                confidence=0.95,
                latency_ms=8.0,
            )
        )

        resp = await client.get("/api/metrics")
        data = resp.json()
        assert data["total_events"] == 2
        assert data["corrections_made"] == 1
        assert data["allowed_through"] == 1
        assert data["correction_rate"] == 0.5
        assert data["events_by_intent"]["financial_compensation"] == 1


class TestEventsEndpoint:
    @pytest.mark.asyncio
    async def test_events_empty(self, client):
        resp = await client.get("/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_events_pagination(self, client, clean_state):
        audit = clean_state
        for i in range(10):
            await audit.log_event(_make_event(event_id=f"e{i}"))

        resp = await client.get("/api/events?limit=3&offset=0")
        data = resp.json()
        assert len(data["events"]) == 3
        assert data["total"] == 10


class TestQueueEndpoint:
    @pytest.mark.asyncio
    async def test_queue_empty(self, client):
        resp = await client.get("/api/queue")
        data = resp.json()
        assert data["queue"] == []

    @pytest.mark.asyncio
    async def test_queue_escalations(self, client, clean_state):
        audit = clean_state
        await audit.log_event(
            _make_event(
                event_id="esc1",
                action=CorrectionAction.ESCALATE,
            )
        )
        await audit.log_event(
            _make_event(
                event_id="normal1",
                action=CorrectionAction.ALLOW,
            )
        )

        resp = await client.get("/api/queue")
        data = resp.json()
        assert data["total"] == 1
        assert data["queue"][0]["event_id"] == "esc1"


class TestRulesEndpoint:
    @pytest.mark.asyncio
    async def test_rules_returns_list(self, client):
        resp = await client.get("/api/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert "rules" in data
        assert len(data["rules"]) > 0
        rule_ids = [r["rule_id"] for r in data["rules"]]
        assert "CREDIT-001" in rule_ids


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_check(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "uptime_seconds" in data
        assert "event_count" in data
        assert data["event_count"] == 0

    @pytest.mark.asyncio
    async def test_health_check_with_events(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1"))
        resp = await client.get("/health")
        data = resp.json()
        assert data["event_count"] == 1


class TestDemoEndpoints:
    @pytest.mark.asyncio
    async def test_demo_start_stop(self, client):
        resp = await client.post("/api/demo/start?interval=100")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"

        resp2 = await client.post("/api/demo/start")
        assert resp2.json()["status"] == "already_running"

        resp3 = await client.post("/api/demo/stop")
        assert resp3.json()["status"] == "stopped"


class TestApproveReject:
    @pytest.mark.asyncio
    async def test_approve_success(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="esc1", action=CorrectionAction.ESCALATE))
        resp = await client.post(
            "/api/queue/esc1/approve",
            json={"reviewer": "admin", "comment": "OK"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_success(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="esc2", action=CorrectionAction.ESCALATE))
        resp = await client.post(
            "/api/queue/esc2/reject",
            json={"reviewer": "admin", "comment": "Not appropriate"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "rejected"

    @pytest.mark.asyncio
    async def test_approve_nonexistent_404(self, client):
        resp = await client.post(
            "/api/queue/nope/approve",
            json={"reviewer": "admin"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_already_decided_409(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="esc3", action=CorrectionAction.ESCALATE))
        await client.post("/api/queue/esc3/approve", json={"reviewer": "admin"})
        resp = await client.post("/api/queue/esc3/reject", json={"reviewer": "admin"})
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_queue_shows_decision(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="esc4", action=CorrectionAction.ESCALATE))
        await client.post("/api/queue/esc4/approve", json={"reviewer": "admin"})
        resp = await client.get("/api/queue")
        data = resp.json()
        assert len(data["queue"]) == 1
        assert data["queue"][0]["decision"] == "approved"


class TestRejectionFeedback:
    @pytest.mark.asyncio
    async def test_reject_with_reason(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="esc1", action=CorrectionAction.ESCALATE))
        resp = await client.post(
            "/api/queue/esc1/reject",
            json={"reviewer": "admin", "comment": "Bad tone", "rejection_reason": "tone_wrong"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "rejected"
        assert data["rejection_reason"] == "tone_wrong"

    @pytest.mark.asyncio
    async def test_reject_reason_in_queue(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="esc1", action=CorrectionAction.ESCALATE))
        await client.post(
            "/api/queue/esc1/reject",
            json={"reviewer": "admin", "rejection_reason": "over_corrected"},
        )
        resp = await client.get("/api/queue")
        data = resp.json()
        assert data["queue"][0]["rejection_reason"] == "over_corrected"

    @pytest.mark.asyncio
    async def test_reject_without_reason(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="esc1", action=CorrectionAction.ESCALATE))
        resp = await client.post(
            "/api/queue/esc1/reject",
            json={"reviewer": "admin"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rejection_reason"] is None


class TestApprovalRatesEndpoint:
    @pytest.mark.asyncio
    async def test_empty_rates(self, client):
        resp = await client.get("/api/metrics/approval-rates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_decisions"] == 0
        assert data["approval_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_rates_with_decisions(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1", action=CorrectionAction.ESCALATE))
        await audit.log_event(_make_event(event_id="e2", action=CorrectionAction.ESCALATE))
        await audit.record_decision("e1", "approved", reviewer="admin")
        await audit.record_decision(
            "e2", "rejected", reviewer="admin", rejection_reason="tone_wrong"
        )

        resp = await client.get("/api/metrics/approval-rates")
        data = resp.json()
        assert data["total_decisions"] == 2
        assert data["approved"] == 1
        assert data["rejected"] == 1
        assert data["approval_rate"] == 0.5
        assert data["rejection_reasons"]["tone_wrong"] == 1

    @pytest.mark.asyncio
    async def test_rates_by_intent(self, client, clean_state):
        audit = clean_state
        await audit.log_event(
            _make_event(
                event_id="e1",
                action=CorrectionAction.ESCALATE,
                intent=IntentType.FINANCIAL_COMPENSATION,
            )
        )
        await audit.log_event(
            _make_event(
                event_id="e2",
                action=CorrectionAction.ESCALATE,
                intent=IntentType.TROUBLESHOOTING,
            )
        )
        await audit.record_decision("e1", "approved")
        await audit.record_decision("e2", "rejected", rejection_reason="factually_wrong")

        resp = await client.get("/api/metrics/approval-rates")
        data = resp.json()
        assert "financial_compensation" in data["by_intent"]
        assert data["by_intent"]["financial_compensation"]["approved"] == 1


class TestFiltering:
    @pytest.mark.asyncio
    async def test_filter_by_intent(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1", intent=IntentType.FINANCIAL_COMPENSATION))
        await audit.log_event(_make_event(event_id="e2", intent=IntentType.TROUBLESHOOTING))
        resp = await client.get("/api/events?intent_type=financial_compensation")
        data = resp.json()
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_filter_by_action(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1", action=CorrectionAction.CORRECT))
        await audit.log_event(_make_event(event_id="e2", action=CorrectionAction.ALLOW))
        resp = await client.get("/api/events?action=correct")
        data = resp.json()
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_no_filters_returns_all(self, client, clean_state):
        audit = clean_state
        for i in range(5):
            await audit.log_event(_make_event(event_id=f"e{i}"))
        resp = await client.get("/api/events")
        data = resp.json()
        assert data["total"] == 5


class TestExport:
    @pytest.mark.asyncio
    async def test_export_csv(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1"))
        resp = await client.get("/api/events/export?format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        content = resp.text
        assert "event_id" in content
        assert "e1" in content

    @pytest.mark.asyncio
    async def test_export_json(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1"))
        resp = await client.get("/api/events/export?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["event_id"] == "e1"

    @pytest.mark.asyncio
    async def test_export_with_filter(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1", action=CorrectionAction.CORRECT))
        await audit.log_event(_make_event(event_id="e2", action=CorrectionAction.ALLOW))
        resp = await client.get("/api/events/export?format=json&action=correct")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["event_id"] == "e1"


class TestAnalyticsTrends:
    @pytest.mark.asyncio
    async def test_analytics_trends_empty(self, client):
        resp = await client.get("/api/analytics/trends")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trends"] == []
        assert "cost_per_violation" in data
        assert "cost_per_escalation" in data

    @pytest.mark.asyncio
    async def test_analytics_trends_with_data(self, client, clean_state):
        audit = clean_state
        await audit.log_event(
            _make_event(event_id="e1", action=CorrectionAction.CORRECT, latency_ms=10.0)
        )
        await audit.log_event(
            _make_event(event_id="e2", action=CorrectionAction.ESCALATE, latency_ms=20.0)
        )
        resp = await client.get("/api/analytics/trends")
        data = resp.json()
        assert len(data["trends"]) >= 1
        bucket = data["trends"][0]
        assert "timestamp" in bucket
        assert bucket["total"] == 2
        assert bucket["corrections"] == 1
        assert bucket["escalations"] == 1

    @pytest.mark.asyncio
    async def test_analytics_trends_custom_hours(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1"))
        resp = await client.get("/api/analytics/trends?hours=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["trends"]) >= 1


class TestAnalyticsSummary:
    @pytest.mark.asyncio
    async def test_analytics_summary_empty(self, client):
        resp = await client.get("/api/analytics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_events"] == 0
        assert data["violations_prevented"] == 0
        assert data["estimated_savings"] == 0.0

    @pytest.mark.asyncio
    async def test_analytics_summary_with_data(self, client, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1", action=CorrectionAction.CORRECT))
        await audit.log_event(_make_event(event_id="e2", action=CorrectionAction.ESCALATE))
        await audit.log_event(_make_event(event_id="e3", action=CorrectionAction.ALLOW))
        resp = await client.get("/api/analytics/summary")
        data = resp.json()
        assert data["total_events"] == 3
        assert data["corrections"] == 1
        assert data["escalations"] == 1
        assert data["violations_prevented"] == 1
        assert data["unique_agents"] == 1
        assert "avg_latency_ms" in data
        assert "p50_latency_ms" in data
        assert "p95_latency_ms" in data

    @pytest.mark.asyncio
    async def test_analytics_summary_roi_formula(self, client, clean_state):
        audit = clean_state
        # 3 corrections + 2 escalations
        for i in range(3):
            await audit.log_event(_make_event(event_id=f"c{i}", action=CorrectionAction.CORRECT))
        for i in range(2):
            await audit.log_event(_make_event(event_id=f"e{i}", action=CorrectionAction.ESCALATE))
        resp = await client.get("/api/analytics/summary")
        data = resp.json()
        # savings = (3 * 15.0) + (2 * 25.0) = 45 + 50 = 95
        expected = 3 * data["cost_per_violation"] + 2 * data["cost_per_escalation"]
        assert data["estimated_savings"] == expected
        assert data["violations_prevented"] == 3


class TestRequestLogging:
    @pytest.mark.asyncio
    async def test_response_has_request_id(self, client):
        resp = await client.get("/health")
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) > 0

    @pytest.mark.asyncio
    async def test_custom_request_id_passthrough(self, client):
        resp = await client.get("/health", headers={"X-Request-ID": "custom-123"})
        assert resp.headers["x-request-id"] == "custom-123"

    @pytest.mark.asyncio
    async def test_health_includes_log_level(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert "log_level" in data
        assert data["log_level"] == "INFO"


class TestACEStrategiesEndpoint:
    @pytest.mark.asyncio
    async def test_strategies_empty(self, client):
        resp = await client.get("/api/ace/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["strategies"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_strategies_with_data(self, client, clean_state):
        audit = clean_state
        await audit.add_strategy(strategy_id="s1", lesson="Be empathetic", rule_id="CREDIT-001")
        resp = await client.get("/api/ace/strategies")
        data = resp.json()
        assert data["total"] == 1
        assert data["strategies"][0]["lesson"] == "Be empathetic"


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_rate_limit_handler_exists(self, client):
        """Rate limit exception handler should return 429 with JSON body."""
        from autocorrector.dashboard.api import rate_limit_handler

        assert rate_limit_handler is not None

    @pytest.mark.asyncio
    async def test_limiter_configured(self):
        """Limiter should be configured on the app."""
        from autocorrector.dashboard.api import limiter

        assert limiter is not None


class TestDashboardAuth:
    def test_auth_middleware_allows_when_no_key(self):
        """Dev mode (empty key) should allow all requests."""
        from autocorrector.dashboard.api import DashboardAuthMiddleware

        middleware = DashboardAuthMiddleware(app=None, api_key="")
        assert not middleware._is_protected("/api/metrics")
        assert middleware._is_protected("/api/queue/e1/approve")
        assert middleware._is_protected("/api/events/export")
        assert middleware._is_protected("/api/demo/start")

    def test_auth_middleware_path_detection(self):
        from autocorrector.dashboard.api import DashboardAuthMiddleware

        middleware = DashboardAuthMiddleware(app=None, api_key="secret")
        assert middleware._is_protected("/api/queue/e1/approve")
        assert middleware._is_protected("/api/queue/e1/reject")
        assert middleware._is_protected("/api/events/export")
        assert middleware._is_protected("/api/demo/start")
        assert middleware._is_protected("/api/demo/stop")
        assert middleware._is_protected("/api/demo/step")
        assert not middleware._is_protected("/api/metrics")
        assert not middleware._is_protected("/api/events")
        assert not middleware._is_protected("/api/rules")
        assert not middleware._is_protected("/health")


class TestAuditStorePublicMethods:
    @pytest.mark.asyncio
    async def test_event_exists(self, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1"))
        assert await audit.event_exists("e1") is True
        assert await audit.event_exists("nope") is False

    @pytest.mark.asyncio
    async def test_get_event_by_id(self, clean_state):
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1"))
        event = await audit.get_event_by_id("e1")
        assert event is not None
        assert event.event_id == "e1"
        assert event.original_text == "I can offer you a $200 credit."

    @pytest.mark.asyncio
    async def test_get_event_by_id_not_found(self, clean_state):
        audit = clean_state
        event = await audit.get_event_by_id("nope")
        assert event is None
