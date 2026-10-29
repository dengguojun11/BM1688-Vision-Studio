# shellcheck shell=bash
# 部署重启前检测/自动暂停进行中的 YOLO 训练（读 MEDIA_ROOT/yolo_train/job-*/phase.json）
#
# 用法（需先设置 SERVER_RUNTIME_DIR 或 MEDIA_ROOT）:
#   source scripts/ops/deploy_yolo_train_guard.sh
#   deploy_yolo_train_prepare_for_restart   # 发版前：自动暂停并等待
#   deploy_yolo_train_resume_after_restart  # 发版后：自动续训

deploy_yolo_train_work_root() {
  local media="${MEDIA_ROOT:-${SERVER_RUNTIME_DIR:-}}"
  if [[ -z "${media}" ]]; then
    return 1
  fi
  local subdir="${YOLO_TRAIN_WORK_SUBDIR:-yolo_train}"
  echo "${media%/}/${subdir}"
}

deploy_yolo_train_active_job_ids() {
  local yolo_root
  yolo_root="$(deploy_yolo_train_work_root)" || return 0
  [[ -d "${yolo_root}" ]] || return 0

  local job_dir phase_file phase job_id
  for job_dir in "${yolo_root}"/job-*; do
    [[ -d "${job_dir}" ]] || continue
    phase_file="${job_dir}/phase.json"
    [[ -f "${phase_file}" ]] || continue
    phase="$(
      grep -E '"phase"' "${phase_file}" 2>/dev/null |
        head -1 |
        sed -n 's/.*"phase"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
    )"
    case "${phase}" in
      running | finalizing | val_per_class | test_val | loading_model | preparing_dataset | dataset_ready | restarting)
        job_id="${job_dir##*/job-}"
        if [[ -n "${job_id}" ]]; then
          echo "${job_id}"
        fi
        ;;
    esac
  done
}

# 旧行为：检测到训练则中止 restart（LABELU_DEPLOY_SKIP_YOLO_TRAIN_PAUSE=1 时使用）
deploy_guard_yolo_train_before_restart() {
  if [[ "${LABELU_DEPLOY_FORCE_RESTART:-}" == "1" ]]; then
    return 0
  fi
  local ids
  ids="$(deploy_yolo_train_active_job_ids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  if [[ -z "${ids}" ]]; then
    return 0
  fi
  echo "[deploy] 检测到 YOLO 训练尚未结束（作业 ID: ${ids}）。" >&2
  echo "[deploy] 已启用自动暂停发版（默认）。若仍要中止 deploy 请设 LABELU_DEPLOY_SKIP_YOLO_TRAIN_PAUSE=1。" >&2
  echo "[deploy] 若必须不暂停直接重启: LABELU_DEPLOY_FORCE_RESTART=1 sudo bash deploy.sh update" >&2
  return 1
}

deploy_yolo_train_pick_python() {
  if [[ -n "${VENV_DIR:-}" && -x "${VENV_DIR}/bin/python" ]]; then
    echo "${VENV_DIR}/bin/python"
    return 0
  fi
  if [[ -n "${LABELU_PYTHON:-}" && -x "${LABELU_PYTHON}" ]]; then
    echo "${LABELU_PYTHON}"
    return 0
  fi
  command -v python3.11 2>/dev/null || command -v python3 2>/dev/null || return 1
}

# 发版前：自动暂停进行中的训练并等待完成
deploy_yolo_train_prepare_for_restart() {
  if [[ "${LABELU_DEPLOY_FORCE_RESTART:-}" == "1" ]]; then
    echo "[deploy] LABELU_DEPLOY_FORCE_RESTART=1：跳过部署前自动暂停（依赖自动续训）" >&2
    return 0
  fi
  if [[ "${LABELU_DEPLOY_SKIP_YOLO_TRAIN_PAUSE:-}" == "1" ]]; then
    deploy_guard_yolo_train_before_restart
    return $?
  fi

  local py repo timeout
  py="$(deploy_yolo_train_pick_python)" || {
    echo "[deploy] 警告: 未找到 Python，无法自动暂停 YOLO 训练" >&2
    deploy_guard_yolo_train_before_restart
    return $?
  }
  repo="${LABELU_PROJECT_ROOT:-${SERVER_PROJECT_DIR:-}}"
  if [[ -z "${repo}" || ! -f "${repo}/pyproject.toml" ]]; then
    repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  fi
  timeout="${LABELU_DEPLOY_YOLO_PAUSE_TIMEOUT:-3600}"

  if [[ -f "${LABELU_ENV_FILE:-${SERVER_RUNTIME_DIR:-}/.env}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${LABELU_ENV_FILE:-${SERVER_RUNTIME_DIR:-}/.env}" 2>/dev/null || true
    set +a
  fi
  export MEDIA_ROOT="${MEDIA_ROOT:-${SERVER_RUNTIME_DIR:-}}"
  export PYTHONPATH="${repo}${PYTHONPATH:+:${PYTHONPATH}}"

  echo "[deploy] YOLO 训练：发版前自动暂停并等待（timeout=${timeout}s）…" >&2
  if ! "${py}" -m labelu.scripts.yolo_train_deploy pause-and-wait --timeout "${timeout}"; then
    echo "[deploy] 错误: YOLO 训练自动暂停失败，已中止 restart" >&2
    return 1
  fi
  return 0
}

# 发版后：恢复部署前暂停的训练
deploy_yolo_train_resume_after_restart() {
  if [[ "${LABELU_DEPLOY_FORCE_RESTART:-}" == "1" ]]; then
    return 0
  fi
  if [[ "${LABELU_DEPLOY_SKIP_YOLO_TRAIN_PAUSE:-}" == "1" ]]; then
    return 0
  fi

  local py repo yolo_root queue_file
  py="$(deploy_yolo_train_pick_python)" || return 0
  repo="${LABELU_PROJECT_ROOT:-${SERVER_PROJECT_DIR:-}}"
  if [[ -z "${repo}" || ! -f "${repo}/pyproject.toml" ]]; then
    repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  fi
  yolo_root="$(deploy_yolo_train_work_root)" || return 0
  queue_file="${yolo_root}/.deploy_resume_queue.json"
  [[ -f "${queue_file}" ]] || return 0

  if [[ -f "${LABELU_ENV_FILE:-${SERVER_RUNTIME_DIR:-}/.env}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${LABELU_ENV_FILE:-${SERVER_RUNTIME_DIR:-}/.env}" 2>/dev/null || true
    set +a
  fi
  export MEDIA_ROOT="${MEDIA_ROOT:-${SERVER_RUNTIME_DIR:-}}"
  export PYTHONPATH="${repo}${PYTHONPATH:+:${PYTHONPATH}}"

  echo "[deploy] YOLO 训练：服务就绪后自动继续训练（队列 ${queue_file}）…" >&2
  local resume_output resume_rc
  set +e
  resume_output="$("${py}" -m labelu.scripts.yolo_train_deploy resume 2>&1)"
  resume_rc=$?
  set -e
  if [[ -n "${resume_output}" ]]; then
    printf '%s\n' "${resume_output}" >&2
  fi
  if [[ "${resume_rc}" -ne 0 ]]; then
    echo "[deploy] 警告: YOLO 自动续训未完全成功，可手工在训练页点「继续运行」或重跑 resume" >&2
    return 0
  fi
  return 0
}
