#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_SH="${ROOT}/deploy.sh"

fail() {
  echo "[test_deploy_scheduler_units] FAIL: $*" >&2
  exit 1
}

grep -Fq 'readonly SCHEDULER_SYSTEMD_UNIT="labelu-training-scheduler.service"' "${DEPLOY_SH}" ||
  fail "scheduler systemd unit is not declared"
grep -Fq 'ExecStart=${VENV_DIR}/bin/python -m labelu.scripts.training_scheduler --interval ${interval}' "${DEPLOY_SH}" ||
  fail "scheduler unit does not use the dedicated training scheduler entrypoint"
grep -Fq 'Environment=LABELU_AUTO_TRAINING_API_WATCHDOG_ENABLED=false' "${DEPLOY_SH}" ||
  fail "API watchdog is not disabled when dedicated scheduler is enabled"
grep -Fq 'systemctl restart "${SCHEDULER_SYSTEMD_UNIT}"' "${DEPLOY_SH}" ||
  fail "scheduler is not restarted with the API service"
grep -Fq 'training_worker.py' "${DEPLOY_SH}" &&
  fail "placeholder training worker must not be started as an unconfigured daemon"

echo "[test_deploy_scheduler_units] OK"
