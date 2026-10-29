#!/usr/bin/env bash
# 生产环境：准备 TPU-MLIR 目录 + 克隆源码树（含 envsetup.sh）+ 在指定 venv 中 pip 安装
# LABELU_DEPLOY_STRICT=1 时失败以 exit 1 中止 deploy（默认由 deploy.sh 在 yolo 模式下开启）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/ops/git_clone_retry.sh
source "${SCRIPT_DIR}/ops/git_clone_retry.sh"
# shellcheck source=scripts/ops/offline_assets_common.sh
source "${SCRIPT_DIR}/ops/offline_assets_common.sh"
OFFLINE_ASSETS_LOG_PREFIX="[tpu-mlir-bootstrap]"
OFFLINE_REPO_ROOT="${REPO_ROOT}"
GIT_CLONE_RETRY_LOG_PREFIX="[tpu-mlir-bootstrap]"

log() { echo "[tpu-mlir-bootstrap] $*"; }

# envsetup.sh 末尾用相对路径 cp hooks/* → .git/hooks，必须在 TPU_MLIR_ROOT 下 source
#（在 labeIuproject/labeIU 目录 source 会 cp: cannot stat 'hooks/*'，且 set -e 下脚本 exit 1）
source_tpu_mlir_envsetup() {
  export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
  export PYTHONPATH="${PYTHONPATH:-}"
  local prev_cwd="${PWD}"
  cd "${TPU_MLIR_ROOT}"
  set +u
  # shellcheck source=/dev/null
  source "${TPU_MLIR_ROOT}/envsetup.sh"
  local rc=$?
  set -u
  cd "${prev_cwd}" || exit 1
  if [[ "${rc}" -ne 0 ]]; then
    log "错误: envsetup.sh 返回 ${rc}"
    exit 1
  fi
}

tpu_strict_fail() {
  log "$1"
  if [[ "${LABELU_DEPLOY_STRICT:-}" == "1" ]] \
    || [[ "${LABELU_DEPLOY_STRICT:-}" == "y" ]] \
    || [[ "${LABELU_DEPLOY_STRICT:-}" == "yes" ]]; then
    exit 1
  fi
  exit 0
}

VENV_DIR="${VENV_DIR:?请设置 VENV_DIR（LabelU 的 venv 根目录）}"
VENV_TPU_MLIR="${VENV_TPU_MLIR:-$(dirname "${VENV_DIR}")/venv-tpu-mlir}"
TPU_MLIR_ROOT="${TPU_MLIR_ROOT:-/data/labeIU/tpu-mlir}"
TPU_TMP_DIR="${TPU_TMP_DIR:-/data/labeIU/tpu_tmp}"
TPU_MLIR_GIT_URL="${TPU_MLIR_GIT_URL:-https://gitee.com/mirrors/TPU-MLIR.git}"
TPU_MLIR_GIT_DEPTH="${TPU_MLIR_GIT_DEPTH:-1}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  log "错误: 未找到虚拟环境: ${VENV_DIR}/bin/python"
  exit 1
fi

mkdir -p "${TPU_TMP_DIR}"
chmod 1777 "${TPU_TMP_DIR}" 2>/dev/null || chmod 755 "${TPU_TMP_DIR}" || true

bootstrap_try_offline_tpu_mlir() {
  local archive=""
  archive="$(offline_find_archive "tpu-mlir" "TPU_MLIR_OFFLINE_ARCHIVE" || true)"
  if [[ -z "${archive}" ]]; then
    return 1
  fi
  log "使用离线包（跳过 git clone）: ${archive}"
  offline_extract_archive "${archive}" "${TPU_MLIR_ROOT}" "envsetup.sh"
}

bootstrap_clone_tpu_mlir() {
  if [[ -f "${TPU_MLIR_ROOT}/envsetup.sh" ]]; then
    log "已存在 TPU-MLIR 源码树: ${TPU_MLIR_ROOT}（保留，不重新 clone）"
    return 0
  fi

  if bootstrap_try_offline_tpu_mlir; then
    return 0
  fi

  log "未找到离线包，准备 git clone TPU-MLIR 至: ${TPU_MLIR_ROOT}"
  mkdir -p "$(dirname "${TPU_MLIR_ROOT}")"

  if [[ -e "${TPU_MLIR_ROOT}" ]] && [[ ! -d "${TPU_MLIR_ROOT}" ]]; then
    log "错误: ${TPU_MLIR_ROOT} 已存在且不是目录"
    exit 1
  fi

  if [[ -d "${TPU_MLIR_ROOT}" ]]; then
    local local_any
    local_any="$(ls -A "${TPU_MLIR_ROOT}" 2>/dev/null || true)"
    if [[ -n "${local_any}" ]] && [[ ! -d "${TPU_MLIR_ROOT}/.git" ]]; then
      log "清理非 git 残留目录后重试 clone"
      rm -rf "${TPU_MLIR_ROOT}"
    fi
  fi

  if ! command -v git >/dev/null 2>&1; then
    log "错误: 未找到 git，请先: apt-get install -y git"
    exit 1
  fi

  if [[ -d "${TPU_MLIR_ROOT}/.git" ]]; then
    log "已存在 git 仓库但未发现 envsetup.sh，尝试 git pull"
    set +e
    git -C "${TPU_MLIR_ROOT}" pull --ff-only
    set -e
    return 0
  fi

  if ! git_clone_retry "${TPU_MLIR_GIT_DEPTH}" "${TPU_MLIR_GIT_URL}" "${TPU_MLIR_ROOT}"; then
    tpu_strict_fail "错误: TPU-MLIR 克隆失败（已重试 ${GIT_CLONE_RETRIES:-8} 次）"
  fi
}

bootstrap_clone_tpu_mlir

if [[ ! -f "${TPU_MLIR_ROOT}/envsetup.sh" ]]; then
  tpu_strict_fail "错误: 仍缺少 ${TPU_MLIR_ROOT}/envsetup.sh"
fi

log "确认: envsetup.sh 存在"

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
log "使用 Python: $(python -V 2>&1)"
py_major="$(python -c 'import sys; print(sys.version_info[0])')"
py_minor="$(python -c 'import sys; print(sys.version_info[1])')"

# shellcheck source=scripts/lib/tpu_mlir_pip_repair.sh
[[ -f "${SCRIPT_DIR}/lib/tpu_mlir_pip_repair.sh" ]] && source "${SCRIPT_DIR}/lib/tpu_mlir_pip_repair.sh"

bootstrap_py310_venv() {
  local ensure="${SCRIPT_DIR}/ensure_tpu_mlir_py310_venv.sh"
  if [[ ! -f "${ensure}" ]]; then
    return 1
  fi
  log "调用 ensure_tpu_mlir_py310_venv.sh（Python 3.10 专用 venv）…"
  env VENV_TPU_MLIR="${VENV_TPU_MLIR}" TPU_MLIR_ROOT="${TPU_MLIR_ROOT}" bash "${ensure}"
}

if [[ "${py_major}" != "3" ]] || [[ "${py_minor}" -ge 11 ]]; then
  log "提示: tpu-mlir PyPI 要求 Python>=3.10,<3.11；LabelU 主 venv 为 3.${py_minor}，不在此安装"
  deactivate 2>/dev/null || true
  if [[ -f "${TPU_MLIR_ROOT}/install/python/tools/model_transform.py" ]]; then
    log "TPU-MLIR 源码已 build（install/python/tools），BM1688 可用 envsetup + venv-tpu-mlir"
    exit 0
  fi
  if [[ -x "${VENV_TPU_MLIR}/bin/python3" ]] && declare -f tpu_mlir_tools_ok >/dev/null \
    && tpu_mlir_tools_ok "${VENV_TPU_MLIR}/bin/python3"; then
    log "TPU-MLIR 工具已在 ${VENV_TPU_MLIR} 就绪"
    exit 0
  fi
  if bootstrap_py310_venv; then
    if [[ -x "${VENV_TPU_MLIR}/bin/python3" ]] && declare -f tpu_mlir_tools_ok >/dev/null \
      && tpu_mlir_tools_ok "${VENV_TPU_MLIR}/bin/python3"; then
      log "TPU-MLIR 引导完成（venv-tpu-mlir）"
      exit 0
    fi
  fi
  if [[ "${LABELU_DEPLOY_RESTART:-}" == "1" ]] && [[ -f "${TPU_MLIR_ROOT}/envsetup.sh" ]]; then
    log "警告: restart 模式下 venv-tpu-mlir 未就绪，不阻断主服务；请稍后执行 ensure_tpu_mlir_py310_venv.sh"
    exit 0
  fi
  tpu_strict_fail "错误: 请执行: sudo env VENV_TPU_MLIR=${VENV_TPU_MLIR} bash scripts/ensure_tpu_mlir_py310_venv.sh"
fi

log "安装 TPU-MLIR Python 工具依赖 (psutil tqdm numpy)"
pip install -q psutil tqdm numpy || tpu_strict_fail "pip install psutil tqdm numpy 失败"

log "安装 tpu-mlir PyPI 包（当前 venv 为 3.${py_minor}）"
set +e
unset PYTHONPATH
export PATH="${VENV_DIR}/bin:${PATH}"
pip install --upgrade pip "setuptools>=70,<82" wheel
pip install --no-cache-dir "tpu-mlir[onnx]" 2>/dev/null || pip install --no-cache-dir "tpu_mlir[onnx]"
pip_rc=$?
set -e
if [[ "${pip_rc}" -ne 0 ]]; then
  log "警告: pip install tpu-mlir 返回 ${pip_rc}"
fi

if declare -f tpu_mlir_pip_repair >/dev/null; then
  set +e
  tpu_mlir_pip_repair "${VENV_DIR}/bin/python3"
  repair_rc=$?
  set -e
  if [[ "${repair_rc}" -ne 0 ]]; then
    log "警告: LabelU venv pip repair 未完成，尝试 venv-tpu-mlir"
    deactivate 2>/dev/null || true
    bootstrap_py310_venv || true
  elif tpu_mlir_tools_ok "${VENV_DIR}/bin/python3"; then
    log "model_transform.py --help OK (pip venv)"
  else
    log "警告: LabelU venv 中 pip 工具未就绪；BM1688 请使用 venv-tpu-mlir"
  fi
elif [[ -f "${TPU_MLIR_ROOT}/install/python/tools/model_transform.py" ]]; then
  source_tpu_mlir_envsetup
  export PATH="${VENV_DIR}/bin:${PATH}"
  if model_transform.py --help >/dev/null 2>&1; then
    log "model_transform.py --help OK (源码 install)"
  else
    tpu_strict_fail "model_transform.py --help 失败。请 pip install 或 cd ${TPU_MLIR_ROOT} && ./build.sh"
  fi
else
  log "提示: 未 build TPU-MLIR 源码。BM1688 编译: bash scripts/ensure_tpu_mlir_py310_venv.sh"
fi

log "TPU-MLIR 引导完成。TPU_MLIR_ROOT=${TPU_MLIR_ROOT}"
deactivate 2>/dev/null || true
