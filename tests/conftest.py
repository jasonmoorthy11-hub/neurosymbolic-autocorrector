"""Shared test fixtures for the autocorrector test suite."""

import os
import sys

import pytest

from autocorrector.intent_classifier.classifier import IntentClassifier
from autocorrector.knowledge_graph.store import KnowledgeGraphStore

# Settings reads `.env` from the working directory, and the process environment
# wins over that file. Pinning these empty keeps a developer's real key or Neo4j
# URI out of the suite: no test can make a paid API call or dial a database by
# accident. A test that needs a value passes it explicitly or uses monkeypatch.
# The one exception is the opt-in Neo4j integration run, which names its server
# on the command line (tests/test_neo4j_integration.py).
_PINNED_EMPTY = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
if os.environ.get("AUTOCORRECTOR_NEO4J_TEST_WIPE") != "1":
    _PINNED_EMPTY.append("NEO4J_URI")
for _name in _PINNED_EMPTY:
    os.environ[f"AUTOCORRECTOR_{_name}"] = ""
os.environ["AUTOCORRECTOR_NEMO_RAILS_ENABLED"] = "false"


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
