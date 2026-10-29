#!/usr/bin/env bash
# 为 TPU-MLIR 创建 Python 3.10 专用 venv（PyPI tpu-mlir 要求 >=3.10,<3.11）
# LabelU 主服务可继续用 3.11；仅 BM1688 bmodel 编译使用本 venv。
#
# 用法:
#   sudo env VENV_TPU_MLIR=/data/labeIU/labeIuMedia/venv-tpu-mlir \
#     TPU_MLIR_ROOT=/data/labeIU/tpu-mlir \
#     bash scripts/ensure_tpu_mlir_py310_venv.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/lib/tpu_mlir_pip_repair.sh
source "${SCRIPT_DIR}/lib/tpu_mlir_pip_repair.sh"

log() { echo "[tpu-py310] $*"; }

# 仅允许 labeIU / 本机 miniconda 下的 Python，勿引用其它项目路径
_py310_path_ok() {
  local p="$1"
  [[ -n "$p" && -x "$p" ]] || return 1
  case "$p" in
    *Smart_RTSP*|*qmodel*) return 1 ;;
  esac
  case "$p" in
    /data/labeIU/*|/root/miniconda3/*) return 0 ;;
  esac
  # 系统 python3.10
  [[ "$p" == *python3.10* ]] && return 0
  return 1
}

VENV_TPU_MLIR="${VENV_TPU_MLIR:-/data/labeIU/labeIuMedia/venv-tpu-mlir}"
TPU_MLIR_ROOT="${TPU_MLIR_ROOT:-/data/labeIU/tpu-mlir}"
PY310=""

pick_python310() {
  if [[ -n "${PYTHON310_BIN:-}" ]]; then
    if _py310_path_ok "${PYTHON310_BIN}"; then
      PY310="${PYTHON310_BIN}"
      return 0
    fi
    log "拒绝 PYTHON310_BIN（非 labeIU/miniconda 路径）: ${PYTHON310_BIN}"
    exit 1
  fi
  if _py310_path_ok /root/miniconda3/envs/tpu310/bin/python3; then
    PY310="/root/miniconda3/envs/tpu310/bin/python3"
    return 0
  fi
  for c in python3.10 /usr/bin/python3.10; do
    if command -v "${c}" >/dev/null 2>&1 && "${c}" -c 'import sys; assert sys.version_info[:2]==(3,10)' 2>/dev/null; then
      PY310="$(command -v "${c}")"
      return 0
    fi
  done
  if command -v conda >/dev/null 2>&1 && [[ -x /root/miniconda3/bin/conda ]]; then
    if /root/miniconda3/bin/conda env list | grep -qE '^\s*tpu310\s'; then
      PY310="/root/miniconda3/envs/tpu310/bin/python3"
      _py310_path_ok "${PY310}" && return 0
    fi
    log "在 /root/miniconda3 创建 conda 环境 tpu310 (python=3.10)…"
    /root/miniconda3/bin/conda create -y -n tpu310 python=3.10 pip
    PY310="/root/miniconda3/envs/tpu310/bin/python3"
    _py310_path_ok "${PY310}" && return 0
  fi
  return 1
}

if ! pick_python310; then
  log "错误: 未找到 Python 3.10（仅 labeIU /root/miniconda3）。请任选其一:"
  log "  apt install python3.10 python3.10-venv python3.10-dev"
  log "  /root/miniconda3/bin/conda create -y -n tpu310 python=3.10"
  exit 1
fi
log "使用 Python 3.10: ${PY310} ($("${PY310}" -V 2>&1))"

if [[ ! -x "${VENV_TPU_MLIR}/bin/python" ]]; then
  log "创建 venv: ${VENV_TPU_MLIR}"
  "${PY310}" -m venv "${VENV_TPU_MLIR}"
fi

# shellcheck source=/dev/null
source "${VENV_TPU_MLIR}/bin/activate"
# 勿 source tpu-mlir/envsetup.sh：会污染 PYTHONPATH，导致 pip 的 mlir 无法 import
unset PYTHONPATH
export PATH="${VENV_TPU_MLIR}/bin:${PATH}"

pip install -U pip "setuptools>=70,<82" wheel
pip install psutil tqdm numpy
if ! pip install --no-cache-dir "tpu-mlir[onnx]"; then
  log "重试: pip install tpu_mlir[onnx] …"
  pip install --no-cache-dir "tpu_mlir[onnx]"
fi
pip show tpu-mlir 2>/dev/null || pip show tpu_mlir 2>/dev/null || true

VPY="${VENV_TPU_MLIR}/bin/python3"
tpu_mlir_pip_repair "${VPY}"
if ! tpu_mlir_tools_ok "${VPY}"; then
  log "错误: model_transform.py 仍不可用（bin 可能指向 tpu-mlir/install 但未 ./build.sh）"
  log "诊断: head -3 ${VENV_TPU_MLIR}/bin/model_transform.py"
  log "      find ${VENV_TPU_MLIR}/lib -name model_transform.py | head"
  exit 1
fi
export PATH="${VENV_TPU_MLIR}/bin:${PATH}"
log "model_transform.py --help OK ($(command -v model_transform.py))"

deactivate 2>/dev/null || true

CFG="${LABELU_APPDATA:-/data/labeIU/labelu_appdata}/bm1688_compiler_config.json"
if [[ -f "${CFG}" ]] && command -v python3 >/dev/null 2>&1; then
  python3 <<PY
import json
from pathlib import Path
p = Path("${CFG}")
data = json.loads(p.read_text(encoding="utf-8"))
data["tpu_mlir_python"] = "${VENV_TPU_MLIR}/bin/python3"
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("已写入 tpu_mlir_python ->", data["tpu_mlir_python"])
PY
fi

log "完成。BM1688 编译将使用: ${VENV_TPU_MLIR}/bin/python3"
log "LabelU 主 venv (3.11) 无需改动。"
