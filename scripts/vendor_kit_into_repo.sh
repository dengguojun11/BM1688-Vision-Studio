#!/usr/bin/env bash
# 将已 clone 的 frontend/labelU-Kit 并入本仓库（去掉嵌套 .git，便于单仓库 git push）
# 用法：在已有 frontend/labelU-Kit 时执行一次；或先 labelu frontend-clone 再执行本脚本
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
KIT="${REPO_ROOT}/frontend/labelU-Kit"

if [[ ! -d "${KIT}" ]]; then
  echo "缺少 ${KIT}，请先: labelu frontend-clone"
  exit 1
fi

if [[ -d "${KIT}/.git" ]]; then
  echo "移除嵌套 .git: ${KIT}/.git"
  rm -rf "${KIT}/.git"
fi

if [[ ! -f "${KIT}/package.json" ]] || [[ ! -d "${KIT}/apps/frontend" ]]; then
  echo "错误: ${KIT} 不像完整的 labelU-Kit 目录"
  exit 1
fi

echo "OK: ${KIT} 已可作为 monorepo 子目录提交到本仓库"
echo "下一步:"
echo "  git add frontend/labelU-Kit frontend/extensions"
echo "  git commit -m \"chore: vendor labelU-Kit source into monorepo\""
