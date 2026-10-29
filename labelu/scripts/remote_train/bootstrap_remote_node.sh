#!/usr/bin/env bash
# 远程 GPU 机一次性环境准备（路径可按本机习惯修改）
set -euo pipefail

VENV_DIR="${VENV_DIR:-/opt/labelu-train-venv}"
AGENT_DIR="${AGENT_DIR:-/opt/labelu-remote-train-agent}"
WORK_ROOT="${WORK_ROOT:-/data/labelu_remote_train}"
WEIGHTS_CACHE="${WEIGHTS_CACHE:-$WORK_ROOT/_cache/weights}"

python3.11 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -U pip
"$VENV_DIR/bin/pip" install torch ultralytics pyyaml

mkdir -p "$AGENT_DIR" "$WORK_ROOT" "$WEIGHTS_CACHE"
echo "Copy agent_worker.py to: $AGENT_DIR/agent_worker.py"
echo "Platform node config:"
echo "  python_path=$VENV_DIR/bin/python"
echo "  agent_path=$AGENT_DIR/agent_worker.py"
echo "  remote_base=$WORK_ROOT"
echo "  weights_cache_dir=$WEIGHTS_CACHE"
"$VENV_DIR/bin/python" -c "import torch, ultralytics; print('cuda', torch.cuda.is_available(), 'devices', torch.cuda.device_count())"
