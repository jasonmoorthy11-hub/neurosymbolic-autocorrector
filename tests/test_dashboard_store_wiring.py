"""The dashboard API and the pipeline share one store built from Settings, and
the API connects it at startup so `/health` reports the real Neo4j state."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import autocorrector.dashboard.state as state_module
from autocorrector.audit.store import AuditStore
from autocorrector.dashboard.api import app, lifespan
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.orchestrator import pipeline


@pytest.fixture(autouse=True)
async def fresh_stores(tmp_path, monkeypatch):
    audit = AuditStore(db_path=str(tmp_path / "wiring.db"))
    await audit.connect()
    monkeypatch.setattr(state_module, "_audit_store", audit)
    monkeypatch.setattr(state_module, "_kg_store", None)
    monkeypatch.setattr(pipeline, "_kg_store", None)
    monkeypatch.setenv("AUTOCORRECTOR_NEO4J_URI", "bolt://graph:7687")
    monkeypatch.setenv("AUTOCORRECTOR_NEO4J_USER", "neo4j")
    monkeypatch.setenv("AUTOCORRECTOR_NEO4J_PASSWORD", "stack-default")
    yield
    await audit.close()


def test_dashboard_store_reads_settings_and_is_the_pipeline_store():
    store = state_module.get_kg_store()
    assert store._uri == "bolt://graph:7687"
    assert store._user == "neo4j"
    assert pipeline._get_kg_store() is store
    assert state_module.get_kg_store() is store


async def test_health_reports_the_store_connection_state():
    store = state_module.get_kg_store()
    store._connected = True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get("/health")).json()
    assert body["neo4j_connected"] is True


async def test_lifespan_connects_the_store_at_startup_and_closes_both_stores():
    store = state_module.get_kg_store()
    store.connect = AsyncMock()
    store.close = AsyncMock()
    audit = state_module._audit_store
    audit.close = AsyncMock()
    async with lifespan(app):
        store.connect.assert_awaited_once()
        store.close.assert_not_awaited()
    store.close.assert_awaited_once()
    audit.close.assert_awaited_once()


async def test_processing_does_not_dial_neo4j_per_event():
    # The lifespan connects once; a per-event connect re-dialled a down server on
    # every reply (the driver's connect timeout is 30 s)
    store = state_module.get_kg_store()
    store.connect = AsyncMock()
    from autocorrector.dashboard.processing import process_and_log

    event = await process_and_log(
        text="Thank you for contacting us.", customer_context={"account_tenure_days": 120}
    )
    assert event["action"] == "allow"
    store.connect.assert_not_awaited()


def test_pipeline_default_store_reads_settings_on_its_own():
    store = pipeline._get_kg_store()
    assert store._uri == "bolt://graph:7687"
    assert isinstance(store, KnowledgeGraphStore)
