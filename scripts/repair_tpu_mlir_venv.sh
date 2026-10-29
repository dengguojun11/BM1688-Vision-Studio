#!/usr/bin/env bash
# 修复 /data/labeIU/labeIuMedia/venv-tpu-mlir（默认轻量：wrapper + torch，不重装 260MB tpu-mlir）
# 全量重装: bash scripts/repair_tpu_mlir_venv.sh --full
# 不 source envsetup.sh，不使用其它项目 Python。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/tpu_mlir_pip_repair.sh
source "${SCRIPT_DIR}/lib/tpu_mlir_pip_repair.sh"

VENV_TPU_MLIR="${VENV_TPU_MLIR:-/data/labeIU/labeIuMedia/venv-tpu-mlir}"
VPY="${VENV_TPU_MLIR}/bin/python3"
if [[ ! -x "${VPY}" ]]; then
  echo "错误: 未找到 ${VPY}，请先运行: bash scripts/ensure_tpu_mlir_py310_venv.sh" >&2
  exit 1
fi
unset PYTHONPATH
if [[ "${1:-}" == "--full" ]]; then
  echo "全量重装 tpu-mlir（约 260MB+，需充足磁盘）…" >&2
  tpu_mlir_pip_repair "${VPY}"
else
  echo "轻量修复（wrapper + 缺省依赖 + torch CPU，不重装 tpu-mlir）…" >&2
  tpu_mlir_pip_repair_light "${VPY}"
fi
if ! tpu_mlir_tools_ok "${VPY}"; then
  echo "错误: model_transform.py 仍不可用" >&2
  echo "  head -5 ${VENV_TPU_MLIR}/bin/model_transform.py" >&2
  echo "  find ${VENV_TPU_MLIR}/lib -name model_transform.py | head -5" >&2
  PYTHONPATH="" model_transform.py --help 2>&1 | tail -20 >&2 || true
  exit 1
fi
echo "OK: $(command -v model_transform.py)"
PYTHONPATH="" model_transform.py --help | head -3
