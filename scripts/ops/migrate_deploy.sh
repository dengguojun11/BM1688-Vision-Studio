#!/usr/bin/env bash
# 供 deploy.sh、labelu_server_update.sh、scripts/ops/migrate.sh 共用的 Alembic 迁移逻辑。
# 用法（source 后调用）:
#   labelu_migrate_upgrade_head /path/to/repo
# 环境变量:
#   LABELU_ENV_FILE / LABELU_DEPLOY_RUNTIME_DIR / VENV_DIR / LABELU_PYTHON
#   DATABASE_URL（可由 load_dotenv 从 .env 载入）
set -euo pipefail

labelu_migrate_die() { echo "[labelu-migrate] 错误: $*" >&2; exit 1; }
labelu_migrate_log() { echo "[labelu-migrate] $*"; }

labelu_migrate_pick_python() {
  if [[ -n "${LABELU_PYTHON:-}" && -x "${LABELU_PYTHON}" ]]; then
    echo "${LABELU_PYTHON}"
    return 0
  fi
  if [[ -n "${VENV_DIR:-}" && -x "${VENV_DIR}/bin/python" ]]; then
    echo "${VENV_DIR}/bin/python"
    return 0
  fi
  local cand repo_root="${LABELU_MIGRATE_REPO_ROOT:-}"
  for cand in \
    "${LABELU_DEPLOY_RUNTIME_DIR:-}/venv/bin/python" \
    "/data/labeIU/labeIuMedia/venv/bin/python" \
    "${repo_root}/../../labeIuMedia/venv/bin/python" \
    "${repo_root}/labeIuMedia/venv/bin/python" \
    "${repo_root}/.venv/bin/python" \
    "${repo_root}/venv/bin/python"
  do
    if [[ -n "${cand}" && -x "${cand}" ]]; then
      echo "${cand}"
      return 0
    fi
  done
  if command -v python3.11 >/dev/null 2>&1; then echo "python3.11"; return 0; fi
  if command -v python3 >/dev/null 2>&1; then echo "python3"; return 0; fi
  command -v python >/dev/null 2>&1 && echo "python" && return 0
  labelu_migrate_die "未找到 Python，请设置 LABELU_PYTHON 或 VENV_DIR"
}

labelu_migrate_load_dotenv() {
  local f tried=0
  for f in \
    "${LABELU_ENV_FILE:-}" \
    "${LABELU_DEPLOY_RUNTIME_DIR:-}/.env" \
    "/data/labeIU/labeIuMedia/.env"
  do
    [[ -z "${f}" ]] && continue
    if [[ -f "${f}" ]]; then
      labelu_migrate_log "载入环境: ${f}"
      set -a
      # shellcheck disable=SC1090
      source "${f}"
      set +a
      tried=1
      break
    fi
  done
  if [[ "${tried}" -eq 0 && -n "${LABELU_MIGRATE_REPO_ROOT:-}" ]]; then
    for f in \
      "${LABELU_MIGRATE_REPO_ROOT}/../../labeIuMedia/.env" \
      "${LABELU_MIGRATE_REPO_ROOT}/labeIuMedia/.env" \
      "${LABELU_MIGRATE_REPO_ROOT}/.env"
    do
      if [[ -f "${f}" ]]; then
        labelu_migrate_log "载入环境: ${f}"
        set -a
        # shellcheck disable=SC1090
        source "${f}"
        set +a
        tried=1
        break
      fi
    done
  fi
  if [[ "${tried}" -eq 0 ]]; then
    labelu_migrate_log "未找到 .env；若迁移失败请设置 LABELU_ENV_FILE"
  fi
}

labelu_migrate_release_gate() {
  local repo_root="$1" py="$2"
  local gate_args=(
    -m labelu.scripts.migration_release_gate
    --alembic-config "${repo_root}/labelu/alembic_labelu/alembic.ini"
    --versions-dir "${repo_root}/labelu/alembic_labelu/versions"
    --candidate-dir "${LABELU_MIGRATION_CANDIDATE_DIR:-${repo_root}/labelu/docs/7gai_phase_one_migration_candidates}"
  )

  # 迁移发布前只允许读取本机沙箱证据。没有明确的沙箱 URL、备份恢复
  # 和锁等待证据时直接失败，不能用 DATABASE_URL（尤其是生产 URL）
  # 冒充演练，也不能通过环境变量关闭该门禁。
  [[ -n "${LABELU_MIGRATION_SANDBOX_URL:-}" ]] || \
    labelu_migrate_die "缺少 LABELU_MIGRATION_SANDBOX_URL，迁移发布闸门拒绝继续"
  [[ -n "${LABELU_MIGRATION_BACKUP_EVIDENCE:-}" ]] || \
    labelu_migrate_die "缺少 LABELU_MIGRATION_BACKUP_EVIDENCE，迁移发布闸门拒绝继续"
  [[ -n "${LABELU_MIGRATION_LOCK_WAIT_EVIDENCE:-}" ]] || \
    labelu_migrate_die "缺少 LABELU_MIGRATION_LOCK_WAIT_EVIDENCE，迁移发布闸门拒绝继续"

  gate_args+=(
    --sandbox-url "${LABELU_MIGRATION_SANDBOX_URL}"
    --backup-evidence "${LABELU_MIGRATION_BACKUP_EVIDENCE}"
    --lock-wait-evidence "${LABELU_MIGRATION_LOCK_WAIT_EVIDENCE}"
  )
  labelu_migrate_log "只读迁移发布闸门（不连接生产数据库）"
  (cd "${repo_root}" && PYTHONPATH="${repo_root}:${PYTHONPATH:-}" "${py}" "${gate_args[@]}") || \
    labelu_migrate_die "迁移发布闸门未通过，禁止连接 DATABASE_URL 或执行 Alembic DDL"
}

# 参数: 仓库根目录（含 pyproject.toml）
labelu_migrate_upgrade_head() {
  local repo_root="$1"
  [[ -f "${repo_root}/pyproject.toml" ]] || labelu_migrate_die "仓库根无效: ${repo_root}"
  export LABELU_MIGRATE_REPO_ROOT="${repo_root}"
  labelu_migrate_load_dotenv
  if [[ -z "${DATABASE_URL:-}" ]]; then
    labelu_migrate_die "DATABASE_URL 未设置，请在运行目录 .env 中配置"
  fi
  local py
  py="$(labelu_migrate_pick_python)"
  labelu_migrate_log "Python: ${py} ($("${py}" -V 2>&1))"
  labelu_migrate_release_gate "${repo_root}" "${py}"
  labelu_migrate_log "alembic upgrade head（${repo_root}/labelu/alembic_labelu）"
  (
    cd "${repo_root}/labelu/alembic_labelu"
    "${py}" -m alembic -c alembic.ini upgrade head
  )
  local rc=$?
  if [[ "${rc}" -ne 0 ]]; then
    labelu_migrate_die "alembic upgrade head 失败 (exit ${rc})"
  fi
  labelu_migrate_log "数据库迁移完成"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  root="${1:-}"
  if [[ -z "${root}" ]]; then
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  fi
  labelu_migrate_upgrade_head "${root}"
fi
