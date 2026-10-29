#!/usr/bin/env bash
# =============================================================================
# LabelU 服务器一键更新：git pull → pip → 迁移 → 前端构建 → 重启服务
#
# 用法（在仓库根或任意目录执行均可）：
#   sudo bash scripts/labelu_server_update.sh
#   sudo bash scripts/labelu_server_update.sh all
#   sudo bash scripts/labelu_server_update.sh migrate
#
# 环境变量（可选）：
#   LABELU_ENV_FILE / LABELU_DEPLOY_RUNTIME_DIR / VENV_DIR / LABELU_PYTHON
#   LABELU_SKIP_GIT_PULL=1 / LABELU_SKIP_PIP=1 / LABELU_SKIP_MIGRATE=1
#   LABELU_SKIP_FRONTEND_BUILD=1  （deploy.sh update 时跳过 labelu frontend-build）
#   LABELU_SKIP_SERVICE_RESTART=1 / LABELU_SERVICE_NAME=labelu
#   LABELU_GIT_BRANCH=main          跟踪分支（默认 main）
#   LABELU_GIT_RESET_ON_PULL=1      默认 fetch + reset --hard，避免服务器手改 deploy.sh 导致 pull 失败
#   LABELU_GIT_RESET_ON_PULL=0      改回普通 git pull（保留工作区改动）
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# shellcheck source=scripts/ops/migrate_deploy.sh
source "${SCRIPT_DIR}/ops/migrate_deploy.sh"

MODE="${1:-all}"
SERVICE_NAME="${LABELU_SERVICE_NAME:-labelu}"
INSTALL_EXTRAS="${LABELU_INSTALL_EXTRAS:-${INSTALL_EXTRAS:-yolo}}"

die() { echo "[labelu-update] 错误: $*" >&2; exit 1; }
log() { echo "[labelu-update] $*"; }

run_git_pull() {
  if [[ "${LABELU_SKIP_GIT_PULL:-}" == "1" ]]; then
    log "跳过 git pull（LABELU_SKIP_GIT_PULL=1）"
    return 0
  fi
  if [[ ! -d "${REPO_ROOT}/.git" ]]; then
    log "非 git 仓库，跳过 git pull"
    return 0
  fi

  local branch="${LABELU_GIT_BRANCH:-main}"
  if [[ "${LABELU_GIT_RESET_ON_PULL:-1}" == "1" ]]; then
    log "git fetch + reset --hard origin/${branch}（${REPO_ROOT}，丢弃代码目录内未提交改动）"
    git -C "${REPO_ROOT}" fetch origin "${branch}"
    git -C "${REPO_ROOT}" reset --hard "origin/${branch}"
    git -C "${REPO_ROOT}" clean -fd
    return 0
  fi

  log "git pull（${REPO_ROOT}，LABELU_GIT_RESET_ON_PULL=0）"
  git -C "${REPO_ROOT}" pull --rebase --autostash 2>/dev/null \
    || git -C "${REPO_ROOT}" pull --ff-only 2>/dev/null \
    || git -C "${REPO_ROOT}" pull
}

run_pip_install() {
  if [[ "${LABELU_SKIP_PIP:-}" == "1" ]]; then
    log "跳过 pip install（LABELU_SKIP_PIP=1）"
    return 0
  fi
  local py
  py="$(labelu_migrate_pick_python)"
  set +e
  if [[ "${INSTALL_EXTRAS}" == *yolo* ]]; then
    log "pip install -e .[${INSTALL_EXTRAS}]"
    "${py}" -m pip install --no-cache-dir -e "${REPO_ROOT}[${INSTALL_EXTRAS}]"
  else
    log "pip install -e ."
    "${py}" -m pip install --no-cache-dir -e "${REPO_ROOT}"
  fi
  local rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    die "pip install 返回 ${rc}（常见原因：磁盘满、依赖冲突）。修复后重试: sudo bash deploy.sh update"
  fi
  run_verify_backend_features
}

run_verify_backend_features() {
  local py
  py="$(labelu_migrate_pick_python)"
  log "校验已安装 labelu 包是否含标注统计所需字段…"
  if ! "${py}" -c "from labelu.internal.application.response.user import UserResponse; assert 'is_task_superuser' in UserResponse.model_fields"; then
    die "已安装的 labelu 仍缺少 UserResponse.is_task_superuser，pip install 未生效或指向错误 Python"
  fi
  log "后端特性校验 OK（UserResponse.is_task_superuser）"
}

run_alembic() {
  if [[ "${LABELU_SKIP_MIGRATE:-}" == "1" ]]; then
    log "跳过数据库迁移（LABELU_SKIP_MIGRATE=1）"
    return 0
  fi
  set +e
  labelu_migrate_upgrade_head "${REPO_ROOT}"
  local rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    log "警告: alembic 迁移返回 ${rc}，请检查 .env 中 DATABASE_URL"
    return 0
  fi
}

run_frontend_build() {
  if [[ "${LABELU_SKIP_FRONTEND_BUILD:-}" == "1" ]]; then
    log "跳过前端构建（LABELU_SKIP_FRONTEND_BUILD=1）"
    return 0
  fi
  if [[ ! -f "${REPO_ROOT}/deploy.sh" ]]; then
    log "警告: 未找到 deploy.sh，跳过前端构建"
    return 0
  fi
  log "前端构建（Node/pnpm 检查 + labelu frontend-build → statics）…"
  bash "${REPO_ROOT}/deploy.sh" frontend-build || {
    log "错误: 前端构建失败，中止更新（可设 LABELU_SKIP_FRONTEND_BUILD=1 仅发后端）"
    exit 1
  }
}

run_restart() {
  if [[ "${LABELU_SKIP_SERVICE_RESTART:-}" == "1" ]]; then
    log "跳过 systemctl restart（LABELU_SKIP_SERVICE_RESTART=1）"
    return 0
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    log "无 systemctl，请手动重启 LabelU"
    return 0
  fi
  if [[ -f "${REPO_ROOT}/scripts/ops/deploy_yolo_train_guard.sh" ]]; then
    # shellcheck source=scripts/ops/deploy_yolo_train_guard.sh
    source "${REPO_ROOT}/scripts/ops/deploy_yolo_train_guard.sh"
    local runtime_python runtime_dir
    runtime_python="$(labelu_migrate_pick_python)" || die "未找到部署训练保护所需的 Python"
    runtime_dir="${LABELU_DEPLOY_RUNTIME_DIR:-${MEDIA_ROOT:-}}"
    if [[ -z "${runtime_dir}" && "${runtime_python}" == */venv/bin/python ]]; then
      runtime_dir="$(cd "$(dirname "${runtime_python}")/../.." && pwd)"
    fi
    export LABELU_PYTHON="${LABELU_PYTHON:-${runtime_python}}"
    export VENV_DIR="${VENV_DIR:-$(cd "$(dirname "${runtime_python}")/.." && pwd)}"
    export LABELU_DEPLOY_RUNTIME_DIR="${LABELU_DEPLOY_RUNTIME_DIR:-${runtime_dir}}"
    export LABELU_ENV_FILE="${LABELU_ENV_FILE:-${runtime_dir%/}/.env}"
    if [[ -f "${LABELU_ENV_FILE}" ]]; then
      set -a
      # shellcheck disable=SC1090
      source "${LABELU_ENV_FILE}" 2>/dev/null || true
      set +a
    fi
    export MEDIA_ROOT="${MEDIA_ROOT:-${runtime_dir}}"
    export SERVER_RUNTIME_DIR="${LABELU_DEPLOY_RUNTIME_DIR:-${MEDIA_ROOT:-}}"
    export LABELU_PROJECT_ROOT="${REPO_ROOT}"
    if ! deploy_yolo_train_prepare_for_restart; then
      die "已中止 systemctl restart（YOLO 训练自动暂停失败）"
    fi
  fi
  log "systemctl restart ${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
  systemctl --no-pager -l status "${SERVICE_NAME}" || true
  if [[ -f "${REPO_ROOT}/scripts/ops/deploy_yolo_train_guard.sh" ]]; then
    # shellcheck source=scripts/ops/deploy_yolo_train_guard.sh
    source "${REPO_ROOT}/scripts/ops/deploy_yolo_train_guard.sh"
    sleep 3
    deploy_yolo_train_resume_after_restart || true
  fi
}

run_http_probe() {
  local port="${LABELU_PORT:-10000}"
  if ! command -v curl >/dev/null 2>&1; then
    return 0
  fi
  local code
  code="$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 8 \
    "http://127.0.0.1:${port}/docs" 2>/dev/null || echo "000")"
  if [[ "${code}" == "200" ]]; then
    log "HTTP 探活 OK: /docs → ${code}"
  else
    log "警告: HTTP 探活失败 (/docs → ${code})，建议: journalctl -u ${SERVICE_NAME} -n 80 --no-pager"
  fi
}

echo "======== LabelU 一键更新 ========"
echo "仓库根: ${REPO_ROOT}"
echo "模式: ${MODE}"

case "${MODE}" in
  all)
    run_git_pull
    run_pip_install
    run_alembic
    run_frontend_build
    run_restart
    run_http_probe
    ;;
  migrate)
    run_alembic
    ;;
  pull-only)
    run_git_pull
    ;;
  pip-only)
    run_pip_install
    ;;
  restart-only)
    run_restart
    run_http_probe
    ;;
  *)
    die "未知参数: ${MODE}。可用: all | migrate | pull-only | pip-only | restart-only"
    ;;
esac

echo "======== 完成 ========"
