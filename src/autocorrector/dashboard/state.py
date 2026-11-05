"""Process-wide dashboard state shared by the routers: stores, the in-memory
event log, the WebSocket connections and the rate limiter."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket
from slowapi import Limiter
from slowapi.util import get_remote_address

from autocorrector.audit.store import AuditStore
from autocorrector.config import get_settings
from autocorrector.knowledge_graph.store import KnowledgeGraphStore

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address, default_limits=[get_settings().rate_limit_default])

_connections: list[WebSocket] = []
_event_log: list[dict[str, Any]] = []
_kg_store: KnowledgeGraphStore | None = None
_audit_store: AuditStore | None = None
_audit_store_lock = asyncio.Lock()

MAX_EVENT_LOG = 500
MAX_CONNECTIONS = 50

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
