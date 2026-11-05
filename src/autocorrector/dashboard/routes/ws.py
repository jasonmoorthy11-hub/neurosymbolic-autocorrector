"""WebSocket stream of correction events."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from autocorrector.dashboard.state import MAX_CONNECTIONS, _connections, _event_log

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
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
