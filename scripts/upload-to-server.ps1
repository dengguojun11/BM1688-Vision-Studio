#requires -Version 5.1
<#
.SYNOPSIS
  Upload this repo to the server via tar-over-ssh (excludes venv, caches, etc.; .env is included).

.USAGE
  From repo root:
    .\scripts\upload-to-server.ps1
    .\scripts\upload-to-server.ps1 -DryRun
    .\scripts\upload-to-server.ps1 -IncludeGit

  Default: root@113.141.72.175:12043 -> /data/7G_AI/app

  Requires: OpenSSH (ssh) + Windows 10+ tar

  Only ONE ssh/password prompt: mkdir + extract run in the same remote shell as the tar stream.
  (Two separate ssh calls caused a second prompt; wrong/missing password there produced
  "gzip: stdin: not in gzip format" because ssh sent text instead of gzip bytes.)
#>

param(
    [string] $RemoteHost = "113.141.72.175",
    [int]    $Port = 12043,
    [string] $User = "root",
    [string] $RemotePath = "/data/7G_AI/app",
    [switch] $IncludeGit,
    [switch] $DryRun
)

$ErrorActionPreference = "Stop"

$SshTarget = "${User}@${RemoteHost}"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$ExcludeReasons = @"
Excluded (why):
  .git              Large; omit unless -IncludeGit
  .venv / .venv_run / venv   Local env; reinstall on Linux
  __pycache__ / *.pyc        Regenerated
  .pytest_cache / .mypy_cache / htmlcov / coverage
  dist / build / *.egg-info  Build artifacts
  test.db                     Local test SQLite (optional; server uses its own data dir)
  .idea / .cursor / terminals  IDE / Cursor noise
  node_modules       Frontend dependency cache; reinstall on the server
  .tmp / tmp_* / .DS_Store / *.log / __MACOSX

  Note: .env is uploaded when present. The isolated production instance loads its
  configuration from /data/7G_AI/config/app.env and is restarted by supervisor.
"@

$excludeArgs = @(
    "--exclude=.venv",
    "--exclude=.venv_run",
    "--exclude=venv",
    "--exclude=__pycache__",
    "--exclude=*.py[cod]",
    "--exclude=.pytest_cache",
    "--exclude=.mypy_cache",
    "--exclude=htmlcov",
    "--exclude=dist",
    "--exclude=build",
    "--exclude=*.egg-info",
    "--exclude=test.db",
    "--exclude=.idea",
    "--exclude=.cursor",
    "--exclude=terminals",
    "--exclude=.tmp",
    "--exclude=tmp_*",
    "--exclude=.DS_Store",
    "--exclude=*.log",
    "--exclude=coverage",
    "--exclude=node_modules",
    "--exclude=__MACOSX"
)
if (-not $IncludeGit) {
    $excludeArgs = @("--exclude=.git") + $excludeArgs
}

Write-Host "=== LabelU upload ===" -ForegroundColor Cyan
Write-Host "Local repo: $RepoRoot"
Write-Host "Remote: ${SshTarget}:${RemotePath} (port $Port)"
Write-Host ""
Write-Host $ExcludeReasons -ForegroundColor Yellow
Write-Host ""

if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw "ssh not found. Install OpenSSH Client (Windows Optional Feature)."
}
if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
    throw "tar not found. Use Windows 10 1903+ or install Git for Windows (includes tar)."
}

if ($DryRun) {
    Write-Host "[DryRun] Local tar + one ssh (mkdir + extract):" -ForegroundColor Magenta
    Write-Host "  tar -czf - $($excludeArgs -join ' ') . | ssh -p $Port $SshTarget `"mkdir -p '$RemotePath' && tar -xzf - -C '$RemotePath'`""
    exit 0
}

Push-Location $RepoRoot
try {
    Write-Host "Packing and streaming upload (ONE SSH password prompt)..." -ForegroundColor Green
    $tarArgs = @("-czf", "-") + $excludeArgs + @(".")
    $remoteCmd = "mkdir -p '$RemotePath' && tar -xzf - -C '$RemotePath'"
    & tar @tarArgs | & ssh -p $Port -o StrictHostKeyChecking=accept-new $SshTarget $remoteCmd

    if ($LASTEXITCODE -ne 0) {
        throw "Upload failed, exit code: $LASTEXITCODE"
    }
    Write-Host "Done. Remote path: $RemotePath" -ForegroundColor Green
    $deployHint = "sudo bash $($RemotePath)/deploy.sh restart"
    Write-Host "On server, review the archive and restart only the isolated service:" -ForegroundColor Gray
    Write-Host "  supervisorctl -c /data/7G_AI/config/supervisord.conf restart 7gai-web" -ForegroundColor Gray
}
finally {
    Pop-Location
}
