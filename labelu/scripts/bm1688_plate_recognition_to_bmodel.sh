#!/usr/bin/env bash
# Compile the four plate-recognition ONNX networks and combine them into one BM1688 BModel.
set -euo pipefail

EXPORT_ROOT="${1:?export root}"
POSE_ONNX="${2:?pose onnx}"
OCR_ONNX="${3:?ocr onnx}"
COLOR_ONNX="${4:?color onnx}"
TYPE_ONNX="${5:?plate type onnx}"
POSE_SIZE="${6:-640}"

: "${TPU_MLIR_PYTHON:?TPU_MLIR_PYTHON is required}"
TOOL_DIR="$(dirname "${TPU_MLIR_PYTHON}")"
MODEL_TRANSFORM="${TOOL_DIR}/model_transform.py"
MODEL_DEPLOY="${TOOL_DIR}/model_deploy.py"
MODEL_TOOL="${TOOL_DIR}/model_tool.py"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_HELPER="${SCRIPT_DIR}/lib/tpu_mlir_pip_repair.sh"

for tool in "${MODEL_TRANSFORM}" "${MODEL_DEPLOY}" "${MODEL_TOOL}"; do
  if [[ ! -f "${tool}" ]]; then
    echo "missing TPU-MLIR tool: ${tool}" >&2
    exit 2
  fi
done

if [[ ! -f "${RUNTIME_HELPER}" ]]; then
  echo "missing TPU-MLIR runtime helper: ${RUNTIME_HELPER}" >&2
  exit 2
fi
# shellcheck disable=SC1090
source "${RUNTIME_HELPER}"
export TPU_MLIR_PRIMARY_PYTHON="${TPU_MLIR_PYTHON}"
tpu_mlir_setup_native_runtime "${TPU_MLIR_PYTHON}"
tpu_mlir_require_chip_kernel_so bm1688

OUT_DIR="${EXPORT_ROOT}/models/BM1688"
WORK_DIR="${EXPORT_ROOT}/.compile_tmp/plate_recognition_$$"
mkdir -p "${OUT_DIR}" "${WORK_DIR}"
trap 'rm -rf "${WORK_DIR}"' EXIT

compile_network() {
  local name="$1"
  local onnx="$2"
  local height="$3"
  local width="$4"
  local mlir="${WORK_DIR}/${name}.mlir"
  local bmodel="${WORK_DIR}/${name}_fp16_1b.bmodel"

  PYTHONPATH="" "${MODEL_TRANSFORM}" \
    --model_name "${name}" \
    --model_def "${onnx}" \
    --input_shapes "[[1,3,${height},${width}]]" \
    --mean 0.0,0.0,0.0 \
    --scale 0.0039216,0.0039216,0.0039216 \
    --pixel_format rgb \
    --mlir "${mlir}"

  PYTHONPATH="" "${MODEL_DEPLOY}" \
    --mlir "${mlir}" \
    --quantize F16 \
    --chip bm1688 \
    --model "${bmodel}"
}

compile_network plate_pose "${POSE_ONNX}" "${POSE_SIZE}" "${POSE_SIZE}"
compile_network plate_ocr "${OCR_ONNX}" 48 192
compile_network plate_color "${COLOR_ONNX}" 64 128
compile_network plate_type "${TYPE_ONNX}" 64 128

COMBINED="${OUT_DIR}/plate_recognition_fp16_1b.bmodel"
PYTHONPATH="" "${MODEL_TOOL}" --combine \
  "${WORK_DIR}/plate_pose_fp16_1b.bmodel" \
  "${WORK_DIR}/plate_ocr_fp16_1b.bmodel" \
  "${WORK_DIR}/plate_color_fp16_1b.bmodel" \
  "${WORK_DIR}/plate_type_fp16_1b.bmodel" \
  -o "${COMBINED}"

test -s "${COMBINED}"
echo "combined_bmodel=${COMBINED}"
