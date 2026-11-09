"""Tests for the dashboard API."""

import csv
import io
from datetime import UTC

import pytest
from httpx import ASGITransport, AsyncClient

import autocorrector.dashboard.state as state_module
from autocorrector.audit.store import AuditStore
from autocorrector.dashboard.api import _connections, _event_log, app
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import (
    CorrectionAction,
    CorrectionEvent,
    DetectedIntent,
    IntentType,
    PolicyViolation,
    Severity,
)
from autocorrector.orchestrator.pipeline import set_kg_store


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

    # Inject a temp audit store so tests don't touch a real DB, and an explicit
    # in-memory policy store so this suite never reads AUTOCORRECTOR_NEO4J_*
    audit = AuditStore(db_path=str(tmp_path / "test_dashboard.db"))
    await audit.connect()
    state_module._audit_store = audit
    previous_kg = state_module._kg_store
    state_module._kg_store = KnowledgeGraphStore()
    set_kg_store(state_module._kg_store)

    yield audit

    _event_log.clear()
    _connections.clear()
    await audit.close()
    state_module._kg_store = previous_kg
    state_module._audit_store = None


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

    @pytest.mark.asyncio
    async def test_queue_severity_is_highest_by_rank(self, client, clean_state):
        audit = clean_state
        violations = [
            PolicyViolation(
                rule_id=rule_id,
                rule_name=rule_id,
                description="d",
                severity=severity,
                source_document="doc",
                source_section="s",
            )
            for rule_id, severity in (
                ("CREDIT-001", Severity.HIGH),
                ("CREDIT-002", Severity.CRITICAL),
            )
        ]
        await audit.log_event(
            _make_event(event_id="esc1", action=CorrectionAction.ESCALATE, violations=violations)
        )

        resp = await client.get("/api/queue")
        assert resp.json()["queue"][0]["severity"] == "critical"


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
        resp = await client.post("/api/demo/start?interval=60")
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
    async def test_export_csv_timestamp_matches_json(self, client, clean_state):
        """Both exports write the same UTC instant in the same form (Z suffix)."""
        audit = clean_state
        await audit.log_event(_make_event(event_id="e1"))
        json_ts = (await client.get("/api/events/export?format=json")).json()[0]["timestamp"]
        csv_text = (await client.get("/api/events/export?format=csv")).text
        csv_ts = next(csv.DictReader(io.StringIO(csv_text)))["timestamp"]
        assert json_ts.endswith("Z")
        assert csv_ts == json_ts

    def test_utc_iso_treats_naive_as_utc(self):
        """A naive datetime must not be shifted by the machine's local offset."""
        from datetime import datetime

        from autocorrector.dashboard.routes.events import _utc_iso

        assert _utc_iso(datetime(2026, 11, 6, 12, 0, 0)) == "2026-11-06T12:00:00Z"
        assert (
            _utc_iso(datetime(2026, 11, 6, 12, 0, 0, 5, tzinfo=UTC))
            == "2026-11-06T12:00:00.000005Z"
        )

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

    async def test_rate_limit_default_applies_to_an_undecorated_route(self, client):
        # /health carries no @limiter.limit; only the middleware enforces the default
        responses = [await client.get("/health") for _ in range(61)]
        assert [r.status_code for r in responses] == [200] * 60 + [429]
        # The middleware's 429 is the app's own response and still passes through
        # the request-logging middleware
        assert responses[-1].json() == {"detail": "Rate limit exceeded. Please try again later."}
        assert responses[-1].headers["x-request-id"]

    async def test_rate_limit_demo_stop_is_limited(self, client):
        codes = [(await client.post("/api/demo/stop")).status_code for _ in range(31)]
        assert codes == [200] * 30 + [429]

    async def test_rate_limit_demo_step_is_limited(self, client):
        codes = [(await client.post("/api/demo/step?index=4")).status_code for _ in range(31)]
        assert codes == [200] * 30 + [429]

    async def test_rate_limit_buckets_are_per_forwarded_client(self, client, trust_test_peer):
        # Behind the bundled nginx every request arrives from the proxy's address;
        # the entry nginx appended to X-Forwarded-For is the real client.
        first = {"X-Forwarded-For": "203.0.113.7"}
        for _ in range(30):
            await client.post("/api/demo/stop", headers=first)
        assert (await client.post("/api/demo/stop", headers=first)).status_code == 429
        other = await client.post("/api/demo/stop", headers={"X-Forwarded-For": "203.0.113.8"})
        assert other.status_code == 200

    async def test_rate_limit_key_ignores_client_supplied_forwarded_entries(
        self, client, trust_test_peer
    ):
        # nginx appends the peer address, so only the LAST entry is trustworthy:
        # rotating the first entry must not buy a fresh bucket.
        for i in range(30):
            await client.post(
                "/api/demo/stop", headers={"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.7"}
            )
        resp = await client.post(
            "/api/demo/stop", headers={"X-Forwarded-For": "10.9.9.99, 203.0.113.7"}
        )
        assert resp.status_code == 429

    async def test_rate_limit_header_buys_nothing_from_an_untrusted_peer(self, client):
        # Default: no trusted proxy, so the header is whatever the client typed,
        # even from a loopback or private peer (a LAN client, the Docker gateway)
        for i in range(30):
            await client.post("/api/demo/stop", headers={"X-Forwarded-For": f"203.0.113.{i}"})
        resp = await client.post("/api/demo/stop", headers={"X-Forwarded-For": "203.0.113.99"})
        assert resp.status_code == 429


@pytest.fixture
def trust_test_peer(monkeypatch):
    """Treat the ASGI test client's address (127.0.0.1) as the trusted proxy."""
    monkeypatch.setattr(
        state_module, "TRUSTED_PROXIES", state_module.parse_trusted_proxies(["127.0.0.1"])
    )


class TestClientKey:
    PROXY = "172.31.250.10"

    @pytest.fixture(autouse=True)
    def _trust_the_proxy(self, monkeypatch):
        monkeypatch.setattr(
            state_module, "TRUSTED_PROXIES", state_module.parse_trusted_proxies([self.PROXY])
        )

    def _request(self, peer: str | None, *forwarded: str):
        from starlette.requests import Request

        headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
        scope = {"type": "http", "headers": headers, "client": (peer, 1234) if peer else None}
        return Request(scope)

    def test_client_key_is_the_peer_without_a_forwarded_header(self):
        assert state_module.client_key(self._request("192.0.2.4")) == "192.0.2.4"

    def test_client_key_uses_the_last_forwarded_entry_from_the_trusted_proxy(self):
        request = self._request(self.PROXY, "198.51.100.1, 203.0.113.7")
        assert state_module.client_key(request) == "203.0.113.7"

    def test_client_key_ignores_the_header_from_an_untrusted_private_peer(self):
        # Another container, a LAN client or the Docker gateway is not the proxy
        request = self._request("172.31.250.1", "203.0.113.7")
        assert state_module.client_key(request) == "172.31.250.1"

    def test_client_key_ignores_the_header_from_a_public_peer(self):
        request = self._request("8.8.8.8", "203.0.113.7")
        assert state_module.client_key(request) == "8.8.8.8"

    def test_client_key_ignores_the_header_when_no_proxy_is_trusted(self, monkeypatch):
        monkeypatch.setattr(state_module, "TRUSTED_PROXIES", ())
        request = self._request("127.0.0.1", "203.0.113.7")
        assert state_module.client_key(request) == "127.0.0.1"

    def test_client_key_falls_back_to_the_peer_for_a_malformed_entry(self):
        request = self._request(self.PROXY, "not-an-address")
        assert state_module.client_key(request) == self.PROXY

    def test_client_key_reads_the_last_of_several_header_lines(self):
        # A proxy may append its own header line instead of extending the first
        request = self._request(self.PROXY, "10.9.9.9", "203.0.113.7")
        assert state_module.client_key(request) == "203.0.113.7"

    def test_client_key_recognises_an_ipv4_mapped_trusted_proxy(self):
        request = self._request(f"::ffff:{self.PROXY}", "203.0.113.7")
        assert state_module.client_key(request) == "203.0.113.7"

    def test_client_key_does_not_trust_an_ipv4_mapped_stranger(self):
        request = self._request("::ffff:8.8.8.8", "203.0.113.7")
        assert state_module.client_key(request) == "::ffff:8.8.8.8"

    def test_parse_trusted_proxies_accepts_addresses_and_networks(self):
        parsed = state_module.parse_trusted_proxies(["172.31.250.10", "10.0.0.0/8", "::1"])
        assert [str(n) for n in parsed] == ["172.31.250.10/32", "10.0.0.0/8", "::1/128"]

    def test_parse_trusted_proxies_rejects_a_bad_entry(self):
        with pytest.raises(ValueError):
            state_module.parse_trusted_proxies(["nginx"])


class TestDemoInterval:
    @pytest.mark.parametrize("interval", ["0", "-1", "0.1", "61", "nan"])
    async def test_demo_start_rejects_an_out_of_range_interval(self, client, interval):
        resp = await client.post(f"/api/demo/start?interval={interval}")
        assert resp.status_code == 422

    async def test_demo_start_accepts_the_default_interval(self, client, monkeypatch):
        import autocorrector.dashboard.routes.demo as demo_module

        async def _no_run(interval: float) -> None:
            return None

        monkeypatch.setattr(demo_module, "_run_demo", _no_run)
        try:
            resp = await client.post("/api/demo/start")
            assert resp.status_code == 200
            assert resp.json()["interval"] == 3.0
        finally:
            await client.post("/api/demo/stop")


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


class TestRedactedAuditText:
    """What the dashboard stores, serves and broadcasts never carries raw PII."""

    SSN = "123-45-6789"
    ESCALATED_WITH_PII = (
        "I'll issue you a $750 credit to make up for everything; your SSN 123-45-6789 is verified."
    )

    async def _process(self):
        from autocorrector.dashboard.api import process_and_log

        return await process_and_log(
            text=self.ESCALATED_WITH_PII,
            customer_context={"account_tenure_days": 200},
            agent_id="agent-test",
        )

    async def test_stored_event_holds_redacted_original(self, clean_state):
        event_dict = await self._process()
        stored = await clean_state.get_event_by_id(event_dict["event_id"])
        assert stored is not None
        assert self.SSN not in stored.original_text
        assert "[SSN REDACTED]" in stored.original_text
        assert self.SSN not in stored.corrected_text
        assert stored.action_taken == CorrectionAction.ESCALATE

    async def test_events_endpoint_has_no_raw_pii(self, client, clean_state):
        await self._process()
        resp = await client.get("/api/events")
        assert resp.status_code == 200
        assert self.SSN not in resp.text
        assert resp.json()["total"] == 1

    async def test_queue_endpoint_has_no_raw_pii(self, client, clean_state):
        await self._process()
        resp = await client.get("/api/queue")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert self.SSN not in resp.text

    async def test_broadcast_and_recent_log_have_no_raw_pii(self, clean_state):
        from unittest.mock import AsyncMock

        ws = AsyncMock()
        _connections.append(ws)
        event_dict = await self._process()
        sent = "".join(str(c.args[0]) for c in ws.send_text.call_args_list)
        assert sent  # the correction_event broadcast reached the client
        assert self.SSN not in sent
        assert self.SSN not in event_dict["original_text"]
        assert all(self.SSN not in e["original_text"] for e in _event_log)

    async def test_stored_original_is_redacted_even_when_translation_changed_it(self, clean_state):
        """The pipeline's PII flag comes from the translated text; the stored original
        is the untranslated input, so it is redacted on its own."""
        from unittest.mock import AsyncMock, patch

        from autocorrector.config import Settings
        from autocorrector.dashboard.api import process_and_log
        from autocorrector.multilingual.translator import Translation

        spanish = "Su número de seguro social 123-45-6789 está verificado."
        settings = Settings(anthropic_api_key="test-key")
        with (
            patch("autocorrector.orchestrator.pipeline.get_settings", return_value=settings),
            patch("anthropic.AsyncAnthropic"),
            patch(
                "autocorrector.multilingual.translator.translate_to_english_checked",
                new_callable=AsyncMock,
                return_value=Translation(
                    "Your social security number 123456789 is verified.", ok=True
                ),
            ),
            patch(
                "autocorrector.multilingual.translator.translate_from_english",
                new_callable=AsyncMock,
                return_value="Su número 123456789 está verificado.",
            ),
        ):
            event_dict = await process_and_log(
                text=spanish, customer_context={"account_tenure_days": 200}
            )
        stored = await clean_state.get_event_by_id(event_dict["event_id"])
        assert self.SSN not in stored.original_text
        assert "[SSN REDACTED]" in stored.original_text
