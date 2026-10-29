#!/usr/bin/env bash
# 离线资源包：查找路径、解压到目标目录（供 tpu_mlir / sophon bootstrap 共用）
# shellcheck disable=SC2034
set -euo pipefail

offline_assets_log() {
  if [[ -n "${OFFLINE_ASSETS_LOG_PREFIX:-}" ]]; then
    echo "${OFFLINE_ASSETS_LOG_PREFIX} $*"
  else
    echo "[offline-assets] $*"
  fi
}

# 按优先级返回第一个存在的归档绝对路径（stdout）
# 用法: offline_find_archive "tpu-mlir" "TPU_MLIR_OFFLINE_ARCHIVE" [extra_paths...]
offline_find_archive() {
  local name="$1"
  local env_var_name="${2:-}"
  shift 2
  local f base repo_root="${OFFLINE_REPO_ROOT:-}"
  local deploy_base="${LABELU_DEPLOY_RESOLVED_BASE:-}"
  local offline_dir="${LABELU_DEPLOY_OFFLINE_DIR:-}"
  local explicit=""

  if [[ -n "${env_var_name}" ]]; then
    explicit="${!env_var_name:-}"
    if [[ -n "${explicit}" && -f "${explicit}" ]]; then
      printf '%s' "$(cd "$(dirname "${explicit}")" && pwd)/$(basename "${explicit}")"
      return 0
    fi
  fi

  for f in "$@"; do
    [[ -f "${f}" ]] && printf '%s' "$(cd "$(dirname "${f}")" && pwd)/$(basename "${f}")" && return 0
  done

  local -a dirs=()
  [[ -n "${offline_dir}" ]] && dirs+=("${offline_dir}")
  [[ -n "${deploy_base}" ]] && dirs+=("${deploy_base}/offline")
  [[ -n "${repo_root}" ]] && dirs+=("${repo_root}/deploy/offline")

  for base in "${dirs[@]}"; do
    [[ -d "${base}" ]] || continue
    for f in "$@"; do
      local bn
      bn="$(basename "${f}")"
      if [[ -f "${base}/${bn}" ]]; then
        printf '%s' "${base}/${bn}"
        return 0
      fi
      if [[ -f "${base}/${name}/${bn}" ]]; then
        printf '%s' "${base}/${name}/${bn}"
        return 0
      fi
    done
    for cand in "${base}/${name}.tar.gz" "${base}/${name}.tgz" "${base}/${name}.zip"; do
      [[ -f "${cand}" ]] && printf '%s' "${cand}" && return 0
    done
  done
  return 1
}

# 解压归档到 dest_dir（目录内须出现 relative_marker，如 envsetup.sh）
offline_extract_archive() {
  local archive="$1"
  local dest_dir="$2"
  local relative_marker="${3:-envsetup.sh}"

  if [[ ! -f "${archive}" ]]; then
    offline_assets_log "错误: 归档不存在: ${archive}"
    return 1
  fi

  local parent tmp
  parent="$(dirname "${dest_dir}")"
  mkdir -p "${parent}"
  tmp="$(mktemp -d "${parent}/.offline_extract.XXXXXX")"

  offline_assets_log "解压离线包: ${archive} -> ${parent}"
  case "${archive}" in
    *.tar.gz | *.tgz)
      tar -xzf "${archive}" -C "${tmp}"
      ;;
    *.zip)
      if ! command -v unzip >/dev/null 2>&1; then
        offline_assets_log "错误: 需要 unzip"
        rm -rf "${tmp}"
        return 1
      fi
      unzip -qo "${archive}" -d "${tmp}"
      ;;
    *)
      offline_assets_log "错误: 不支持的归档格式: ${archive}"
      rm -rf "${tmp}"
      return 1
      ;;
  esac

  local found=""
  if [[ -f "${tmp}/${relative_marker}" ]]; then
    found="${tmp}"
  else
    found="$(find "${tmp}" -maxdepth 3 -name "${relative_marker}" -type f 2>/dev/null | head -1)"
    if [[ -n "${found}" ]]; then
      found="$(dirname "${found}")"
    fi
  fi

  if [[ -z "${found}" || ! -f "${found}/${relative_marker}" ]]; then
    offline_assets_log "错误: 归档内未找到 ${relative_marker}"
    rm -rf "${tmp}"
    return 1
  fi

  rm -rf "${dest_dir}"
  mv "${found}" "${dest_dir}"
  rm -rf "${tmp}"
  offline_assets_log "离线解压完成: ${dest_dir}"
  return 0
}
