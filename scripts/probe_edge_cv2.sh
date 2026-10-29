#!/usr/bin/env bash
set -euo pipefail
CFG="${1:-/data/labeIU/labelu_appdata/edge_ssh_config.json}"
HOST=$(python3 -c "import json; print(json.load(open('$CFG'))['host'])")
USER=$(python3 -c "import json; print(json.load(open('$CFG'))['user'])")
PASS=$(python3 -c "import json; print(json.load(open('$CFG'))['password'])")
PORT=$(python3 -c "import json; print(json.load(open('$CFG')).get('port',22))")
DC=$(python3 -c "import json; print(json.load(open('$CFG')).get('docker_container','bm1688'))")
REMOTE='for p in python3.10 python3.8 python3; do echo "=== $p ==="; command -v $p 2>/dev/null || continue; $p --version; $p -c "import cv2; print(cv2.__version__)" 2>&1 || echo cv2-missing; done'
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -p "$PORT" "$USER@$HOST" \
  "sudo docker exec $DC bash -lc $(printf '%q' "$REMOTE")"
