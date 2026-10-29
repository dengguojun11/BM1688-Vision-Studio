#!/usr/bin/env bash
# 将 scripts/git-hooks/* 安装到 LabelU 裸库 hooks/ 目录
#
# 用法（服务器）:
#   sudo bash scripts/install_git_hooks.sh
#   sudo bash scripts/install_git_hooks.sh /data/labeIU/repos/labelU.git
#
# 用法（本机经 SSH）:
#   ssh -p 12043 root@113.141.72.175 'bash -s' < scripts/install_git_hooks.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BARE_REPO="${1:-/data/labeIU/repos/labelU.git}"
SRC_HOOK="${SCRIPT_DIR}/git-hooks/pre-receive"
DEST_HOOK="${BARE_REPO}/hooks/pre-receive"

if [[ ! -d "${BARE_REPO}" ]]; then
  echo "错误: 裸库目录不存在: ${BARE_REPO}" >&2
  exit 1
fi
if [[ ! -f "${SRC_HOOK}" ]]; then
  echo "错误: 找不到 ${SRC_HOOK}" >&2
  exit 1
fi

install -m 0755 "${SRC_HOOK}" "${DEST_HOOK}"
echo "[install_git_hooks] 已安装 pre-receive -> ${DEST_HOOK}"

if [[ -f "${DEST_HOOK}" ]]; then
  head -n 3 "${DEST_HOOK}"
  echo "[install_git_hooks] main 保护已生效（需 .allow-main-push 方可 push main）"
fi
