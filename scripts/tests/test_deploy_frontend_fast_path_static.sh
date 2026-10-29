#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_SH="${ROOT}/deploy.sh"

fail() {
  echo "[test_deploy_frontend_fast_path_static] FAIL: $*" >&2
  exit 1
}

fast_path_block="$(
  awk '
    /labelu_deploy_changes_are_frontend_only/ { in_block=1 }
    in_block { print }
    in_block && /return 0/ { exit }
  ' "${DEPLOY_SH}"
)"

[[ -n "${fast_path_block}" ]] || fail "fast path block not found"

grep -q "deploy_try_frontend_build" <<<"${fast_path_block}" ||
  fail "fast path does not build frontend"

grep -q "deploy_reload_nginx_frontend_only" <<<"${fast_path_block}" ||
  fail "fast path does not refresh nginx frontend"

if grep -q "labelu_server_update.sh.*all" <<<"${fast_path_block}"; then
  fail "fast path calls full update script"
fi

if grep -q "deploy_ensure_service_running" <<<"${fast_path_block}"; then
  fail "fast path restarts LabelU service"
fi

if grep -q "systemctl restart.*labelu" <<<"${fast_path_block}"; then
  fail "fast path directly restarts LabelU service"
fi

echo "[test_deploy_frontend_fast_path_static] OK"
