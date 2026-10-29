#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/ops/deploy_change_scope.sh
source "${ROOT}/scripts/ops/deploy_change_scope.sh"

fail() {
  echo "[test_deploy_change_scope] FAIL: $*" >&2
  exit 1
}

assert_frontend_only() {
  labelu_deploy_changes_are_frontend_only "$@" || fail "expected frontend-only: $*"
}

assert_not_frontend_only() {
  if labelu_deploy_changes_are_frontend_only "$@"; then
    fail "expected full deploy: $*"
  fi
}

assert_frontend_only \
  "frontend/labelU-Kit/apps/frontend/src/routes.tsx" \
  "frontend/labelU-Kit/apps/frontend/index.html" \
  "frontend/labelU-Kit/pnpm-lock.yaml"

assert_not_frontend_only
assert_not_frontend_only "labelu/main.py"
assert_not_frontend_only "deploy.sh"
assert_not_frontend_only "scripts/labelu_server_update.sh"
assert_not_frontend_only "frontend/extensions/assets/labelu_portal.js"
assert_not_frontend_only \
  "frontend/labelU-Kit/apps/frontend/src/routes.tsx" \
  "labelu/internal/common/config.py"

echo "[test_deploy_change_scope] OK"
