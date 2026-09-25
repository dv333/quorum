#!/usr/bin/env bash
# Self-hosted Firecrawl for the Researcher agent (web search + page scraping).
#   scripts/firecrawl.sh up      clone (first time), pull images, start on http://localhost:3002
#   scripts/firecrawl.sh down    stop and remove the containers
#   scripts/firecrawl.sh status  show container status
#   scripts/firecrawl.sh logs    follow the API logs
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${FIRECRAWL_TAG:-v2.11.0}"               # source tag for docker-compose.yaml
IMAGE_TAG="${FIRECRAWL_IMAGE_TAG:-2.11}"       # matching ghcr.io image tag
PORT="${FIRECRAWL_PORT:-3002}"
DIR=vendor/firecrawl

command -v docker >/dev/null || { echo "Docker is required: https://docs.docker.com/get-docker/"; exit 1; }

setup() {
  if [ ! -d "$DIR/.git" ]; then
    git clone --depth 1 --branch "$TAG" https://github.com/firecrawl/firecrawl.git "$DIR"
  fi
  # Prebuilt images instead of building from source, localhost-only port (the API has no auth),
  # and limits that fit a typical 8 GB Docker VM
  cat > "$DIR/docker-compose.override.yaml" <<YAML
services:
  api:
    image: ghcr.io/firecrawl/firecrawl:$IMAGE_TAG
    ports: !override
      - "127.0.0.1:$PORT:3002"
    cpus: 2.0
    mem_limit: 4G
    memswap_limit: 4G
  playwright-service:
    image: ghcr.io/firecrawl/playwright-service:latest
  nuq-postgres:
    image: ghcr.io/firecrawl/nuq-postgres:latest
YAML
  if [ ! -f "$DIR/.env" ]; then
    printf 'USE_DB_AUTHENTICATION=false\nPORT=%s\n' "$PORT" > "$DIR/.env"
  fi
}

case "${1:-up}" in
  up)
    setup
    (cd "$DIR" && docker compose pull && docker compose up -d --no-build)
    echo "Firecrawl is starting on http://localhost:$PORT (first start can take a minute)."
    ;;
  down) (cd "$DIR" && docker compose down) ;;
  status) (cd "$DIR" && docker compose ps) ;;
  logs) (cd "$DIR" && docker compose logs -f api) ;;
  *) echo "usage: $0 [up|down|status|logs]"; exit 1 ;;
esac
