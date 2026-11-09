---
globs: dashboard/**/*.{ts,tsx,css}
---

# Frontend Conventions

- React 19 + TypeScript, function components (the one class component is `ErrorBoundary.tsx`: error boundaries have no hook form)
- Hooks in `dashboard/src/hooks/` — useApi.ts for REST, useWebSocket.ts for WS; both switch to the replay hooks in `dashboard/src/replay/` when `VITE_REPLAY=1`
- Components in `dashboard/src/components/` — one component per file
- Types in `dashboard/src/types/` — shared interfaces
- WCAG 2.1 AA required: ARIA labels, focus-visible, color contrast 4.5:1
- Use lucide-react for icons
- CSS in component-scoped files or App.css for global styles
- API calls are same-origin: relative `/api/...` paths and a WebSocket on `ws(s)://<page host>/ws`. Vite proxies both to :8000 in dev and nginx does in the compose stack; never hardcode a backend host
