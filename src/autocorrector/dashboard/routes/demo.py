"""Demo mode: cycles scripted agent replies through the pipeline for presentations."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, Request

from autocorrector.dashboard.processing import process_and_log
from autocorrector.dashboard.state import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

DEMO_SCENARIOS = [
    {
        "title": "Dashboard demo: $200 credit, 30-day account",
        "text": "I can offer you a $200 credit for your trouble.",
        "context": {"account_tenure_days": 30},
        "agent_id": "agent-sarah",
    },
    {
        "title": "Dashboard demo: Dental coverage quote",
        "text": "Your emergency dental coverage is $5,000 under the Standard plan.",
        "context": {"plan_type": "standard_2024", "account_tenure_days": 90},
        "agent_id": "agent-mike",
    },
    {
        "title": "Dashboard demo: $750 credit",
        "text": "I'll apply a $750 credit to your account right away.",
        "context": {"account_tenure_days": 120},
        "agent_id": "agent-lisa",
    },
    {
        "title": "Dashboard demo: SSN in a reply",
        "text": "Your SSN 123-45-6789 has been verified. Your account is fine.",
        "context": {"account_tenure_days": 90},
        "agent_id": "agent-james",
    },
    {
        "title": "Dashboard demo: Modem troubleshooting",
        "text": "Let me check the status of your modem. Can you try restarting it?",
        "context": {"account_tenure_days": 60},
        "agent_id": "agent-sarah",
    },
    {
        "title": "Dashboard demo: $30 credit",
        "text": "I can offer you a $30 credit for the inconvenience.",
        "context": {"account_tenure_days": 30},
        "agent_id": "agent-mike",
    },
    {
        "title": "Dashboard demo: Emergency room coverage quote",
        "text": "Your emergency room coverage is $15,000 under the Standard plan.",
        "context": {"plan_type": "standard_2024", "account_tenure_days": 90},
        "agent_id": "agent-lisa",
    },
    {
        "title": "Dashboard demo: $1,000 outage credit",
        "text": "I can offer you a $1,000 credit as compensation for the outage.",
        "context": {"account_tenure_days": 200},
        "agent_id": "agent-james",
    },
    {
        "title": "Dashboard demo: Greeting",
        "text": "Thank you for contacting us. How can I help you today?",
        "context": {"account_tenure_days": 45},
        "agent_id": "agent-sarah",
    },
    {
        "title": "Dashboard demo: Email address in a reply",
        "text": "I've sent the confirmation to john.doe@example.com. Is there anything else?",
        "context": {"account_tenure_days": 90},
        "agent_id": "agent-mike",
    },
]

_demo_running = False

# Each tick runs the pipeline and writes an audit row; an interval of 0 would spin
MIN_INTERVAL_SECONDS = 0.5
MAX_INTERVAL_SECONDS = 60.0


@router.post("/api/demo/start")
@limiter.limit("5/minute")
async def start_demo(
    request: Request,
    interval: float = Query(default=3.0, ge=MIN_INTERVAL_SECONDS, le=MAX_INTERVAL_SECONDS),
):
    """Start the demo simulation — sends a correction event every `interval` seconds."""
    global _demo_running
    if _demo_running:
        return {"status": "already_running"}

    _demo_running = True
    asyncio.create_task(_run_demo(interval))
    return {"status": "started", "interval": interval, "scenarios": len(DEMO_SCENARIOS)}


@router.post("/api/demo/stop")
@limiter.limit("30/minute")
async def stop_demo(request: Request):
    """Stop the demo simulation."""
    global _demo_running
    _demo_running = False
    return {"status": "stopped"}


@router.post("/api/demo/step")
@limiter.limit("30/minute")
async def demo_step(request: Request, index: int = 0):
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
