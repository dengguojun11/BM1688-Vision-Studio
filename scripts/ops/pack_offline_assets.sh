#!/usr/bin/env bash
# 在「能稳定访问 GitHub」的机器上打包离线资源，供服务器 deploy 解压安装。
# 用法:
#   bash scripts/ops/pack_offline_assets.sh tpu-mlir
#   bash scripts/ops/pack_offline_assets.sh sophon-demo
#   bash scripts/ops/pack_offline_assets.sh all
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT_DIR="${LABELU_OFFLINE_PACK_DIR:-${REPO_ROOT}/deploy/offline}"
WORK_DIR="$(mktemp -d)"

cleanup() { rm -rf "${WORK_DIR}"; }
trap cleanup EXIT

mkdir -p "${OUT_DIR}"

pack_tpu_mlir() {
  local dest="${OUT_DIR}/tpu-mlir.tar.gz"
  echo "[pack] TPU-MLIR -> ${dest}"
  git clone --depth 1 "${TPU_MLIR_GIT_URL:-https://gitee.com/mirrors/TPU-MLIR.git}" "${WORK_DIR}/tpu-mlir"
  [[ -f "${WORK_DIR}/tpu-mlir/envsetup.sh" ]] || { echo "缺少 envsetup.sh"; exit 1; }
  tar -czf "${dest}" -C "${WORK_DIR}" tpu-mlir
  ls -lh "${dest}"
}

pack_sophon_demo() {
  local dest="${OUT_DIR}/sophon-demo.tar.gz"
  local branch="${SOPHON_DEMO_GIT_BRANCH:-release}"
  echo "[pack] sophon-demo (${branch}) -> ${dest}"
  git clone --depth 1 --branch "${branch}" https://github.com/sophgo/sophon-demo.git "${WORK_DIR}/sophon-demo"
  [[ -d "${WORK_DIR}/sophon-demo/sample/YOLOv8_plus_det" ]] || { echo "缺少 YOLOv8_plus_det"; exit 1; }
  tar -czf "${dest}" -C "${WORK_DIR}" sophon-demo
  ls -lh "${dest}"
}

cmd="${1:-all}"
case "${cmd}" in
  tpu-mlir) pack_tpu_mlir ;;
  sophon-demo) pack_sophon_demo ;;
  all)
    pack_tpu_mlir
    pack_sophon_demo
    ;;
  *)
    echo "用法: $0 {tpu-mlir|sophon-demo|all}"
    exit 1
    ;;
esac

cat <<EOF

打包完成。任选一种方式部署到服务器:

1) 放到服务器目录（推荐，不进 git）:
   scp ${OUT_DIR}/tpu-mlir.tar.gz root@113.141.72.175:/data/labeIU/offline/
   scp ${OUT_DIR}/sophon-demo.tar.gz root@113.141.72.175:/data/labeIU/offline/

2) 放进本仓库 deploy/offline/ 后 push（体积大，需 git add -f）:
   git add -f deploy/offline/*.tar.gz && git commit && git push

3) 部署时指定:
   export LABELU_DEPLOY_OFFLINE_DIR=/data/labeIU/offline
   sudo env INSTALL_EXTRAS=yolo,image-pipeline bash deploy.sh restart

EOF
