#!/usr/bin/env bash
# BM1688 Docker 量化环境：安装 Docker、加载 sophgo/tpuc_dev 镜像、初始化容器（deploy 自动调用）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CONTAINER_NAME="${LABELU_BM1688_CONTAINER:-labelu_bm1688}"
IMAGE="${LABELU_BM1688_IMAGE:-sophgo/tpuc_dev:v3.2}"
TAR_NAME="${LABELU_BM1688_DOCKER_TAR:-sophgo-tpuc_dev-v3.2_191a433358ad.tar.gz}"
TAR_URL="${LABELU_SOPHON_DOCKER_TAR_URL:-https://sophon-file.sophon.cn/sophon-prod-s3/drive/24/06/14/12/sophgo-tpuc_dev-v3.2_191a433358ad.tar.gz}"

BASE="${LABELU_DEPLOY_RESOLVED_BASE:-/data/labeIU}"
MOUNT="${1:-${BASE}}"
OFFLINE_DIR="${LABELU_DEPLOY_OFFLINE_DIR:-${BASE}/offline}"
LABELU_PKG="${REPO_ROOT}/labelu"

log() { echo "[bm1688-docker] $*"; }
warn() { echo "[bm1688-docker] WARN: $*" >&2; }

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "[bm1688-docker] 需要 root（请通过 sudo deploy.sh update 执行）" >&2
    exit 1
  fi
}

ensure_docker_engine() {
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    log "Docker 已就绪"
    return 0
  fi
  log "安装 Docker Engine…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq docker.io ca-certificates curl
  systemctl enable docker
  systemctl start docker
  if ! docker info >/dev/null 2>&1; then
    echo "[bm1688-docker] Docker 安装后仍不可用" >&2
    exit 1
  fi
  log "Docker 安装完成"
}

resolve_tar_path() {
  local p
  for p in \
    "${OFFLINE_DIR}/${TAR_NAME}" \
    "${BASE}/sophon/docker/${TAR_NAME}" \
    "${REPO_ROOT}/deploy/offline/${TAR_NAME}"; do
    if [[ -f "${p}" ]]; then
      echo "${p}"
      return 0
    fi
  done
  return 1
}

ensure_docker_image() {
  if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    log "镜像已存在: ${IMAGE}"
    return 0
  fi
  local tar_path=""
  if tar_path="$(resolve_tar_path)"; then
    log "docker load ${tar_path}"
    docker load -i "${tar_path}"
    return 0
  fi
  mkdir -p "${OFFLINE_DIR}"
  local dest="${OFFLINE_DIR}/${TAR_NAME}"
  if [[ ! -f "${dest}" ]] && command -v wget >/dev/null 2>&1; then
    log "下载 Docker 镜像包（约数 GB，仅首次）…"
    log "URL: ${TAR_URL}"
    if wget -q --show-progress -O "${dest}" "${TAR_URL}"; then
      log "docker load ${dest}"
      docker load -i "${dest}"
      return 0
    fi
    rm -f "${dest}"
    warn "wget 下载失败，请将 ${TAR_NAME} 放到 ${OFFLINE_DIR}/ 或 ${BASE}/sophon/docker/"
  fi
  echo "[bm1688-docker] 未找到镜像 ${IMAGE}，且无可用 tar 包" >&2
  exit 1
}

ensure_container() {
  if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
    docker start "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  else
    log "创建容器 ${CONTAINER_NAME}（挂载 ${MOUNT}）"
    docker run --privileged --name "${CONTAINER_NAME}" \
      -v "${MOUNT}:${MOUNT}" \
      -v "${LABELU_PKG}:/labelu:ro" \
      -d "${IMAGE}" sleep infinity
  fi
  if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
    echo "[bm1688-docker] 容器未运行: ${CONTAINER_NAME}" >&2
    exit 1
  fi
}

container_pip_setup() {
  log "容器内依赖（首次较慢）…"
  docker exec "${CONTAINER_NAME}" bash -lc 'set -e
MARKER=/root/.labelu_bm1688_setup_done
if [[ -f "$MARKER" ]]; then echo "container deps ok"; exit 0; fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq lrzsz >/dev/null
pip install -q torch==2.5.0 torchvision==0.20.0 torchaudio==2.5.0 typing-extensions==4.8.0
pip install -q "tensorflow-cpu==2.12.0"
pip install -q "tpu_mlir[onnx]"
pip install -q ultralytics==8.3.163 numpy==1.24.3 scipy==1.11.1 onnxslim
touch "$MARKER"
echo "container deps installed"
'
}

main() {
  need_root
  log "BM1688 Docker bootstrap mount=${MOUNT}"
  ensure_docker_engine
  ensure_docker_image
  ensure_container
  container_pip_setup
  log "BM1688 Docker 环境就绪: ${CONTAINER_NAME}"
}

main "$@"
