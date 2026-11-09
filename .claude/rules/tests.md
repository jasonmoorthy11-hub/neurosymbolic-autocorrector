---
globs: tests/**/*.py
---

# Testing Conventions

- pytest with `asyncio_mode = "auto"` — async tests just work
- Mock all external services (Neo4j, Anthropic, OpenAI, NeMo) via unittest.mock
- Tests must pass fully offline — no network calls
- Fixtures in `tests/conftest.py` for shared setup
- Golden dataset cases in `src/autocorrector/evaluation/dataset.py`
- Test naming: `test_{feature}_{scenario}` e.g. `test_pipeline_pii_detected`
- New async tests need no marker; many older tests still carry a redundant `@pytest.mark.asyncio`, which is harmless under auto mode
- The Neo4j integration tests wipe the database and run only with `AUTOCORRECTOR_NEO4J_TEST_WIPE=1`; never point them at a database worth keeping
- Assertions: prefer specific (`assert result.action == CorrectionAction.CORRECT`) over generic
