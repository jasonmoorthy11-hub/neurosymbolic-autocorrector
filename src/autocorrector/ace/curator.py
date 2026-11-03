"""ACE Curator — manages the correction strategies lifecycle."""

from __future__ import annotations

import logging
import uuid

from autocorrector.audit.store import AuditStore

logger = logging.getLogger(__name__)


async def curate_strategy(
    audit_store: AuditStore,
    lesson: str,
    rule_id: str | None = None,
    intent_type: str | None = None,
    source_event_id: str | None = None,
) -> dict[str, str]:
    """Add a new strategy or merge into an existing one if similar.

    Returns {"action": "created"|"merged", "strategy_id": "..."}.
    """
    existing = await audit_store.find_similar_strategy(lesson, rule_id=rule_id)

    if existing:
        sid = existing["strategy_id"]
        await audit_store.increment_strategy_harmful(sid)
        logger.info("Merged into existing strategy %s", sid)
        return {"action": "merged", "strategy_id": sid}

    sid = str(uuid.uuid4())[:12]
    await audit_store.add_strategy(
        strategy_id=sid,
        lesson=lesson,
        rule_id=rule_id,
        intent_type=intent_type,
        source_event_id=source_event_id,
    )
    logger.info("Created new strategy %s", sid)
    return {"action": "created", "strategy_id": sid}


async def on_approval(
    audit_store: AuditStore, event_id: str, intent_type: str | None = None
) -> None:
    """Bump helpful_count on top strategies that match the approved event's intent."""
    strategies = await audit_store.get_top_strategies(intent_type=intent_type, limit=5)
    for s in strategies:
        if s["score"] >= 0:
            await audit_store.increment_strategy_helpful(s["strategy_id"])
