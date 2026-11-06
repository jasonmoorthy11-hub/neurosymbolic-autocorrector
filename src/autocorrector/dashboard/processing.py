"""Run one agent reply through the pipeline, log it and broadcast it."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from autocorrector.dashboard.models import CorrectionEventOut
from autocorrector.dashboard.state import (
    MAX_EVENT_LOG,
    _event_log,
    broadcast,
    get_audit_store,
    get_kg_store,
)
from autocorrector.guardrails.validators import find_pii_spans, redact_spans
from autocorrector.logging_config import request_id_var
from autocorrector.models import (
    CorrectionAction,
    CorrectionEvent,
    DetectedIntent,
    IntentType,
)
from autocorrector.orchestrator.pipeline import run_autocorrection


async def process_and_log(
    text: str,
    customer_context: dict[str, Any],
    agent_id: str = "agent-1",
    session_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run text through the pipeline, log the event, and broadcast to clients."""
    # Connected once by the API lifespan (a per-event connect re-dialled a down
    # server on every reply); the in-memory rules need no connection.
    kg = get_kg_store()

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

    # The event row is append-only and served unauthenticated: keep raw PII out
    # of it by storing the original as the PII rail redacts it. Not gated on the
    # pipeline's flag: that is computed on the translated text, this is the input.
    original = redact_spans(text, find_pii_spans(text))

    event = CorrectionEventOut(
        event_id=str(uuid.uuid4())[:8],
        timestamp=datetime.now(UTC).isoformat(),
        agent_id=agent_id,
        session_id=session_id or str(uuid.uuid4())[:8],
        original_text=original,
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
