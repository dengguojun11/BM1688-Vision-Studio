#!/usr/bin/env bash
# 在服务器上编译 TPU-MLIR，生成 BM1688 INT8 codegen 所需的 libbmtpulv60_kernel_module.so
# 产物路径: ${TPU_MLIR_ROOT}/install/lib/libbmtpulv60_kernel_module.so
#
# 用法:
#   export TPU_MLIR_ROOT=/data/labeIU/tpu-mlir
#   bash scripts/build_tpu_mlir_chip_libs.sh
#
# 官方推荐在 sophgo/tpuc_dev Docker 内执行；裸机可能缺依赖导致 build 失败。
set -euo pipefail

ROOT="${TPU_MLIR_ROOT:-/data/labeIU/tpu-mlir}"
CHIP_SO="libbmtpulv60_kernel_module.so"

if [[ ! -d "$ROOT" ]]; then
  echo "ERROR: TPU_MLIR_ROOT 不是目录: $ROOT" >&2
  exit 1
fi
if [[ ! -f "${ROOT}/envsetup.sh" ]]; then
  echo "ERROR: 缺少 ${ROOT}/envsetup.sh，请 clone sophgo/tpu-mlir" >&2
  exit 1
fi

if [[ -f "${ROOT}/install/lib/${CHIP_SO}" ]]; then
  echo "OK: 已存在 ${ROOT}/install/lib/${CHIP_SO}"
  ls -la "${ROOT}/install/lib/${CHIP_SO}"
  exit 0
fi

echo "=== 编译 TPU-MLIR（首次较慢，建议 tpuc_dev Docker 内执行）===" >&2
echo "ROOT=${ROOT}" >&2
df -h "${ROOT}" /data 2>/dev/null | head -5 >&2 || true

cd "${ROOT}"
set +u
# shellcheck disable=SC1091
source "${ROOT}/envsetup.sh"
set -u

if [[ -f "${ROOT}/requirements.txt" ]]; then
  python3 -m pip install -q -r "${ROOT}/requirements.txt" || {
    echo "WARN: pip install requirements.txt 失败，若在 Docker 内可忽略后重试 build" >&2
  }
fi

if [[ ! -x "${ROOT}/build.sh" ]]; then
  echo "ERROR: 缺少 ${ROOT}/build.sh" >&2
  exit 1
fi

echo "=== 运行 ./build.sh ===" >&2
bash "${ROOT}/build.sh"

if [[ -f "${ROOT}/install/lib/${CHIP_SO}" ]]; then
  echo "OK: ${ROOT}/install/lib/${CHIP_SO}"
  ls -la "${ROOT}/install/lib/${CHIP_SO}"
  echo "编译 LabelU int8 前请: export TPU_MLIR_ROOT=${ROOT}" >&2
  exit 0
fi

echo "ERROR: build 完成但未找到 ${ROOT}/install/lib/${CHIP_SO}" >&2
echo "  请检查 build 日志；或改用 Docker:" >&2
echo "  docker pull sophgo/tpuc_dev:latest" >&2
echo "  docker run --privileged -v ${ROOT}:/workspace -it sophgo/tpuc_dev:latest" >&2
echo "  cd /workspace && source ./envsetup.sh && ./build.sh" >&2
exit 1
