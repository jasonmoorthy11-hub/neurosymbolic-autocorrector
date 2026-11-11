# Neurosymbolic Autocorrector

AI quality assurance system that **corrects** (not blocks) agent outputs in real-time using knowledge graphs + symbolic reasoning. On the offline path every policy/fact edit cites its rule and source; PII is redacted to type tokens (not edits).

## Commands

```bash
# Tests (1809 passing offline; the 16 Neo4j integration tests are skipped without the opt-in below)
python3 -m pytest
python3 -m pytest --ignore=tests/test_neo4j_integration.py  # skip Neo4j tests
# Neo4j integration tests: they DELETE EVERY NODE, so a disposable server only (e.g. the compose one)
AUTOCORRECTOR_NEO4J_TEST_WIPE=1 AUTOCORRECTOR_NEO4J_URI=bolt://localhost:7687 \
  AUTOCORRECTOR_NEO4J_PASSWORD=changeme python3 -m pytest tests/test_neo4j_integration.py
python3 -m pytest tests/test_pipeline.py -v                  # single file
python3 -m pytest -k "test_name" -v                          # single test

# Demo (15 scenarios: 10 step-by-step + 5 full pipeline)
python3 scripts/demo.py

# Evaluation (527-case golden dataset v3.5, in-sample)
python3 scripts/evaluate.py
python3 scripts/evaluate.py --llm-judge              # with LLM quality scoring
python3 scripts/evaluate.py --invariants             # corrected text: idempotence, edit replay, untouched sentences, citations, output hashes
python3 scripts/evaluate.py --category true_positive  # filter by category
# Held-out set (60 blind cases): do NOT run, edit or tune on it. Its first and only
# run is published in docs/evaluation.md; fixes go into new golden rows.
#   python3 scripts/evaluate.py --dataset heldout
python3 scripts/bench_pii.py                          # external PII span benchmark (downloads once into .cache/)
python3 scripts/bench_pii.py --detector presidio      # report-only comparison; needs the [bench] extra + spaCy en_core_web_lg
python3 scripts/bench_real_turns.py --input turns.jsonl   # flag report for any JSONL of agent turns
# Real-turn audit on ABCD: a registered run, published in docs/evaluation.md. Do not
# re-run it to tune; fixes go into new golden rows.
#   python3 scripts/bench_real_turns.py --flagged .private/abcd_flagged.jsonl
python3 scripts/mutate_policy.py                      # mutation score of the golden set (about 70 s, not in CI)
python3 scripts/audit_keywords.py --check             # dictionary words a rule keyword matches by accident

# Replay data for the backend-free dashboard (refuses with an API key set or NeMo on)
python3 scripts/record_replay.py           # writes dashboard/public/replay/replay.json
python3 scripts/record_replay.py --check   # exit 1 if stale; re-record after pipeline/scenario/rule changes
# The one LLM-tier recording (replay_llm.json, Claude Haiku 5.5): paid, needs the key
# inline as AUTOCORRECTOR_ANTHROPIC_API_KEY and nothing else set; never run in CI
#   python3 scripts/record_replay.py --llm

# Dashboard
./scripts/start_dashboard.sh  # FastAPI :8000 + React :3000
cd dashboard && npm test      # vitest
cd dashboard && VITE_REPLAY=1 npm run dev  # replay mode, no backend

# Lint
ruff check src tests scripts
ruff format --check src tests scripts

# Docker
docker compose up --build  # neo4j → seed (one-shot) → autocorrector :8000; dashboard (nginx, built from source) :3000
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
- **ACE** (`ace/`) — ACE-style supervisor-feedback loop (after Agentic Context Engineering, Zhang et al., 2025, arXiv 2510.04618): Reflector + Curator; strategies feed the LLM tier only
- **Audit** (`audit/`) — SQLite event logging, supervisor decisions, analytics
- **Dashboard** (`dashboard/`) — FastAPI REST/WebSocket backend + analytics/ROI
- **Evaluation** (`evaluation/`) — Golden dataset, scoring, LLM-as-judge
- **Explainability** (`explainability/`) — Human-readable correction reasoning

## Key Constraints

- **Correct, don't block** — Never break conversation flow. Corrections must be seamless.
- **4-tier correction fallback** — Neo4j template (connected only) → LLM (key only) → span rewrite of the offending sentences (cited edits) → whole-message template
- **Fail-open guardrails, fail-closed validation** — NeMo/Guardrails failures pass through rather than block; a policy or fact check that raises escalates (`validation-unavailable`), never allows
- **Async everywhere** — All I/O is async (aiosqlite, the Anthropic async client, the Neo4j async driver)
- **Immutable events** — `correction_events` table is append-only; decisions go in separate table

## Module Layout

```
src/autocorrector/
├── config.py          — Pydantic Settings (env prefix: AUTOCORRECTOR_)
├── models.py          — Shared Pydantic models, enums (Severity, CorrectionAction, IntentType)
├── logging_config.py  — JSON structured logging, correlation IDs, request context
├── llm_response.py    — Claude Messages API response helpers (stop-reason allowlist, refusal fallback), shared by the LLM callers
├── ace/               — Supervisor-feedback strategies for the LLM tier (reflector.py, curator.py)
├── audit/store.py     — AuditStore: SQLite async, correction_events + supervisor_decisions
├── dashboard/         — api.py (app, middleware, /health), routes/{events,queue,demo,ws}.py, state.py, processing.py, models.py
├── evaluation/        — dataset.py (golden cases), heldout.py (blind set: never run or edit), registry.py, scorer.py, runner.py, invariants.py (corrected-text checks; tests/golden_outputs.json pins each case's output; re-record with `--invariants --record-outputs`), llm_judge.py
├── explainability/    — Correction reasoning engine
├── guardrails/        — validators.py (PII detection), nemo_rails.py (NeMo wrapper), telemetry.py (Guardrails telemetry off switch)
├── intent_classifier/ — Rule-based intent detection (9 types)
├── knowledge_graph/   — store.py (KG + LLM corrections), defaults.py, rules.py, facts.py, claims.py, rewriter.py (span tier), templates.py, schema.py (Neo4j seed); ingestion.py (the Markdown parser also reads `policies/` for the citation check in evaluation/invariants.py) and retriever.py are experimental
├── mcp_server/        — server.py (FastMCP tools + resources)
├── multilingual/      — Language detection + LLM translation (detector.py, translator.py)
└── orchestrator/      — pipeline.py (LangGraph graph definition)
```

## Config

All settings via env vars with `AUTOCORRECTOR_` prefix or `.env` file. Key ones:
- `AUTOCORRECTOR_ANTHROPIC_API_KEY` — For LLM corrections
- `AUTOCORRECTOR_NEO4J_URI/USER/PASSWORD` — Neo4j connection (any server; unset = in-memory store)
- `AUTOCORRECTOR_MCP_API_KEY` — MCP auth
- `AUTOCORRECTOR_NEMO_RAILS_ENABLED` — Toggle NeMo input rails
- `AUTOCORRECTOR_SUPPORTED_LANGUAGES` — Multilingual support (default: en,es,fr,pt,de,it,nl,ar,zh,ja,ko,ru)

## Testing Conventions

- pytest with `asyncio_mode = "auto"`
- Mock external services (Neo4j, Anthropic, NeMo) — tests must pass offline
- Golden dataset in `src/autocorrector/evaluation/dataset.py`
- Test files mirror source: `test_pipeline.py` tests `orchestrator/pipeline.py`
- CI (`.github/workflows/ci.yml`): pytest, ruff, replay `--check` on Python 3.12 + 3.14; dashboard build/lint/vitest + replay build; the Neo4j integration tests against a disposable service; a build of both Docker images. No secrets.
