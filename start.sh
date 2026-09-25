#!/usr/bin/env bash
# Quorum launcher: checks prerequisites (offering to install anything missing), installs dependencies,
# then starts the backend (port 8002), local web search (if Docker is available) and the app (port 5173).
#
#   ./start.sh           check, install what's needed, start
#   ./start.sh --check   only check and install; don't start anything
#   ./start.sh --yes     install missing prerequisites without asking
set -euo pipefail
cd "$(dirname "$0")"

CHECK_ONLY=""
ASSUME_YES="${QUORUM_YES:-}"
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --yes | -y) ASSUME_YES=1 ;;
    -h | --help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)"; exit 1 ;;
  esac
done

OS="$(uname -s)"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# Ask before installing anything. Without a terminal (and without --yes) the answer is no.
confirm() {
  [ -n "$ASSUME_YES" ] && return 0
  [ -t 0 ] || return 1
  local answer
  read -r -p "    $1 [Y/n] " answer
  [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

# brew_install "<name>" <brew args...>: offer a Homebrew install on macOS
brew_install() {
  local name="$1"
  shift
  if [ "$OS" = Darwin ] && have brew && confirm "Install $name now with Homebrew (brew install $*)?"; then
    brew install "$@"
    return $?
  fi
  return 1
}

wait_for() {  # wait_for <seconds> <command...>
  local seconds="$1"
  shift
  for _ in $(seq 1 "$seconds"); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}

echo "Checking what Quorum needs…"
if [ "$OS" = Darwin ] && ! have brew; then
  warn "Homebrew isn't installed, so missing tools can't be installed for you. Get it at https://brew.sh"
fi

# --- uv (Python)
if have uv || brew_install "uv" uv; then
  ok "uv $(uv --version | awk '{print $2}')"
else
  fail "uv is required. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh  (or: brew install uv)"
  exit 1
fi

# --- Node.js 20+
if have node || brew_install "Node.js" node; then
  NODE_MAJOR="$(node -v | sed 's/^v//; s/\..*//')"
  if [ "$NODE_MAJOR" -lt 20 ]; then
    warn "Node.js $(node -v) is old; Quorum needs 20 or newer (brew upgrade node)"
  else
    ok "Node.js $(node -v)"
  fi
else
  fail "Node.js 20+ is required: https://nodejs.org  (or: brew install node)"
  exit 1
fi

# --- Ollama: runs the models
ollama_up() { curl -s --max-time 2 "$OLLAMA_URL/api/version" >/dev/null; }
if ! ollama_up; then
  if ! have ollama && [ ! -d /Applications/Ollama.app ]; then
    if [ "$OS" = Linux ] && confirm "Install Ollama now (curl -fsSL https://ollama.com/install.sh | sh)?"; then
      curl -fsSL https://ollama.com/install.sh | sh
    else
      brew_install "Ollama" ollama || true
    fi
  fi
  if [ -d /Applications/Ollama.app ]; then
    open -a Ollama
  elif have ollama; then
    mkdir -p data
    nohup ollama serve > data/ollama.log 2>&1 &
  fi
  if have ollama || [ -d /Applications/Ollama.app ]; then
    printf '  … starting Ollama\n'
    wait_for 20 ollama_up || true
  fi
fi
if ollama_up; then
  MODELS="$(curl -s "$OLLAMA_URL/api/tags" | { grep -o '"name"' || true; } | wc -l | tr -d ' ')"
  if [ "$MODELS" -eq 0 ]; then
    ok "Ollama is running (no models yet: the setup walkthrough in the app will download a starter set)"
  else
    ok "Ollama is running with $MODELS model(s)"
  fi
else
  warn "Ollama isn't running. Install it from https://ollama.com/download (or: brew install ollama), then run: ollama serve"
fi

# --- Docker: needed for web search (Beagle)
WEB=""
if [ -n "${QUORUM_NO_WEB:-}" ]; then
  warn "Web search skipped (QUORUM_NO_WEB is set)"
else
  if ! have docker; then
    if [ "$OS" = Darwin ] && have brew && confirm "Install Docker Desktop for web search (brew install --cask docker)?"; then
      brew install --cask docker
    fi
  fi
  if have docker && ! docker info >/dev/null 2>&1 && [ "$OS" = Darwin ] && [ -d /Applications/Docker.app ]; then
    printf '  … starting Docker Desktop (the first start can take a minute)\n'
    open -a Docker
    wait_for 90 docker info || true
  fi
  if have docker && docker info >/dev/null 2>&1; then
    COMPOSE="$(docker compose version --short 2>/dev/null || echo 0)"
    if printf '2.24.0\n%s\n' "${COMPOSE#v}" | sort -V -C; then
      ok "Docker is running (Compose $COMPOSE)"
      WEB=1
    else
      warn "Docker Compose $COMPOSE is too old for web search; update Docker Desktop (needs 2.24+)"
    fi
  elif have docker; then
    warn "Docker is installed but not running. Start Docker Desktop to turn on web search."
  else
    warn "Docker isn't installed, so web search is off. Get Docker Desktop: https://www.docker.com/products/docker-desktop/"
  fi
fi

# --- Python dependencies
uv sync -q
ok "Python packages"

# --- App dependencies. Reinstall when node_modules is missing, incomplete (an interrupted install)
# or older than package-lock.json.
if [ ! -x frontend/node_modules/.bin/vite ] || [ ! -f frontend/node_modules/.package-lock.json ] \
  || [ frontend/package-lock.json -nt frontend/node_modules/.package-lock.json ]; then
  # A custom registry in ~/.npmrc (often a company mirror) may only resolve on a work network or VPN.
  # Every package in package-lock.json comes from the public registry, so fall back to it.
  PUBLIC_REGISTRY="https://registry.npmjs.org/"
  NPM_REGISTRY="$(cd frontend && npm config get registry 2>/dev/null || echo "$PUBLIC_REGISTRY")"
  if [ "$NPM_REGISTRY" != "$PUBLIC_REGISTRY" ] && ! curl -s --max-time 5 -o /dev/null "$NPM_REGISTRY"; then
    warn "npm is set to use $NPM_REGISTRY, which isn't reachable (off VPN?). Using the public registry instead."
    NPM_REGISTRY="$PUBLIC_REGISTRY"
  fi
  printf '  … installing app packages (npm install)\n'
  if ! (cd frontend && npm install --no-audit --no-fund --loglevel=error --registry="$NPM_REGISTRY"); then
    fail "npm install failed. Check your network, then try again (npm registry: $NPM_REGISTRY)"
    exit 1
  fi
fi
ok "App packages"

if [ -n "$CHECK_ONLY" ]; then
  echo "All set. Run ./start.sh to open Quorum."
  exit 0
fi

# --- Start everything
port_busy() { lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1; }
if port_busy 5173; then
  echo "Port 5173 is already in use; Quorum may already be running at http://localhost:5173"
  echo "Stop that process first (lsof -ti tcp:5173 -sTCP:LISTEN | xargs kill) and try again."
  exit 1
fi

BACKEND_PID=""
if curl -s --max-time 2 http://127.0.0.1:8002/api/health | grep -q ok; then
  echo "Backend already running on :8002; reusing it."
elif port_busy 8002; then
  echo "Port 8002 is used by another program. Stop it and try again."
  exit 1
else
  uv run python -m backend.main &
  BACKEND_PID=$!
  trap '[ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null' EXIT INT TERM
fi

# Web search: start Firecrawl in the background (the first run downloads about 4 GB)
if [ -n "$WEB" ]; then
  if curl -s --max-time 2 http://127.0.0.1:3002/ >/dev/null; then
    echo "Web search: Firecrawl is running."
  else
    mkdir -p data
    echo "Web search: starting Firecrawl in the background (log: data/firecrawl.log)"
    (./scripts/firecrawl.sh up > data/firecrawl.log 2>&1 &)
  fi
fi

echo ""
echo "Quorum: http://localhost:5173"
cd frontend && npm run dev -- --strictPort
