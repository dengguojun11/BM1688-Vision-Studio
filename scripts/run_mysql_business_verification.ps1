param(
    [string]$EnvFile = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $RepoRoot

if (-not $EnvFile) {
    $EnvFile = Join-Path $RepoRoot ".env"
}
$ResolvedEnvFile = (Resolve-Path -LiteralPath $EnvFile).Path
$env:LABELU_ENV_FILE = $ResolvedEnvFile
$env:LABELU_SKIP_MIGRATIONS = "true"

if (Test-Path Env:DATABASE_URL) {
    Remove-Item Env:DATABASE_URL
}

Write-Host "MySQL business verification" -ForegroundColor Cyan
Write-Host "  env file: $ResolvedEnvFile"
Write-Host "  mode: existing database, read-only"

python -m pytest mysql_live_tests -v
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
