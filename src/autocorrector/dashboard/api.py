"""Dashboard REST API and WebSocket server.

Provides:
    - WebSocket endpoint for real-time correction event streaming
    - REST endpoints for metrics, rules, queue, and agent feed
    - Demo mode: simulates live agent activity for presentations
"""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from autocorrector import __version__
from autocorrector.config import get_settings
from autocorrector.dashboard.processing import process_and_log
from autocorrector.dashboard.routes import demo, events, queue, ws
from autocorrector.dashboard.routes.demo import DEMO_SCENARIOS
from autocorrector.dashboard.routes.events import rule_catalog
from autocorrector.dashboard.state import (
    _connections,
    _event_log,
    _start_time,
    broadcast,
    get_audit_store,
    get_kg_store,
    limiter,
)
from autocorrector.logging_config import request_id_var, setup_logging

logger = logging.getLogger(__name__)

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Connect the stores at startup so `/health` reports the real Neo4j state
    from the first request (a failed Neo4j connection falls back to memory)."""
    kg = get_kg_store()
    await kg.connect()
    audit = await get_audit_store()
    try:
        yield
    finally:
        await kg.close()
        await audit.close()


app = FastAPI(title="Autocorrector Dashboard API", version=__version__, lifespan=lifespan)

_settings = get_settings()

app.state.limiter = limiter
# Without this middleware `rate_limit_default` is inert: slowapi applies default
# limits only through it (routes with their own @limiter.limit are left to that).
# Added first, so it is innermost: its 429 still gets CORS headers, the request
# log line and X-Request-ID from the middlewares below.
app.add_middleware(SlowAPIMiddleware)

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

        expected = self.api_key.encode()
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if hmac.compare_digest(token.encode(), expected):
                return await call_next(request)

        api_key_header = request.headers.get("x-api-key", "")
        if hmac.compare_digest(api_key_header.encode(), expected):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )


if _settings.dashboard_api_key:
    app.add_middleware(DashboardAuthMiddleware, api_key=_settings.dashboard_api_key)


# A plain function on purpose: SlowAPIMiddleware cannot await a handler and would
# answer with slowapi's own body instead of this one.
@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )


# --- Health Check ---


@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers and Docker."""
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


app.include_router(events.router)
app.include_router(queue.router)
app.include_router(demo.router)
app.include_router(ws.router)

__all__ = [
    "DEMO_SCENARIOS",
    "DashboardAuthMiddleware",
    "RequestLoggingMiddleware",
    "_connections",
    "_event_log",
    "app",
    "broadcast",
    "get_audit_store",
    "get_kg_store",
    "limiter",
    "process_and_log",
    "rate_limit_handler",
    "rule_catalog",
]
