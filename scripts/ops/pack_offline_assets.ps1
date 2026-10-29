# 在 Windows 上打包离线资源（需已安装 git；tar 可用 Windows 10+ 自带或 WSL）
param(
    [ValidateSet("tpu-mlir", "sophon-demo", "all")]
    [string]$Target = "all"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$OutDir = Join-Path $RepoRoot "deploy\offline"
$Work = Join-Path $env:TEMP ("labelu-offline-" + [guid]::NewGuid().ToString("n").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $OutDir, $Work | Out-Null

function Pack-TpuMlir {
    $dest = Join-Path $OutDir "tpu-mlir.tar.gz"
    Write-Host "[pack] TPU-MLIR -> $dest"
    $tpuUrl = if ($env:TPU_MLIR_GIT_URL) { $env:TPU_MLIR_GIT_URL } else { "https://gitee.com/mirrors/TPU-MLIR.git" }
    git clone --depth 1 $tpuUrl (Join-Path $Work "tpu-mlir")
    tar -czf $dest -C $Work tpu-mlir
    Get-Item $dest | Format-List Name, Length
}

function Pack-SophonDemo {
    $dest = Join-Path $OutDir "sophon-demo.tar.gz"
    Write-Host "[pack] sophon-demo -> $dest"
    git clone --depth 1 --branch release https://github.com/sophgo/sophon-demo.git (Join-Path $Work "sophon-demo")
    tar -czf $dest -C $Work sophon-demo
    Get-Item $dest | Format-List Name, Length
}

try {
    switch ($Target) {
        "tpu-mlir" { Pack-TpuMlir }
        "sophon-demo" { Pack-SophonDemo }
        "all" { Pack-TpuMlir; Pack-SophonDemo }
    }
    Write-Host "`n完成。请 scp 到服务器 /data/labeIU/offline/ 或 git add -f deploy/offline/*.tar.gz"
    Write-Host "详见 deploy/offline/README.md"
}
finally {
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
