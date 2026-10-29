# Local simulation of deploy.sh app-layer steps (Windows, no apt/systemd)
# Usage: .\scripts\simulate_deploy_local.ps1
# Production: sudo ./deploy.sh start | update | restart
param(
    [int]$Port = 9891
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $RepoRoot

$tmpDbDir = Join-Path $RepoRoot ".tmp\\db"
if (-not (Test-Path $tmpDbDir)) {
    New-Item -ItemType Directory -Path $tmpDbDir -Force | Out-Null
}
$dbPath = (($tmpDbDir -replace '\\', '/') + "/deploy_simulate.db")
$env:DATABASE_URL = "sqlite:///$dbPath"
$env:PASSWORD_SECRET_KEY = "deploy-simulate-secret-key-32bytes!!"
$env:MEDIA_HOST = "http://127.0.0.1:$Port"

function Step([int]$n, [int]$total, [string]$title) {
    Write-Host ""
    Write-Host "======== [$n/$total] $title ========" -ForegroundColor Cyan
}

$total = 12

Step 1 $total "Paths - same as deploy.sh init_labelu_deploy_paths"
Write-Host "  PROD BASE: /data/labeIU"
Write-Host "  PROD runtime: /data/labeIU/labeIuMedia"
Write-Host "  PROD appdata: /data/labeIU/labelu_appdata"
Write-Host "  PROD code: /data/labeIU/labeIuproject/labeIU"
Write-Host "  LOCAL repo: $RepoRoot"
Write-Host "  LOCAL DATABASE_URL: $env:DATABASE_URL"
Write-Host "  LOCAL MEDIA_HOST: $env:MEDIA_HOST"

Step 2 $total "Disk precheck - require_rootfs_free_mb on /"
Write-Host "  [OK] workspace accessible" -ForegroundColor Green

Step 3 $total "apt base packages"
Write-Host "  [SKIP] Windows - no apt" -ForegroundColor Yellow

Step 4 $total "Python 3.11 and venv"
Write-Host "  Python: $(python -V 2>&1)"

Step 5 $total "write_env_file - BASE_DATA_DIR, MEDIA_HOST, JWT"
Write-Host "  [SIM] env vars set for this run"

Step 6 $total "pip install -e project with yolo extras"
Write-Host "  [SKIP] assume already installed" -ForegroundColor Yellow
python -c "import labelu; print('  [OK] import labelu')"

Step 7 $total "MySQL drivers if DATABASE_URL contains mysql"
if ($env:DATABASE_URL -match 'mysql') {
    Write-Host "  would run apt + pymysql"
} else {
    Write-Host "  [SKIP] SQLite mode" -ForegroundColor Yellow
}

Step 8 $total "alembic upgrade head - deploy_run_migrate"
python -m labelu.alembic_labelu.run_migrate
Write-Host "  [OK] migrate done" -ForegroundColor Green

Step 9 $total "TPU-MLIR bootstrap"
Write-Host "  [SKIP] local simulation" -ForegroundColor Yellow

Step 10 $total "systemd unit labelu.service"
Write-Host "  [SKIP] no systemd on Windows"
Write-Host "  PROD: labelu --host 0.0.0.0 --port 10000 --media-host MEDIA_HOST"

Step 11 $total "start service, doctor, HTTP probe"
$serverJob = Start-Job -ScriptBlock {
    param($root, $port, $db, $secret, $media)
    Set-Location $root
    $env:DATABASE_URL = $db
    $env:PASSWORD_SECRET_KEY = $secret
    $env:MEDIA_HOST = $media
    & python -m labelu.main --host 127.0.0.1 --port $port --media-host $media
} -ArgumentList $RepoRoot, $Port, $env:DATABASE_URL, $env:PASSWORD_SECRET_KEY, $env:MEDIA_HOST

Start-Sleep -Seconds 14
Write-Host "  --- labelu doctor ---"
python -m labelu.main doctor
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/docs" -UseBasicParsing -TimeoutSec 10
    Write-Host "  [OK] GET /docs status $($r.StatusCode)" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] HTTP probe failed" -ForegroundColor Yellow
}

Stop-Job $serverJob -ErrorAction SilentlyContinue
Remove-Job $serverJob -Force -ErrorAction SilentlyContinue
$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($c in $listeners) {
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
}

Step 12 $total "Production update - deploy.sh update"
Write-Host "  git pull -> pip -> alembic -> systemctl restart"
Write-Host "  cmd: sudo ./deploy.sh update"

Write-Host ""
Write-Host "======== Simulation finished ========" -ForegroundColor Green
Write-Host "First deploy: sudo ./deploy.sh start"
Write-Host "Release:      sudo ./deploy.sh update"
Write-Host "Restart only: sudo ./deploy.sh restart"
