---
globs: src/**/*.py
---

# Backend Conventions

- Python 3.12 to 3.14 (`requires-python = ">=3.12,<3.15"`; CI runs 3.12 and 3.14, the Docker image 3.12); async/await for all I/O
- Pydantic v2 models with `from __future__ import annotations`
- FastAPI with async endpoints; shared state comes from the accessors in `dashboard/state.py` (`get_kg_store()`, `get_audit_store()`), not `Depends()`
- Type hints on all function signatures
- Use `logging.getLogger(__name__)` not print statements
- Config via `Settings` class in `config.py` — never hardcode secrets
- Neo4j queries pass every value as a Cypher parameter; never format input into a query. The one f-string query (`schema.py` `create_vector_indexes`) interpolates only hardcoded labels and index names, which Cypher cannot parameterize
- Stores have an explicit lifecycle: `await store.connect()` at startup, `await store.close()` at shutdown (see the lifespan in `dashboard/api.py`)
- Error handling: fail-open for guardrails (log and continue), fail-closed for validation (a policy or fact check that raises escalates)
- Import style: stdlib → third-party → local (enforced by ruff isort)
