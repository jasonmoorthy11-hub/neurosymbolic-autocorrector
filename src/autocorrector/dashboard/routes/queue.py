"""The supervisor queue and its approve/reject decisions (with the ACE hooks)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from autocorrector.dashboard.models import SupervisorDecisionRequest, SupervisorQueueItem
from autocorrector.dashboard.state import broadcast, get_audit_store, get_kg_store, limiter
from autocorrector.models import Severity

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/queue")
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
                # Rank order (Severity is declared low -> critical), not string order
                severity=max(
                    (v.severity for v in e.violations),
                    key=list(Severity).index,
                    default=Severity.LOW,
                ).value,
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


@router.post("/api/queue/{event_id}/approve")
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


@router.post("/api/queue/{event_id}/reject")
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
