#!/usr/bin/env bash
# 将功能分支合并进 main 并 push（自动在裸库创建/删除 .allow-main-push）
#
# 用法:
#   bash scripts/merge_feature_to_main.sh feat/wangliying-标注优化
#   bash scripts/merge_feature_to_main.sh feat/xxx "merge: 标注对比页修复"
#
# 环境变量（可选）:
#   LABELU_BARE_REPO=/data/labeIU/repos/labelU.git
#   LABELU_REPO_ROOT=/data/labeIU/labeIuproject/labeIU
#   LABELU_SSH=root@113.141.72.175
#   LABELU_SSH_PORT=12043
#
set -euo pipefail

BR="${1:?用法: merge_feature_to_main.sh <分支名> [合并说明]}"
MSG="${2:-merge: ${BR} into main}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BARE_REPO="${LABELU_BARE_REPO:-/data/labeIU/repos/labelU.git}"
WORK_TREE="${LABELU_REPO_ROOT:-${REPO_ROOT}}"
ALLOW_REMOTE="${BARE_REPO}/.allow-main-push"

SSH_HOST="${LABELU_SSH:-root@113.141.72.175}"
SSH_PORT="${LABELU_SSH_PORT:-12043}"
GIT_BIN="${LABELU_GIT_BIN:-git}"
if [[ -z "${LABELU_GIT_BIN:-}" && "${REPO_ROOT}" == /mnt/* ]] && command -v git.exe >/dev/null 2>&1; then
  GIT_BIN="git.exe"
fi
GIT_WORK_TREE="${WORK_TREE}"
if [[ "${GIT_BIN}" == *git.exe && "${WORK_TREE}" == /mnt/* ]] && command -v wslpath >/dev/null 2>&1; then
  GIT_WORK_TREE="$(wslpath -w "${WORK_TREE}")"
fi

allow_main_push() {
  if [[ -d "${BARE_REPO}" ]]; then
    touch "${ALLOW_REMOTE}"
    return 0
  fi
  ssh -p "${SSH_PORT}" "${SSH_HOST}" "touch '${ALLOW_REMOTE}'"
}

revoke_main_push() {
  if [[ -d "${BARE_REPO}" ]]; then
    rm -f "${ALLOW_REMOTE}"
    return 0
  fi
  ssh -p "${SSH_PORT}" "${SSH_HOST}" "rm -f '${ALLOW_REMOTE}'"
}

if ! "${GIT_BIN}" -C "${GIT_WORK_TREE}" rev-parse --git-dir >/dev/null 2>&1; then
  echo "错误: 工作副本不存在: ${WORK_TREE}" >&2
  exit 1
fi

echo "[merge] fetch origin ..."
"${GIT_BIN}" -C "${GIT_WORK_TREE}" fetch origin

if ! "${GIT_BIN}" -C "${GIT_WORK_TREE}" show-ref --verify --quiet "refs/remotes/origin/${BR}"; then
  echo "错误: 远程不存在分支 origin/${BR}，请先 push 功能分支。" >&2
  exit 1
fi

"${GIT_BIN}" -C "${GIT_WORK_TREE}" checkout main
if [[ "${LABELU_GIT_RESET_ON_PULL:-1}" == "1" ]]; then
  "${GIT_BIN}" -C "${GIT_WORK_TREE}" reset --hard origin/main
else
  "${GIT_BIN}" -C "${GIT_WORK_TREE}" pull --ff-only origin main
fi

echo "[merge] merge --no-ff origin/${BR} ..."
"${GIT_BIN}" -C "${GIT_WORK_TREE}" merge --no-ff "origin/${BR}" -m "${MSG}"

allow_main_push
trap 'revoke_main_push' EXIT

echo "[merge] push origin main ..."
"${GIT_BIN}" -C "${GIT_WORK_TREE}" push origin main

echo "[merge] 完成。可在服务器执行:"
echo "  cd /data/labeIU/labeIuproject/labeIU && sudo env INSTALL_EXTRAS=yolo,image-pipeline bash deploy.sh update"
