"""Shared test fixtures for the autocorrector test suite."""

import sys

import pytest

from autocorrector.intent_classifier.classifier import IntentClassifier
from autocorrector.knowledge_graph.store import KnowledgeGraphStore


@pytest.fixture
def kg_store():
    """Create a fresh in-memory KnowledgeGraphStore."""
    return KnowledgeGraphStore()


@pytest.fixture
async def connected_kg_store():
    """Create a KG store and connect (falls back to in-memory without Neo4j URI)."""
    store = KnowledgeGraphStore()
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
def classifier():
    """Create a fresh IntentClassifier."""
    return IntentClassifier()


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """The dashboard's limiter is process-wide and in memory: without a reset on
    both sides, a test that exhausts a bucket fails whichever test hits the same
    route next, in any file."""

    def reset() -> None:
        state = sys.modules.get("autocorrector.dashboard.state")
        if state is not None:
            state.limiter.reset()

    reset()
    yield
    reset()
