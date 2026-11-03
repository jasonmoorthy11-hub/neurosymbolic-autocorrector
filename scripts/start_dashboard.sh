#!/usr/bin/env bash
# Start the dashboard: FastAPI backend + React frontend
# Usage: ./scripts/start_dashboard.sh

set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

echo "=== Neurosymbolic Autocorrector Dashboard ==="
echo ""

# Start FastAPI backend
echo "[1/2] Starting FastAPI backend on :8000 ..."
source .venv/bin/activate
uvicorn autocorrector.dashboard.api:app --host 0.0.0.0 --port 8000 --reload &
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
