#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDGE_ROOT="${LABELU_EDGE_ROOT:-/data/labelu_edge}"
REPORT_PATH="${1:-${EDGE_ROOT}/results/environment_report.json}"
CONTAINER_NAME="${LABELU_BM1688_CONTAINER:-bm1688}"
DEMO_ROOT="${LABELU_SOPHON_DEMO_ROOT:-${EDGE_ROOT}/runtime/sophon-demo/sample/YOLOv8_plus_det}"
INSTALL_ROOT="${LABELU_VALIDATOR_ROOT:-${EDGE_ROOT}/runtime/validator}"
BIN_DIR="${LABELU_EDGE_BIN_DIR:-${EDGE_ROOT}/bin}"

[[ "${EDGE_ROOT}" == /* && "${EDGE_ROOT}" != "/" && "${EDGE_ROOT}" != "/data" ]] || exit 2
[[ ! -L "${EDGE_ROOT}" ]] || exit 2
mkdir -p "${EDGE_ROOT}" 2>/dev/null \
  || sudo -n install -d -o "$(id -u)" -g "$(id -g)" "${EDGE_ROOT}" \
  || exit 2
edge_root_real="$(readlink -m "${EDGE_ROOT}")"
for managed_path in "${REPORT_PATH}" "${DEMO_ROOT}" "${INSTALL_ROOT}" "${BIN_DIR}"; do
  managed_real="$(readlink -m "${managed_path}")"
  case "${managed_real}" in
    "${edge_root_real}"|"${edge_root_real}"/*) ;;
    *) echo "ERROR: path escapes LABELU_EDGE_ROOT: ${managed_path}" >&2; exit 2 ;;
  esac
done

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

docker_run() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  elif command_exists sudo && sudo docker info >/dev/null 2>&1; then
    sudo docker "$@"
  else
    return 127
  fi
}

arch="$(uname -m 2>/dev/null || printf 'unknown')"
os_id="unknown"
os_version="unknown"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  os_id="${ID:-unknown}"
  os_version="${VERSION_ID:-unknown}"
fi

arch_ok=false
[[ "${arch}" == "aarch64" ]] && arch_ok=true
device_ok=false
[[ -e /dev/bmdev-ctl || -e /dev/bm-tpu0 || -e /dev/bm-sophon0 ]] && device_ok=true
libsophon_ok=false
[[ -d /opt/sophon/libsophon-current ]] && libsophon_ok=true
docker_ok=false
docker_run version >/dev/null 2>&1 && docker_ok=true
image_ok=false
${docker_ok} && docker_run image inspect 7g >/dev/null 2>&1 && image_ok=true
container_ok=false
${docker_ok} && [[ "$(docker_run inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" 2>/dev/null)" == "true" ]] && container_ok=true
python_runtime_ok=false
if ${container_ok}; then
  docker_run exec "${CONTAINER_NAME}" python3 -c \
    'import cv2, numpy, sophon.sail' >/dev/null 2>&1 && python_runtime_ok=true
fi
demo_ok=false
[[ -f "${DEMO_ROOT}/python/yolov8_bmcv.py" ]] && demo_ok=true
validator_ok=false
[[ -f "${INSTALL_ROOT}/app/validate.py" && -x "${BIN_DIR}/bmodel-validate" ]] && validator_ok=true

ready=false
if ${arch_ok} && ${device_ok} && ${libsophon_ok} && ${docker_ok} \
  && ${image_ok} && ${container_ok} && ${python_runtime_ok} \
  && ${demo_ok} && ${validator_ok}; then
  ready=true
fi

mkdir -p "$(dirname "${REPORT_PATH}")"
cat >"${REPORT_PATH}" <<EOF
{
  "format": "labelu_bm1688_environment_report_v1",
  "environment_ready": ${ready},
  "architecture": "${arch}",
  "architecture_ok": ${arch_ok},
  "os": "${os_id}",
  "os_version": "${os_version}",
  "bm1688_device_ok": ${device_ok},
  "libsophon_ok": ${libsophon_ok},
  "docker_ok": ${docker_ok},
  "docker_image_ok": ${image_ok},
  "container_ok": ${container_ok},
  "python_runtime_ok": ${python_runtime_ok},
  "sophon_demo_ok": ${demo_ok},
  "validator_ok": ${validator_ok},
  "container": "${CONTAINER_NAME}",
  "sophon_demo_root": "${DEMO_ROOT}"
}
EOF

cat "${REPORT_PATH}"
${ready}
