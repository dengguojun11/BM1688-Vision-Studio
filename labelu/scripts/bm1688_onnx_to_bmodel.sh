#!/usr/bin/env bash
# LabelU：ONNX → sophon-demo YOLOv8_plus_det models/ 布局
# https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/README.md §3.2
#
# 用法:
#   bash bm1688_onnx_to_bmodel.sh <onnx> <export_root> <imgsz> <chip> <mode> \
#     [cali_dataset] [qtable] [batches] [model_name] [platforms]
#
# 输出目录（与官方 download.sh 一致）:
#   {export_root}/models/onnx/          # 由 LabelU 写入 yolo11s.onnx、yolov11s_qtable
#   {export_root}/models/BM1688/        # yolov11s_fp32_1b.bmodel 等
#   {export_root}/models/BM1684X/
#   {export_root}/models/CV186X/
#
# mode: fp32 | fp16 | int8 | full
# platforms: 逗号分隔，默认 bm1688；可 bm1684x,bm1688,cv186x
set -eo pipefail

ONNX_ABS="${1:?onnx path}"
EXPORT_ROOT="${2:?export root}"
IMGSZ="${3:-640}"
_CHIP_SINGLE="${4:-bm1688}"
MODE="${5:-full}"
CALI_DATASET="${6:-}"
QTABLE="${7:-}"
_MODEL_BATCHES="${8:-1}"
MODEL_NAME="${9:-yolov11n}"
PLATFORMS="${10:-${_CHIP_SINGLE}}"

: "${TPU_MLIR_ROOT:?export TPU_MLIR_ROOT=/path/to/tpu-mlir}"
if [[ ! -d "$TPU_MLIR_ROOT" ]]; then
  echo "TPU_MLIR_ROOT is not a directory: $TPU_MLIR_ROOT" >&2
  exit 1
fi
if [[ ! -f "$TPU_MLIR_ROOT/envsetup.sh" ]]; then
  echo "WARN: $TPU_MLIR_ROOT/envsetup.sh 不存在，将仅使用 pip venv-tpu-mlir（勿 source envsetup）" >&2
fi
export PYTHONPATH="${PYTHONPATH:-}"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"

TPU_MLIR_PIP_EXTRAS=(psutil tqdm numpy)
TPU_MLIR_PIP_MODE=0
_BM1688_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_TPU_MLIR_REPAIR_LIB="${TPU_MLIR_REPAIR_LIB:-${_BM1688_SCRIPT_DIR}/lib/tpu_mlir_pip_repair.sh}"
if [[ ! -f "${_TPU_MLIR_REPAIR_LIB}" ]]; then
  _TPU_MLIR_REPAIR_LIB="/data/labeIU/labeIuproject/labeIU/scripts/lib/tpu_mlir_pip_repair.sh"
fi
if [[ -f "${_TPU_MLIR_REPAIR_LIB}" ]]; then
  # shellcheck source=/dev/null
  source "${_TPU_MLIR_REPAIR_LIB}"
else
  echo "WARN: 未找到 tpu_mlir_pip_repair.sh (${_TPU_MLIR_REPAIR_LIB})" >&2
fi

TPU_MLIR_PRIMARY_PYTHON=""

_install_tpu_mlir_pip_deps() {
  local py="$1"
  [[ -n "$py" && -x "$py" ]] || return 1
  local pkg
  for pkg in "${TPU_MLIR_PIP_EXTRAS[@]}"; do
    "$py" -c "import ${pkg}" 2>/dev/null || "$py" -m pip install -q "${pkg}" || return 1
  done
}

_try_import_mlir() {
  local py="$1"
  PYTHONPATH="" "$py" -c "import mlir" 2>/dev/null
}

_pip_has_tpu_mlir() {
  local py="$1"
  "$py" -m pip show tpu-mlir >/dev/null 2>&1 \
    || "$py" -m pip show tpu_mlir >/dev/null 2>&1
}

# PyPI tpu_mlir wheel 常无顶层 mlir 模块；以编译 CLI 为准
_try_tpu_compile_tools() {
  local py="$1"
  if declare -F tpu_mlir_tools_ok >/dev/null 2>&1; then
    tpu_mlir_tools_ok "$py"
    return $?
  fi
  local py_dir
  py_dir="$(dirname "$py")"
  export PATH="${py_dir}:${PATH}"
  hash -r 2>/dev/null || true
  PYTHONPATH="" command -v model_transform.py >/dev/null 2>&1 \
    && PYTHONPATH="" command -v model_deploy.py >/dev/null 2>&1 \
    && PYTHONPATH="" model_transform.py --help >/dev/null 2>&1
}

_ensure_pip_tpu_mlir() {
  local py="$1"
  if declare -F tpu_mlir_tools_ok >/dev/null 2>&1 && tpu_mlir_tools_ok "$py"; then
    return 0
  fi
  if declare -F tpu_mlir_pip_repair_light >/dev/null 2>&1; then
    tpu_mlir_pip_repair_light "$py"
  elif declare -F tpu_mlir_pip_repair >/dev/null 2>&1; then
    echo "[tpu-mlir] ERROR: 编译中禁止 force-reinstall；请先: bash scripts/repair_tpu_mlir_venv.sh" >&2
    return 1
  else
    _install_tpu_mlir_pip_deps "$py" || return 1
    PYTHONPATH="" "$py" -m pip install -q "tpu-mlir[onnx]" 2>/dev/null \
      || PYTHONPATH="" "$py" -m pip install -q "tpu_mlir[onnx]" 2>/dev/null \
      || return 1
  fi
  if declare -F tpu_mlir_tools_ok >/dev/null 2>&1; then
    tpu_mlir_tools_ok "$py"
    return $?
  fi
  _try_tpu_compile_tools "$py"
}

# 优先 pip 版 tpu-mlir（venv-tpu-mlir）；禁止回退到未 build 的源码 python/tools
ensure_tpu_mlir_python_deps() {
  local py primary=""
  local -a candidates=()

  if [[ -n "${TPU_MLIR_PYTHON:-}" && -x "${TPU_MLIR_PYTHON}" ]]; then
    candidates=("${TPU_MLIR_PYTHON}")
  else
    candidates=(
      /data/labeIU/labeIuMedia/venv-tpu-mlir/bin/python3
      /root/miniconda3/envs/tpu310/bin/python3
    )
  fi

  for py in "${candidates[@]}"; do
    ver="$("$py" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "?")"
    echo "candidate python: $py ($ver)" >&2
    _install_tpu_mlir_pip_deps "$py" || continue
    if _ensure_pip_tpu_mlir "$py"; then
      primary="$py"
      TPU_MLIR_PIP_MODE=1
      break
    fi
  done

  if [[ -z "$primary" ]]; then
    echo "ERROR: pip tpu-mlir 不可用。请执行:" >&2
    echo "  bash scripts/repair_tpu_mlir_venv.sh" >&2
    echo "  并配置 tpu_mlir_python=/data/labeIU/labeIuMedia/venv-tpu-mlir/bin/python3" >&2
    return 1
  fi

  TPU_MLIR_PRIMARY_PYTHON="${primary}"
  unset PYTHONPATH
  export PATH="$(dirname "$primary"):/usr/bin:/bin:/usr/local/bin"
  hash -r 2>/dev/null || true
  echo "TPU_MLIR_PIP_MODE=${TPU_MLIR_PIP_MODE} primary=${primary}" >&2
  echo "model_transform.py=$(PYTHONPATH="" command -v model_transform.py)" >&2
  echo "model_deploy.py=$(PYTHONPATH="" command -v model_deploy.py)" >&2
  if ! PYTHONPATH="" model_transform.py --help >/dev/null 2>&1; then
    PYTHONPATH="" model_transform.py --help 2>&1 | tail -25 >&2 || true
    return 1
  fi
  echo "model_transform.py --help OK" >&2
}
ensure_tpu_mlir_python_deps || exit 1

if declare -F tpu_mlir_setup_native_runtime >/dev/null 2>&1; then
  tpu_mlir_setup_native_runtime "${TPU_MLIR_PRIMARY_PYTHON}"
  echo "RELEASE_PATH=${RELEASE_PATH:-}" >&2
fi

tpu_tool() {
  local bindir
  bindir="$(dirname "$TPU_MLIR_PRIMARY_PYTHON")"
  echo "${bindir}/$1"
}

# INT8 校准/部署需要 pymlir（官方 envsetup 会加入 PYTHONPATH；pip wheel 通常不含）
TPU_MLIR_PYMLIR_PATH=""

_tpu_pymlir_import_ok() {
  local pp="${1:-}"
  PYTHONPATH="${pp}" "${TPU_MLIR_PRIMARY_PYTHON}" -c "import pymlir" 2>/dev/null
}

discover_tpu_mlir_pymlir_path() {
  local root="${TPU_MLIR_ROOT:-}"
  local py="${TPU_MLIR_PRIMARY_PYTHON}"
  local -a found=()
  local d sp
  if [[ -n "$root" ]]; then
    for d in \
      "${root}/python" \
      "${root}/install/python" \
      "${root}/install/lib" \
      "${root}/lib" \
      "${root}/build/python" \
      ; do
        [[ -d "$d" ]] || continue
        if _tpu_pymlir_import_ok "$d"; then
          found+=("$d")
        fi
      done
  fi
  sp="$("$py" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || true)"
  if [[ -n "$sp" && -d "${sp}/tpu_mlir/python" ]]; then
    if _tpu_pymlir_import_ok "${sp}/tpu_mlir/python"; then
      found+=("${sp}/tpu_mlir/python")
    fi
  fi
  if [[ ${#found[@]} -eq 0 ]]; then
    return 1
  fi
  local IFS=:
  TPU_MLIR_PYMLIR_PATH="${found[*]}"
  export TPU_MLIR_PYMLIR_PATH
  echo "TPU_MLIR_PYMLIR_PATH=${TPU_MLIR_PYMLIR_PATH}" >&2
}

# 在含 pymlir 的环境下执行（优先 PYTHONPATH，否则 subshell source envsetup）
tpu_run_pymlir() {
  local tool="$1"
  shift
  if declare -F tpu_mlir_setup_native_runtime >/dev/null 2>&1; then
    tpu_mlir_setup_native_runtime "${TPU_MLIR_PRIMARY_PYTHON}"
  else
    export PROJECT_ROOT="${TPU_MLIR_ROOT}"
    export RELEASE_PATH="${TPU_MLIR_ROOT}"
    export TPUC_ROOT="${TPU_MLIR_ROOT}"
  fi
  if discover_tpu_mlir_pymlir_path; then
    PYTHONPATH="${TPU_MLIR_PYMLIR_PATH}" LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}" \
      RELEASE_PATH="${RELEASE_PATH:-${TPU_MLIR_ROOT}}" \
      PROJECT_ROOT="${PROJECT_ROOT:-${TPU_MLIR_ROOT}}" \
      TPUC_ROOT="${TPUC_ROOT:-${TPU_MLIR_ROOT}}" \
      "$(tpu_tool "$tool")" "$@"
    return $?
  fi
  if [[ ! -f "${TPU_MLIR_ROOT}/envsetup.sh" ]]; then
    echo "ERROR: 未找到 pymlir，且 ${TPU_MLIR_ROOT}/envsetup.sh 不存在。" >&2
    echo "  请在 TPU-MLIR 源码目录 build 后重试，或使用官方 Docker/envsetup。" >&2
    return 1
  fi
  echo "WARN: 使用 source ${TPU_MLIR_ROOT}/envsetup.sh 运行 ${tool}（INT8 需要 pymlir）" >&2
  (
    set +u
    set -eo pipefail
    cd "${TPU_MLIR_ROOT}"
    # shellcheck disable=SC1091
    source "${TPU_MLIR_ROOT}/envsetup.sh"
    export PROJECT_ROOT="${TPU_MLIR_ROOT}"
    export RELEASE_PATH="${TPU_MLIR_ROOT}"
    export TPUC_ROOT="${TPU_MLIR_ROOT}"
    if declare -F tpu_mlir_setup_native_runtime >/dev/null 2>&1; then
      tpu_mlir_setup_native_runtime "${TPU_MLIR_PRIMARY_PYTHON}"
    fi
    cd "${WORKDIR}"
    "$(tpu_tool "$tool")" "$@"
  )
}

chip_to_dir() {
  case "${1,,}" in
    bm1684x) echo "BM1684X" ;;
    bm1688) echo "BM1688" ;;
    cv186x) echo "CV186X" ;;
    *) echo "${1^^}" ;;
  esac
}

SCALE="0.0039216,0.0039216,0.0039216"
MLIR_1B="${MODEL_NAME}_1b.mlir"

gen_mlir_1b() {
  PYTHONPATH="" "$(tpu_tool model_transform.py)" \
    --model_name "${MODEL_NAME}" \
    --model_def model.onnx \
    --input_shapes "[[1,3,${IMGSZ},${IMGSZ}]]" \
    --mean 0.0,0.0,0.0 \
    --scale "${SCALE}" \
    --keep_aspect_ratio \
    --pixel_format rgb \
    --mlir "${MLIR_1B}"
}

deploy_fp32_1b() {
  local chip="$1"
  local out_dir="$2"
  PYTHONPATH="" "$(tpu_tool model_deploy.py)" --mlir "${MLIR_1B}" --quantize F32 --chip "$chip" \
    --model "${MODEL_NAME}_fp32_1b.bmodel"
  cp -f "${MODEL_NAME}_fp32_1b.bmodel" "$out_dir/"
}

deploy_fp16_1b() {
  local chip="$1"
  local out_dir="$2"
  PYTHONPATH="" "$(tpu_tool model_deploy.py)" --mlir "${MLIR_1B}" --quantize F16 --chip "$chip" \
    --model "${MODEL_NAME}_fp16_1b.bmodel"
  cp -f "${MODEL_NAME}_fp16_1b.bmodel" "$out_dir/"
}

gen_cali_table_1b() {
  if [[ -z "$CALI_DATASET" || ! -d "$CALI_DATASET" ]]; then
    echo "INT8 requires calibration dataset directory (e.g. datasets/coco128)" >&2
    return 1
  fi
  tpu_run_pymlir run_calibration.py "${MLIR_1B}" \
    --dataset "$CALI_DATASET" \
    --input_num "${CALI_INPUT_NUM:-128}" \
    --inference_num "${CALI_INFERENCE_NUM:-10}" \
    -o "${MODEL_NAME}_cali_table"
}

deploy_int8_1b() {
  local chip="$1"
  local out_dir="$2"
  local qtable_args=()
  if [[ -n "$QTABLE" && -f "$QTABLE" ]]; then
    local qt_base
    qt_base="$(basename "$QTABLE")"
    if [[ "$qt_base" == *"${MODEL_NAME}"* ]]; then
      qtable_args=(--quantize_table "$QTABLE")
    else
      echo "WARN: qtable=${QTABLE} 与 MODEL_NAME=${MODEL_NAME} 不匹配，int8 仅使用 cali_table（不用 qtable）" >&2
    fi
  fi
  if [[ ! -f "${MODEL_NAME}_cali_table" ]]; then
    gen_cali_table_1b || return 1
  fi
  if declare -F tpu_mlir_require_chip_kernel_so >/dev/null 2>&1; then
    tpu_mlir_require_chip_kernel_so "$chip" || return 1
  fi
  tpu_run_pymlir model_deploy.py --mlir "${MLIR_1B}" --quantize INT8 --chip "$chip" \
    --calibration_table "${MODEL_NAME}_cali_table" \
    "${qtable_args[@]}" \
    --model "${MODEL_NAME}_int8_1b.bmodel"
  cp -f "${MODEL_NAME}_int8_1b.bmodel" "$out_dir/"
}

prune_platform_dir() {
  local out_dir="$1"
  shift
  local -a keep=("$@")
  local f base found
  shopt -s nullglob
  for f in "$out_dir"/*.bmodel; do
    base="$(basename "$f")"
    found=0
    for k in "${keep[@]}"; do
      if [[ "$base" == "$k" ]]; then
        found=1
        break
      fi
    done
    if [[ "$found" -eq 0 ]]; then
      rm -f "$f"
      echo "pruned unexpected bmodel: $base in $out_dir" >&2
    fi
  done
  shopt -u nullglob
}

run_mode_for_chip() {
  local chip="$1"
  local platform_dir
  platform_dir="$(chip_to_dir "$chip")"
  local out_dir="${EXPORT_ROOT}/models/${platform_dir}"
  mkdir -p "$out_dir"

  case "$MODE" in
    fp32)
      gen_mlir_1b
      deploy_fp32_1b "$chip" "$out_dir"
      prune_platform_dir "$out_dir" "${MODEL_NAME}_fp32_1b.bmodel"
      ;;
    fp16)
      gen_mlir_1b
      deploy_fp16_1b "$chip" "$out_dir"
      prune_platform_dir "$out_dir" "${MODEL_NAME}_fp16_1b.bmodel"
      ;;
    int8)
      gen_mlir_1b
      deploy_int8_1b "$chip" "$out_dir"
      prune_platform_dir "$out_dir" "${MODEL_NAME}_int8_1b.bmodel"
      ;;
    int8_only)
      # 已有 fp32/fp16 时仅补 int8，不 prune 以免删掉已有 bmodel
      gen_mlir_1b
      deploy_int8_1b "$chip" "$out_dir"
      ;;
    full)
      gen_mlir_1b
      deploy_fp32_1b "$chip" "$out_dir"
      deploy_fp16_1b "$chip" "$out_dir"
      deploy_int8_1b "$chip" "$out_dir"
      prune_platform_dir "$out_dir" \
        "${MODEL_NAME}_fp32_1b.bmodel" \
        "${MODEL_NAME}_fp16_1b.bmodel" \
        "${MODEL_NAME}_int8_1b.bmodel"
      ;;
    fp32_fp16)
      # Docker INT8 已成功时仅补 fp32/fp16，保留已有 int8 bmodel
      gen_mlir_1b
      deploy_fp32_1b "$chip" "$out_dir"
      deploy_fp16_1b "$chip" "$out_dir"
      prune_platform_dir "$out_dir" \
        "${MODEL_NAME}_fp32_1b.bmodel" \
        "${MODEL_NAME}_fp16_1b.bmodel" \
        "${MODEL_NAME}_int8_1b.bmodel"
      ;;
    *)
      echo "unsupported mode: $MODE (fp32|fp16|int8|int8_only|full|fp32_fp16)" >&2
      exit 2
      ;;
  esac
}

# 不依赖 mktemp（部分环境 PATH 无 /usr/bin）
labelu_compile_workdir() {
  local base="${EXPORT_ROOT}/.compile_tmp"
  mkdir -p "$base"
  local d="${base}/run-$$"
  rm -rf "$d"
  mkdir -p "$d"
  echo "$d"
}
WORKDIR="$(labelu_compile_workdir)"
trap 'rm -rf "$WORKDIR"' EXIT
cp -f "$ONNX_ABS" "$WORKDIR/model.onnx"
cd "$WORKDIR"

IFS=',' read -ra PLAT_ARR <<< "$PLATFORMS"
for chip_raw in "${PLAT_ARR[@]}"; do
  chip="$(echo "$chip_raw" | tr -d ' ' | tr '[:upper:]' '[:lower:]')"
  [[ -z "$chip" ]] && continue
  echo "=== compile platform: $chip (models/$(chip_to_dir "$chip")/) ===" >&2
  run_mode_for_chip "$chip"
done

expected=()
case "$MODE" in
  fp32) suffix="_fp32_1b.bmodel" ;;
  fp16) suffix="_fp16_1b.bmodel" ;;
  int8|int8_only) suffix="_int8_1b.bmodel" ;;
  full|fp32_fp16) suffix="" ;;
esac

missing=0
for chip_raw in "${PLAT_ARR[@]}"; do
  chip="$(echo "$chip_raw" | tr -d ' ' | tr '[:upper:]' '[:lower:]')"
  [[ -z "$chip" ]] && continue
  platform_dir="$(chip_to_dir "$chip")"
  out_dir="${EXPORT_ROOT}/models/${platform_dir}"
  if [[ "$MODE" == "full" || "$MODE" == "fp32_fp16" ]]; then
    for e in \
      "${MODEL_NAME}_fp32_1b.bmodel" \
      "${MODEL_NAME}_fp16_1b.bmodel" \
      "${MODEL_NAME}_int8_1b.bmodel"; do
      if [[ ! -f "$out_dir/$e" ]]; then
        echo "missing $platform_dir/$e" >&2
        missing=1
      fi
    done
  else
    e="${MODEL_NAME}${suffix}"
    if [[ ! -f "$out_dir/$e" ]]; then
      echo "missing $platform_dir/$e" >&2
      missing=1
    fi
  fi
done

if [[ "$missing" -ne 0 ]]; then
  exit 3
fi

find "${EXPORT_ROOT}/models" -name '*.bmodel' -print
exit 0
