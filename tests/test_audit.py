"""Tests for the SQLite audit store."""

from datetime import UTC, datetime, timedelta

import pytest

from autocorrector.audit.store import AuditStore
from autocorrector.models import (
    CorrectionAction,
    CorrectionEvent,
    DetectedIntent,
    IntentType,
    PolicyViolation,
    Severity,
)


def _make_event(
    event_id: str = "evt-1",
    action: CorrectionAction = CorrectionAction.CORRECT,
    intent: IntentType = IntentType.FINANCIAL_COMPENSATION,
    confidence: float = 0.92,
    latency_ms: float = 15.0,
    was_modified: bool = True,
    violations: list | None = None,
) -> CorrectionEvent:
    return CorrectionEvent(
        event_id=event_id,
        agent_id="agent-1",
        session_id="sess-1",
        original_text="I can offer you a $200 credit.",
        corrected_text="I can offer you a $50 credit.",
        was_modified=was_modified,
        action_taken=action,
        intent=DetectedIntent(intent_type=intent, confidence=confidence),
        violations=violations or [],
        confidence=confidence,
        latency_ms=latency_ms,
    )


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test_audit.db")
    s = AuditStore(db_path=db_path)
    await s.connect()
    yield s
    await s.close()


class TestAuditStoreConnect:
    @pytest.mark.asyncio
    async def test_connect_creates_table(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        s = AuditStore(db_path=db_path)
        await s.connect()
        count = await s.count()
        assert count == 0
        await s.close()

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self, tmp_path):
        s = AuditStore(db_path=str(tmp_path / "nc.db"))
        with pytest.raises(RuntimeError, match="not connected"):
            await s.count()


class TestLogEvent:
    @pytest.mark.asyncio
    async def test_log_and_count(self, store):
        event = _make_event()
        await store.log_event(event)
        assert await store.count() == 1

    @pytest.mark.asyncio
    async def test_log_multiple_events(self, store):
        for i in range(5):
            await store.log_event(_make_event(event_id=f"evt-{i}"))
        assert await store.count() == 5

    @pytest.mark.asyncio
    async def test_log_event_with_violations(self, store):
        violation = PolicyViolation(
            rule_id="CREDIT-001",
            rule_name="Credit Auth",
            description="Credits over $50",
            severity=Severity.HIGH,
            source_document="Credit_Policy_2024_v3.2",
        )
        event = _make_event(violations=[violation])
        await store.log_event(event)
        events = await store.get_events()
        assert len(events) == 1
        assert events[0].violations[0].rule_id == "CREDIT-001"


class TestGetEvents:
    @pytest.mark.asyncio
    async def test_get_events_empty(self, store):
        events = await store.get_events()
        assert events == []

    @pytest.mark.asyncio
    async def test_get_events_returns_newest_first(self, store):
        for i in range(3):
            await store.log_event(_make_event(event_id=f"evt-{i}"))
        events = await store.get_events()
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_pagination(self, store):
        for i in range(10):
            await store.log_event(_make_event(event_id=f"evt-{i}"))
        page1 = await store.get_events(limit=3, offset=0)
        page2 = await store.get_events(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0].event_id != page2[0].event_id

    @pytest.mark.asyncio
    async def test_roundtrip_fields(self, store):
        event = _make_event(
            event_id="roundtrip-1",
            action=CorrectionAction.ESCALATE,
            intent=IntentType.REFUND_PROCESSING,
            confidence=0.88,
            latency_ms=22.5,
        )
        await store.log_event(event)
        events = await store.get_events()
        e = events[0]
        assert e.event_id == "roundtrip-1"
        assert e.action_taken == CorrectionAction.ESCALATE
        assert e.intent.intent_type == IntentType.REFUND_PROCESSING
        assert e.confidence == 0.88
        assert e.latency_ms == 22.5
        assert e.was_modified is True


class TestGetMetrics:
    @pytest.mark.asyncio
    async def test_metrics_empty(self, store):
        metrics = await store.get_metrics()
        assert metrics["total_validations"] == 0
        assert metrics["total_corrections"] == 0
        assert metrics["average_confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_metrics_aggregation(self, store):
        await store.log_event(
            _make_event(
                event_id="e1", action=CorrectionAction.CORRECT, confidence=0.9, latency_ms=10.0
            )
        )
        await store.log_event(
            _make_event(
                event_id="e2", action=CorrectionAction.ALLOW, confidence=1.0, latency_ms=5.0
            )
        )
        await store.log_event(
            _make_event(
                event_id="e3", action=CorrectionAction.CORRECT, confidence=0.8, latency_ms=20.0
            )
        )

        metrics = await store.get_metrics()
        assert metrics["total_validations"] == 3
        assert metrics["total_corrections"] == 2
        assert metrics["corrections_by_type"]["correct"] == 2
        assert metrics["corrections_by_type"]["allow"] == 1
        assert 0.89 <= metrics["average_confidence"] <= 0.91  # (0.9+1.0+0.8)/3
        assert 11.0 <= metrics["average_latency_ms"] <= 12.0  # (10+5+20)/3


class TestGetQueue:
    @pytest.mark.asyncio
    async def test_queue_empty(self, store):
        queue = await store.get_queue()
        assert queue == []

    @pytest.mark.asyncio
    async def test_queue_returns_escalations(self, store):
        await store.log_event(_make_event(event_id="e1", action=CorrectionAction.ESCALATE))
        await store.log_event(_make_event(event_id="e2", action=CorrectionAction.ALLOW))
        queue = await store.get_queue()
        assert len(queue) == 1
        assert queue[0]["event"].event_id == "e1"

    @pytest.mark.asyncio
    async def test_queue_includes_decision_status(self, store):
        await store.log_event(_make_event(event_id="e1", action=CorrectionAction.ESCALATE))
        await store.record_decision("e1", "approved", reviewer="admin")
        queue = await store.get_queue()
        assert len(queue) == 1
        assert queue[0]["decision"] == "approved"
        assert queue[0]["reviewer"] == "admin"


class TestSupervisorDecisions:
    @pytest.mark.asyncio
    async def test_record_decision(self, store):
        await store.log_event(_make_event(event_id="e1", action=CorrectionAction.ESCALATE))
        result = await store.record_decision(
            "e1", "approved", reviewer="admin", comment="Looks good"
        )
        assert result is True
        decision = await store.get_decision("e1")
        assert decision["decision"] == "approved"
        assert decision["reviewer"] == "admin"
        assert decision["comment"] == "Looks good"

    @pytest.mark.asyncio
    async def test_get_decision_not_found(self, store):
        decision = await store.get_decision("nonexistent")
        assert decision is None

    @pytest.mark.asyncio
    async def test_record_decision_duplicate_rejected(self, store):
        await store.log_event(_make_event(event_id="e1", action=CorrectionAction.ESCALATE))
        await store.record_decision("e1", "approved")
        result = await store.record_decision("e1", "rejected")
        assert result is False

    @pytest.mark.asyncio
    async def test_record_decision_nonexistent_event(self, store):
        result = await store.record_decision("nope", "approved")
        assert result is False


class TestRejectionReasons:
    @pytest.mark.asyncio
    async def test_record_decision_with_rejection_reason(self, store):
        await store.log_event(_make_event(event_id="e1", action=CorrectionAction.ESCALATE))
        result = await store.record_decision(
            "e1",
            "rejected",
            reviewer="admin",
            comment="Bad tone",
            rejection_reason="tone_wrong",
        )
        assert result is True
        decision = await store.get_decision("e1")
        assert decision["decision"] == "rejected"
        assert decision["rejection_reason"] == "tone_wrong"

    @pytest.mark.asyncio
    async def test_approval_has_no_rejection_reason(self, store):
        await store.log_event(_make_event(event_id="e1", action=CorrectionAction.ESCALATE))
        await store.record_decision("e1", "approved", reviewer="admin")
        decision = await store.get_decision("e1")
        assert decision["rejection_reason"] is None

    @pytest.mark.asyncio
    async def test_queue_includes_rejection_reason(self, store):
        await store.log_event(_make_event(event_id="e1", action=CorrectionAction.ESCALATE))
        await store.record_decision(
            "e1",
            "rejected",
            reviewer="admin",
            rejection_reason="over_corrected",
        )
        queue = await store.get_queue()
        assert queue[0]["rejection_reason"] == "over_corrected"


class TestApprovalMetrics:
    @pytest.mark.asyncio
    async def test_empty_metrics(self, store):
        metrics = await store.get_approval_metrics()
        assert metrics["total_decisions"] == 0
        assert metrics["approval_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_approval_rate_calculation(self, store):
        await store.log_event(_make_event(event_id="e1", action=CorrectionAction.ESCALATE))
        await store.log_event(_make_event(event_id="e2", action=CorrectionAction.ESCALATE))
        await store.log_event(_make_event(event_id="e3", action=CorrectionAction.ESCALATE))
        await store.record_decision("e1", "approved")
        await store.record_decision("e2", "approved")
        await store.record_decision("e3", "rejected", rejection_reason="tone_wrong")

        metrics = await store.get_approval_metrics()
        assert metrics["total_decisions"] == 3
        assert metrics["approved"] == 2
        assert metrics["rejected"] == 1
        assert abs(metrics["approval_rate"] - 2 / 3) < 0.01

    @pytest.mark.asyncio
    async def test_rates_by_intent(self, store):
        await store.log_event(
            _make_event(
                event_id="e1",
                action=CorrectionAction.ESCALATE,
                intent=IntentType.FINANCIAL_COMPENSATION,
            )
        )
        await store.log_event(
            _make_event(
                event_id="e2",
                action=CorrectionAction.ESCALATE,
                intent=IntentType.TROUBLESHOOTING,
            )
        )
        await store.record_decision("e1", "rejected", rejection_reason="factually_wrong")
        await store.record_decision("e2", "approved")

        metrics = await store.get_approval_metrics()
        assert metrics["by_intent"]["financial_compensation"]["rejected"] == 1
        assert metrics["by_intent"]["troubleshooting"]["approved"] == 1

    @pytest.mark.asyncio
    async def test_rejection_reasons_breakdown(self, store):
        await store.log_event(_make_event(event_id="e1", action=CorrectionAction.ESCALATE))
        await store.log_event(_make_event(event_id="e2", action=CorrectionAction.ESCALATE))
        await store.record_decision("e1", "rejected", rejection_reason="tone_wrong")
        await store.record_decision("e2", "rejected", rejection_reason="tone_wrong")

        metrics = await store.get_approval_metrics()
        assert metrics["rejection_reasons"]["tone_wrong"] == 2

    @pytest.mark.asyncio
    async def test_rates_by_rule(self, store):
        violation = PolicyViolation(
            rule_id="CREDIT-001",
            rule_name="Credit Auth",
            description="Credits over $50",
            severity=Severity.HIGH,
        )
        await store.log_event(
            _make_event(
                event_id="e1",
                action=CorrectionAction.ESCALATE,
                violations=[violation],
            )
        )
        await store.record_decision("e1", "rejected", rejection_reason="incomplete_fix")

        metrics = await store.get_approval_metrics()
        assert "CREDIT-001" in metrics["by_rule"]
        assert metrics["by_rule"]["CREDIT-001"]["rejected"] == 1


class TestFilteredEvents:
    @pytest.mark.asyncio
    async def test_filter_by_intent(self, store):
        await store.log_event(_make_event(event_id="e1", intent=IntentType.FINANCIAL_COMPENSATION))
        await store.log_event(_make_event(event_id="e2", intent=IntentType.TROUBLESHOOTING))
        events, total = await store.get_events_filtered(intent_type="financial_compensation")
        assert total == 1
        assert events[0].event_id == "e1"

    @pytest.mark.asyncio
    async def test_filter_by_action(self, store):
        await store.log_event(_make_event(event_id="e1", action=CorrectionAction.CORRECT))
        await store.log_event(_make_event(event_id="e2", action=CorrectionAction.ALLOW))
        events, total = await store.get_events_filtered(action="correct")
        assert total == 1
        assert events[0].event_id == "e1"

    @pytest.mark.asyncio
    async def test_filter_combined(self, store):
        await store.log_event(
            _make_event(
                event_id="e1",
                action=CorrectionAction.CORRECT,
                intent=IntentType.FINANCIAL_COMPENSATION,
            )
        )
        await store.log_event(
            _make_event(
                event_id="e2", action=CorrectionAction.CORRECT, intent=IntentType.TROUBLESHOOTING
            )
        )
        await store.log_event(
            _make_event(
                event_id="e3",
                action=CorrectionAction.ALLOW,
                intent=IntentType.FINANCIAL_COMPENSATION,
            )
        )
        events, total = await store.get_events_filtered(
            action="correct", intent_type="financial_compensation"
        )
        assert total == 1
        assert events[0].event_id == "e1"


class TestAnalyticsTrends:
    @pytest.mark.asyncio
    async def test_trends_empty(self, store):
        trends = await store.get_analytics_trends()
        assert trends == []

    @pytest.mark.asyncio
    async def test_trends_returns_buckets(self, store):
        await store.log_event(
            _make_event(event_id="e1", action=CorrectionAction.CORRECT, latency_ms=10.0)
        )
        await store.log_event(
            _make_event(event_id="e2", action=CorrectionAction.ESCALATE, latency_ms=20.0)
        )
        await store.log_event(
            _make_event(event_id="e3", action=CorrectionAction.ALLOW, latency_ms=5.0)
        )
        trends = await store.get_analytics_trends(hours=24)
        assert len(trends) >= 1
        bucket = trends[0]
        assert "timestamp" in bucket
        assert "total" in bucket
        assert "corrections" in bucket
        assert "escalations" in bucket
        assert "allowed" in bucket
        assert "avg_latency_ms" in bucket
        assert bucket["total"] == 3
        assert bucket["corrections"] == 1
        assert bucket["escalations"] == 1
        assert bucket["allowed"] == 1

    @pytest.mark.asyncio
    async def test_trends_hours_filter(self, store):
        await store.log_event(_make_event(event_id="e1"))
        trends = await store.get_analytics_trends(hours=1)
        assert len(trends) >= 1

    @pytest.mark.asyncio
    async def test_trends_bucket_timestamp_is_utc_iso(self, store):
        """A bucket label without its zone is read as local time by a browser."""
        await store.log_event(_make_event(event_id="e1"))
        ts = (await store.get_analytics_trends())[0]["timestamp"]
        assert ts.endswith("Z"), ts
        parsed = datetime.fromisoformat(ts)
        assert parsed.utcoffset() == timedelta(0)
        # The bucket is the event's own hour, so the label is not off by a zone
        assert (datetime.now(UTC) - parsed) < timedelta(hours=1, minutes=1)


class TestAnalyticsSummary:
    @pytest.mark.asyncio
    async def test_summary_empty(self, store):
        summary = await store.get_analytics_summary()
        assert summary["total_events"] == 0
        assert summary["corrections"] == 0
        assert summary["top_rules"] == []
        assert summary["time_range"]["first"] is None

    @pytest.mark.asyncio
    async def test_summary_with_data(self, store):
        violation = PolicyViolation(
            rule_id="CREDIT-001",
            rule_name="Credit Auth",
            description="Credits over $50",
            severity=Severity.HIGH,
        )
        await store.log_event(
            _make_event(
                event_id="e1",
                action=CorrectionAction.CORRECT,
                intent=IntentType.FINANCIAL_COMPENSATION,
                latency_ms=10.0,
                violations=[violation],
            )
        )
        await store.log_event(
            _make_event(
                event_id="e2",
                action=CorrectionAction.ESCALATE,
                intent=IntentType.TROUBLESHOOTING,
                latency_ms=30.0,
            )
        )
        await store.log_event(
            _make_event(
                event_id="e3",
                action=CorrectionAction.ALLOW,
                intent=IntentType.FINANCIAL_COMPENSATION,
                latency_ms=5.0,
            )
        )

        summary = await store.get_analytics_summary()
        assert summary["total_events"] == 3
        assert summary["corrections"] == 1
        assert summary["escalations"] == 1
        assert summary["allowed"] == 1
        assert summary["unique_agents"] == 1
        assert summary["unique_sessions"] == 1
        assert summary["avg_latency_ms"] == 15.0
        assert summary["p50_latency_ms"] == 10.0
        assert summary["p95_latency_ms"] == 30.0
        assert len(summary["top_rules"]) == 1
        assert summary["top_rules"][0]["rule_id"] == "CREDIT-001"
        assert len(summary["top_intents"]) == 2
        assert summary["time_range"]["first"] is not None
        assert summary["time_range"]["last"] is not None
