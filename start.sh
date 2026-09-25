#!/usr/bin/env bash
# Starts the backend (port 8002) and the frontend dev server (port 5173).
# Reuses a backend that's already running; refuses to start a second frontend.
set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
command -v npm >/dev/null || { echo "Node.js/npm is required: https://nodejs.org"; exit 1; }
curl -s --max-time 2 http://localhost:11434/api/version >/dev/null \
  || echo "Warning: Ollama doesn't seem to be running on :11434 (start it with 'ollama serve')."

port_busy() { lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1; }

if port_busy 5173; then
  echo "Port 5173 is already in use — the app may already be running at http://localhost:5173"
  echo "Stop that process first (lsof -ti tcp:5173 -sTCP:LISTEN | xargs kill) and try again."
  exit 1
fi

uv sync -q
[ -d frontend/node_modules ] || (cd frontend && npm install)

BACKEND_PID=""
if curl -s --max-time 2 http://127.0.0.1:8002/api/health | grep -q ok; then
  echo "Backend already running on :8002 — reusing it."
elif port_busy 8002; then
  echo "Port 8002 is used by another program. Stop it and try again."
  exit 1
else
  uv run python -m backend.main &
  BACKEND_PID=$!
  trap '[ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null' EXIT INT TERM
fi

# Web search (Beagle): start the local Firecrawl when Docker is available. The first run downloads
# about 4 GB, so it runs in the background; set QUORUM_NO_WEB=1 to skip.
if [ -n "${QUORUM_NO_WEB:-}" ]; then
  :
elif curl -s --max-time 2 http://127.0.0.1:3002/ >/dev/null; then
  echo "Web search: Firecrawl is running."
elif command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  mkdir -p data
  echo "Web search: starting Firecrawl in the background (log: data/firecrawl.log)"
  (./scripts/firecrawl.sh up > data/firecrawl.log 2>&1 &)
else
  echo "Web search: off. Install and start Docker to let Beagle search the web: https://www.docker.com/products/docker-desktop/"
fi

echo "Backend:  http://localhost:8002"
echo "Frontend: http://localhost:5173"
cd frontend && npm run dev -- --strictPort
