"""Process-wide dashboard state shared by the routers: stores, the in-memory
event log, the WebSocket connections and the rate limiter."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from typing import Any

from fastapi import WebSocket
from slowapi import Limiter
from starlette.requests import Request

from autocorrector.audit.store import AuditStore
from autocorrector.config import get_settings
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.orchestrator.pipeline import set_kg_store

logger = logging.getLogger(__name__)


_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """An address, with an IPv4-mapped IPv6 one (`::ffff:a.b.c.d`) as its IPv4 form."""
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    return getattr(address, "ipv4_mapped", None) or address


def parse_trusted_proxies(entries: list[str]) -> tuple[_Network, ...]:
    """Addresses or CIDR networks from `AUTOCORRECTOR_TRUSTED_PROXIES`; a bad entry
    raises at startup instead of silently trusting nothing or everything."""
    return tuple(ipaddress.ip_network(entry.strip(), strict=False) for entry in entries)


TRUSTED_PROXIES: tuple[_Network, ...] = parse_trusted_proxies(get_settings().trusted_proxies)


def client_key(request: Request) -> str:
    """Rate-limit bucket for a request: the client's address.

    That is the direct peer, unless the peer is a configured trusted proxy. Behind
    the bundled nginx every request arrives from the proxy, so one shared bucket
    would throttle all users together; nginx appends the address it saw to
    `X-Forwarded-For`, and that last entry is then the client. Earlier entries are
    client-supplied, and so is the whole header from any other peer (a private
    address is not proof of a proxy: a LAN client or the Docker gateway has one).
    """
    peer = request.client.host if request.client else ""
    peer_ip = _parse_ip(peer)
    if peer_ip is not None and any(peer_ip in network for network in TRUSTED_PROXIES):
        forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
        last_hop = _parse_ip(forwarded.rsplit(",", 1)[-1]) if forwarded else None
        if last_hop is not None:
            return str(last_hop)
    return peer or "unknown"


limiter = Limiter(key_func=client_key, default_limits=[get_settings().rate_limit_default])

_connections: list[WebSocket] = []
_event_log: list[dict[str, Any]] = []
_kg_store: KnowledgeGraphStore | None = None
_audit_store: AuditStore | None = None
_audit_store_lock = asyncio.Lock()

MAX_EVENT_LOG = 500
MAX_CONNECTIONS = 50

_start_time = time.monotonic()


def get_kg_store() -> KnowledgeGraphStore:
    """One store for the whole process: built from Settings (so AUTOCORRECTOR_NEO4J_*
    reaches it) and handed to the pipeline, which would otherwise build its own."""
    global _kg_store
    if _kg_store is None:
        _kg_store = KnowledgeGraphStore.from_settings(get_settings())
        set_kg_store(_kg_store)
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
