#!/usr/bin/env bash
# 在边端 bm1688 容器内为 python3.8 安装 pythonTest 依赖（opencv 等）
set -euo pipefail
CFG="${1:-/data/labeIU/labelu_appdata/edge_ssh_config.json}"
HOST=$(python3 -c "import json; print(json.load(open('$CFG'))['host'])")
USER=$(python3 -c "import json; print(json.load(open('$CFG'))['user'])")
PASS=$(python3 -c "import json; print(json.load(open('$CFG'))['password'])")
PORT=$(python3 -c "import json; print(json.load(open('$CFG')).get('port',22))")
DC=$(python3 -c "import json; print(json.load(open('$CFG')).get('docker_container','bm1688'))")

REMOTE='set -e
export DEBIAN_FRONTEND=noninteractive
PY=python3.8
command -v $PY || { echo "python3.8 not found"; exit 1; }
$PY --version
if $PY -c "import cv2" 2>/dev/null; then
  echo "cv2 already ok for $PY"
  $PY -c "import cv2; print(cv2.__version__)"
  exit 0
fi
apt-get update -qq
apt-get install -y -qq python3-pip 2>/dev/null || true
$PY -m pip install --upgrade pip setuptools wheel 2>/dev/null || python3 -m pip install --upgrade pip
# headless 避免 GUI 依赖；arm64 容器内常见可用
$PY -m pip install opencv-python-headless numpy 2>/dev/null || $PY -m pip install opencv-python-headless numpy --user
$PY -c "import cv2; import numpy; print(\"cv2\", cv2.__version__, \"numpy\", numpy.__version__)"'

sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -p "$PORT" "$USER@$HOST" \
  "sudo docker exec $DC bash -lc $(printf '%q' "$REMOTE")"
