"""Response and request models of the dashboard API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
