# 将 config/env/.env.local 同步到项目根 .env（供 labelu / pytest 外的本地运行使用）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Src = Join-Path $Root "config\env\.env.local"
$Dst = Join-Path $Root ".env"
if (-not (Test-Path $Src)) {
    Write-Error "Missing template: $Src"
}

# Keep local-only provider credentials out of the tracked template.  The
# existing root .env is the normal local secret store; an optional ignored
# .env.local.secrets file can be used when the root file is recreated.
$QwenKey = ""
$SecretFile = Join-Path $Root ".env.local.secrets"
foreach ($Candidate in @($SecretFile, $Dst)) {
    if (-not (Test-Path $Candidate)) {
        continue
    }
    $Line = Get-Content -LiteralPath $Candidate | Where-Object {
        $_ -match '^VISION_QWEN_API_KEY=(.+)$'
    } | Select-Object -First 1
    if ($Line) {
        $QwenKey = $Line.Substring('VISION_QWEN_API_KEY='.Length).Trim()
        if ($QwenKey) {
            break
        }
    }
}

Copy-Item -Force $Src $Dst

if ($QwenKey) {
    Add-Content -LiteralPath $Dst -Value "VISION_QWEN_API_KEY=$QwenKey"
}

Write-Host "Synced $Src -> $Dst"
if ($QwenKey) {
    Write-Host "Preserved local Qwen vision provider credential"
} else {
    Write-Warning "VISION_QWEN_API_KEY is empty; automatic global-tag preannotation will be unavailable"
}
