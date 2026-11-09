# Dashboard

React 19 + TypeScript + Vite. One codebase, two builds, chosen at build time by
`VITE_REPLAY` (`src/replay/mode.ts`):

| Mode | Command | Data |
|---|---|---|
| Live | `npm run dev` | REST and WebSocket from the FastAPI backend on :8000 (Vite proxies `/api` and `/ws`; start both with `../scripts/start_dashboard.sh`) |
| Replay | `VITE_REPLAY=1 npm run dev` | `public/replay/replay.json`, recorded runs of the offline pipeline (or `replay_llm.json` with `?lane=llm`); no backend, no data requests beyond that one file |

The hooks in `src/hooks/useApi.ts` and `src/hooks/useWebSocket.ts` switch to the
replay hooks in `src/replay/` when `VITE_REPLAY=1`, so the components are the same in
both modes. The demo deploy (`.github/workflows/pages.yml`) builds the replay mode
with `--base=/neurosymbolic-autocorrector/` and publishes `dist/` to GitHub Pages.

```bash
npm ci
npm run dev                    # live mode
VITE_REPLAY=1 npm run dev      # replay mode
npm run build                  # type-check + production build (live)
VITE_REPLAY=1 npm run build    # production build (replay)
npm run lint
npm test                       # vitest
```

## Live mode

The filter bar (agent, action, severity, PII) narrows the feed and the CSV/JSON
export alike (`src/replay/derive.ts` `filterEvents`, shared with replay mode; the
severity filter matches a violation's severity, as the backend's export does). The
metrics cards poll every 2 s and the analytics panel every 10 s; the rules panel
loads once. A new WebSocket event also refreshes the analytics and rules panels, at
most once per 2 s (`src/hooks/useAnalyticsRefresh.ts`), so the latency figures and
the per-rule violation counts stay current while the demo runs. Timestamps from the
API are UTC ISO strings with a `Z` suffix and are shown in the browser's local time.

The feed is not a history view: it holds the events received over the WebSocket
since the page connected, plus the last 10 the backend keeps in memory and replays
on connect (none after a backend restart). The metrics, the analytics and the
export read the whole audit log, so after a reload in a quiet period the feed can
be empty under non-zero metrics; its empty state says so and points to the export. `docker compose up --build` from the repo root
serves this build on :3000 behind nginx, which proxies `/api` and `/ws` to the API
container (`Dockerfile`, `nginx.conf`; see `../docs/deployment.md`).

This UI sends no API key. If the backend runs with `AUTOCORRECTOR_DASHBOARD_API_KEY`
set, the approve/reject, export and demo buttons get a 401 while the read-only
panels keep working: the key is meant for API clients, and a key built into a
browser bundle would be readable by anyone who loads the page.

The backend rate-limits per client address and per endpoint, with limits fixed in
the code: 60 requests a minute on the data routes, 30 on approve, reject, demo stop
and demo step, 10 on export and 5 on demo start. `AUTOCORRECTOR_RATE_LIMIT_DEFAULT`
(60/minute) covers only the remaining routes such as `/health`. The client is the
direct peer; `X-Forwarded-For` is read only from the proxies listed in
`AUTOCORRECTOR_TRUSTED_PROXIES`, which the compose file sets to the nginx
container's fixed address.

## Replay data

`public/replay/replay.json` is written by `python3 scripts/record_replay.py` from the
repo root and is never edited by hand. It records 25 scenarios run through the
offline pipeline (no API key, in-memory policy store, NeMo off); the recorder
refuses to run otherwise. `python3 scripts/record_replay.py --check` fails when the
file no longer matches what the pipeline produces, and CI runs it.

`public/replay/replay_llm.json` is the second lane: one recorded run of the same 25
scenarios with the LLM correction tier (`python3 scripts/record_replay.py --llm`,
Claude Haiku 5.5, key passed inline, never in CI). Its header (`mode:
"llm-recorded"`, `llm: {model, calls, tokens, usd}`) is shown nowhere but documents
the run; `src/replay/lane.ts` maps `?lane=llm` to the file and the segmented
control in `ReplayControls` switches lanes (push state, player and decisions
reset). `--check` ignores this file: re-record it by hand after a pipeline change.

## Honesty rules for the replay build

- The banner names the lane: "Recorded runs of the offline pipeline — no backend, no
  API key", or for the LLM lane "One recorded run with the LLM correction tier
  (Claude Haiku 5.5) — no backend, no API key at view time".
- Every number shown is derived from the loaded replay file (`src/replay/derive.ts`).
- Agent names are fictional and all PII is fake.
- Confidence is shown only as a labelled heuristic; ROI figures are labelled
  illustrative, with their assumptions visible.
