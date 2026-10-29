#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_SH="${ROOT}/deploy.sh"

fail() {
  echo "[test_deploy_bm1688_worker_units] FAIL: $*" >&2
  exit 1
}

grep -Fq 'readonly BM1688_WORKER_SYSTEMD_UNIT="labelu-bm1688-worker.service"' "${DEPLOY_SH}" ||
  fail "BM1688 worker systemd unit is not declared"
grep -Fq 'LABELU_ENABLE_BM1688_WORKER="${LABELU_ENABLE_BM1688_WORKER:-0}"' "${DEPLOY_SH}" ||
  fail "BM1688 worker is not opt-in by default"
grep -Fq 'Environment=LABELU_BM1688_DEDICATED_WORKER_ENABLED=true' "${DEPLOY_SH}" ||
  fail "API and worker units do not receive the dedicated-worker switch"
grep -Fq 'Environment=LABELU_BM1688_DEDICATED_WORKER_ENABLED=false' "${DEPLOY_SH}" ||
  fail "API unit does not explicitly disable the dedicated-worker switch"
grep -Fq 'write_and_enable_bm1688_worker_systemd' "${DEPLOY_SH}" ||
  fail "BM1688 worker unit is not rendered with the API unit"
grep -Fq -- '-m labelu.scripts.bm1688_worker --interval ${interval} --lease-seconds ${lease} --limit ${limit}' "${DEPLOY_SH}" ||
  fail "BM1688 worker unit does not use the dedicated CLI with bounded arguments"
grep -Fq 'deploy_bm1688_worker_values_valid' "${DEPLOY_SH}" ||
  fail "BM1688 worker numeric settings are not validated"
grep -Fq 'MemoryMax=${memory_max}' "${DEPLOY_SH}" || fail "BM1688 worker lacks MemoryMax"
grep -Fq 'CPUQuota=${cpu_quota}' "${DEPLOY_SH}" || fail "BM1688 worker lacks CPUQuota"
grep -Fq 'TasksMax=${tasks_max}' "${DEPLOY_SH}" || fail "BM1688 worker lacks TasksMax"
grep -Fq 'NoNewPrivileges=true' "${DEPLOY_SH}" || fail "worker unit lacks NoNewPrivileges"
grep -Fq 'ProtectSystem=strict' "${DEPLOY_SH}" || fail "worker unit lacks ProtectSystem"
grep -Fq 'PrivateTmp=true' "${DEPLOY_SH}" || fail "worker unit lacks PrivateTmp"
grep -Fq 'ProtectHome=true' "${DEPLOY_SH}" || fail "worker unit lacks ProtectHome"
grep -Fq 'ReadWritePaths=${SERVER_RUNTIME_DIR} ${SERVER_APPDATA_DIR} ${SERVER_TPU_TMP} ${SERVER_TPU_MLIR_ROOT}' "${DEPLOY_SH}" ||
  fail "BM1688 worker writable paths are not bounded"
grep -Fq 'systemctl stop "${BM1688_WORKER_SYSTEMD_UNIT}"' "${DEPLOY_SH}" ||
  fail "BM1688 worker is not stopped by lifecycle commands"
grep -Fq 'deploy_checklist_bm1688_worker' "${DEPLOY_SH}" ||
  fail "BM1688 worker is missing from the deployment checklist"

# Exercise the pure numeric validator without sourcing deploy.sh (which would
# resolve the host deployment layout and may touch system tools).
validator="$(sed -n '/^deploy_bm1688_worker_values_valid()/,/^}/p' "${DEPLOY_SH}")"
eval "${validator}"
LABELU_BM1688_WORKER_INTERVAL=2 \
LABELU_BM1688_WORKER_LEASE_SECONDS=300 \
LABELU_BM1688_WORKER_BATCH_LIMIT=1 \
LABELU_BM1688_WORKER_MEMORY_MAX=16G \
LABELU_BM1688_WORKER_CPU_QUOTA=400% \
LABELU_BM1688_WORKER_TASKS_MAX=1024 \
deploy_bm1688_worker_values_valid || fail "default resource settings rejected"
if LABELU_BM1688_WORKER_MEMORY_MAX=0G deploy_bm1688_worker_values_valid; then
  fail "invalid MemoryMax accepted"
fi
if LABELU_BM1688_WORKER_MEMORY_MAX=512M deploy_bm1688_worker_values_valid; then
  fail "too-small MemoryMax accepted"
fi
if LABELU_BM1688_WORKER_CPU_QUOTA=0% deploy_bm1688_worker_values_valid; then
  fail "invalid CPUQuota accepted"
fi
if LABELU_BM1688_WORKER_TASKS_MAX=bad deploy_bm1688_worker_values_valid; then
  fail "invalid TasksMax accepted"
fi

echo "[test_deploy_bm1688_worker_units] OK"
