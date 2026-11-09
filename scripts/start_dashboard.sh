#!/usr/bin/env bash
# Start the dashboard: FastAPI backend + React frontend
# Usage: ./scripts/start_dashboard.sh

set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

echo "=== Neurosymbolic Autocorrector Dashboard ==="
echo ""

# Both halves must be installed first; a missing one would otherwise fail in the
# background while the other keeps running
if [ ! -f .venv/bin/activate ]; then
    echo "No .venv found. Run:  python3 -m venv .venv && source .venv/bin/activate && pip install -e \".[dev,langgraph,guardrails]\""
    exit 1
fi
if [ ! -d dashboard/node_modules ]; then
    echo "Dashboard dependencies are missing. Run:  (cd dashboard && npm ci)"
    exit 1
fi

# Start FastAPI backend (loopback only: the read routes are unauthenticated)
echo "[1/2] Starting FastAPI backend on 127.0.0.1:8000 ..."
source .venv/bin/activate
uvicorn autocorrector.dashboard.api:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!

# Start React frontend
echo "[2/2] Starting React frontend on :3000 ..."
cd dashboard
npm run dev &
FRONTEND_PID=$!

echo ""
echo "Dashboard: http://localhost:3000"
echo "API docs:  http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both servers."

# Cleanup on exit
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    wait
}
trap cleanup EXIT INT TERM

wait
