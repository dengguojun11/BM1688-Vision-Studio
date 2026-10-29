#!/usr/bin/env bash
# 边端 bm1688 容器：安装 pythonTest_evaluation.py 所需 Python 依赖
set -euo pipefail
CFG="${1:-/data/labeIU/labelu_appdata/edge_ssh_config.json}"
HOST=$(python3 -c "import json; print(json.load(open('$CFG'))['host'])")
USER=$(python3 -c "import json; print(json.load(open('$CFG'))['user'])")
PASS=$(python3 -c "import json; print(json.load(open('$CFG'))['password'])")
PORT=$(python3 -c "import json; print(json.load(open('$CFG')).get('port',22))")
DC=$(python3 -c "import json; print(json.load(open('$CFG')).get('docker_container','bm1688'))")

REMOTE='set -e
export DEBIAN_FRONTEND=noninteractive
PY=
for p in python3.10 python3.8 python3; do
  command -v $p >/dev/null 2>&1 || continue
  $p -c "import cv2" >/dev/null 2>&1 && PY=$p && break
done
if [ -z "$PY" ]; then PY=python3; fi
echo "using $PY $($PY --version 2>&1)"
apt-get update -qq
apt-get install -y -qq python3-pip 2>/dev/null || true
$PY -m pip install --upgrade "pip<24" setuptools wheel 2>/dev/null || true
$PY -m pip install tqdm numpy 2>/dev/null || $PY -m pip install tqdm numpy --user
$PY -c "import cv2, numpy, tqdm; print(\"ok cv2\", cv2.__version__, \"tqdm\", tqdm.__version__)"'

sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -p "$PORT" "$USER@$HOST" \
  "sudo docker exec $DC bash -lc $(printf '%q' "$REMOTE")"
