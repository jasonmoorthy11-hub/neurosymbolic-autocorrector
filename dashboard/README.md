# Dashboard

React 19 + TypeScript + Vite. One codebase, two builds, chosen at build time by
`VITE_REPLAY` (`src/replay/mode.ts`):

| Mode | Command | Data |
|---|---|---|
| Live | `npm run dev` | REST and WebSocket from the FastAPI backend on :8000 (Vite proxies `/api` and `/ws`; start both with `../scripts/start_dashboard.sh`) |
| Replay | `VITE_REPLAY=1 npm run dev` | `public/replay/replay.json`, recorded runs of the offline pipeline; no backend, no data requests beyond that file |

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

## Honesty rules for the replay build

- The banner always reads "Recorded runs of the offline pipeline — no backend, no
  API key".
- Every number shown is derived from `replay.json` (`src/replay/derive.ts`).
- Agent names are fictional and all PII is fake.
- Confidence is shown only as a labelled heuristic; ROI figures are labelled
  illustrative, with their assumptions visible.
