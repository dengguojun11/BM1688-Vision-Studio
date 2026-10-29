#!/usr/bin/env bash
# 从已完成 export 继续：边端验证 + AICM 发布
set -euo pipefail
EXPORT_ID="${1:?export id}"
ROOT="/data/labeIU/labeIuproject/labeIU"
VENV="/data/labeIU/labeIuMedia/venv/bin/python"
cd "$ROOT"
git pull origin main -q || true
LABELU_SKIP_FRONTEND_BUILD=1 bash deploy.sh update >/tmp/deploy_continue.log 2>&1 || true
"$VENV" scripts/retry_edge_ssh_validation.py "$EXPORT_ID"
"$VENV" scripts/run_yolo_full_pipeline.py \
  --base http://127.0.0.1:10001/api/v1 \
  --user admin@123.com \
  --password 'Admin@123' \
  --task-id 14 \
  --skip-train --job-id 78 \
  --epochs 50
