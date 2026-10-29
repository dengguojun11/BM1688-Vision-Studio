#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_SH="${ROOT}/deploy.sh"

fail() {
  echo "[test_deploy_experiment_dataset_worker_units] FAIL: $*" >&2
  exit 1
}

grep -Fq 'readonly EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT="labelu-experiment-dataset-worker.service"' "${DEPLOY_SH}" ||
  fail "S3 worker systemd unit is not declared"
grep -Fq 'LABELU_ENABLE_EXPERIMENT_DATASET_WORKER="${LABELU_ENABLE_EXPERIMENT_DATASET_WORKER:-0}"' "${DEPLOY_SH}" ||
  fail "S3 worker is not opt-in by default"
grep -Fq 'Environment=EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=true' "${DEPLOY_SH}" ||
  fail "S3 worker unit does not enable the durable worker"
grep -Fq 'Environment=EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=false' "${DEPLOY_SH}" ||
  fail "API unit does not fail closed when S3 worker is disabled"
grep -Fq 'write_and_enable_experiment_dataset_worker_systemd' "${DEPLOY_SH}" ||
  fail "S3 worker unit is not rendered with the API unit"
grep -Fq -- '-m labelu.scripts.experiment_dataset_import_worker --limit ${limit}' "${DEPLOY_SH}" ||
  fail "S3 worker unit does not use the dedicated CLI"
grep -Fq 'deploy_experiment_dataset_worker_values_valid' "${DEPLOY_SH}" ||
  fail "S3 worker numeric settings are not validated"
grep -Fq 'NoNewPrivileges=true' "${DEPLOY_SH}" || fail "S3 worker lacks NoNewPrivileges"
grep -Fq 'ProtectSystem=strict' "${DEPLOY_SH}" || fail "S3 worker lacks ProtectSystem"
grep -Fq 'PrivateTmp=true' "${DEPLOY_SH}" || fail "S3 worker lacks PrivateTmp"
grep -Fq 'ProtectHome=true' "${DEPLOY_SH}" || fail "S3 worker lacks ProtectHome"
grep -Fq 'ReadWritePaths=${SERVER_RUNTIME_DIR} ${SERVER_APPDATA_DIR}' "${DEPLOY_SH}" ||
  fail "S3 worker writable paths are not bounded"
grep -Fq 'systemctl stop "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"' "${DEPLOY_SH}" ||
  fail "S3 worker is not stopped by lifecycle commands"
grep -Fq 'systemctl start "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"' "${DEPLOY_SH}" ||
  fail "S3 worker is not started by lifecycle commands"
grep -Fq 'systemctl restart "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"' "${DEPLOY_SH}" ||
  fail "S3 worker is not restarted by lifecycle commands"
grep -Fq 'deploy_checklist_experiment_dataset_worker' "${DEPLOY_SH}" ||
  fail "S3 worker is missing from the deployment checklist"

validator="$(sed -n '/^deploy_experiment_dataset_worker_values_valid()/,/^}/p' "${DEPLOY_SH}")"
eval "${validator}"
EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS=5 \
EXPERIMENT_DATASET_WORKER_LEASE_SECONDS=300 \
EXPERIMENT_DATASET_WORKER_BATCH_SIZE=1 \
deploy_experiment_dataset_worker_values_valid || fail "default S3 worker settings rejected"
if EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS=0 deploy_experiment_dataset_worker_values_valid; then
  fail "zero interval accepted"
fi
if EXPERIMENT_DATASET_WORKER_LEASE_SECONDS=bad deploy_experiment_dataset_worker_values_valid; then
  fail "invalid lease accepted"
fi
if EXPERIMENT_DATASET_WORKER_BATCH_SIZE=0 deploy_experiment_dataset_worker_values_valid; then
  fail "zero batch size accepted"
fi

echo "[test_deploy_experiment_dataset_worker_units] OK"
