#!/usr/bin/env bash
# Compile the single plate-detection Pose network for BM1688.
set -euo pipefail

EXPORT_ROOT="${1:?export root}"
POSE_ONNX="${2:?pose onnx}"
POSE_SIZE="${3:-640}"

: "${TPU_MLIR_PYTHON:?TPU_MLIR_PYTHON is required}"
TOOL_DIR="$(dirname "${TPU_MLIR_PYTHON}")"
MODEL_TRANSFORM="${TOOL_DIR}/model_transform.py"
MODEL_DEPLOY="${TOOL_DIR}/model_deploy.py"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_HELPER="${SCRIPT_DIR}/lib/tpu_mlir_pip_repair.sh"

for tool in "${MODEL_TRANSFORM}" "${MODEL_DEPLOY}"; do
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
WORK_DIR="${EXPORT_ROOT}/.compile_tmp/plate_detection_$$"
mkdir -p "${OUT_DIR}" "${WORK_DIR}"
trap 'rm -rf "${WORK_DIR}"' EXIT

MLIR="${WORK_DIR}/plate_detection.mlir"
BMODEL="${OUT_DIR}/plate_detection_fp16_1b.bmodel"
PYTHONPATH="" "${MODEL_TRANSFORM}" \
  --model_name plate_detection \
  --model_def "${POSE_ONNX}" \
  --input_shapes "[[1,3,${POSE_SIZE},${POSE_SIZE}]]" \
  --mean 0.0,0.0,0.0 \
  --scale 0.0039216,0.0039216,0.0039216 \
  --pixel_format rgb \
  --mlir "${MLIR}"

PYTHONPATH="" "${MODEL_DEPLOY}" \
  --mlir "${MLIR}" \
  --quantize F16 \
  --chip bm1688 \
  --model "${BMODEL}"

test -s "${BMODEL}"
echo "bmodel=${BMODEL}"
