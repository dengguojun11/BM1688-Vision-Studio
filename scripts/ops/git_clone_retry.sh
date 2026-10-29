#!/usr/bin/env bash
# 供 deploy 引导脚本共用：带重试的 git clone（缓解 GitHub 网络中断）
# 用法: source 后调用 git_clone_retry <depth> <url> <dest>
# 环境变量: GIT_CLONE_RETRIES（默认 5）、GIT_CLONE_RETRY_SLEEP（默认 5 秒）

git_clone_retry_log() {
  if [[ -n "${GIT_CLONE_RETRY_LOG_PREFIX:-}" ]]; then
    echo "${GIT_CLONE_RETRY_LOG_PREFIX} $*"
  else
    echo "[git-clone-retry] $*"
  fi
}

git_clone_retry_prepare() {
  git config --global http.postBuffer 524288000 2>/dev/null || true
  git config --global http.lowSpeedLimit 1000 2>/dev/null || true
  git config --global http.lowSpeedTime 600 2>/dev/null || true
  # 缓解 HTTP/2 stream CANCEL / early EOF（国内服务器 clone GitHub 常见）
  git config --global http.version HTTP/1.1 2>/dev/null || true
}

# 用法: git_clone_retry <depth> <url> <dest> [extra git clone args...]
# 返回 0 表示 clone 成功
git_clone_retry() {
  local depth="${1:-1}"
  local url="$2"
  local dest="$3"
  shift 3
  local -a extra_args=("$@")
  local max="${GIT_CLONE_RETRIES:-8}"
  local sleep_base="${GIT_CLONE_RETRY_SLEEP:-5}"
  local attempt parent partial

  if [[ -z "${url}" || -z "${dest}" ]]; then
    git_clone_retry_log "错误: 缺少 url 或 dest"
    return 1
  fi

  if [[ -d "${dest}/.git" ]] && [[ -n "$(ls -A "${dest}" 2>/dev/null)" ]]; then
    git_clone_retry_log "目标已是 git 仓库，跳过 clone: ${dest}"
    return 0
  fi

  parent="$(dirname "${dest}")"
  mkdir -p "${parent}"
  git_clone_retry_prepare

  for ((attempt = 1; attempt <= max; attempt++)); do
    git_clone_retry_log "clone 第 ${attempt}/${max} 次: ${url} -> ${dest}"
    partial="$(ls -A "${dest}" 2>/dev/null || true)"
    if [[ -n "${partial}" ]]; then
      rm -rf "${dest}"
    fi
    mkdir -p "${dest}"
    if git clone --depth "${depth}" "${extra_args[@]}" "${url}" "${dest}"; then
      git_clone_retry_log "clone 成功"
      return 0
    fi
    git_clone_retry_log "clone 失败，${sleep_base}s 后重试…"
    sleep "${sleep_base}"
  done
  return 1
}
