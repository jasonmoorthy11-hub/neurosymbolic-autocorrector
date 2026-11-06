# Deployment Guide

## Local Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,langgraph,guardrails]"   # offline core; ".[all,dev]" adds Neo4j + Redis
cp .env.example .env
# Edit .env with your API keys
pytest
```

## Docker

```bash
# Build and start the stack from a fresh clone; no .env needed
docker compose up --build

# Services, in start order:
#   neo4j:         http://localhost:7474 (browser), bolt://localhost:7687; neo4j/changeme
#   seed:          one-shot, loads the default policies, facts and templates once
#                  Neo4j is healthy (idempotent; re-run with `docker compose run --rm seed`)
#   autocorrector: http://localhost:8000 (FastAPI API), starts after the seed
#   dashboard:     http://localhost:3000, the live-mode build behind nginx, which
#                  proxies /api and /ws to the API container (dashboard/nginx.conf)

# Health check: expect "neo4j_connected": true
curl http://localhost:8000/health

# The 15 Neo4j integration tests against the compose database (they clear and reseed it)
AUTOCORRECTOR_NEO4J_URI=bolt://localhost:7687 AUTOCORRECTOR_NEO4J_PASSWORD=changeme \
  python3 -m pytest tests/test_neo4j_integration.py -q

docker compose down        # keeps the neo4j_data volume; add -v to drop it
```

The API's `environment` entries set the compose Neo4j credentials and take
precedence over a `.env`, which is read only if present (API keys for the LLM
tier). `tests/test_docker_config.py` pins these invariants. The image bakes in
the fastText language-identification model `lid.176.bin` (Facebook AI Research,
CC BY-SA 3.0) so no download happens at runtime; keep that attribution if the
image is ever published.

## Environment Variables

All prefixed with `AUTOCORRECTOR_`:

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `NEO4J_URI` | No | (empty: in-memory store) | `bolt://localhost:7687` for a local server, `neo4j+s://` for Aura |
| `NEO4J_USER` | When URI set | (empty) | |
| `NEO4J_PASSWORD` | When URI set | (empty) | |
| `ANTHROPIC_API_KEY` | For the LLM correction tier and translation | (empty) | |
| `OPENAI_API_KEY` | For vector embeddings only (`seed_neo4j.py --embed`) | (empty) | Not used by the correction path |
| `PORT` | No | `8000` | API server port |
| `CORS_ORIGINS` | No | `["http://localhost:3000"]` | JSON array |
| `MAX_CORRECTION_ATTEMPTS` | No | `3` | Correct-then-revalidate rounds |
| `CORRECTION_CONFIDENCE_SCORE` | No | `0.92` | Fixed heuristic reported when the text changed; not a probability |
| `LOG_LEVEL` | No | `INFO` | |

## Neo4j Setup

### Local (Docker)
Neo4j is included in `docker-compose.yml`. Default credentials: `neo4j/changeme`.

### Neo4j Aura (Cloud)
1. Create a free instance at https://neo4j.com/cloud/aura/
2. Set `AUTOCORRECTOR_NEO4J_URI=neo4j+s://<id>.databases.neo4j.io`
3. Set user and password from Aura dashboard
4. Run `python3 scripts/seed_neo4j.py` to populate the graph

## Production Checklist

- [ ] Set all API keys in environment (never in code)
- [ ] Configure `CORS_ORIGINS` to your frontend domain
- [ ] Use Neo4j Aura or a managed Neo4j instance
- [ ] Set `LOG_LEVEL=WARNING` for production
- [ ] Set `DEBUG=false`
- [ ] Build React dashboard: `cd dashboard && npm run build`
- [ ] Serve dashboard via nginx or CDN
- [ ] Set up monitoring on `/health` endpoint
- [ ] Configure log aggregation (JSON format)
