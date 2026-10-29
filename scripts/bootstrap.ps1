# AirGap native profile - one entry point for Windows (no Docker, no WSL).
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1          # check + start
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Check   # check only
# It never installs system software, edits .wslconfig, firewall rules or environment
# variables. Missing prerequisites are reported with the official source to install from.
param([switch]$Check, [switch]$WithDocumentAssistant)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$missing = @()

function Have($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

if (-not (Have "uv")) { $missing += "uv (Python project manager): https://docs.astral.sh/uv/getting-started/installation/" }
if (-not (Have "ollama") -and -not (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) {
  $missing += "Ollama (local LLM runtime): https://ollama.com/download"
}
$ui = Join-Path $root "frontend\dist\index.html"
if (-not (Test-Path $ui) -and -not (Have "npm")) { $missing += "Node.js 22+ to build the UI once: https://nodejs.org/" }

if ($missing.Count -gt 0) {
  Write-Host "Install these first, then run this script again:" -ForegroundColor Yellow
  $missing | ForEach-Object { Write-Host "  - $_" }
  exit 1
}

Push-Location (Join-Path $root "backend")
try {
  $extra = @()
  if ($WithDocumentAssistant) { $extra = @("--extra", "models") }   # CPU PyTorch, ~1 GB
  uv sync --frozen @extra
  if (-not (Test-Path $ui)) { Push-Location (Join-Path $root "frontend"); npm ci --no-audit --no-fund; npm run build; Pop-Location }
  uv run airgap --data-dir data/app --models-dir ../models doctor
  if ($Check) { exit $LASTEXITCODE }
  Write-Host "Starting on http://127.0.0.1:8000 (Ctrl+C to stop). Setup opens in the browser." -ForegroundColor Green
  Start-Process "http://127.0.0.1:8000"
  uv run airgap --data-dir data/app --models-dir ../models serve
} finally { Pop-Location }
