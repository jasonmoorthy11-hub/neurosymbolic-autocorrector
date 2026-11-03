"""Shared test fixtures for the autocorrector test suite."""

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
