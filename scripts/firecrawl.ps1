# Self-hosted Firecrawl for Beagle (web search + page reading) on Windows.
#   .\scripts\firecrawl.ps1 up      clone (first time), pull images, start on http://127.0.0.1:3002
#   .\scripts\firecrawl.ps1 down    stop and remove the containers
#   .\scripts\firecrawl.ps1 status  show container status
param([string]$Command = "up")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$Tag = if ($env:FIRECRAWL_TAG) { $env:FIRECRAWL_TAG } else { "v2.11.0" }
$ImageTag = if ($env:FIRECRAWL_IMAGE_TAG) { $env:FIRECRAWL_IMAGE_TAG } else { "2.11" }
$Port = if ($env:FIRECRAWL_PORT) { $env:FIRECRAWL_PORT } else { "3002" }
$Dir = "vendor/firecrawl"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Error "Docker is required: https://docs.docker.com/get-docker/" }

function Setup {
    if (-not (Test-Path "$Dir/.git")) {
        git clone --depth 1 --branch $Tag https://github.com/firecrawl/firecrawl.git $Dir
    }
    # Prebuilt images, localhost-only port (the API has no auth), limits that fit an 8 GB Docker VM
    @"
services:
  api:
    image: ghcr.io/firecrawl/firecrawl:$ImageTag
    ports: !override
      - "127.0.0.1:${Port}:3002"
    cpus: 2.0
    mem_limit: 4G
    memswap_limit: 4G
  playwright-service:
    image: ghcr.io/firecrawl/playwright-service:latest
  nuq-postgres:
    image: ghcr.io/firecrawl/nuq-postgres:latest
"@ | Set-Content "$Dir/docker-compose.override.yaml"
    if (-not (Test-Path "$Dir/.env")) { "USE_DB_AUTHENTICATION=false`nPORT=$Port" | Set-Content "$Dir/.env" }
}

switch ($Command) {
    "up" {
        Setup
        Push-Location $Dir; docker compose pull; docker compose up -d --no-build; Pop-Location
        Write-Host "Firecrawl is starting on http://localhost:$Port (first start can take a minute)."
    }
    "down" { Push-Location $Dir; docker compose down; Pop-Location }
    "status" { Push-Location $Dir; docker compose ps; Pop-Location }
    default { Write-Host "usage: scripts\firecrawl.ps1 [up|down|status]"; exit 1 }
}
