#!/usr/bin/env bash
# 将 labelU 自研扩展脚本同步到 labelU-Kit 开发目录（供 Vite 热更新使用）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export LABELU_KIT_DIR="${LABELU_KIT_DIR:-${REPO_ROOT}/frontend/labelU-Kit}"
exec python -c "from labelu.internal.common.labelu_frontend_integration import sync_extensions_to_kit; sync_extensions_to_kit()"
