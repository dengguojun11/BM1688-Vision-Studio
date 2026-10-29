#!/usr/bin/env bash
# 包装：与仓库根 scripts/build_tpu_mlir_chip_libs.sh 相同
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "${ROOT_DIR}/scripts/build_tpu_mlir_chip_libs.sh" "$@"
