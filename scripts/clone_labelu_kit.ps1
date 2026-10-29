# 克隆 labelU-Kit 到 frontend/labelU-Kit（Windows）
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
labelu frontend-clone @args
