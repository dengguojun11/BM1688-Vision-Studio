#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${LABELU_LA_SERVICE_NAME:-labelu-locateanything}"
PROJECT_ROOT="${LABELU_PROJECT_ROOT:-/data/labeIU/labeIuproject/labeIU}"
INSTALL_ROOT="${LABELU_LA_INSTALL_ROOT:-/data/ai/locateanything-3b}"
MODEL_PATH="${LABELU_LA_MODEL_PATH:-${INSTALL_ROOT}/models/LocateAnything-3B}"
PYTHON_BIN="${LABELU_LA_PYTHON:-${INSTALL_ROOT}/venv/bin/python}"
HOST="${LABELU_LA_HOST:-127.0.0.1}"
PORT="${LABELU_LA_PORT:-5001}"
CUDA_VISIBLE_DEVICES_VALUE="${LABELU_LA_CUDA_VISIBLE_DEVICES:-0}"
IDLE_UNLOAD_SECONDS="${LABELU_LA_GPU_IDLE_UNLOAD_SECONDS:-300}"
QUANTIZATION="${LABELU_LA_QUANTIZATION:-q4}"
PRELOAD="${LABELU_LA_PRELOAD:-0}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo env ... bash $0" >&2
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "LocateAnything Python not found or not executable: ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/model_server/locateanything/server.py" ]]; then
  echo "LocateAnything adapter not found under project root: ${PROJECT_ROOT}" >&2
  exit 1
fi

extra_args=()
if [[ "${PRELOAD}" == "1" || "${PRELOAD}" == "true" || "${PRELOAD}" == "yes" ]]; then
  extra_args+=(--preload)
fi

unit_path="/etc/systemd/system/${SERVICE_NAME}.service"
cat >"${unit_path}" <<EOF
[Unit]
Description=LabelU LocateAnything model adapter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_ROOT}
Environment=PYTHONUNBUFFERED=1
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_OFFLINE=1
Environment=CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES_VALUE}
Environment=LA_GPU_IDLE_UNLOAD_SECONDS=${IDLE_UNLOAD_SECONDS}
Environment=LA_QUANTIZATION=${QUANTIZATION}
ExecStart=${PYTHON_BIN} ${PROJECT_ROOT}/model_server/locateanything/server.py --host ${HOST} --port ${PORT} --install-root ${INSTALL_ROOT} --model ${MODEL_PATH} --quantization ${QUANTIZATION} ${extra_args[*]}
Restart=on-failure
RestartSec=10
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

echo "Installed ${unit_path}"
for _ in $(seq 1 30); do
  if curl -fsS "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
    echo "Health OK: http://${HOST}:${PORT}/health"
    exit 0
  fi
  sleep 1
done

echo "Service did not become healthy within 30s: ${SERVICE_NAME}.service" >&2
systemctl status "${SERVICE_NAME}.service" --no-pager -n 50 >&2 || true
exit 1
