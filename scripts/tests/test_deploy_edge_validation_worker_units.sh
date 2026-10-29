#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_SH="${ROOT}/deploy.sh"

fail() {
  echo "[test_deploy_edge_validation_worker_units] FAIL: $*" >&2
  exit 1
}

grep -Fq 'readonly EDGE_VALIDATION_WORKER_SYSTEMD_UNIT="labelu-edge-validation-worker.service"' "${DEPLOY_SH}" ||
  fail "edge validation worker systemd unit is not declared"
grep -Fq 'LABELU_ENABLE_EDGE_VALIDATION_WORKER="${LABELU_ENABLE_EDGE_VALIDATION_WORKER:-0}"' "${DEPLOY_SH}" ||
  fail "edge validation worker is not opt-in by default"
grep -Fq 'Environment=YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED=true' "${DEPLOY_SH}" ||
  fail "edge worker unit does not enable dedicated execution"
grep -Fq 'Environment=YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED=false' "${DEPLOY_SH}" ||
  fail "API unit does not disable Web-side edge execution when separated"
grep -Fq 'write_and_enable_edge_validation_worker_systemd' "${DEPLOY_SH}" ||
  fail "edge worker unit is not rendered with the API unit"
grep -Fq -- '-m labelu.scripts.edge_validation_worker --interval ${interval} --limit ${limit}' "${DEPLOY_SH}" ||
  fail "edge worker unit does not use the bounded CLI"
grep -Fq 'deploy_edge_validation_worker_values_valid' "${DEPLOY_SH}" ||
  fail "edge worker numeric settings are not validated"
grep -Fq 'NoNewPrivileges=true' "${DEPLOY_SH}" || fail "edge worker lacks NoNewPrivileges"
grep -Fq 'ProtectSystem=strict' "${DEPLOY_SH}" || fail "edge worker lacks ProtectSystem"
grep -Fq 'PrivateTmp=true' "${DEPLOY_SH}" || fail "edge worker lacks PrivateTmp"
grep -Fq 'ProtectHome=true' "${DEPLOY_SH}" || fail "edge worker lacks ProtectHome"
grep -Fq 'systemctl stop "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}"' "${DEPLOY_SH}" ||
  fail "edge worker is not stopped by lifecycle commands"
grep -Fq 'deploy_checklist_edge_validation_worker' "${DEPLOY_SH}" ||
  fail "edge worker is missing from the deployment checklist"

validator="$(sed -n '/^deploy_edge_validation_worker_values_valid()/,/^}/p' "${DEPLOY_SH}")"
eval "${validator}"
LABELU_EDGE_VALIDATION_WORKER_INTERVAL=5 \
LABELU_EDGE_VALIDATION_WORKER_BATCH_LIMIT=1 \
deploy_edge_validation_worker_values_valid || fail "default edge worker settings rejected"
if LABELU_EDGE_VALIDATION_WORKER_INTERVAL=0 deploy_edge_validation_worker_values_valid; then
  fail "zero interval accepted"
fi
if LABELU_EDGE_VALIDATION_WORKER_BATCH_LIMIT=bad deploy_edge_validation_worker_values_valid; then
  fail "invalid batch limit accepted"
fi

echo "[test_deploy_edge_validation_worker_units] OK"
