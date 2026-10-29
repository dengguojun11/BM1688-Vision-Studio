# 本地 E2E / Playwright：独立 SQLite + 与端口一致的 MEDIA_HOST，避免 9876 占用冲突。
# 用法：.\scripts\run_labelu_e2e.ps1
#      .\scripts\run_labelu_e2e.ps1 -Port 9890
param(
    [int]$Port = 9888
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$tmpDbDir = Join-Path (Get-Location) ".tmp\\db"
if (-not (Test-Path $tmpDbDir)) {
    New-Item -ItemType Directory -Path $tmpDbDir -Force | Out-Null
}
$dbPath = ($tmpDbDir -replace '\\', '/') + "/playwright_e2e.db"
$env:DATABASE_URL = "sqlite:///$dbPath"
if (-not $env:PASSWORD_SECRET_KEY) {
    $env:PASSWORD_SECRET_KEY = "e2e-local-dev-only-change-in-prod"
}
$media = "http://127.0.0.1:$Port"
$env:MEDIA_HOST = $media

Write-Host "Starting LabelU E2E: PORT=$Port MEDIA_HOST=$media DB=$($env:DATABASE_URL)" -ForegroundColor Cyan
python -m labelu.main --host 127.0.0.1 --port $Port --media-host $media
