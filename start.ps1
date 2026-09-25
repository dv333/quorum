# Starts the backend (port 8002) and the frontend dev server (port 5173) on Windows.
# Reuses a backend that's already running; refuses to start a second frontend.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Write-Error "uv is required: https://docs.astral.sh/uv/" }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Write-Error "Node.js/npm is required: https://nodejs.org" }
try { Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 2 | Out-Null }
catch { Write-Warning "Ollama doesn't seem to be running on :11434 (start the Ollama app)." }

function Test-Port($port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

if (Test-Port 5173) {
    Write-Host "Port 5173 is already in use - the app may already be running at http://localhost:5173"
    exit 1
}

uv sync -q
if (-not (Test-Path frontend/node_modules)) { Push-Location frontend; npm install; Pop-Location }

$backend = $null
try {
    Invoke-RestMethod http://127.0.0.1:8002/api/health -TimeoutSec 2 | Out-Null
    Write-Host "Backend already running on :8002 - reusing it."
} catch {
    if (Test-Port 8002) { Write-Error "Port 8002 is used by another program. Stop it and try again." }
    $backend = Start-Process uv -ArgumentList "run", "python", "-m", "backend.main" -PassThru -NoNewWindow
}

# Web search (Beagle): start the local Firecrawl when Docker is available (first run downloads about 4 GB)
if (-not $env:QUORUM_NO_WEB) {
    $firecrawlUp = $false
    try { Invoke-WebRequest http://127.0.0.1:3002/ -TimeoutSec 2 -UseBasicParsing | Out-Null; $firecrawlUp = $true } catch {}
    if ($firecrawlUp) {
        Write-Host "Web search: Firecrawl is running."
    } elseif ((Get-Command docker -ErrorAction SilentlyContinue) -and ((docker info 2>$null) -ne $null)) {
        New-Item -ItemType Directory -Force data | Out-Null
        Write-Host "Web search: starting Firecrawl in the background (log: data\firecrawl.log)"
        Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", "scripts\firecrawl.ps1", "up" `
            -RedirectStandardOutput data\firecrawl.log -WindowStyle Hidden
    } else {
        Write-Host "Web search: off. Install and start Docker Desktop to let Beagle search the web."
    }
}

try {
    Write-Host "Backend:  http://localhost:8002"
    Write-Host "Frontend: http://localhost:5173"
    Push-Location frontend
    npm run dev -- --strictPort
} finally {
    Pop-Location
    if ($backend) { Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue }
}
