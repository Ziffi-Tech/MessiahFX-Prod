# Bring up the full stack on Podman with HOT RELOAD (Windows / Podman Desktop).
# Host code edits then reflect in the running containers instantly — no rebuild.
#
#   pwsh scripts/dev-up.ps1            # start / update the stack
#   pwsh scripts/dev-up.ps1 -Build     # force-rebuild images first
#   pwsh scripts/dev-up.ps1 -Down      # stop the stack
#
# Backend services reuse their existing images + bind-mount app/ and shared/ with
# uvicorn --reload (forced polling). Only the dashboard-next DEV image is built.
param(
  [switch]$Build,
  [switch]$Down
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$compose = @("compose", "-p", "mezna", "-f", "podman-compose.yml", "-f", "podman-compose.dev.yml")

if ($Down) {
  Write-Host "Stopping the stack..." -ForegroundColor Yellow
  podman @compose down
  return
}

if (-not (podman machine list --format "{{.Running}}" | Select-String -Quiet "true")) {
  Write-Host "Starting the Podman machine..." -ForegroundColor Yellow
  podman machine start
}

# Validate the merged compose before touching anything.
Write-Host "Validating compose config..." -ForegroundColor Cyan
podman @compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "compose config invalid" }

if ($Build) {
  Write-Host "Rebuilding all images..." -ForegroundColor Cyan
  podman @compose build
} else {
  # First run: build only the dashboard dev image (backend images already exist).
  Write-Host "Building dashboard-next dev image (first run only)..." -ForegroundColor Cyan
  podman @compose build dashboard-next
}

Write-Host "Starting the stack (hot-reload)..." -ForegroundColor Cyan
podman @compose up -d

Write-Host ""
Write-Host "Up. Terminal: http://localhost:3001" -ForegroundColor Green
Write-Host "Edits under services/*/app and shared/mezna_shared reload automatically." -ForegroundColor Green
podman @compose ps
