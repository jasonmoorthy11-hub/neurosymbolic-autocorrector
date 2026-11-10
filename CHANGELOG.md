# Changelog

One entry per release tag (`vX.Y`; the version's patch number stays 0); each line
under a release is that tag's own message.
The version number lives in `src/autocorrector/__init__.py` (`__version__`), which
the package metadata, the dashboard API and `/health` read.

## [1.5] - 2026-11-09

Audit round: things a reader following the docs could trip over.

- `.env.example` is safe to copy as it is: every line is commented out and names a
  real setting.
- The Neo4j integration tests, which wipe the database, run only with
  `AUTOCORRECTOR_NEO4J_TEST_WIPE=1` and no longer read `.env`.
- Dashboard rate limits apply to every HTTP route, including demo stop and step; the
  demo interval is bounded; clients behind a trusted proxy get their own bucket
  (`AUTOCORRECTOR_TRUSTED_PROXIES`).
- Every document, section and page cited by a default rule or fact exists in
  `policies/`, and a test keeps it that way.
- MCP resources: `policies://active` returns the store's documents and rule ids
  (it was an empty stub, and its `last_updated` field is gone);
  `metrics://corrections/summary` can be read (every read failed with "Unknown
  resource"); `rules://compliance/{domain}` no longer invents `FACT-001` and
  `FACT-002` for a domain without rules, and each rule carries its source.
- The `[redis]` extra is removed: nothing imported it.
- Local servers bind loopback by default: the MCP HTTP server
  (`AUTOCORRECTOR_HOST`, was every interface) and `scripts/start_dashboard.sh`.
  `autocorrector --help` prints usage instead of starting a server.
- The dashboard API's CORS policy names what it serves: GET and POST, and the
  headers `Content-Type`, `Authorization`, `X-API-Key`, `X-Request-ID` (it was `*`).
- `AUTOCORRECTOR_NEO4J_DATABASE` reaches the driver, so sessions now open the
  database it names (default `neo4j`) instead of the server's home database; the settings nothing read
  (`DEBUG`, `EMBEDDING_*`, `DEFAULT_LANGUAGE`) are gone.
- Installs are refused on Python 3.15 (a dependency does not build there yet);
  three unused `langchain-*` packages left the `[langgraph]` extra and `httpx`
  moved to `[dev]`.
- Docker stack: published ports are bound to `127.0.0.1`; the API image runs as an
  unprivileged user and no longer ships `tests/`; the audit log is on an
  `audit_data` volume; the unused APOC plugin is gone. The image build now fails
  if the language model is not baked in (a failed download used to pass silently
  and the first request then fetched 125 MB).
- CI runs the Neo4j integration tests against a disposable service, builds both
  Docker images, and builds the replay bundle under a sub-path.
- One version source, and this changelog.

## [1.4] - 2026-11-09

Verification round (live-mode fixes, phone layout, ARIA, Docker stack with Neo4j).

## [1.3] - 2026-11-05

One recorded LLM-tier run (Claude Haiku 5.5) with a lane switch.

## [1.2] - 2026-11-05

Intent scoring fixed, modules split, theme-aware charts.

## [1.1] - 2026-11-05

Rules by their triggers, fail-closed validation, redaction everywhere, full Neo4j seed.

## [1.0] - 2026-11-04

MIT, replay demo, honest eval.
