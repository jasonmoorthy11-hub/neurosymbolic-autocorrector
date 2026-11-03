# Neurosymbolic Autocorrector

> Personal project, sole author.


Enterprise AI quality assurance and autocorrection system that uses knowledge graphs + symbolic reasoning to **correct** agent outputs in real-time — without breaking conversation flow.

Unlike traditional guardrails that block bad outputs, this system **fixes them** automatically using policy knowledge graphs, fact verification, and PII detection.

## Architecture

```
Agent Output
    |
    v
[Language Detection] ──> detect 176 languages (fast-langdetect), translate to English
    |
    v
[NeMo Input Rails] ──> jailbreak detection, topic steering
    |
    v
[Intent Classifier] ──> financial_compensation | policy_explanation | refund | ...
    |
    v
[Guardrails Check] ──> PII detection & redaction (10 types)
    |
    v
[Policy Validator] ──> Neo4j Knowledge Graph (8 rules, vector search)
    |                   + Fact Verification (coverage amounts, plan details)
    v
┌─────────────────────────────────────────┐
│  ALLOW ──> pass through unchanged       │
│  CORRECT ──> auto-fix with KG context   │
│  ESCALATE ──> supervisor review queue   │
└─────────────────────────────────────────┘
    |
    v
[Translate Back] ──> return correction in original language
    |
    v
[Dashboard] ──> real-time WebSocket + approve/reject + analytics/ROI
```

## Quick Start

```bash
# Clone and install
git clone <repo-url>
cd neurosymbolic-autocorrector
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"

# Set up environment
cp .env.example .env
# Edit .env with your API keys and Neo4j credentials

# Run tests
pytest

# Run demo (15 scenarios)
python scripts/demo.py

# Run evaluation (410-case golden dataset v3.2)
python scripts/evaluate.py

# Start dashboard (API + React frontend)
./scripts/start_dashboard.sh
```

## Project Structure

```
src/autocorrector/
├── config.py                 # Pydantic Settings (env vars, validation)
├── models.py                 # Shared data models (Severity, CorrectionAction, etc.)
├── intent_classifier/        # Rule-based intent detection (9 intent types)
├── knowledge_graph/          # Neo4j policy KG + in-memory fallback
│   ├── store.py              # Compliance checking, fact verification, LLM corrections
│   ├── schema.py             # Neo4j schema, seed data, constraints
│   ├── ingestion.py          # Policy document extraction (markdown → Neo4j)
│   └── retriever.py          # GraphRAG hybrid search (keyword + Cypher)
├── guardrails/               # Guardrails AI validators
│   ├── validators.py         # PII, compliance, factual accuracy validators
│   └── nemo_rails.py         # NeMo input safety rails (jailbreak + topic)
├── orchestrator/             # LangGraph pipeline
│   └── pipeline.py           # detect_lang → classify → guardrails → validate → {allow|correct|escalate}
├── multilingual/             # Multi-language support (176 languages)
│   ├── detector.py           # fast-langdetect language detection (176 languages)
│   └── translator.py         # LLM translation to/from English (Anthropic, fail-open)
├── ace/                      # Automated Correction Enhancement (self-improving)
│   ├── reflector.py          # Extract lessons from rejected corrections
│   └── curator.py            # Deduplicate strategies, manage scores
├── mcp_server/               # MCP server (3 tools, 3 resources)
│   └── server.py             # validate_action, get_correction, log_correction_event
├── evaluation/               # Golden dataset + metrics (F0.5, precision, recall)
│   ├── dataset.py            # 410 test cases across 5 categories (v3.2)
│   ├── runner.py             # Async evaluation runner
│   ├── scorer.py             # F0.5 scoring, latency stats, confusion matrix
│   └── llm_judge.py          # LLM-as-judge quality scoring (4 dimensions)
├── audit/                    # Persistent audit trail + supervisor decisions
│   └── store.py              # SQLite async store, approval metrics
├── dashboard/                # FastAPI backend + WebSocket
│   └── api.py                # REST endpoints, approve/reject, filter, export
└── explainability/           # Correction explainability engine
    └── engine.py             # Human-readable correction reasoning

dashboard/                    # React/TypeScript frontend
├── src/components/           # MetricsPanel, AgentFeed, EventChart, etc.
├── src/hooks/                # useWebSocket, useApi
└── src/types/                # TypeScript API types

tests/                        # 393 tests (pytest, excludes 14 Neo4j integration)
scripts/                      # Demo, evaluation, seeding, dashboard launcher
```

## Configuration

All configuration is via environment variables (prefix: `AUTOCORRECTOR_`) or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOCORRECTOR_PORT` | `8000` | Server port |
| `AUTOCORRECTOR_NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI |
| `AUTOCORRECTOR_NEO4J_USER` | (empty) | Neo4j username |
| `AUTOCORRECTOR_NEO4J_PASSWORD` | (empty) | Neo4j password |
| `AUTOCORRECTOR_ANTHROPIC_API_KEY` | (empty) | Anthropic API key |
| `AUTOCORRECTOR_OPENAI_API_KEY` | (empty) | OpenAI API key |
| `AUTOCORRECTOR_MAX_CORRECTION_ATTEMPTS` | `3` | Max retry corrections |
| `AUTOCORRECTOR_CORRECTION_CONFIDENCE_THRESHOLD` | `0.85` | Min confidence |
| `AUTOCORRECTOR_CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed CORS origins |

## Key Commands

```bash
# Tests
pytest                              # Run all tests
pytest tests/test_pipeline.py       # Run specific test file
pytest -k "test_pii"               # Run tests matching pattern

# Demo
python scripts/demo.py              # Interactive 15-scenario demo
python scripts/demo.py --step       # Step through one at a time

# Evaluation
python scripts/evaluate.py          # Run 410-case golden dataset
python scripts/evaluate.py --verbose # Show per-case results

# Dashboard
./scripts/start_dashboard.sh        # Start FastAPI:8000 + React:3000
curl localhost:8000/health           # Health check

# MCP Server
autocorrector                       # Start MCP server (streamable-http)
autocorrector --stdio               # Start MCP server (stdio transport)

# Neo4j
python scripts/seed_neo4j.py        # Seed knowledge graph
```

## Tech Stack

- **Python 3.14** with type hints
- **LangGraph** — Orchestration pipeline (classify → validate → correct/escalate)
- **Guardrails AI** — PII detection, compliance checking, fact verification
- **Neo4j** — Policy knowledge graph (with in-memory fallback)
- **FastAPI** — Dashboard REST API + WebSocket
- **MCP** — Model Context Protocol server for AI agent integration
- **React 19 + TypeScript** — Real-time monitoring dashboard
- **Pydantic v2** — Data validation and settings management
- **Anthropic Claude** — LLM corrections + multilingual translation + LLM-as-judge

## License

MIT — see [LICENSE](LICENSE)
