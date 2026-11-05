"""The dashboard API split into routers keeps its routes, state and re-exported names."""

import autocorrector.dashboard.api as api_module
from autocorrector.dashboard import processing, state
from autocorrector.dashboard.routes import demo, events, queue, ws

EXPECTED_ROUTES = {
    ("GET", "/health"),
    ("GET", "/api/metrics"),
    ("GET", "/api/events"),
    ("GET", "/api/events/export"),
    ("GET", "/api/queue"),
    ("POST", "/api/queue/{event_id}/approve"),
    ("POST", "/api/queue/{event_id}/reject"),
    ("GET", "/api/rules"),
    ("GET", "/api/analytics/trends"),
    ("GET", "/api/analytics/summary"),
    ("GET", "/api/metrics/approval-rates"),
    ("GET", "/api/ace/strategies"),
    ("POST", "/api/demo/start"),
    ("POST", "/api/demo/stop"),
    ("POST", "/api/demo/step"),
}


def _rest_routes() -> list[tuple[str, str]]:
    # The OpenAPI schema is the flattened view of every mounted router
    paths = api_module.app.openapi()["paths"]
    return [(method.upper(), path) for path, ops in paths.items() for method in ops]


def test_every_route_is_still_registered_once():
    found = _rest_routes()
    assert set(found) == EXPECTED_ROUTES
    assert len(found) == len(EXPECTED_ROUTES)


def test_routers_are_mounted_on_the_app():
    mounted = {path for _, path in _rest_routes()}
    for router in (events.router, queue.router, demo.router):
        assert router.routes, "router has no routes"
        assert {r.path for r in router.routes} <= mounted
    assert [r.path for r in ws.router.routes] == ["/ws"]


def test_websocket_router_is_mounted():
    from starlette.testclient import TestClient

    with TestClient(api_module.app) as client, client.websocket_connect("/ws") as websocket:
        websocket.send_text("ping")
        assert websocket.receive_text() == "pong"
    assert state._connections == []


def test_shared_state_is_the_same_object_everywhere():
    assert api_module._event_log is state._event_log
    assert api_module._connections is state._connections
    assert api_module.limiter is state.limiter
    assert api_module.process_and_log is processing.process_and_log
    assert api_module.DEMO_SCENARIOS is demo.DEMO_SCENARIOS
    assert api_module.rule_catalog is events.rule_catalog
    assert api_module.get_audit_store is state.get_audit_store


def test_rate_limits_survive_on_router_endpoints():
    # slowapi keys each decorated endpoint by "<module>.<function>"
    limited = dict(state.limiter._route_limits)
    assert "autocorrector.dashboard.routes.events.get_metrics" in limited
    assert "autocorrector.dashboard.routes.events.export_events" in limited
    assert "autocorrector.dashboard.routes.queue.approve_event" in limited
    assert "autocorrector.dashboard.routes.demo.start_demo" in limited
    assert str(limited["autocorrector.dashboard.routes.events.export_events"][0].limit) == (
        "10 per 1 minute"
    )


async def test_rate_limit_applies_through_the_router(tmp_path):
    from httpx import ASGITransport, AsyncClient

    from autocorrector.audit.store import AuditStore

    audit = AuditStore(db_path=str(tmp_path / "limits.db"))
    await audit.connect()
    state._audit_store = audit
    state.limiter.reset()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=api_module.app), base_url="http://test"
        ) as client:
            codes = [
                (await client.get("/api/events/export?format=json")).status_code for _ in range(11)
            ]
            last = await client.get("/api/events/export?format=json")
        assert codes == [200] * 10 + [429]
        assert last.json() == {"detail": "Rate limit exceeded. Please try again later."}
    finally:
        state.limiter.reset()
        state._audit_store = None
        await audit.close()
