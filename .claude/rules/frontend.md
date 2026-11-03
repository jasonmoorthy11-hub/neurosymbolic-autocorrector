---
globs: dashboard/**/*.{ts,tsx,css}
---

# Frontend Conventions

- React 19 + TypeScript, functional components only
- Hooks in `dashboard/src/hooks/` — useApi.ts for REST, useWebSocket.ts for WS
- Components in `dashboard/src/components/` — one component per file
- Types in `dashboard/src/types/` — shared interfaces
- WCAG 2.1 AA required: ARIA labels, focus-visible, color contrast 4.5:1
- Use lucide-react for icons
- CSS in component-scoped files or App.css for global styles
- API base URL: `http://localhost:8000`
- WebSocket URL: `ws://localhost:8000/ws`
