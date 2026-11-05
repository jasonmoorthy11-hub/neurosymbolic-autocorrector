"""Metrics, the event feed and its export, the rule catalogue and analytics."""

from __future__ import annotations

import csv
import io
import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from autocorrector.config import get_settings
from autocorrector.dashboard.models import DashboardMetrics
from autocorrector.dashboard.state import (
    _event_log,
    _start_time,
    get_audit_store,
    get_kg_store,
    limiter,
)
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import CorrectionAction

router = APIRouter()


@router.get("/api/metrics", response_model=DashboardMetrics)
@limiter.limit("60/minute")
async def get_metrics(request: Request):
    """Get aggregated dashboard metrics from the audit store."""
    audit = await get_audit_store()
    all_events = await audit.get_events(limit=10000, offset=0)

    total = len(all_events)
    corrections = sum(1 for e in all_events if e.action_taken == CorrectionAction.CORRECT)
    escalations = sum(1 for e in all_events if e.action_taken == CorrectionAction.ESCALATE)
    allowed = sum(1 for e in all_events if e.action_taken == CorrectionAction.ALLOW)

    # PII count comes from in-memory log (not stored in audit DB)
    pii = sum(1 for e in _event_log if e.get("pii_detected"))

    latencies = [e.latency_ms for e in all_events if e.latency_ms]
    confidences = [e.confidence for e in all_events if e.confidence]

    violations_by_rule: dict[str, int] = {}
    events_by_intent: dict[str, int] = {}
    for e in all_events:
        for v in e.violations:
            violations_by_rule[v.rule_id] = violations_by_rule.get(v.rule_id, 0) + 1
        intent = e.intent.intent_type.value
        events_by_intent[intent] = events_by_intent.get(intent, 0) + 1

    return DashboardMetrics(
        total_events=total,
        corrections_made=corrections,
        escalations=escalations,
        allowed_through=allowed,
        pii_detected=pii,
        correction_rate=corrections / total if total > 0 else 0.0,
        avg_latency_ms=sum(latencies) / len(latencies) if latencies else 0.0,
        avg_confidence=sum(confidences) / len(confidences) if confidences else 0.0,
        violations_by_rule=violations_by_rule,
        events_by_intent=events_by_intent,
        uptime_seconds=time.monotonic() - _start_time,
    )


@router.get("/api/events")
@limiter.limit("60/minute")
async def get_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    intent_type: str | None = Query(default=None),
    action: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
):
    """Get recent correction events with optional filters."""
    audit = await get_audit_store()

    has_filters = any([date_from, date_to, intent_type, action, severity, agent_id])
    if has_filters:
        events, total = await audit.get_events_filtered(
            limit=limit,
            offset=offset,
            date_from=date_from,
            date_to=date_to,
            intent_type=intent_type,
            action=action,
            severity=severity,
            agent_id=agent_id,
        )
    else:
        events = await audit.get_events(limit=limit, offset=offset)
        total = await audit.count()

    return {
        "events": [e.model_dump(mode="json") for e in events],
        "total": total,
    }


@router.get("/api/events/export")
@limiter.limit("10/minute")
async def export_events(
    request: Request,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    intent_type: str | None = Query(default=None),
    action: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
):
    """Export filtered events as CSV or JSON."""
    audit = await get_audit_store()
    events, _ = await audit.get_events_filtered(
        limit=10000,
        offset=0,
        date_from=date_from,
        date_to=date_to,
        intent_type=intent_type,
        action=action,
        severity=severity,
        agent_id=agent_id,
    )

    if format == "json":
        data = [e.model_dump(mode="json") for e in events]
        return JSONResponse(
            content=data,
            headers={"Content-Disposition": "attachment; filename=events.json"},
        )

    # CSV export
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "event_id",
            "timestamp",
            "agent_id",
            "session_id",
            "original_text",
            "corrected_text",
            "was_modified",
            "action_taken",
            "intent_type",
            "confidence",
            "latency_ms",
        ]
    )
    for e in events:
        writer.writerow(
            [
                e.event_id,
                e.timestamp.isoformat(),
                e.agent_id,
                e.session_id,
                e.original_text,
                e.corrected_text,
                e.was_modified,
                e.action_taken.value,
                e.intent.intent_type.value,
                e.confidence,
                e.latency_ms,
            ]
        )

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=events.csv"},
    )


@router.get("/api/rules")
@limiter.limit("60/minute")
async def get_rules(request: Request):
    """Get active policy rules with violation counts."""
    audit = await get_audit_store()
    all_events = await audit.get_events(limit=10000, offset=0)
    rules_data = [
        {
            **rule,
            "violation_count": sum(
                1 for e in all_events for v in e.violations if v.rule_id == rule["rule_id"]
            ),
        }
        for rule in rule_catalog(get_kg_store())
    ]

    return {"rules": rules_data}


def rule_catalog(kg: KnowledgeGraphStore) -> list[dict[str, Any]]:
    """Active policy rules as the dashboard lists them (also recorded for the replay demo)."""
    return [
        {
            "rule_id": rule["rule_id"],
            "name": rule["rule_name"],
            "description": rule["description"],
            "severity": rule["severity"],
            "intent_type": intent_type,
            "source_document": rule.get("source_document"),
            "source_section": rule.get("source_section"),
        }
        for intent_type, rules in kg._policy_rules.items()
        for rule in rules
    ]


@router.get("/api/analytics/trends")
@limiter.limit("60/minute")
async def get_analytics_trends(
    request: Request,
    hours: int = Query(default=24, ge=1, le=720),
    bucket_minutes: int = Query(default=60, ge=1, le=1440),
):
    """Get time-series trend data for analytics charts."""
    audit = await get_audit_store()
    settings = get_settings()
    trends = await audit.get_analytics_trends(hours=hours, bucket_minutes=bucket_minutes)
    return {
        "trends": trends,
        "cost_per_violation": settings.cost_per_violation,
        "cost_per_escalation": settings.cost_per_escalation,
    }


@router.get("/api/analytics/summary")
@limiter.limit("60/minute")
async def get_analytics_summary(request: Request):
    """Get executive summary with ROI calculations."""
    audit = await get_audit_store()
    settings = get_settings()
    summary = await audit.get_analytics_summary()

    corrections = summary["corrections"]
    escalations = summary["escalations"]
    estimated_savings = (
        corrections * settings.cost_per_violation + escalations * settings.cost_per_escalation
    )

    return {
        **summary,
        "violations_prevented": corrections,
        "estimated_savings": round(estimated_savings, 2),
        "cost_per_violation": settings.cost_per_violation,
        "cost_per_escalation": settings.cost_per_escalation,
    }


@router.get("/api/metrics/approval-rates")
@limiter.limit("60/minute")
async def get_approval_rates(request: Request):
    """Get approval/rejection rates by intent type and rule for ACE feedback loop."""
    audit = await get_audit_store()
    return await audit.get_approval_metrics()


@router.get("/api/ace/strategies")
@limiter.limit("60/minute")
async def get_ace_strategies(request: Request):
    """Get all ACE correction strategies with scores."""
    audit = await get_audit_store()
    strategies = await audit.get_all_strategies()
    return {"strategies": strategies, "total": len(strategies)}
