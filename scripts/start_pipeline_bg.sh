#!/usr/bin/env bash
set -euo pipefail
ROOT="/data/labeIU/labeIuproject/labeIU"
VENV="/data/labeIU/labeIuMedia/venv/bin/python"
cd "$ROOT"
sed -i 's/\r$//' scripts/run_yolo_full_pipeline.py scripts/run_pipeline_on_server.sh 2>/dev/null || true
nohup "$VENV" scripts/run_yolo_full_pipeline.py \
  --base http://127.0.0.1:10001/api/v1 \
  --user admin@123.com \
  --password 'Admin@123' \
  --task-id 14 \
  --epochs 50 \
  --category "闭环巡检" \
  > /tmp/pipeline_run.log 2>&1 &
echo "pid=$!"
