#!/usr/bin/env bash
# 将 labelU-Kit 克隆到 frontend/labelU-Kit，供二次开发（不纳入 labelU git 提交）
#
# 用法（在 labelU 仓库根目录）：
#   bash scripts/clone_labelu_kit.sh
#   LABELU_KIT_GH_PROXY=https://ghproxy.net/ bash scripts/clone_labelu_kit.sh
#   LABELU_KIT_VERSION=v5.10.3 bash scripts/clone_labelu_kit.sh
#
# 等价: labelu frontend-clone
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

export LABELU_KIT_VERSION="${LABELU_KIT_VERSION:-}"
export LABELU_KIT_GH_PROXY="${LABELU_KIT_GH_PROXY:-}"

exec python -c "from labelu.internal.common.labelu_frontend_integration import clone_kit; clone_kit()"
