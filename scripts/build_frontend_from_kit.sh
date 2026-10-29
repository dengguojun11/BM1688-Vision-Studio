#!/usr/bin/env bash
# 从 frontend/labelU-Kit 源码构建主站，合并 extensions，写入 labelu/internal/statics/
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"
exec python -c "from labelu.internal.common.labelu_frontend_integration import build_kit_to_statics; build_kit_to_statics()"
