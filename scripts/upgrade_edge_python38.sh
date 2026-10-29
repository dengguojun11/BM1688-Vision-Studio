#!/usr/bin/env bash
set -euo pipefail
CFG="${1:-/data/labeIU/labelu_appdata/edge_ssh_config.json}"
HOST=$(python3 -c "import json; print(json.load(open('$CFG'))['host'])")
USER=$(python3 -c "import json; print(json.load(open('$CFG'))['user'])")
PASS=$(python3 -c "import json; print(json.load(open('$CFG'))['password'])")
PORT=$(python3 -c "import json; print(json.load(open('$CFG')).get('port',22))")
DC=$(python3 -c "import json; print(json.load(open('$CFG')).get('docker_container','bm1688'))")

REMOTE='set -e; export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y -qq python3.8 python3.8-venv python3.8-distutils 2>/dev/null || apt-get install -y -qq python3.8; for p in python3.8 python3; do command -v $p && $p --version; done'

sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -p "$PORT" "$USER@$HOST" \
  "sudo docker exec $DC bash -lc $(printf '%q' "$REMOTE")"
