"""Tests for MCP and dashboard authentication middleware."""

import hmac
import sys
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import autocorrector.mcp_server.server as server_module
from autocorrector.dashboard.api import DashboardAuthMiddleware
from autocorrector.mcp_server.server import MCPAuthMiddleware


def _make_app(api_key: str = "") -> Starlette:
    """Create a test Starlette app with the auth middleware."""

    async def mcp_handler(request):
        return PlainTextResponse("ok")

    async def health_handler(request):
        return PlainTextResponse("healthy")

    app = Starlette(
        routes=[
            Route("/mcp", mcp_handler),
            Route("/mcp/sse", mcp_handler),
            Route("/health", health_handler),
        ],
    )
    app.add_middleware(MCPAuthMiddleware, api_key=api_key)
    return app


class TestMCPAuthMiddleware:
    def test_auth_disabled_when_no_key(self):
        """With empty api_key, all requests should pass through."""
        app = _make_app(api_key="")
        client = TestClient(app)
        resp = client.get("/mcp")
        assert resp.status_code == 200

    def test_valid_bearer_token(self):
        """Valid Bearer token should authenticate."""
        app = _make_app(api_key="test-secret-key")
        client = TestClient(app)
        resp = client.get("/mcp", headers={"Authorization": "Bearer test-secret-key"})
        assert resp.status_code == 200

    def test_valid_x_api_key(self):
        """Valid X-API-Key header should authenticate."""
        app = _make_app(api_key="test-secret-key")
        client = TestClient(app)
        resp = client.get("/mcp", headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 200

    def test_invalid_key_returns_401(self):
        """Invalid API key should return 401."""
        app = _make_app(api_key="test-secret-key")
        client = TestClient(app)
        resp = client.get("/mcp", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]

    def test_missing_key_returns_401(self):
        """Missing auth headers should return 401."""
        app = _make_app(api_key="test-secret-key")
        client = TestClient(app)
        resp = client.get("/mcp")
        assert resp.status_code == 401

    def test_non_mcp_path_bypasses_auth(self):
        """Non-/mcp paths should not require authentication."""
        app = _make_app(api_key="test-secret-key")
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_mcp_subpath_requires_auth(self):
        """Subpaths of /mcp should also require authentication."""
        app = _make_app(api_key="test-secret-key")
        client = TestClient(app)
        resp = client.get("/mcp/sse")
        assert resp.status_code == 401

        resp2 = client.get("/mcp/sse", headers={"Authorization": "Bearer test-secret-key"})
        assert resp2.status_code == 200


def _make_dashboard_app(api_key: str) -> Starlette:
    async def handler(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/api/demo/start", handler, methods=["POST"])])
    app.add_middleware(DashboardAuthMiddleware, api_key=api_key)
    return app


class TestTimingSafeCompare:
    """Both auth middlewares compare keys with hmac.compare_digest."""

    @pytest.mark.parametrize(
        ("client_factory", "path", "method"),
        [(_make_app, "/mcp", "get"), (_make_dashboard_app, "/api/demo/start", "post")],
        ids=["mcp", "dashboard"],
    )
    @pytest.mark.parametrize(
        "headers",
        [{"Authorization": "Bearer test-secret-key"}, {"X-API-Key": "test-secret-key"}],
        ids=["bearer", "x-api-key"],
    )
    def test_api_key_compare_digest(self, client_factory, path, method, headers):
        """Each header path accepts the key through exactly one compare_digest call."""
        client = TestClient(client_factory(api_key="test-secret-key"))
        with patch("hmac.compare_digest", wraps=hmac.compare_digest) as spy:
            resp = getattr(client, method)(path, headers=headers)
        assert resp.status_code == 200
        assert spy.call_count == 1

    @pytest.mark.parametrize(
        ("client_factory", "path", "method"),
        [(_make_app, "/mcp", "get"), (_make_dashboard_app, "/api/demo/start", "post")],
        ids=["mcp", "dashboard"],
    )
    def test_api_key_wrong_or_missing_rejected(self, client_factory, path, method):
        client = TestClient(client_factory(api_key="test-secret-key"))
        send = getattr(client, method)
        assert send(path, headers={"Authorization": "Bearer wrong-key"}).status_code == 401
        assert send(path, headers={"X-API-Key": "wrong-key"}).status_code == 401
        assert send(path).status_code == 401

    def test_api_key_non_ascii_header_is_rejected_not_crashed(self):
        """Latin-1 header bytes must yield 401, not a TypeError from compare_digest."""
        for client, path, method in (
            (TestClient(_make_app(api_key="test-secret-key")), "/mcp", "get"),
            (TestClient(_make_dashboard_app(api_key="test-secret-key")), "/api/demo/start", "post"),
        ):
            resp = getattr(client, method)(path, headers=[(b"x-api-key", b"cl\xe9")])
            assert resp.status_code == 401


class TestMCPHttpEntryPoint:
    def test_mcp_http_uses_settings_host_port(self, monkeypatch):
        monkeypatch.setenv("AUTOCORRECTOR_HOST", "127.0.0.1")
        monkeypatch.setenv("AUTOCORRECTOR_PORT", "9123")
        monkeypatch.setattr(sys, "argv", ["autocorrector"])
        with (
            patch("uvicorn.run") as run,
            patch("autocorrector.logging_config.setup_logging"),
        ):
            server_module.main()
        _, kwargs = run.call_args
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 9123
