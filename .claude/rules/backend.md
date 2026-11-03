---
globs: src/**/*.py
---

# Backend Conventions

- Python 3.14, async/await for all I/O
- Pydantic v2 models with `from __future__ import annotations`
- FastAPI with async endpoints, dependency injection via `Depends()`
- Type hints on all function signatures
- Use `logging.getLogger(__name__)` not print statements
- Config via `Settings` class in `config.py` — never hardcode secrets
- Neo4j queries use parameterized Cypher (no f-strings in queries)
- All stores use async context managers for connection lifecycle
- Error handling: fail-open for guardrails, log and continue
- Import style: stdlib → third-party → local (enforced by ruff isort)
