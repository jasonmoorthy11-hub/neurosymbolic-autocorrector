"""Loaded once by the "Try it" worker (and by tryit-parity.mjs) inside Pyodide."""

import json

from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.orchestrator.runner import result_record, run_deterministic

_kg = KnowledgeGraphStore()


async def check(text: str, context: str) -> str:
    """One reply and its customer context (a JSON object) to a trace record (JSON)."""
    result = await run_deterministic(
        text, json.loads(context), kg_store=_kg, assume_english=True
    )
    return json.dumps(result_record(result))
