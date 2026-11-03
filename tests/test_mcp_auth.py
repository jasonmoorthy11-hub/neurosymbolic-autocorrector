"""Tests for MCP authentication middleware."""

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

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
