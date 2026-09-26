# Quorum launcher for Windows: checks prerequisites (offering to install anything missing with winget),
# installs dependencies, then starts the backend (port 8002), local web search (if Docker is available)
# and the app (port 5173).
#
#   .\start.ps1           check, install what's needed, start
#   .\start.ps1 -Check    only check and install; don't start anything
#   .\start.ps1 -Yes      install missing prerequisites without asking
#   .\start.ps1 -Demo     replay a recorded debate: needs only Node.js (no models, Docker or keys)
param([switch]$Check, [switch]$Yes, [switch]$Demo)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$OllamaUrl = if ($env:OLLAMA_URL) { $env:OLLAMA_URL } else { "http://localhost:11434" }
function Ok($msg) { Write-Host "  [ok] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  [x]  $msg" -ForegroundColor Red }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }

function Confirm-Install($question) {
    if ($Yes) { return $true }
    if (-not [Environment]::UserInteractive) { return $false }
    $answer = Read-Host "    $question [Y/n]"
    return ($answer -eq "" -or $answer -match "^[Yy]")
}

function Winget-Install($name, $id) {
    if ((Have winget) -and (Confirm-Install "Install $name now with winget ($id)?")) {
        winget install --id $id -e --accept-source-agreements --accept-package-agreements
        Refresh-Path
        return $true
    }
    return $false
}

function Test-Url($url) {
    try { Invoke-WebRequest $url -TimeoutSec 2 -UseBasicParsing | Out-Null; return $true } catch { return $false }
}

function Wait-For($seconds, [scriptblock]$test) {
    for ($i = 0; $i -lt $seconds; $i++) { if (& $test) { return $true }; Start-Sleep 1 }
    return $false
}

Write-Host "Checking what Quorum needs..."

# uv (Python)
if ($Demo) { }
elseif ((Have uv) -or ((Winget-Install "uv" "astral-sh.uv") -and (Have uv))) { Ok "uv" }
else { Fail "uv is required: https://docs.astral.sh/uv/getting-started/installation/"; exit 1 }

# Node.js 20+
if ((Have node) -or ((Winget-Install "Node.js LTS" "OpenJS.NodeJS.LTS") -and (Have node))) {
    $major = [int]((node -v).TrimStart("v").Split(".")[0])
    if ($major -lt 20) { Warn "Node.js $(node -v) is old; Quorum needs 20 or newer" } else { Ok "Node.js $(node -v)" }
} else { Fail "Node.js 20+ is required: https://nodejs.org"; exit 1 }

if (-not $Demo) {
# Memory and disk: what fits, and room for the models (the starter set is picked from about 3/4 of memory)
$ramGB = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$usableGB = [math]::Floor($ramGB * 3 / 4)
if ($usableGB -ge 30) { $starterGB = 22; $fits = "models up to about 30B parameters" }
elseif ($usableGB -ge 14) { $starterGB = 11; $fits = "models up to about 14B parameters" }
else { $starterGB = 8; $fits = "small models (3-8B parameters)" }
if ($ramGB -lt 8) { Warn "$ramGB GB of memory: only the smallest models fit, and debates will be slow. 16 GB or more works well." }
else { Ok "$ramGB GB of memory: $fits fit (with an NVIDIA GPU, its memory decides instead)" }
$modelDir = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { $env:USERPROFILE }
$drive = Get-PSDrive -Name (Split-Path -Qualifier $modelDir).TrimEnd(":") -ErrorAction SilentlyContinue
if ($drive) {
    $freeGB = [math]::Floor($drive.Free / 1GB)
    $webGB = if ($env:QUORUM_NO_WEB) { 0 } else { 4 }
    $needGB = $starterGB + $webGB + 2
    if ($freeGB -lt $needGB) {
        Warn "$freeGB GB free disk; Quorum needs about $needGB GB: $starterGB GB for the starter models, $webGB GB for web search (downloaded on its first start) and room to spare"
    } else { Ok "$freeGB GB free disk (the starter models take about $starterGB GB, web search about 4 GB)" }
}

# Ollama: runs the models
$ollamaApp = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
if (-not (Test-Url "$OllamaUrl/api/version")) {
    if (-not (Have ollama) -and -not (Test-Path $ollamaApp)) { Winget-Install "Ollama" "Ollama.Ollama" | Out-Null }
    if (Test-Path $ollamaApp) { Start-Process $ollamaApp }
    elseif (Have ollama) { Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden }
    if ((Have ollama) -or (Test-Path $ollamaApp)) {
        Write-Host "  ... starting Ollama"
        Wait-For 20 { Test-Url "$OllamaUrl/api/version" } | Out-Null
    }
}
if (Test-Url "$OllamaUrl/api/version") {
    $models = (Invoke-RestMethod "$OllamaUrl/api/tags").models.Count
    if ($models -eq 0) { Ok "Ollama is running (no models yet: the setup walkthrough will download a starter set)" }
    else { Ok "Ollama is running with $models model(s)" }
} else {
    Warn "Ollama isn't running. Install it from https://ollama.com/download, then start the Ollama app."
    Warn "Just want to look first? .\start.ps1 -Demo replays a recorded debate and needs only Node.js."
}

# Docker: needed for web search (Beagle)
$web = $false
if ($env:QUORUM_NO_WEB) { Warn "Web search skipped (QUORUM_NO_WEB is set)" }
else {
    if (-not (Have docker)) { Winget-Install "Docker Desktop (for web search)" "Docker.DockerDesktop" | Out-Null }
    $dockerOk = { docker info *> $null; $LASTEXITCODE -eq 0 }
    if ((Have docker) -and -not (& $dockerOk)) {
        $dockerApp = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
        if (Test-Path $dockerApp) {
            Write-Host "  ... starting Docker Desktop (the first start can take a minute)"
            Start-Process $dockerApp
            Wait-For 90 $dockerOk | Out-Null
        }
    }
    if ((Have docker) -and (& $dockerOk)) { Ok "Docker is running"; $web = $true }
    elseif (Have docker) { Warn "Docker is installed but not running. Start Docker Desktop to turn on web search." }
    else { Warn "Docker isn't installed, so web search is off: https://www.docker.com/products/docker-desktop/" }
}

# The Coder: a coding agent that reads your repository in code debates (optional)
if (Have claude) { Ok "Coder: Claude Code can read repositories in code debates (read-only; sign in once by running claude)" }
elseif (Have codex) { Ok "Coder: Codex can read repositories in code debates (read-only; sign in once by running codex)" }
else { Warn "Coder (optional): install Claude Code or Codex so code debates can read your repository" }

# Python dependencies
uv sync -q
Ok "Python packages"
}

# App dependencies: reinstall when node_modules is missing, incomplete (an interrupted install) or stale
$marker = "frontend/node_modules/.package-lock.json"
$stale = -not (Test-Path "frontend/node_modules/.bin/vite.cmd") -or -not (Test-Path $marker) -or
    ((Get-Item "frontend/package-lock.json").LastWriteTime -gt (Get-Item $marker -ErrorAction SilentlyContinue).LastWriteTime)
if ($stale) {
    Push-Location frontend
    # A custom registry in .npmrc (often a company mirror) may only resolve on a work network or VPN.
    # Every package in package-lock.json comes from the public registry, so fall back to it.
    $publicRegistry = "https://registry.npmjs.org/"
    $registry = (npm config get registry).Trim()
    if ($registry -ne $publicRegistry) {
        try { Invoke-WebRequest $registry -TimeoutSec 5 -UseBasicParsing | Out-Null; $reachable = $true }
        catch { $reachable = [bool]$_.Exception.Response }  # an HTTP error still means the host answered
        if (-not $reachable) {
            Warn "npm is set to use $registry, which isn't reachable (off VPN?). Using the public registry instead."
            $registry = $publicRegistry
        }
    }
    Write-Host "  ... installing app packages (npm install)"
    npm install --no-audit --no-fund --loglevel=error --registry=$registry
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Fail "npm install failed. Check your network, then try again (npm registry: $registry)"; exit 1 }
}
Ok "App packages"

function Test-Port($port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }

if ($Demo) {
    if (Test-Port 5173) { Write-Host "Quorum is already running: open http://localhost:5173/#demo"; exit 0 }
    Write-Host ""
    Write-Host "Replay demo: http://localhost:5173/#demo  (a recording: no models, Docker or keys needed)"
    Push-Location frontend
    try { npm run dev -- --strictPort } finally { Pop-Location }
    exit 0
}

if ($Check) { Write-Host "All set. Run .\start.ps1 to open Quorum."; exit 0 }
if (Test-Port 5173) {
    Write-Host "Port 5173 is already in use; Quorum may already be running at http://localhost:5173"
    exit 1
}

$backend = $null
if (Test-Url "http://127.0.0.1:8002/api/health") {
    Write-Host "Backend already running on :8002; reusing it."
} else {
    if (Test-Port 8002) { Write-Error "Port 8002 is used by another program. Stop it and try again." }
    $backend = Start-Process uv -ArgumentList "run", "python", "-m", "backend.main" -PassThru -NoNewWindow
}

if ($web) {
    if (Test-Url "http://127.0.0.1:3002/") { Write-Host "Web search: Firecrawl is running." }
    else {
        New-Item -ItemType Directory -Force data | Out-Null
        docker image ls --format "{{.Repository}}" 2>$null | Select-String -Quiet firecrawl | Set-Variable pulled
        if ($pulled) { Write-Host "Web search: starting Firecrawl in the background (log: data\firecrawl.log)" }
        else {
            Write-Host "Web search: first start. Firecrawl downloads about 4 GB in the background (5-15 minutes; log:"
            Write-Host "  data\firecrawl.log). The council works meanwhile; web search turns on when the download is done."
        }
        Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", "scripts\firecrawl.ps1", "up" `
            -RedirectStandardOutput data\firecrawl.log -WindowStyle Hidden
    }
}

try {
    Write-Host ""
    Write-Host "Quorum: http://localhost:5173"
    Push-Location frontend
    npm run dev -- --strictPort
} finally {
    Pop-Location
    if ($backend) { Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue }
}
