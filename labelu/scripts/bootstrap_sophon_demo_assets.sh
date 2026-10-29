#!/usr/bin/env bash
# 部署时拉取 sophon-demo YOLOv8_plus_det 的 onnx/qtable/coco128（供 BM1688 INT8 全自动编译）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
if [[ -f "${REPO_ROOT}/scripts/ops/git_clone_retry.sh" ]]; then
  # shellcheck source=scripts/ops/git_clone_retry.sh
  source "${REPO_ROOT}/scripts/ops/git_clone_retry.sh"
  GIT_CLONE_RETRY_LOG_PREFIX="[sophon-assets]"
fi
if [[ -f "${REPO_ROOT}/scripts/ops/offline_assets_common.sh" ]]; then
  # shellcheck source=scripts/ops/offline_assets_common.sh
  source "${REPO_ROOT}/scripts/ops/offline_assets_common.sh"
  OFFLINE_ASSETS_LOG_PREFIX="[sophon-assets]"
  OFFLINE_REPO_ROOT="${REPO_ROOT}"
fi

log() { echo "[sophon-assets] $*"; }

fail_or_warn() {
  log "$1"
  if [[ "${LABELU_DEPLOY_STRICT:-}" == "1" ]] \
    || [[ "${LABELU_DEPLOY_STRICT:-}" == "y" ]] \
    || [[ "${LABELU_DEPLOY_STRICT:-}" == "yes" ]]; then
    exit 1
  fi
  exit 0
}

QTABLE_NAME="${YOLO_QTABLE_NAME:-yolov11s_qtable}"
COCO128_DIRNAME="${SOPHON_COCO128_DIRNAME:-coco128}"

BASE="${LABELU_DEPLOY_RESOLVED_BASE:-}"
if [[ -z "${BASE}" ]]; then
  if [[ -n "${MEDIA_ROOT:-}" ]]; then
    BASE="$(dirname "${MEDIA_ROOT}")"
  else
    BASE="/data/labeIU"
  fi
fi

SOPHON_DEMO_ROOT="${SOPHON_DEMO_ROOT:-${BASE}/sophon-demo}"
DET_ROOT="${SOPHON_DET_ROOT:-${SOPHON_DEMO_ROOT}/sample/YOLOv8_plus_det}"
GIT_URL="${SOPHON_DEMO_GIT_URL:-https://github.com/sophgo/sophon-demo.git}"
GIT_BRANCH="${SOPHON_DEMO_GIT_BRANCH:-release}"

mkdir -p "$(dirname "${SOPHON_DEMO_ROOT}")"

bootstrap_try_offline_sophon() {
  local archive marker="sample/YOLOv8_plus_det/scripts/download.sh"
  archive="$(offline_find_archive "sophon-demo" "SOPHON_DEMO_OFFLINE_ARCHIVE" || true)"
  if [[ -z "${archive}" ]]; then
    return 1
  fi
  log "使用离线包（跳过 git clone）: ${archive}"
  offline_extract_archive "${archive}" "${SOPHON_DEMO_ROOT}" "${marker}"
}

bootstrap_clone_sophon_demo() {
  if [[ -d "${SOPHON_DEMO_ROOT}/.git" ]] || [[ -f "${DET_ROOT}/scripts/download.sh" ]]; then
    log "sophon-demo exists, skip clone"
    return 0
  fi
  if declare -f bootstrap_try_offline_sophon >/dev/null 2>&1 && bootstrap_try_offline_sophon; then
    log "sophon-demo 已从离线包解压"
    return 0
  fi
  log "clone sophon-demo -> ${SOPHON_DEMO_ROOT}"
  if declare -f git_clone_retry >/dev/null 2>&1; then
    git_clone_retry 1 "${GIT_URL}" "${SOPHON_DEMO_ROOT}" --branch "${GIT_BRANCH}" \
      || fail_or_warn "错误: sophon-demo clone 失败（已重试）"
    return 0
  fi
  git clone --depth 1 --branch "${GIT_BRANCH}" "${GIT_URL}" "${SOPHON_DEMO_ROOT}" \
    || fail_or_warn "错误: sophon-demo clone 失败"
}

bootstrap_clone_sophon_demo

if [[ ! -d "${DET_ROOT}/scripts" ]]; then
  fail_or_warn "错误: 缺少 ${DET_ROOT}/scripts"
fi

chmod -R +x "${DET_ROOT}/scripts/" 2>/dev/null || true
cd "${DET_ROOT}"

normalize_onnx_layout() {
  mkdir -p models/onnx
  if [[ -d onnx ]]; then
    shopt -s nullglob
    local f base
    for f in onnx/*; do
      [[ -e "${f}" ]] || continue
      base="$(basename "${f}")"
      if [[ ! -e "models/onnx/${base}" ]]; then
        mv -f "${f}" "models/onnx/" 2>/dev/null || cp -a "${f}" "models/onnx/" || true
      fi
    done
    shopt -u nullglob
    rmdir onnx 2>/dev/null || true
  fi
  if [[ -d models/onnx/onnx ]]; then
    shopt -s nullglob
    local f base
    for f in models/onnx/onnx/*; do
      [[ -e "${f}" ]] || continue
      base="$(basename "${f}")"
      if [[ ! -e "models/onnx/${base}" ]]; then
        mv -f "${f}" "models/onnx/" 2>/dev/null || true
      fi
    done
    shopt -u nullglob
    rmdir models/onnx/onnx 2>/dev/null || true
  fi
}

has_qtable() {
  [[ -f "models/onnx/${QTABLE_NAME}" ]] \
    || [[ -f "onnx/${QTABLE_NAME}" ]] \
    || find models/onnx onnx -maxdepth 1 -name '*qtable*' -type f 2>/dev/null | grep -q .
}

has_coco128() {
  local d="datasets/${COCO128_DIRNAME}"
  local img_dir="${d}/images"
  [[ -d "${d}" ]] || return 1
  if [[ -d "${img_dir}" ]]; then
    local listing
    listing="$(ls -A "${img_dir}" 2>/dev/null || true)"
    [[ -n "${listing}" ]] && return 0
  fi
  find "${d}" -maxdepth 2 -type f \( -iname '*.jpg' -o -iname '*.png' \) 2>/dev/null | grep -q .
}

download_coco128_fallback() {
  local target="datasets/${COCO128_DIRNAME}"
  if has_coco128; then
    return 0
  fi
  mkdir -p datasets
  local zip="datasets/${COCO128_DIRNAME}.zip"
  local urls=(
    "https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip"
    "https://ultralytics.com/assets/coco128.zip"
  )
  log "coco128 备用下载（wget/curl）…"
  rm -f "${zip}"
  local url ok=1
  for url in "${urls[@]}"; do
    if command -v curl >/dev/null 2>&1; then
      if curl -fSL --retry 3 --connect-timeout 30 -o "${zip}" "${url}"; then
        ok=0
        break
      fi
    elif command -v wget >/dev/null 2>&1; then
      if wget -q --tries=3 -O "${zip}" "${url}"; then
        ok=0
        break
      fi
    fi
  done
  if [[ "${ok}" -ne 0 ]]; then
    return 1
  fi
  if command -v unzip >/dev/null 2>&1; then
    unzip -qo "${zip}" -d datasets
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c "import zipfile; zipfile.ZipFile('${zip}').extractall('datasets')"
  else
    log "错误: 需要 unzip 或 python3 解压 coco128"
    return 1
  fi
  rm -f "${zip}"
  if [[ -d datasets/coco128 ]] && [[ ! -d "${target}" ]]; then
    mv datasets/coco128 "${target}" 2>/dev/null || true
  fi
  has_coco128
}

run_download_sh() {
  [[ -x "./scripts/download.sh" ]] || return 1
  if ! has_qtable; then
    log "download.sh --onnx (qtable + onnx)…"
    ./scripts/download.sh --onnx
    normalize_onnx_layout
  fi
  if has_coco128; then
    return 0
  fi
  log "download.sh 数据集 coco128（多参数兼容）…"
  set +e
  local rc_ds=1
  if ./scripts/download.sh --dataset 2>/dev/null; then
    rc_ds=0
  elif ./scripts/download.sh --datasets 2>/dev/null; then
    rc_ds=0
  elif ./scripts/download.sh datasets 2>/dev/null; then
    rc_ds=0
  elif ./scripts/download.sh dataset 2>/dev/null; then
    rc_ds=0
  fi
  set -e
  if [[ "${rc_ds}" -ne 0 ]]; then
    log "download.sh 数据集参数未命中，尝试备用下载"
  fi
  normalize_onnx_layout
}

if has_qtable && has_coco128; then
  normalize_onnx_layout
  log "onnx/qtable 与 coco128 已就绪"
  exit 0
fi

if run_download_sh; then
  :
else
  log "WARN: 未找到 ./scripts/download.sh"
fi

normalize_onnx_layout

if ! has_coco128; then
  download_coco128_fallback || fail_or_warn "错误: coco128 未就绪（${DET_ROOT}/datasets/${COCO128_DIRNAME}）"
fi

if ! has_qtable; then
  fail_or_warn "错误: qtable 未就绪（期望 models/onnx/${QTABLE_NAME}）"
fi

if ! has_coco128; then
  fail_or_warn "错误: coco128 未就绪"
fi

qtable_disp="$(readlink -f "models/onnx/${QTABLE_NAME}" 2>/dev/null || echo ok)"
log "finished: DET_ROOT=${DET_ROOT} qtable=${qtable_disp} coco128=datasets/${COCO128_DIRNAME}"
