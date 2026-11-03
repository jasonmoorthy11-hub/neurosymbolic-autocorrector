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
- Use `@pytest.mark.asyncio` only if auto mode fails for specific test
- Assertions: prefer specific (`assert result.action == CorrectionAction.CORRECT`) over generic
