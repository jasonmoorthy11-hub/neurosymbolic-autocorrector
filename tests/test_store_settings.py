"""The services build their store from Settings and connect to Neo4j once.

`docker compose up` set AUTOCORRECTOR_NEO4J_* and the API still logged "No Neo4j
URI configured": no runtime entry point passed the Neo4j settings to the store,
and the dashboard reconnected (a new driver each time) on every event. The neo4j
driver is not installed offline, so a fake module stands in for it.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from autocorrector.config import Settings
from autocorrector.knowledge_graph.store import KnowledgeGraphStore


def _settings(**overrides) -> Settings:
    base = {
        "neo4j_uri": "bolt://graph:7687",
        "neo4j_user": "neo4j",
        "neo4j_password": "stack-default",
        "anthropic_api_key": "",
        "correction_llm": "claude-test",
    }
    return Settings(**{**base, **overrides})


@pytest.fixture
def fake_neo4j(monkeypatch):
    """A stand-in `neo4j` module whose driver factory records every call."""
    driver = MagicMock()
    driver.verify_connectivity = AsyncMock()
    driver.close = AsyncMock()
    factory = MagicMock(return_value=driver)
    module = types.ModuleType("neo4j")
    module.AsyncGraphDatabase = types.SimpleNamespace(driver=factory)
    monkeypatch.setitem(sys.modules, "neo4j", module)
    return factory, driver


def test_from_settings_carries_the_neo4j_and_llm_settings():
    store = KnowledgeGraphStore.from_settings(_settings())
    assert store._uri == "bolt://graph:7687"
    assert store._user == "neo4j"
    assert store._password == "stack-default"
    assert store._correction_llm == "claude-test"
    assert store.is_configured


def test_from_settings_without_a_uri_is_the_in_memory_store():
    store = KnowledgeGraphStore.from_settings(_settings(neo4j_uri=""))
    assert not store.is_configured


async def test_connect_creates_one_driver_however_often_it_is_called(fake_neo4j):
    factory, driver = fake_neo4j
    store = KnowledgeGraphStore.from_settings(_settings())
    await store.connect()
    await store.connect()
    await store.connect()
    factory.assert_called_once_with(
        "bolt://graph:7687", auth=("neo4j", "stack-default"), database="neo4j"
    )
    assert store.is_connected
    driver.close.assert_not_awaited()


async def test_connect_uses_the_configured_database(fake_neo4j):
    # AUTOCORRECTOR_NEO4J_DATABASE was a setting nothing passed to the driver
    factory, _ = fake_neo4j
    store = KnowledgeGraphStore.from_settings(_settings(neo4j_database="policies"))
    await store.connect()
    assert factory.call_args.kwargs["database"] == "policies"


async def test_close_resets_the_store_so_a_later_connect_opens_a_new_driver(fake_neo4j):
    factory, driver = fake_neo4j
    store = KnowledgeGraphStore.from_settings(_settings())
    await store.connect()
    await store.close()
    assert not store.is_connected
    driver.close.assert_awaited_once()
    await store.connect()
    assert factory.call_count == 2
    assert store.is_connected


async def test_failed_connect_closes_its_driver_and_falls_back_to_memory(fake_neo4j):
    _, driver = fake_neo4j
    driver.verify_connectivity.side_effect = OSError("connection refused")
    store = KnowledgeGraphStore.from_settings(_settings())
    await store.connect()
    assert not store.is_connected
    driver.close.assert_awaited_once()
    # The in-memory rules still answer
    violations = await store.check_policy_compliance(
        "I can offer you a $200 credit.", "financial_compensation", {"account_tenure_days": 30}
    )
    assert any(v.rule_id == "CREDIT-001" for v in violations)
