#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_SH="${ROOT}/deploy.sh"

fail() {
  echo "[test_deploy_nginx_training_timeout] FAIL: $*" >&2
  exit 1
}

grep -Fq 'local read_timeout="${2:-}"' "${DEPLOY_SH}" ||
  fail "proxy location helper does not accept a read timeout"

grep -Fq 'proxy_read_timeout ${read_timeout};' "${DEPLOY_SH}" ||
  fail "proxy location helper does not render the read timeout"

grep -Fq 'deploy_write_nginx_proxy_location "= /api/v1/yolo-training-tasks" "1800s"' "${DEPLOY_SH}" ||
  fail "training-task creation does not have a dedicated 1800s timeout"

echo "[test_deploy_nginx_training_timeout] OK"
