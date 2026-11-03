"""Dashboard REST API and WebSocket server.

Provides:
    - WebSocket endpoint for real-time correction event streaming
    - REST endpoints for metrics, rules, queue, and agent feed
    - Demo mode: simulates live agent activity for presentations
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from autocorrector.audit.store import AuditStore
from autocorrector.config import get_settings
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.logging_config import request_id_var, setup_logging
from autocorrector.models import (
    CorrectionAction,
    CorrectionEvent,
    DetectedIntent,
    IntentType,
)
from autocorrector.orchestrator.pipeline import run_autocorrection

logger = logging.getLogger(__name__)

setup_logging()

app = FastAPI(title="Autocorrector Dashboard API", version="1.0.0")

_settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request with method/path/status/latency and manages correlation IDs."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        start = time.monotonic()
        try:
            response = await call_next(request)
            latency_ms = (time.monotonic() - start) * 1000
            logger.info(
                "%s %s %s %.1fms",
                request.method,
                request.url.path,
                response.status_code,
                latency_ms,
            )
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)


app.add_middleware(RequestLoggingMiddleware)

class DashboardAuthMiddleware(BaseHTTPMiddleware):
    """API key authentication for sensitive dashboard endpoints.

    Protects approve/reject, export, and demo endpoints.
    Empty key = dev mode (no auth), matching MCP pattern.
    """

    def __init__(self, app, api_key: str = ""):
        super().__init__(app)
        self.api_key = api_key

    def _is_protected(self, path: str) -> bool:
        if path.startswith("/api/queue/") and (
            path.endswith("/approve") or path.endswith("/reject")
        ):
            return True
        if path == "/api/events/export" or path.startswith("/api/events/export?"):
            return True
        return path.startswith("/api/demo/")

    async def dispatch(self, request: Request, call_next):
        if not self.api_key:
            return await call_next(request)

        if not self._is_protected(request.url.path):
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if token == self.api_key:
                return await call_next(request)

        api_key_header = request.headers.get("x-api-key", "")
        if api_key_header == self.api_key:
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )


if _settings.dashboard_api_key:
    app.add_middleware(DashboardAuthMiddleware, api_key=_settings.dashboard_api_key)

limiter = Limiter(key_func=get_remote_address, default_limits=[_settings.rate_limit_default])
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )


# --- In-memory state ---

_connections: list[WebSocket] = []
_event_log: list[dict[str, Any]] = []
_kg_store: KnowledgeGraphStore | None = None
_audit_store: AuditStore | None = None
_audit_store_lock = asyncio.Lock()

MAX_EVENT_LOG = 500


# --- Models ---


class CorrectionEventOut(BaseModel):
    event_id: str
    timestamp: str
    agent_id: str
    session_id: str
    original_text: str
    corrected_text: str
    was_modified: bool
    action: str
    intent_type: str
    confidence: float
    violations: list[dict[str, Any]] = Field(default_factory=list)
    pii_detected: bool = False
    latency_ms: float = 0.0
    severity: str = "low"


class DashboardMetrics(BaseModel):
    total_events: int
    corrections_made: int
    escalations: int
    allowed_through: int
    pii_detected: int
    correction_rate: float
    avg_latency_ms: float
    avg_confidence: float
    violations_by_rule: dict[str, int]
    events_by_intent: dict[str, int]
    uptime_seconds: float


class SupervisorQueueItem(BaseModel):
    event_id: str
    timestamp: str
    agent_id: str
    original_text: str
    action: str
    severity: str
    violations: list[dict[str, Any]]
    status: str = "pending_review"
    decision: str | None = None
    reviewer: str | None = None
    comment: str | None = None
    rejection_reason: str | None = None
    decided_at: str | None = None


class SupervisorDecisionRequest(BaseModel):
    reviewer: str = ""
    comment: str = ""
    rejection_reason: str | None = None


# --- Startup ---

_start_time = time.monotonic()


def get_kg_store() -> KnowledgeGraphStore:
    global _kg_store
    if _kg_store is None:
        _kg_store = KnowledgeGraphStore()
    return _kg_store


async def get_audit_store() -> AuditStore:
    global _audit_store
    async with _audit_store_lock:
        if _audit_store is None:
            settings = get_settings()
            _audit_store = AuditStore(db_path=settings.audit_db_path)
            await _audit_store.connect()
    return _audit_store


# --- Health Check ---


@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers and Docker."""
    from autocorrector import __version__

    kg = get_kg_store()
    audit = await get_audit_store()
    event_count = await audit.count()
    return {
        "status": "healthy",
        "version": __version__,
        "neo4j_connected": kg.is_connected,
        "uptime_seconds": round(time.monotonic() - _start_time, 1),
        "event_count": event_count,
        "websocket_connections": len(_connections),
        "log_level": _settings.log_level,
    }


# --- WebSocket ---


async def broadcast(event: dict[str, Any]) -> None:
    """Send an event to all connected WebSocket clients."""
    message = json.dumps(event, default=str)
    disconnected = []
    for ws in _connections:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        _connections.remove(ws)


MAX_CONNECTIONS = 50


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if len(_connections) >= MAX_CONNECTIONS:
        await websocket.close(code=1013, reason="Max connections reached")
        return

    await websocket.accept()
    _connections.append(websocket)
    logger.info(f"WebSocket client connected ({len(_connections)} total)")

    try:
        # Send recent events on connect
        for event in _event_log[-10:]:
            await websocket.send_text(
                json.dumps({"type": "correction_event", "data": event}, default=str)
            )
            await asyncio.sleep(0.05)

        # Keep connection alive — respond to client pings
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _connections:
            _connections.remove(websocket)
        logger.info(f"WebSocket client disconnected ({len(_connections)} total)")


# --- REST Endpoints ---


@app.get("/api/metrics", response_model=DashboardMetrics)
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


@app.get("/api/events")
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


@app.get("/api/events/export")
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


@app.get("/api/queue")
@limiter.limit("60/minute")
async def get_supervisor_queue(request: Request):
    """Get items needing supervisor review (escalations + critical corrections)."""
    audit = await get_audit_store()
    rows = await audit.get_queue(limit=50, offset=0)
    queue = []
    for row in rows:
        e = row["event"]
        decision = row.get("decision")
        status = decision if decision else "pending_review"
        queue.append(
            SupervisorQueueItem(
                event_id=e.event_id,
                timestamp=e.timestamp.isoformat(),
                agent_id=e.agent_id or "unknown",
                original_text=e.original_text,
                action=e.action_taken.value,
                severity=max(
                    (v.severity.value for v in e.violations),
                    default="low",
                )
                if e.violations
                else "low",
                violations=[v.model_dump(mode="json") for v in e.violations],
                status=status,
                decision=row.get("decision"),
                reviewer=row.get("reviewer"),
                comment=row.get("comment"),
                rejection_reason=row.get("rejection_reason"),
                decided_at=row.get("decided_at"),
            )
        )
    return {"queue": queue, "total": len(queue)}


@app.post("/api/queue/{event_id}/approve")
@limiter.limit("30/minute")
async def approve_event(event_id: str, body: SupervisorDecisionRequest, request: Request):
    """Approve a correction event."""
    audit = await get_audit_store()

    # Check if event exists
    if not await audit.event_exists(event_id):
        return JSONResponse(status_code=404, content={"detail": "Event not found"})

    # Check if already decided
    existing = await audit.get_decision(event_id)
    if existing:
        return JSONResponse(status_code=409, content={"detail": "Already decided"})

    await audit.record_decision(
        event_id=event_id,
        decision="approved",
        reviewer=body.reviewer,
        comment=body.comment,
    )

    # ACE: bump helpful_count on matching strategies
    try:
        from autocorrector.ace.curator import on_approval

        event_data = await audit.get_event_by_id(event_id)
        intent = event_data.intent.intent_type.value if event_data else None
        await on_approval(audit, event_id, intent_type=intent)
    except Exception as e:
        logger.warning("ACE approval hook failed (non-fatal): %s", e)

    msg = {"type": "queue_decision", "data": {"event_id": event_id, "decision": "approved"}}
    await broadcast(msg)
    return {"event_id": event_id, "decision": "approved"}


@app.post("/api/queue/{event_id}/reject")
@limiter.limit("30/minute")
async def reject_event(event_id: str, body: SupervisorDecisionRequest, request: Request):
    """Reject a correction event."""
    audit = await get_audit_store()

    if not await audit.event_exists(event_id):
        return JSONResponse(status_code=404, content={"detail": "Event not found"})

    existing = await audit.get_decision(event_id)
    if existing:
        return JSONResponse(status_code=409, content={"detail": "Already decided"})

    await audit.record_decision(
        event_id=event_id,
        decision="rejected",
        reviewer=body.reviewer,
        comment=body.comment,
        rejection_reason=body.rejection_reason,
    )

    # ACE: extract lesson from rejection via reflector → curator
    try:
        from autocorrector.ace.curator import curate_strategy
        from autocorrector.ace.reflector import extract_lesson

        event_data = await audit.get_event_by_id(event_id)
        if event_data:
            violations_data = [v.model_dump(mode="json") for v in event_data.violations]
            kg = get_kg_store()
            client = kg._get_anthropic_client()
            lesson = await extract_lesson(
                anthropic_client=client,
                model=kg._correction_llm,
                original_text=event_data.original_text,
                corrected_text=event_data.corrected_text,
                violations=violations_data,
                rejection_reason=body.rejection_reason,
                reviewer_comment=body.comment,
            )
            if lesson:
                rule_ids = [v.get("rule_id") for v in violations_data if v.get("rule_id")]
                await curate_strategy(
                    audit_store=audit,
                    lesson=lesson,
                    rule_id=rule_ids[0] if rule_ids else None,
                    intent_type=event_data.intent.intent_type.value,
                    source_event_id=event_id,
                )
    except Exception as e:
        logger.warning("ACE rejection hook failed (non-fatal): %s", e)

    await broadcast(
        {
            "type": "queue_decision",
            "data": {
                "event_id": event_id,
                "decision": "rejected",
                "rejection_reason": body.rejection_reason,
            },
        }
    )
    return {"event_id": event_id, "decision": "rejected", "rejection_reason": body.rejection_reason}


@app.get("/api/rules")
@limiter.limit("60/minute")
async def get_rules(request: Request):
    """Get active policy rules with violation counts."""
    kg = get_kg_store()
    audit = await get_audit_store()
    all_events = await audit.get_events(limit=10000, offset=0)
    rules_data = []

    for intent_type, rules in kg._policy_rules.items():
        for rule in rules:
            violation_count = sum(
                1 for e in all_events for v in e.violations if v.rule_id == rule["rule_id"]
            )
            rules_data.append(
                {
                    "rule_id": rule["rule_id"],
                    "name": rule["rule_name"],
                    "description": rule["description"],
                    "severity": rule["severity"],
                    "intent_type": intent_type,
                    "violation_count": violation_count,
                    "source_document": rule.get("source_document"),
                    "source_section": rule.get("source_section"),
                }
            )

    return {"rules": rules_data}


@app.get("/api/analytics/trends")
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


@app.get("/api/analytics/summary")
@limiter.limit("60/minute")
async def get_analytics_summary(request: Request):
    """Get executive summary with ROI calculations."""
    audit = await get_audit_store()
    settings = get_settings()
    summary = await audit.get_analytics_summary()

    corrections = summary["corrections"]
    escalations = summary["escalations"]
    estimated_savings = (
        corrections * settings.cost_per_violation
        + escalations * settings.cost_per_escalation
    )

    return {
        **summary,
        "violations_prevented": corrections,
        "estimated_savings": round(estimated_savings, 2),
        "cost_per_violation": settings.cost_per_violation,
        "cost_per_escalation": settings.cost_per_escalation,
    }


@app.get("/api/metrics/approval-rates")
@limiter.limit("60/minute")
async def get_approval_rates(request: Request):
    """Get approval/rejection rates by intent type and rule for ACE feedback loop."""
    audit = await get_audit_store()
    return await audit.get_approval_metrics()


@app.get("/api/ace/strategies")
@limiter.limit("60/minute")
async def get_ace_strategies(request: Request):
    """Get all ACE correction strategies with scores."""
    audit = await get_audit_store()
    strategies = await audit.get_all_strategies()
    return {"strategies": strategies, "total": len(strategies)}


# --- Process and log a correction ---


async def process_and_log(
    text: str,
    customer_context: dict[str, Any],
    agent_id: str = "agent-1",
    session_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run text through the pipeline, log the event, and broadcast to clients."""
    kg = get_kg_store()
    await kg.connect()

    audit = await get_audit_store()

    start = time.monotonic()
    result = await run_autocorrection(
        text=text,
        customer_context=customer_context,
        kg_store=kg,
        request_id=request_id or request_id_var.get(""),
        audit_store=audit,
    )
    latency = (time.monotonic() - start) * 1000

    event = CorrectionEventOut(
        event_id=str(uuid.uuid4())[:8],
        timestamp=datetime.now(UTC).isoformat(),
        agent_id=agent_id,
        session_id=session_id or str(uuid.uuid4())[:8],
        original_text=text,
        corrected_text=result.corrected_text,
        was_modified=result.was_modified,
        action=result.action_taken.value,
        intent_type=result.intent.intent_type.value,
        confidence=result.confidence,
        violations=[v.model_dump() for v in result.validation.violations],
        pii_detected=result.pii_detected,
        latency_ms=latency,
        severity=result.validation.severity.value,
    )

    event_dict = event.model_dump()
    _event_log.append(event_dict)
    if len(_event_log) > MAX_EVENT_LOG:
        _event_log.pop(0)

    # Persist to audit store
    audit = await get_audit_store()
    audit_event = CorrectionEvent(
        event_id=event.event_id,
        agent_id=event.agent_id,
        session_id=event.session_id,
        original_text=event.original_text,
        corrected_text=event.corrected_text,
        was_modified=event.was_modified,
        action_taken=CorrectionAction(event.action),
        intent=DetectedIntent(
            intent_type=IntentType(event.intent_type),
            confidence=event.confidence,
        ),
        violations=result.validation.violations,
        confidence=event.confidence,
        latency_ms=event.latency_ms,
    )
    await audit.log_event(audit_event)

    await broadcast({"type": "correction_event", "data": event_dict})
    return event_dict


# --- Demo simulation ---

DEMO_SCENARIOS = [
    {
        "text": "I can offer you a $200 credit for your trouble.",
        "context": {"account_tenure_days": 30},
        "agent_id": "agent-sarah",
    },
    {
        "text": "Your emergency dental coverage is $5,000 under the Standard plan.",
        "context": {"plan_type": "standard_2024", "account_tenure_days": 90},
        "agent_id": "agent-mike",
    },
    {
        "text": "I'll apply a $750 credit to your account right away.",
        "context": {"account_tenure_days": 120},
        "agent_id": "agent-lisa",
    },
    {
        "text": "Your SSN 123-45-6789 has been verified. Your account is fine.",
        "context": {"account_tenure_days": 90},
        "agent_id": "agent-james",
    },
    {
        "text": "Let me check the status of your modem. Can you try restarting it?",
        "context": {"account_tenure_days": 60},
        "agent_id": "agent-sarah",
    },
    {
        "text": "I can offer you a $30 credit for the inconvenience.",
        "context": {"account_tenure_days": 30},
        "agent_id": "agent-mike",
    },
    {
        "text": "Your emergency room coverage is $15,000 under the Standard plan.",
        "context": {"plan_type": "standard_2024", "account_tenure_days": 90},
        "agent_id": "agent-lisa",
    },
    {
        "text": "I can offer you a $1,000 credit as compensation for the outage.",
        "context": {"account_tenure_days": 200},
        "agent_id": "agent-james",
    },
    {
        "text": "Thank you for contacting us. How can I help you today?",
        "context": {"account_tenure_days": 45},
        "agent_id": "agent-sarah",
    },
    {
        "text": "I've sent the confirmation to john.doe@example.com. Is there anything else?",
        "context": {"account_tenure_days": 90},
        "agent_id": "agent-mike",
    },
]

_demo_running = False


@app.post("/api/demo/start")
@limiter.limit("5/minute")
async def start_demo(request: Request, interval: float = 3.0):
    """Start the demo simulation — sends a correction event every `interval` seconds."""
    global _demo_running
    if _demo_running:
        return {"status": "already_running"}

    _demo_running = True
    asyncio.create_task(_run_demo(interval))
    return {"status": "started", "interval": interval, "scenarios": len(DEMO_SCENARIOS)}


@app.post("/api/demo/stop")
async def stop_demo():
    """Stop the demo simulation."""
    global _demo_running
    _demo_running = False
    return {"status": "stopped"}


@app.post("/api/demo/step")
async def demo_step(index: int = 0):
    """Run a single demo scenario by index."""
    scenario = DEMO_SCENARIOS[index % len(DEMO_SCENARIOS)]
    event = await process_and_log(
        text=scenario["text"],
        customer_context=scenario["context"],
        agent_id=scenario["agent_id"],
    )
    return event


async def _run_demo(interval: float) -> None:
    """Background task that cycles through demo scenarios."""
    global _demo_running
    idx = 0
    while _demo_running:
        scenario = DEMO_SCENARIOS[idx % len(DEMO_SCENARIOS)]
        try:
            await process_and_log(
                text=scenario["text"],
                customer_context=scenario["context"],
                agent_id=scenario["agent_id"],
            )
        except Exception as e:
            logger.error(f"Demo scenario error: {e}")
        idx += 1
        await asyncio.sleep(interval)
