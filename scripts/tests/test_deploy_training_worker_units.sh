#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_SH="${ROOT}/deploy.sh"

fail() {
  echo "[test_deploy_training_worker_units] FAIL: $*" >&2
  exit 1
}

grep -Fq 'readonly WORKER_SYSTEMD_UNIT="labelu-training-worker.service"' "${DEPLOY_SH}" ||
  fail "worker systemd unit is not declared"
grep -Fq 'LABELU_ENABLE_TRAINING_WORKER="${LABELU_ENABLE_TRAINING_WORKER:-0}"' "${DEPLOY_SH}" ||
  fail "worker is not opt-in by default"
grep -Fq 'Environment=LABELU_AUTO_TRAINING_WORKER_COMMAND_ENABLED=true' "${DEPLOY_SH}" ||
  fail "API unit does not enable the worker command producer when the worker is enabled"
grep -Fq 'Environment=LABELU_AUTO_TRAINING_WORKER_COMMAND_ENABLED=false' "${DEPLOY_SH}" ||
  fail "API unit does not explicitly disable the worker command producer"
grep -Fq 'if ! deploy_training_worker_images_valid; then' "${DEPLOY_SH}" ||
  fail "worker image allowlist is not validated"
grep -Fq '^[A-Za-z0-9][A-Za-z0-9._/@:-]*$' "${DEPLOY_SH}" ||
  fail "worker image references are not restricted to safe exact syntax"
grep -Fq -- '--outbox --execute-container' "${DEPLOY_SH}" ||
  fail "worker unit does not require container execution"
grep -Fq -- '--allow-image ${image}' "${DEPLOY_SH}" ||
  fail "worker unit does not pass exact allowlisted images"
grep -Fq 'NoNewPrivileges=true' "${DEPLOY_SH}" || fail "worker unit lacks NoNewPrivileges"
grep -Fq 'ProtectSystem=strict' "${DEPLOY_SH}" || fail "worker unit lacks ProtectSystem"
grep -Fq 'PrivateTmp=true' "${DEPLOY_SH}" || fail "worker unit lacks PrivateTmp"
grep -Fq 'ProtectHome=true' "${DEPLOY_SH}" || fail "worker unit lacks ProtectHome"
grep -Fq 'ReadWritePaths=${SERVER_RUNTIME_DIR} ${SERVER_APPDATA_DIR} ${SERVER_TPU_TMP}' "${DEPLOY_SH}" ||
  fail "worker writable paths are not bounded"
grep -Fq 'write_and_enable_training_worker_systemd' "${DEPLOY_SH}" ||
  fail "worker unit is not rendered with the API unit"
grep -Fq 'systemctl stop "${WORKER_SYSTEMD_UNIT}"' "${DEPLOY_SH}" ||
  fail "worker is not stopped by lifecycle commands"
grep -Fq 'deploy_checklist_training_worker' "${DEPLOY_SH}" ||
  fail "worker is missing from deployment checklist"

echo "[test_deploy_training_worker_units] OK"
