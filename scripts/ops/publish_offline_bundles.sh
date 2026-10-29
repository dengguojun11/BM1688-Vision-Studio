#!/usr/bin/env bash
# 在服务器上把 /data/labeIU/offline/*.tar.gz 安装到仓库 deploy/offline/ 并 Git LFS 提交推送。
# 用法（在服务器项目根）:
#   sudo bash scripts/ops/publish_offline_bundles.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OFFLINE_SRC="${LABELU_OFFLINE_SRC:-/data/labeIU/offline}"
DEST="${REPO_ROOT}/deploy/offline"

log() { echo "[publish-offline] $*"; }

mkdir -p "${DEST}"
for name in tpu-mlir sophon-demo; do
  src="${OFFLINE_SRC}/${name}.tar.gz"
  if [[ ! -f "${src}" ]]; then
    log "缺少 ${src}，请先打包或 scp 到 ${OFFLINE_SRC}/"
    exit 1
  fi
  log "安装 ${src} -> ${DEST}/"
  cp -f "${src}" "${DEST}/${name}.tar.gz"
done

cd "${REPO_ROOT}"
if command -v git-lfs >/dev/null 2>&1; then
  git lfs install
  git lfs track "deploy/offline/*.tar.gz"
  git add .gitattributes 2>/dev/null || true
else
  log "警告: 未安装 git-lfs，大包 push 可能失败；可 apt install git-lfs"
fi

git add deploy/offline/*.tar.gz deploy/offline/README.md deploy/offline/.gitkeep 2>/dev/null || true
if git diff --cached --quiet; then
  log "无新变更，跳过 commit"
  exit 0
fi

git commit -m "chore(offline): 添加 TPU-MLIR 与 sophon-demo 离线安装包"
git push origin main
log "已推送离线包到 origin/main"
