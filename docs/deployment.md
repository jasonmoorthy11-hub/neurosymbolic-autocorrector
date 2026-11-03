# Deployment Guide

## Local Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
cp .env.example .env
# Edit .env with your API keys
pytest
```

## Docker

```bash
# Build and start all services
docker compose up --build

# Services:
#   autocorrector: http://localhost:8000 (FastAPI + MCP)
#   neo4j:         http://localhost:7474 (browser), bolt://localhost:7687
#   dashboard:     http://localhost:3000 (React UI)

# Seed the knowledge graph
docker compose exec autocorrector python scripts/seed_neo4j.py

# Health check
curl http://localhost:8000/health
```

## Environment Variables

All prefixed with `AUTOCORRECTOR_`:

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `NEO4J_URI` | No | `bolt://localhost:7687` | Use `neo4j+s://` for Aura |
| `NEO4J_USER` | When URI set | (empty) | |
| `NEO4J_PASSWORD` | When URI set | (empty) | |
| `ANTHROPIC_API_KEY` | For LLM corrections | (empty) | |
| `OPENAI_API_KEY` | For fact checking | (empty) | |
| `PORT` | No | `8000` | API server port |
| `CORS_ORIGINS` | No | `["http://localhost:3000"]` | JSON array |
| `MAX_CORRECTION_ATTEMPTS` | No | `3` | |
| `CORRECTION_CONFIDENCE_THRESHOLD` | No | `0.85` | |
| `LOG_LEVEL` | No | `INFO` | |

## Neo4j Setup

### Local (Docker)
Neo4j is included in `docker-compose.yml`. Default credentials: `neo4j/changeme`.

### Neo4j Aura (Cloud)
1. Create a free instance at https://neo4j.com/cloud/aura/
2. Set `AUTOCORRECTOR_NEO4J_URI=neo4j+s://<id>.databases.neo4j.io`
3. Set user and password from Aura dashboard
4. Run `python scripts/seed_neo4j.py` to populate the graph

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
