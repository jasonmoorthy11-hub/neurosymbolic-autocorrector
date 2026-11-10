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


def test_root_dockerignore_keeps_private_and_local_state_out_of_the_image():
    ignored = (ROOT / ".dockerignore").read_text().split()
    for entry in (".env", ".private/", ".cache/", "*.db", ".git/", ".venv/"):
        assert entry in ignored, entry


def test_api_trusts_exactly_the_dashboard_proxy_address():
    # The rate limiter reads X-Forwarded-For only from this address, so it must be
    # the nginx container's fixed address and nothing wider
    import json

    address = SERVICES["dashboard"]["networks"]["default"]["ipv4_address"]
    assert json.loads(_env("autocorrector")["AUTOCORRECTOR_TRUSTED_PROXIES"]) == [address]
    subnet = COMPOSE["networks"]["default"]["ipam"]["config"][0]["subnet"]
    import ipaddress

    assert ipaddress.ip_address(address) in ipaddress.ip_network(subnet)


def _production_stage() -> str:
    dockerfile = (ROOT / "Dockerfile").read_text()
    return dockerfile.split("FROM base AS production", 1)[1]


def test_published_ports_are_loopback_only():
    # A local development stack: nothing it publishes should be reachable from the LAN
    for name, service in SERVICES.items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name}: {port}"


def test_neo4j_loads_no_plugins():
    # Nothing calls APOC; a plugin is a download at every first start
    assert "NEO4J_PLUGINS" not in _env("neo4j")


def test_audit_log_lives_on_a_named_volume():
    # Otherwise the audit trail is lost whenever the API container is recreated
    api = SERVICES["autocorrector"]
    mounts = dict(v.split(":", 1) for v in api["volumes"])
    assert "audit_data" in COMPOSE["volumes"]
    mount_path = mounts["audit_data"]
    db_path = _env("autocorrector")["AUTOCORRECTOR_AUDIT_DB_PATH"]
    assert db_path.startswith(mount_path.rstrip("/") + "/"), (db_path, mount_path)
    # A new named volume copies the image directory's owner, so the image must
    # create the mount point and hand it to the runtime user
    assert re.search(rf"chown [^\n]*{re.escape(mount_path)}", _production_stage())


def test_production_image_runs_as_non_root():
    stage = _production_stage()
    users = re.findall(r"^USER (\S+)$", stage, re.MULTILINE)
    # The last USER is the one the container runs as
    assert users and users[-1].split(":")[0] not in ("root", "0")
    assert stage.index("USER ") < stage.index("CMD ")
    # The baked fastText model must stay readable and its cache directory writable
    assert re.search(r"chown [^\n]*/app/\.cache", stage)


def test_production_image_ships_no_tests():
    # The API and the seed service need src/ and scripts/ only
    dockerfile = (ROOT / "Dockerfile").read_text()
    sources = re.findall(r"^COPY\s+(?!--from)(\S+)\s", dockerfile, re.MULTILINE)
    assert sources and set(sources) <= {"pyproject.toml", "src/", "scripts/"}, sources


def test_ci_runs_the_neo4j_tests_against_a_disposable_service():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = ci["jobs"]["neo4j"]
    service = job["services"]["neo4j"]
    assert service["image"] == SERVICES["neo4j"]["image"]
    assert service["env"]["NEO4J_AUTH"] == _env("neo4j")["NEO4J_AUTH"]
    steps = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert "seed_neo4j.py" in steps and "tests/test_neo4j_integration.py" in steps
    # The fixture skips when it cannot connect, and 16 skips would exit 0
    assert 'grep -q "16 passed"' in steps
    assert str(job["env"]["AUTOCORRECTOR_NEO4J_TEST_WIPE"]) == "1"


def test_image_build_fails_when_the_language_model_is_missing():
    # detect_language() swallows a failed download and answers "en", so the
    # warm-up alone passes with an empty cache; the build must check the file
    dockerfile = (ROOT / "Dockerfile").read_text()
    base_stage = dockerfile.split("FROM base AS production", 1)[0]
    assert re.search(r"test -s \S*lid\.176\.bin", base_stage)
    assert base_stage.index("import detect_language") < base_stage.index("test -s ")
    # A model that is present but does not load also answers "en": the warm-up
    # sentence is French and the build fails unless the answer is "fr"
    assert re.search(r"sys\.exit\(lang != 'fr'\)", base_stage)


def test_ci_builds_both_images_and_checks_the_runtime_user():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = ci["jobs"]["docker"]
    steps = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert "docker build --target production" in steps and " dashboard" in steps
    uid = re.search(r"useradd --uid (\d+)", _production_stage()).group(1)
    assert f'test "$uid" = "{uid}"' in steps
    assert job["timeout-minutes"] <= 60


def test_ci_builds_the_replay_bundle_under_a_sub_path():
    # GitHub Pages serves the demo under /<repo>/; a root-only build would 404 there
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    replay = [s for s in ci["jobs"]["dashboard"]["steps"] if "VITE_REPLAY" in s.get("env", {})]
    assert replay and "--base=/" in replay[0]["run"]
