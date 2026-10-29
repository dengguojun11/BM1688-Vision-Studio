#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_DIR="${SCRIPT_DIR}/payload"
APP_SOURCE="${SCRIPT_DIR}/app"
EDGE_ROOT="${LABELU_EDGE_ROOT:-/data/labelu_edge}"
INSTALL_ROOT="${LABELU_VALIDATOR_ROOT:-${EDGE_ROOT}/runtime/validator}"
BIN_DIR="${LABELU_EDGE_BIN_DIR:-${EDGE_ROOT}/bin}"
CONTAINER_NAME="${LABELU_BM1688_CONTAINER:-bm1688}"
IMAGE_NAME="${LABELU_BM1688_IMAGE:-7g}"
DEMO_INSTALL_BASE="${LABELU_SOPHON_DEMO_INSTALL_BASE:-${EDGE_ROOT}/runtime/sophon-demo}"
DEMO_ROOT="${DEMO_INSTALL_BASE}/sample/YOLOv8_plus_det"
DEMO_MARKER="${DEMO_ROOT}/python/yolov8_bmcv.py"

if [[ "${EUID}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo --preserve-env=LABELU_EDGE_ROOT,LABELU_VALIDATOR_ROOT,LABELU_EDGE_BIN_DIR,LABELU_BM1688_CONTAINER,LABELU_BM1688_IMAGE,LABELU_SOPHON_DEMO_INSTALL_BASE,TMPDIR \
      bash "$0" "$@"
  fi
  echo "ERROR: setup.sh must run as root." >&2
  exit 2
fi

fail() {
  echo "ERROR: $*" >&2
  exit 2
}

require_edge_path() {
  local candidate_real
  candidate_real="$(readlink -m "$1")"
  case "${candidate_real}" in
    "${EDGE_ROOT_REAL}"|"${EDGE_ROOT_REAL}"/*) ;;
    *) fail "path escapes LABELU_EDGE_ROOT: $1" ;;
  esac
}

[[ "${EDGE_ROOT}" == /* && "${EDGE_ROOT}" != "/" && "${EDGE_ROOT}" != "/data" ]] \
  || fail "LABELU_EDGE_ROOT must be a safe absolute directory"
[[ ! -L "${EDGE_ROOT}" ]] || fail "LABELU_EDGE_ROOT must not be a symbolic link"
mkdir -p "${EDGE_ROOT}"
EDGE_ROOT_REAL="$(readlink -m "${EDGE_ROOT}")"
for managed_path in "${INSTALL_ROOT}" "${BIN_DIR}" "${DEMO_INSTALL_BASE}" "${TMPDIR:-${EDGE_ROOT}/tmp}"; do
  require_edge_path "${managed_path}"
done

[[ "$(uname -m)" == "aarch64" ]] || fail "this package only supports aarch64 BM1688 boxes"
[[ -e /dev/bmdev-ctl || -e /dev/bm-tpu0 || -e /dev/bm-sophon0 ]] \
  || fail "BM1688 device node is missing; install the matching official firmware/driver first"
[[ -d /opt/sophon/libsophon-current ]] \
  || fail "/opt/sophon/libsophon-current is missing; install the matching official BM1688 system image first"
command -v docker >/dev/null 2>&1 || fail "docker is missing from the BM1688 host system"
docker info >/dev/null 2>&1 || fail "docker daemon is unavailable"

IMAGE_ARCHIVE="${PAYLOAD_DIR}/bm1688-7g-runtime-image.tar.gz"
DEMO_ARCHIVE="${PAYLOAD_DIR}/sophon-demo-yolov8-plus-det.tar.gz"
[[ -s "${IMAGE_ARCHIVE}" ]] || fail "runtime image payload is missing: ${IMAGE_ARCHIVE}"
[[ -s "${DEMO_ARCHIVE}" ]] || fail "Sophon Demo payload is missing: ${DEMO_ARCHIVE}"
[[ -s "${PAYLOAD_DIR}/SHA256SUMS" ]] || fail "payload checksum file is missing"
(cd "${PAYLOAD_DIR}" && sha256sum -c SHA256SUMS)

if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
  echo "[1/4] Loading offline BM1688 runtime image ..."
  gzip -dc "${IMAGE_ARCHIVE}" | docker load
else
  echo "[1/4] Runtime image already exists: ${IMAGE_NAME}"
fi
docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1 \
  || fail "Docker image was loaded but ${IMAGE_NAME} is unavailable"

if ! docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  echo "[2/4] Creating BM1688 runtime container ..."
  run_args=(
    run -d --name "${CONTAINER_NAME}" --privileged --network host
    --restart always
    --volume /data:/data:rslave
    --volume /dev:/dev
    --volume /opt/sophon/libsophon-current:/opt/sophon/libsophon-current
  )
  [[ -d /mnt ]] && run_args+=(--volume /mnt:/mnt)
  [[ -d /opt/sophon/sophon-opencv-latest ]] && run_args+=(--volume /opt/sophon/sophon-opencv-latest:/opt/sophon/sophon-opencv-latest)
  [[ -d /opt/sophon/sophon-ffmpeg-latest ]] && run_args+=(--volume /opt/sophon/sophon-ffmpeg-latest:/opt/sophon/sophon-ffmpeg)
  [[ -d /opt/sophon/sophon-soc-libisp_1.0.0 ]] && run_args+=(--volume /opt/sophon/sophon-soc-libisp_1.0.0:/opt/sophon/sophon-soc-libisp)
  [[ -d /usr/local/7g ]] && run_args+=(--volume /usr/local/7g:/usr/local/7g)
  run_args+=("${IMAGE_NAME}")
  docker "${run_args[@]}"
elif [[ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}")" != "true" ]]; then
  echo "[2/4] Starting existing container: ${CONTAINER_NAME}"
  docker start "${CONTAINER_NAME}" >/dev/null
else
  echo "[2/4] Runtime container is already running: ${CONTAINER_NAME}"
fi

echo "[3/4] Installing offline Sophon Demo and validator ..."
if [[ ! -f "${DEMO_MARKER}" ]]; then
  stage="${EDGE_ROOT}/tmp/.sophon-demo-install.$$"
  mkdir -p "${stage}"
  tar -xzf "${DEMO_ARCHIVE}" -C "${stage}"
  [[ -f "${stage}/sophon-demo/sample/YOLOv8_plus_det/python/yolov8_bmcv.py" ]] \
    || fail "Sophon Demo archive has an invalid layout"
  mkdir -p "${DEMO_INSTALL_BASE}"
  cp -a "${stage}/sophon-demo/." "${DEMO_INSTALL_BASE}/"
  rm -rf "${stage}"
fi

mkdir -p "${INSTALL_ROOT}/app" "${INSTALL_ROOT}/jobs" "${INSTALL_ROOT}/results" "${BIN_DIR}"
cp -f "${APP_SOURCE}/validate.py" "${INSTALL_ROOT}/app/validate.py"
cp -f "${APP_SOURCE}/plate_recognition_bm1688_infer.py" \
  "${INSTALL_ROOT}/app/plate_recognition_bm1688_infer.py"
cp -f "${APP_SOURCE}/plate_detection_bm1688_infer.py" \
  "${INSTALL_ROOT}/app/plate_detection_bm1688_infer.py"
cp -f "${APP_SOURCE}/config.json" "${INSTALL_ROOT}/app/config.json"
install -m 0755 "${SCRIPT_DIR}/bmodel-validate" "${BIN_DIR}/bmodel-validate"

echo "[4/4] Verifying runtime ..."
docker exec "${CONTAINER_NAME}" python3 -c \
  'import cv2, numpy, sophon.sail; print("runtime imports: OK")'
LABELU_SOPHON_DEMO_ROOT="${DEMO_ROOT}" \
  bash "${SCRIPT_DIR}/check_env.sh" "${EDGE_ROOT}/results/environment_report.json"
echo "Environment installation completed."
