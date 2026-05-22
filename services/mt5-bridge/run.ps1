# MeznaQuantFX — MT5 Bridge Service Launcher (PowerShell)
# Run with: .\run.ps1

Write-Host "========================================"
Write-Host "  MeznaQuantFX MT5 Bridge Service"
Write-Host "  Port: 8010"
Write-Host "========================================"
Write-Host ""
Write-Host "IMPORTANT: MetaTrader 5 terminal must be running and logged in."
Write-Host ""

Set-Location $PSScriptRoot

python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
