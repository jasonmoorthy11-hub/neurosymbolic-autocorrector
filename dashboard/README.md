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
