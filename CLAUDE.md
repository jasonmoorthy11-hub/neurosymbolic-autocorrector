# Neurosymbolic Autocorrector

Enterprise AI quality assurance system that **corrects** (not blocks) agent outputs in real-time using knowledge graphs + symbolic reasoning.

## Commands

```bash
# Tests (393 passing, excludes Neo4j integration which needs separate install)
python3 -m pytest
python3 -m pytest --ignore=tests/test_neo4j_integration.py  # skip Neo4j tests
python3 -m pytest tests/test_pipeline.py -v                  # single file
python3 -m pytest -k "test_name" -v                          # single test

# Demo (15 scenarios: 10 step-by-step + 5 full pipeline)
python3 scripts/demo.py

# Evaluation (410-case golden dataset v3.2)
python3 scripts/evaluate.py
python3 scripts/evaluate.py --llm-judge              # with LLM quality scoring
python3 scripts/evaluate.py --category true_positive  # filter by category

# Dashboard
./scripts/start_dashboard.sh  # FastAPI :8000 + React :3000

# Lint
ruff check src/ tests/

# Docker
docker compose up --build  # 3 services: autocorrector, neo4j, dashboard
```

## Architecture

```
Agent Output → Language Detection → Translate to English → NeMo Input Rails → Intent Classifier → Guardrails (PII) → KG Validation → Correction/Allow/Escalate → Translate Back
```

- **Multilingual** (`multilingual/`) — fast-langdetect (176 languages) + LLM translation layer
- **MCP Server** (`mcp_server/`) — Exposes tools via Model Context Protocol
- **LangGraph Pipeline** (`orchestrator/`) — Stateful correction workflow with nodes
- **Knowledge Graph** (`knowledge_graph/`) — Neo4j policy store with in-memory fallback + vector embeddings
- **Intent Classifier** (`intent_classifier/`) — 9 intent types with confidence scores
- **Guardrails** (`guardrails/`) — PII detection (10 types), Guardrails AI validators, NeMo input rails
- **ACE** (`ace/`) — Self-improving corrections via supervisor feedback (Reflector + Curator)
- **Audit** (`audit/`) — SQLite event logging, supervisor decisions, analytics
- **Dashboard** (`dashboard/`) — FastAPI REST/WebSocket backend + analytics/ROI
- **Evaluation** (`evaluation/`) — Golden dataset, scoring, LLM-as-judge
- **Explainability** (`explainability/`) — Human-readable correction reasoning

## Key Constraints

- **Correct, don't block** — Never break conversation flow. Corrections must be seamless.
- **3-tier correction fallback** — Neo4j template → LLM (Claude) → Hardcoded template
- **Fail-open guardrails** — NeMo/Guardrails failures pass through rather than block
- **Async everywhere** — All I/O is async (aiosqlite, httpx, Neo4j driver)
- **Immutable events** — `correction_events` table is append-only; decisions go in separate table

## Module Layout

```
src/autocorrector/
├── config.py          — Pydantic Settings (env prefix: AUTOCORRECTOR_)
├── models.py          — Shared Pydantic models, enums (Severity, CorrectionAction, IntentType)
├── logging_config.py  — JSON structured logging, correlation IDs, request context
├── ace/               — Self-improving corrections (reflector.py, curator.py)
├── audit/store.py     — AuditStore: SQLite async, correction_events + supervisor_decisions
├── dashboard/api.py   — FastAPI app: REST + WebSocket, CORS, rate limiting, analytics
├── evaluation/        — dataset.py (golden cases), scorer.py, runner.py, llm_judge.py
├── explainability/    — Correction reasoning engine
├── guardrails/        — PII detection, NeMo rails wrapper
├── intent_classifier/ — Rule-based intent detection (9 types)
├── knowledge_graph/   — store.py (KG + LLM corrections), ingestion, retriever, vector search
├── mcp_server/        — server.py (FastMCP tools + resources)
├── multilingual/      — Language detection + LLM translation (detector.py, translator.py)
└── orchestrator/      — pipeline.py (LangGraph graph definition)
```

## Config

All settings via env vars with `AUTOCORRECTOR_` prefix or `.env` file. Key ones:
- `AUTOCORRECTOR_ANTHROPIC_API_KEY` — For LLM corrections
- `AUTOCORRECTOR_NEO4J_URI/USER/PASSWORD` — Neo4j Aura connection
- `AUTOCORRECTOR_MCP_API_KEY` — MCP auth
- `AUTOCORRECTOR_NEMO_RAILS_ENABLED` — Toggle NeMo input rails
- `AUTOCORRECTOR_SUPPORTED_LANGUAGES` — Multilingual support (default: en,es,fr,pt,de,it,nl,ar,zh,ja,ko,ru)

## Testing Conventions

- pytest with `asyncio_mode = "auto"`
- Mock external services (Neo4j, Anthropic, NeMo) — tests must pass offline
- Golden dataset in `src/autocorrector/evaluation/dataset.py`
- Test files mirror source: `test_pipeline.py` tests `orchestrator/pipeline.py`
