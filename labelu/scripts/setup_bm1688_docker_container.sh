#!/usr/bin/env bash
# 在 Linux 服务器上准备 BM1688 Docker 量化容器（文档 3.5 Docker 流程的平台化）。
# 用法: sudo bash labelu/scripts/setup_bm1688_docker_container.sh [/data/mount/path]
set -euo pipefail

CONTAINER_NAME="${LABELU_BM1688_CONTAINER:-labelu_bm1688}"
IMAGE="${LABELU_BM1688_IMAGE:-sophgo/tpuc_dev:v3.2}"
MOUNT="${1:-/data}"
# 脚本位于 labelu/scripts/；LABELU_PKG=.../labelu 包目录
LABELU_PKG="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${LABELU_PKG}/.." && pwd)"

echo "[labelu] repo=${REPO_ROOT} mount=${MOUNT} container=${CONTAINER_NAME} image=${IMAGE}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker 未安装，请先安装 Docker Engine" >&2
  exit 1
fi

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  TAR_DIR="${LABELU_BM1688_TAR_DIR:-${MOUNT}/sophon/docker}"
  TAR_FILE="${TAR_DIR}/sophgo-tpuc_dev-v3.2_191a433358ad.tar.gz"
  if [[ -f "${TAR_FILE}" ]]; then
    echo "[labelu] docker load ${TAR_FILE}"
    docker load -i "${TAR_FILE}"
  else
    echo "WARN: 镜像 ${IMAGE} 不存在，且未找到 ${TAR_FILE}" >&2
    echo "请按文档下载 sophgo-tpuc_dev 镜像 tar 放到 ${TAR_DIR} 后重试" >&2
    exit 1
  fi
fi

if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  docker start "${CONTAINER_NAME}" >/dev/null 2>&1 || true
else
  docker run --privileged --name "${CONTAINER_NAME}" \
    -v "${MOUNT}:${MOUNT}" \
    -v "${LABELU_PKG}:/labelu:ro" \
    -d "${IMAGE}" sleep infinity
fi

echo "[labelu] installing python deps inside container (first run may take long)..."
docker exec "${CONTAINER_NAME}" bash -lc '
set -e
MARKER=/root/.labelu_bm1688_setup_done
if [[ -f "$MARKER" ]]; then
  echo "setup already done"
  exit 0
fi
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq lrzsz >/dev/null
pip install -q torch==2.5.0 torchvision==0.20.0 torchaudio==2.5.0 typing-extensions==4.8.0
pip install -q "tensorflow-cpu==2.12.0"
pip install -q "tpu_mlir[onnx]"
pip install -q ultralytics==8.3.163 numpy==1.24.3 scipy==1.11.1 onnxslim
touch "$MARKER"
echo "container setup finished"
'

echo "[labelu] container ${CONTAINER_NAME} ready. Test:"
echo "  docker exec -it ${CONTAINER_NAME} python3 --version"
