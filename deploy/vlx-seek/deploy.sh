#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root (for example: sudo bash deploy.sh)." >&2
  exit 1
fi

PACKAGE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEPLOY_ROOT=${VLX_DEPLOY_ROOT:-/home/ubuntu/vlx-seek-deploy}
SOURCE_DIR=${VLX_SOURCE_DIR:-${DEPLOY_ROOT}/VLX-Seek}
SERVICE_PYTHON=${VLX_SERVICE_PYTHON:-/home/ubuntu/JoyAI-VL-Interaction/services/.venv/bin/python}
SERVICE_UNIT=${VLX_SERVICE_UNIT:-/etc/systemd/system/vlx-seek.service}
MODEL_DIR=${VLX_MODEL_PATH:-${DEPLOY_ROOT}/models/VLX-Seek-1.5-10B}
DETECTOR_FILE=${VLX_DETECTOR_PATH:-${SOURCE_DIR}/resources/wedetect_base_uni.pth}

required_paths=(
  "${SOURCE_DIR}/inference.py"
  "${SOURCE_DIR}/detect_tools/WeDetect/generate_proposal.py"
  "${MODEL_DIR}/config.json"
  "${DETECTOR_FILE}"
  "${SERVICE_PYTHON}"
)
for path in "${required_paths[@]}"; do
  if [[ ! -e ${path} ]]; then
    echo "Required deployment path is missing: ${path}" >&2
    exit 1
  fi
done

install -o ubuntu -g ubuntu -m 0644 "${PACKAGE_DIR}/runtime/app.py" "${SOURCE_DIR}/app.py"
install -o ubuntu -g ubuntu -m 0644 "${PACKAGE_DIR}/runtime/vlx_seek_worker.py" "${SOURCE_DIR}/vlx_seek_worker.py"
install -d -o ubuntu -g ubuntu -m 0755 "${SOURCE_DIR}/static"
install -o ubuntu -g ubuntu -m 0644 "${PACKAGE_DIR}/runtime/static/index.html" "${SOURCE_DIR}/static/index.html"

while IFS= read -r relative_path; do
  target="${SOURCE_DIR}/${relative_path#runtime/}"
  install -d -o ubuntu -g ubuntu -m 0755 "$(dirname -- "${target}")"
  install -o ubuntu -g ubuntu -m 0644 "${PACKAGE_DIR}/${relative_path}" "${target}"
done < <(cd "${PACKAGE_DIR}" && find runtime/vlx_seek -type f -print | sort)

commit=${LABELU_DEPLOY_COMMIT:-unknown}
printf '%s\n' "${commit}" > "${DEPLOY_ROOT}/labelu-deployment-revision"
chown ubuntu:ubuntu "${DEPLOY_ROOT}/labelu-deployment-revision"

PYTHONDONTWRITEBYTECODE=1 "${SERVICE_PYTHON}" -m py_compile \
  "${SOURCE_DIR}/app.py" \
  "${SOURCE_DIR}/vlx_seek_worker.py" \
  "${SOURCE_DIR}/vlx_seek/models/vlx_seek_1_5/builder.py" \
  "${SOURCE_DIR}/vlx_seek/models/vlx_seek_1_5/multimodal_encoder/qwen3_5_vl_encoder.py" \
  "${SOURCE_DIR}/vlx_seek/models/vlx_seek_1_5/multimodal_encoder/c_radio_v4_aux_encoder.py"

install -o root -g root -m 0644 "${PACKAGE_DIR}/systemd/vlx-seek.service" "${SERVICE_UNIT}"
systemctl daemon-reload
systemctl enable vlx-seek.service
systemctl restart vlx-seek.service
echo "VLX-Seek restart requested from LabelU commit ${commit}."
