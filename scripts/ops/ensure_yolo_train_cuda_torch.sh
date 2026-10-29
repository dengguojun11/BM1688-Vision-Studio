#!/usr/bin/env bash
# 在 LabelU 部署 venv 中安装/校验 CUDA 版 PyTorch（YOLO 训练与 conda base 无关）。
#
# venv 默认在运行目录，不在代码仓库内：
#   /data/labeIU/labeIuMedia/venv   （与 deploy.sh 的 SERVER_RUNTIME_DIR 一致）
#
# 用法（在代码根 /data/labeIU/labeIuproject/labeIU）:
#   sudo bash scripts/ops/ensure_yolo_train_cuda_torch.sh
#   VENV_DIR=/data/labeIU/labeIuMedia/venv sudo bash scripts/ops/ensure_yolo_train_cuda_torch.sh
#   TORCH_CUDA_INDEX=cu128 sudo bash scripts/ops/ensure_yolo_train_cuda_torch.sh
#
# TORCH_CUDA_INDEX: cu124（默认）或 cu128

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

resolve_default_venv_dir() {
  if [[ -n "${VENV_DIR:-}" ]]; then
    echo "${VENV_DIR}"
    return
  fi
  if [[ -f "${REPO_ROOT}/pyproject.toml" ]]; then
    local parent grand
    parent="$(dirname "${REPO_ROOT}")"
    if [[ "$(basename "${parent}")" == "labeIuproject" ]]; then
      grand="$(dirname "${parent}")"
      echo "${grand}/labeIuMedia/venv"
      return
    fi
  fi
  echo "/data/labeIU/labeIuMedia/venv"
}

VENV_DIR="$(resolve_default_venv_dir)"
PY="${VENV_DIR}/bin/python"
PIP="${VENV_DIR}/bin/pip"
INDEX="${TORCH_CUDA_INDEX:-cu124}"
WHEEL_INDEX="https://download.pytorch.org/whl/${INDEX}"

log() { echo "[yolo-cuda-torch] $*"; }

if [[ ! -x "${PY}" ]]; then
  log "错误: 未找到 ${PY}"
  log "提示: LabelU venv 在运行目录 labeIuMedia/venv，不在代码仓库 labeIuproject/labeIU/venv"
  log "可先执行: cd ${REPO_ROOT} && sudo bash deploy.sh restart"
  exit 1
fi

log "VENV_DIR=${VENV_DIR}"
log "Python: ${PY} ($("${PY}" -V 2>&1))"
log "检查 torch.cuda.is_available() …"
if "${PY}" -c "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
  "${PY}" -c "import torch; print('OK:', torch.__version__, 'cuda', torch.version.cuda, 'count', torch.cuda.device_count())"
  exit 0
fi

log "当前 venv 无可用 CUDA，从 ${WHEEL_INDEX} 安装 torch/torchvision …"
"${PIP}" install --upgrade pip
"${PIP}" install --upgrade torch torchvision --index-url "${WHEEL_INDEX}"

if "${PY}" -c "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)"; then
  "${PY}" -c "import torch; print('OK:', torch.__version__, 'cuda', torch.version.cuda, 'count', torch.cuda.device_count())"
  log "完成。请执行: sudo systemctl restart labelu"
  exit 0
fi

log "错误: 安装后 cuda 仍不可用，请检查 nvidia-smi、驱动与 TORCH_CUDA_INDEX（可试 cu128）"
exit 1
