#!/usr/bin/env python3
"""What the browser panel must give: every golden case and replay scenario through
``run_deterministic(assume_english=True)``, as JSON on stdout.

    python3 scripts/tryit_expected.py > expected.json
    cd dashboard && node scripts/tryit-parity.mjs ../expected.json

The dashboard's parity script runs the same replies in Pyodide and compares.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scenarios import PIPELINE_SCENARIOS, STEP_SCENARIOS

from autocorrector.evaluation.dataset import GOLDEN_DATASET
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.orchestrator.runner import result_record, run_deterministic


def inputs() -> list[tuple[str, str, dict[str, Any]]]:
    golden = [(c.id, c.agent_text, c.customer_context) for c in GOLDEN_DATASET.cases]
    demo = [
        (f"demo-{n:02d}", s.text, s.context)
        for n, s in enumerate(STEP_SCENARIOS + PIPELINE_SCENARIOS, 1)
    ]
    return golden + demo


async def expected() -> list[dict[str, Any]]:
    kg = KnowledgeGraphStore()
    rows = []
    for case_id, text, context in inputs():
        result = await run_deterministic(text, context, kg_store=kg, assume_english=True)
        rows.append(
            {"id": case_id, "text": text, "context": context, "record": result_record(result)}
        )
    return rows


if __name__ == "__main__":
    json.dump(asyncio.run(expected()), sys.stdout)
