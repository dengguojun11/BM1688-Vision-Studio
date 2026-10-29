#!/usr/bin/env bash
set -euo pipefail
ROOT="/data/labeIU/labeIuproject/labeIU"
VENV="/data/labeIU/labeIuMedia/venv/bin/python"
ENV_FILE="/data/labeIU/labeIuMedia/.env"
cd "$ROOT"

python3 /tmp/patch_edge_ssh_sophon_root.py

PASS="${LABELU_ADMIN_PASSWORD:-}"
if [ -z "$PASS" ] && [ -f "$ENV_FILE" ]; then
  for key in LABELU_BOOTSTRAP_ADMIN_PASSWORD ADMIN_PASSWORD LABELU_ADMIN_PASSWORD; do
    val=$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)
    if [ -n "$val" ]; then PASS="$val"; break; fi
  done
fi
if [ -z "$PASS" ]; then
  PASS="Admin@123"
  echo "WARN: use bootstrap default password"
fi

export LABELU_ADMIN_PASSWORD="$PASS"
TASK_ID="${1:-14}"
EPOCHS="${2:-50}"

echo "=== pipeline task_id=$TASK_ID epochs=$EPOCHS ==="
"$VENV" scripts/run_yolo_full_pipeline.py \
  --base http://127.0.0.1:10001/api/v1 \
  --user admin@123.com \
  --password "$PASS" \
  --task-id "$TASK_ID" \
  --epochs "$EPOCHS" \
  --category "闭环巡检-$(date +%m%d%H%M)"
