"""The compose stack must come up from a fresh clone with no manual steps.

`docker compose up --build` is a documented entry point (docs/deployment.md),
so these tests pin what it needs: no required `.env`, Neo4j credentials that
match the Neo4j service, a seed step before the API starts (an empty graph
answers every policy check with "no rules"), and a dashboard that is built
from source and proxies `/api` and `/ws` to the API container.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]


def _env(service: str) -> dict[str, str]:
    raw = SERVICES[service].get("environment", [])
    if isinstance(raw, dict):
        return {k: str(v) for k, v in raw.items()}
    return dict(item.split("=", 1) for item in raw)


def test_env_file_is_optional():
    for name, service in SERVICES.items():
        for entry in service.get("env_file", []):
            assert isinstance(entry, dict) and entry.get("required") is False, (
                f"{name}: env_file {entry!r} must be optional; a fresh clone has no .env"
            )


def test_api_gets_the_compose_neo4j_credentials():
    user, password = _env("neo4j")["NEO4J_AUTH"].split("/", 1)
    api = _env("autocorrector")
    assert api["AUTOCORRECTOR_NEO4J_URI"] == "bolt://neo4j:7687"
    assert api["AUTOCORRECTOR_NEO4J_USER"] == user
    assert api["AUTOCORRECTOR_NEO4J_PASSWORD"] == password


def test_graph_is_seeded_before_the_api_starts():
    seed = SERVICES["seed"]
    assert "seed_neo4j.py" in " ".join(seed["command"])
    assert seed["depends_on"]["neo4j"]["condition"] == "service_healthy"
    assert (
        _env("seed")["AUTOCORRECTOR_NEO4J_PASSWORD"]
        == _env("autocorrector")["AUTOCORRECTOR_NEO4J_PASSWORD"]
    )
    api_deps = SERVICES["autocorrector"]["depends_on"]
    assert api_deps["seed"]["condition"] == "service_completed_successfully"


def test_dashboard_is_built_from_source_and_proxies_the_api():
    dashboard = SERVICES["dashboard"]
    assert dashboard["build"]["context"] == "./dashboard"
    assert (ROOT / "dashboard" / "Dockerfile").is_file()
    conf = (ROOT / "dashboard" / "nginx.conf").read_text()
    # No URI after the upstream: a trailing slash would strip the matched prefix
    assert re.search(r"location /api/ \{[^}]*proxy_pass http://autocorrector:8000;", conf)
    ws = re.search(r"location = /ws \{[^}]*\}", conf)  # exact: the API has one /ws route
    assert ws and "proxy_pass http://autocorrector:8000;" in ws.group(0)
    assert "Upgrade" in ws.group(0) and "proxy_http_version 1.1" in ws.group(0)


def test_neo4j_healthcheck_probes_bolt():
    # The seed and the API connect over bolt; an HTTP-only probe can pass first
    test = " ".join(SERVICES["neo4j"]["healthcheck"]["test"])
    assert "cypher-shell" in test and "RETURN 1" in test


def test_image_warms_the_language_model_at_build_time():
    # Otherwise the first request downloads the 125 MB fastText model inside the
    # container and blocks the event loop while it does
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "FTLANG_CACHE" in dockerfile
    assert re.search(r"RUN python -c .*detect_language\(", dockerfile, re.DOTALL)


def test_no_service_carries_an_api_key():
    for name in SERVICES:
        for key in _env(name):
            assert not re.search(r"ANTHROPIC|OPENAI|API_KEY", key), f"{name}: {key}"


def test_dockerignore_keeps_local_builds_out_of_the_context():
    ignored = (ROOT / "dashboard" / ".dockerignore").read_text().split()
    assert "node_modules" in ignored and "dist" in ignored
    assert ".env*" in ignored  # a local VITE_* .env must not be baked into the bundle
