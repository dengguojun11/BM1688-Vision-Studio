#!/usr/bin/env bash
# 在仓库根执行 Alembic upgrade head（与 deploy / 应用启动时迁移一致）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=scripts/ops/migrate_deploy.sh
source "${ROOT}/scripts/ops/migrate_deploy.sh"
labelu_migrate_upgrade_head "${ROOT}"
