#!/usr/bin/env bash
# =============================================================================
# LabelU 服务控制（Ubuntu 服务器）
#
#   sudo ./deploy.sh start    # 首次会完成安装并启动；之后仅启动
#   sudo ./deploy.sh stop
#   sudo ./deploy.sh restart  # 同步 .env / systemd + 数据库迁移 + 重启（保留数据）
#   sudo ./deploy.sh update   # pull + pip + 迁移 + 前端构建 + 重启（日常发版，全自动）
#
# 目录与 MEDIA_HOST 由脚本自动推断（也可全部用环境变量覆盖），默认生产布局在「部署根」下：
#   <BASE>/labeIuMedia          → venv、.env、MEDIA_ROOT（上传、YOLO 工作目录）
#   <BASE>/labelu_appdata      → BASE_DATA_DIR（SQLite、JSON 等）
#   <BASE>/labeIuproject/labeIU → 代码根（须含 pyproject.toml；deploy.sh 通常与此目录同级）
#   <BASE>/tpu-mlir            → TPU_MLIR_ROOT
#   <BASE>/tpu_tmp             → systemd TMPDIR
#
# 部署根 <BASE> 解析顺序：
#   1) 显式 LABELU_DEPLOY_BASE=/path
#   2) 若 LABELU_PROJECT_ROOT 或本脚本所在目录为 .../labeIuproject/labeIU 且含 pyproject.toml
#      → BASE 为 labeIuproject 的父目录
#   3) 否则 LABELU_DEPLOY_BASE_DEFAULT（默认 /data/labeIU）
#
# 子目录名可改（一般不用）：
#   LABELU_DEPLOY_RUNTIME_NAME   默认 labeIuMedia
#   LABELU_DEPLOY_APPDATA_NAME   默认 labelu_appdata
# 或直接给绝对路径覆盖合成结果：
#   LABELU_DEPLOY_RUNTIME_DIR / LABELU_DEPLOY_APPDATA_DIR / LABELU_DEPLOY_PROJECT_DIR
#   LABELU_DEPLOY_TPU_MLIR_ROOT / LABELU_DEPLOY_TPU_TMP
#
# 对外访问地址（写入 .env 的 MEDIA_HOST，须与浏览器访问的「协议+主机+端口」一致）：
#   云主机 hostname -I 常为内网 10.x，自动推断会错；请在运行目录 .env 中任选其一（推荐前两种）：
#   LABELU_MEDIA_HOST=http://113.141.72.175:10000
#   LABELU_DEPLOY_PUBLIC_HOST=113.141.72.175
#   或在执行部署前 export LABELU_MEDIA_HOST / LABELU_DEPLOY_PUBLIC_HOST / LABELU_PUBLIC_HOST
#   端口仍由 LABELU_PORT（默认 10000）拼接在 LABELU_DEPLOY_PUBLIC_HOST 模式下使用
#
# 其他可选环境变量：
#   INSTALL_EXTRAS= / LABELU_BOOTSTRAP_TPU_MLIR= / LABELU_PROJECT_ROOT=
#   LABELU_DEPLOY_AI_*（见 append_labelu_production_env_defaults）
#   LABELU_SKIP_MIGRATE=1         跳过 alembic upgrade head（restart/update 时）
#   LABELU_DISABLE_FRONTEND_ONLY_FAST_DEPLOY=1  关闭纯主站前端变更快速发版路径
#
# MySQL：若在 ${ENV_FILE} 中配置了 DATABASE_URL 且连接串含 mysql（如 mysql+pymysql://…），
#   部署脚本会自动 apt 安装 libmysqlclient 头文件，并在 venv 中 pip 安装 pymysql / mysqlclient。
#   reset-clean 会按备份 .env 中的 DATABASE_URL 自动 DROP/CREATE 库；跳过：LABELU_RESET_SKIP_MYSQL=1
#   跳过系统 apt：DEPLOY_SKIP_MYSQL_APT=1
#
# 环境文件（个人仓库，含密钥）：
#   config/env/.env.server → 安装/重启时写入 ${SERVER_RUNTIME_DIR}/.env（LABELU_DEPLOY_USE_REPO_ENV=0 可关闭）
#   config/env/.env.local  → 本机开发；Windows: scripts/sync_local_env.ps1
#
# 日志详细程度：
#   DEPLOY_VERBOSE=1   打印更多阶段说明，且 apt / pip 不静默（输出较多，适合排障）
#
# 数据保留策略（重要）：
#   restart / start（服务已安装时）→ 只同步 .env 缺省项、systemd、重启进程；不删数据库、上传、用户等数据
#   reset-clean                      → 清空本机业务数据与 venv 后重新完整安装（执行即表示确认）
#   代码仓库 labeIuproject/labeIU 与裸库不受 reset-clean 影响
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SYSTEMD_UNIT="labelu.service"
readonly SCHEDULER_SYSTEMD_UNIT="labelu-training-scheduler.service"
readonly WORKER_SYSTEMD_UNIT="labelu-training-worker.service"
readonly BM1688_WORKER_SYSTEMD_UNIT="labelu-bm1688-worker.service"
readonly EDGE_VALIDATION_WORKER_SYSTEMD_UNIT="labelu-edge-validation-worker.service"
readonly EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT="labelu-experiment-dataset-worker.service"
readonly NGINX_LABELU_CONF="/etc/nginx/conf.d/labelu.conf"
readonly DEADSNAKES_PPA="ppa:deadsnakes/ppa"

INSTALL_EXTRAS="${INSTALL_EXTRAS:-yolo}"
DEPLOY_VERBOSE="${DEPLOY_VERBOSE:-0}"
# 可选步骤失败时累积说明，安装结束统一打印
declare -a DEPLOY_OPTIONAL_WARNINGS=()

guess_public_ip() {
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if [[ -n "${ip}" ]]; then
    echo "${ip}"
    return
  fi
  echo "127.0.0.1"
}

# 从运行目录 .env 读取仅用于 deploy 的公网提示（应用内可忽略未知键）
load_deploy_media_hints_from_envfile() {
  local f="${SERVER_RUNTIME_DIR}/.env"
  [[ -f "${f}" ]] || return 0
  local raw val
  if [[ -z "${LABELU_MEDIA_HOST:-}" ]]; then
    raw="$( { grep -E '^[[:space:]]*LABELU_MEDIA_HOST=' "${f}" 2>/dev/null || true; } | head -1 )"
    if [[ -n "${raw}" ]]; then
      val="${raw#*=}"
      val="${val//$'\r'/}"
      val="$(printf '%s' "${val}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^"//;s/"$//;s/^'"'"'//;s/'"'"'$//')"
      [[ -n "${val}" ]] && export LABELU_MEDIA_HOST="${val}"
    fi
  fi
  if [[ -z "${LABELU_DEPLOY_PUBLIC_HOST:-}" ]]; then
    raw="$( { grep -E '^[[:space:]]*LABELU_DEPLOY_PUBLIC_HOST=' "${f}" 2>/dev/null || true; } | head -1 )"
    if [[ -n "${raw}" ]]; then
      val="${raw#*=}"
      val="${val//$'\r'/}"
      val="$(printf '%s' "${val}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^"//;s/"$//;s/^'"'"'//;s/'"'"'$//')"
      [[ -n "${val}" ]] && export LABELU_DEPLOY_PUBLIC_HOST="${val}"
    fi
  fi
}

# 推断部署根目录 <BASE>（见文件头说明）
infer_labelu_deploy_base() {
  if [[ -n "${LABELU_DEPLOY_BASE:-}" ]]; then
    ( cd "${LABELU_DEPLOY_BASE}" && pwd )
    return
  fi
  local pr p
  if [[ -n "${LABELU_PROJECT_ROOT:-}" ]] && [[ -f "${LABELU_PROJECT_ROOT}/pyproject.toml" ]]; then
    pr="$(cd "${LABELU_PROJECT_ROOT}" && pwd)"
    p="$(dirname "${pr}")"
    if [[ "$(basename "${p}")" == "labeIuproject" ]]; then
      ( cd "${p}/.." && pwd )
      return
    fi
  fi
  if [[ -f "${SCRIPT_DIR}/pyproject.toml" ]]; then
    p="$(cd "${SCRIPT_DIR}/.." && pwd)"
    if [[ "$(basename "${p}")" == "labeIuproject" ]]; then
      ( cd "${p}/.." && pwd )
      return
    fi
  fi
  echo "${LABELU_DEPLOY_BASE_DEFAULT:-/data/labeIU}"
}

init_labelu_deploy_paths() {
  local base runtime_name appdata_name
  base="$(infer_labelu_deploy_base)"
  LABELU_DEPLOY_RESOLVED_BASE="${base}"
  runtime_name="${LABELU_DEPLOY_RUNTIME_NAME:-labeIuMedia}"
  appdata_name="${LABELU_DEPLOY_APPDATA_NAME:-labelu_appdata}"

  SERVER_RUNTIME_DIR="${LABELU_DEPLOY_RUNTIME_DIR:-${base}/${runtime_name}}"
  SERVER_APPDATA_DIR="${LABELU_DEPLOY_APPDATA_DIR:-${base}/${appdata_name}}"
  SERVER_TPU_MLIR_ROOT="${LABELU_DEPLOY_TPU_MLIR_ROOT:-${base}/tpu-mlir}"
  SERVER_TPU_TMP="${LABELU_DEPLOY_TPU_TMP:-${base}/tpu_tmp}"
  SERVER_PROJECT_DIR="${LABELU_DEPLOY_PROJECT_DIR:-${base}/labeIuproject/labeIU}"
  LABELU_DEPLOY_OFFLINE_DIR="${LABELU_DEPLOY_OFFLINE_DIR:-${base}/offline}"

  LABELU_PORT="${LABELU_PORT:-10000}"
  LABELU_ENABLE_NGINX_FRONTEND="${LABELU_ENABLE_NGINX_FRONTEND:-1}"
  LABELU_BACKEND_PORT="${LABELU_BACKEND_PORT:-10001}"
  # Auto-training is consumed by one dedicated scheduler process. The API
  # watchdog is disabled in production when this unit is enabled.
  LABELU_ENABLE_TRAINING_SCHEDULER="${LABELU_ENABLE_TRAINING_SCHEDULER:-1}"
  # The worker is opt-in until a real command producer and isolated runtime
  # are provisioned. Enabling it requires an explicit image allowlist.
  LABELU_ENABLE_TRAINING_WORKER="${LABELU_ENABLE_TRAINING_WORKER:-}"
  LABELU_TRAINING_WORKER_ALLOWED_IMAGES="${LABELU_TRAINING_WORKER_ALLOWED_IMAGES:-}"
  LABELU_ENABLE_BM1688_WORKER="${LABELU_ENABLE_BM1688_WORKER:-}"
  LABELU_ENABLE_EDGE_VALIDATION_WORKER="${LABELU_ENABLE_EDGE_VALIDATION_WORKER:-}"
  LABELU_ENABLE_EXPERIMENT_DATASET_WORKER="${LABELU_ENABLE_EXPERIMENT_DATASET_WORKER:-}"
  load_deploy_media_hints_from_envfile
  load_deploy_training_worker_hints_from_envfile
  LABELU_ENABLE_TRAINING_WORKER="${LABELU_ENABLE_TRAINING_WORKER:-0}"
  LABELU_TRAINING_WORKER_ALLOWED_IMAGES="${LABELU_TRAINING_WORKER_ALLOWED_IMAGES:-}"
  LABELU_ENABLE_BM1688_WORKER="${LABELU_ENABLE_BM1688_WORKER:-0}"
  LABELU_ENABLE_EDGE_VALIDATION_WORKER="${LABELU_ENABLE_EDGE_VALIDATION_WORKER:-0}"
  LABELU_ENABLE_EXPERIMENT_DATASET_WORKER="${LABELU_ENABLE_EXPERIMENT_DATASET_WORKER:-0}"
  if [[ -n "${LABELU_MEDIA_HOST:-}" ]]; then
    MEDIA_HOST="${LABELU_MEDIA_HOST}"
  elif [[ -n "${LABELU_DEPLOY_PUBLIC_HOST:-}" ]]; then
    MEDIA_HOST="http://${LABELU_DEPLOY_PUBLIC_HOST}:${LABELU_PORT}"
  else
    MEDIA_HOST="http://${LABELU_PUBLIC_HOST:-$(guess_public_ip)}:${LABELU_PORT}"
  fi

  VENV_DIR="${SERVER_RUNTIME_DIR}/venv"
  ENV_FILE="${SERVER_RUNTIME_DIR}/.env"
}

# Worker settings may live in the runtime .env. Keep parsing strict and only
# import deployment control keys; secrets and arbitrary application settings
# are never sourced by this shell script.
load_deploy_training_worker_hints_from_envfile() {
  local f="${SERVER_RUNTIME_DIR}/.env"
  [[ -f "${f}" ]] || return 0
  local raw val key
  for key in LABELU_ENABLE_TRAINING_WORKER LABELU_TRAINING_WORKER_ALLOWED_IMAGES \
    LABELU_TRAINING_WORKER_INTERVAL LABELU_TRAINING_WORKER_LEASE_SECONDS \
    LABELU_TRAINING_WORKER_BATCH_LIMIT LABELU_TRAINING_WORKER_MAX_ATTEMPTS \
    LABELU_ENABLE_BM1688_WORKER LABELU_BM1688_WORKER_INTERVAL \
    LABELU_BM1688_WORKER_LEASE_SECONDS LABELU_BM1688_WORKER_BATCH_LIMIT \
    LABELU_BM1688_WORKER_MEMORY_MAX LABELU_BM1688_WORKER_CPU_QUOTA \
    LABELU_BM1688_WORKER_TASKS_MAX LABELU_ENABLE_EDGE_VALIDATION_WORKER \
    LABELU_EDGE_VALIDATION_WORKER_INTERVAL LABELU_EDGE_VALIDATION_WORKER_BATCH_LIMIT \
    LABELU_ENABLE_EXPERIMENT_DATASET_WORKER EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS \
    EXPERIMENT_DATASET_WORKER_LEASE_SECONDS EXPERIMENT_DATASET_WORKER_BATCH_SIZE; do
    if [[ -n "${!key:-}" ]]; then
      continue
    fi
    raw="$( { grep -E "^[[:space:]]*${key}=" "${f}" 2>/dev/null || true; } | head -1 )"
    [[ -n "${raw}" ]] || continue
    val="${raw#*=}"
    val="${val//$'\r'/}"
    val="$(printf '%s' "${val}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^"//;s/"$//;s/^'\''//;s/'\''$//')"
    [[ -n "${val}" ]] && export "${key}=${val}"
  done
}

init_labelu_deploy_paths

log() { echo "[labelu-deploy] $*"; }

# 分阶段醒目标记（默认开启；与 DEPLOY_VERBOSE 无关）
log_step() { log ">>> $*"; }

# shellcheck source=scripts/ops/deploy_change_scope.sh
source "${SCRIPT_DIR}/scripts/ops/deploy_change_scope.sh"

# curl -sf 在 4xx/5xx 时仍会输出 http_code 且 exit 非零，会与 || echo 000 拼成 502000
deploy_curl_http_code() {
  local url="$1"
  local timeout="${2:-15}"
  local code
  code="$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout "${timeout}" "${url}" 2>/dev/null || true)"
  code="${code//$'\r'/}"
  code="${code//$'\n'/}"
  if [[ "${code}" =~ ^[0-9]{3}$ ]]; then
    printf '%s' "${code}"
  else
    printf '000'
  fi
}

DEPLOY_LAST_HTTP_CODE="000"
DEPLOY_LAST_HTTP_CONTENT_TYPE=""
DEPLOY_LAST_HTTP_BODY_PREFIX=""

deploy_probe_url_expect_200() {
  local url="$1"
  local max_attempts="${2:-15}"
  local i code
  for ((i = 1; i <= max_attempts; i++)); do
    code="$(deploy_curl_http_code "${url}" 8)"
    DEPLOY_LAST_HTTP_CODE="${code}"
    if [[ "${code}" == "200" ]]; then
      return 0
    fi
    sleep 2
  done
  return 1
}

deploy_probe_json_url_expect_200() {
  local url="$1"
  local max_attempts="${2:-15}"
  local i code content_type body_prefix header_file body_file
  header_file="$(mktemp)"
  body_file="$(mktemp)"
  for ((i = 1; i <= max_attempts; i++)); do
    code="$(curl -sS -D "${header_file}" -o "${body_file}" -w "%{http_code}" --connect-timeout 8 --max-time 20 "${url}" 2>/dev/null || true)"
    code="${code//$'\r'/}"
    code="${code//$'\n'/}"
    content_type="$(awk 'BEGIN {IGNORECASE=1} /^Content-Type:/ {sub(/^[^:]*:[[:space:]]*/, ""); sub(/[[:space:]]*;.*/, ""); print; exit}' "${header_file}" | tr -d '\r')"
    body_prefix="$(head -c 1 "${body_file}" 2>/dev/null || true)"
    DEPLOY_LAST_HTTP_CODE="${code:-000}"
    DEPLOY_LAST_HTTP_CONTENT_TYPE="${content_type:-unknown}"
    DEPLOY_LAST_HTTP_BODY_PREFIX="${body_prefix:-empty}"
    if [[ "${code}" == "200" && "${content_type,,}" == *"application/json"* && ( "${body_prefix}" == "{" || "${body_prefix}" == "[" ) ]]; then
      rm -f "${header_file}" "${body_file}"
      return 0
    fi
    sleep 2
  done
  rm -f "${header_file}" "${body_file}"
  return 1
}

deploy_wait_backend_ready() {
  local port="${1:-${LABELU_BACKEND_PORT}}"
  local max_attempts="${2:-25}"
  log_step "等待 LabelU 后端就绪 (127.0.0.1:${port}/docs)…"
  if deploy_probe_url_expect_200 "http://127.0.0.1:${port}/docs" "${max_attempts}"; then
    log "后端探活 OK: HTTP 200"
    return 0
  fi
  deploy_optional_warn "后端 127.0.0.1:${port} 未在约 $((max_attempts * 2))s 内返回 200（最近 HTTP ${DEPLOY_LAST_HTTP_CODE}）"
  return 1
}

# install/restart/update 异常退出时仍打印检查清单
_DEPLOY_TRAP_PROJECT_ROOT=""
DEPLOY_FINALIZE_DONE=0

deploy_mark_finalize_trap() {
  _DEPLOY_TRAP_PROJECT_ROOT="${1:-}"
  DEPLOY_FINALIZE_DONE=0
  trap 'deploy_exit_trap' EXIT
}

deploy_exit_trap() {
  local rc=$?
  if [[ "${DEPLOY_FINALIZE_DONE}" == "1" ]]; then
    trap - EXIT
    exit "${rc}"
  fi
  if [[ -n "${_DEPLOY_TRAP_PROJECT_ROOT}" ]]; then
    log ""
    log_step "收尾：补打部署检查清单（脚本提前退出 exit=${rc}）…"
    set +e
    deploy_finalize "${_DEPLOY_TRAP_PROJECT_ROOT}" || true
    set -e
  fi
  trap - EXIT
  exit "${rc}"
}

usage() {
  cat >&2 <<'USAGE'
用法: sudo ./deploy.sh <子命令>

  start         首次完整安装并启动；若已安装则仅启动服务（保留数据）
  stop          停止 labelu 服务
  restart       同步 .env / systemd + 数据库迁移后重启（保留业务数据）
  preserve      与 restart 相同（显式表达「保留数据」）
  update        fetch + reset + pip + 迁移 + 前端构建(pnpm) + 重启 + 部署检查清单
  frontend-build  仅构建前端 statics（update/install 已自动调用，一般不必单独执行）
  nginx-disable  禁用 LabelU Nginx 前端分发并切回旧模式（LabelU 直接监听 LABELU_PORT）
  reset-clean   停止服务 → 删除本机业务数据与 venv → 重新完整安装（回到近似初始状态）
                执行前会备份 .env；若 DATABASE_URL 为 MySQL 则自动 DROP/CREATE 库

环境变量（经 sudo 时请写在 sudo 后，见下方示例）:
  DEPLOY_VERBOSE=1              详细 apt/pip 输出
  LABELU_SKIP_MIGRATE=1         跳过 alembic（restart/update）
  INSTALL_EXTRAS=yolo,image-pipeline   pip 可选依赖，默认 yolo
  LABELU_RESET_SKIP_MYSQL=1       reset-clean 时不自动 DROP/CREATE MySQL 库
  LABELU_SKIP_MYSQL_ENSURE_USER=1 跳过 MySQL 用户/密码同步（连接已正常时 restart 可设此项）
  LABELU_DEPLOY_RESTART=1          由 deploy.sh restart 自动设置；TPU 已就绪时不重装 tpu-mlir pip
  LABELU_DEPLOY_USE_REPO_ENV=0    不用 config/env/.env.server，改用手写/脚本生成 .env
  LABELU_GIT_RESET_ON_PULL=0      update 时用普通 git pull（默认 1=reset --hard）
  LABELU_SKIP_FRONTEND_BUILD=1    update 时跳过 pnpm/labelu frontend-build（仅改后端时可用）
  LABELU_DISABLE_FRONTEND_ONLY_FAST_DEPLOY=1  关闭纯 frontend/labelU-Kit 变更快速发版路径
  LABELU_DEPLOY_SKIP_YOLO_TRAIN_PAUSE=1  有训练时中止 restart（不自动暂停，旧行为）
  LABELU_DEPLOY_FORCE_RESTART=1   不暂停直接 restart（依赖 YOLO 自动续训）
  LABELU_DEPLOY_YOLO_PAUSE_TIMEOUT=3600  发版前等待训练暂停的最长秒数
  LABELU_ENABLE_NGINX_FRONTEND=1   用 Nginx 监听 LABELU_PORT 并分发前端静态资源（默认 1；设 0 回退旧模式）
  LABELU_BACKEND_PORT=10001        Nginx 模式下 LabelU 后端内部监听端口
  LABELU_ENABLE_TRAINING_SCHEDULER=1  独立启动自动训练 scheduler（默认 1；设 0 使用 API watchdog）
  LABELU_TRAINING_SCHEDULER_INTERVAL=5  scheduler 扫描间隔秒数
  LABELU_ENABLE_TRAINING_WORKER=0  独立训练 Worker（默认关闭，须先配置真实 producer/runtime）
  LABELU_TRAINING_WORKER_ALLOWED_IMAGES=repo/image:tag[,repo/other:tag]  Worker 精确镜像白名单（启用必填）
  LABELU_TRAINING_WORKER_INTERVAL=5  Worker Outbox 扫描间隔秒数
  LABELU_TRAINING_WORKER_LEASE_SECONDS=3600  Worker 事件租约秒数
  LABELU_TRAINING_WORKER_BATCH_LIMIT=20  Worker 每轮最大事件数
  LABELU_TRAINING_WORKER_MAX_ATTEMPTS=3  Worker 单命令最大重试次数
  LABELU_ENABLE_BM1688_WORKER=0  BM1688 独立 Worker（默认关闭，正式迁移后显式开启）
  LABELU_BM1688_WORKER_INTERVAL=2  BM1688 Worker 扫描间隔秒数
  LABELU_BM1688_WORKER_LEASE_SECONDS=300  BM1688 派发交接租约秒数
  LABELU_BM1688_WORKER_BATCH_LIMIT=1  BM1688 Worker 每轮最大领取数量
  LABELU_BM1688_WORKER_MEMORY_MAX=16G  BM1688 Worker systemd 内存上限
  LABELU_BM1688_WORKER_CPU_QUOTA=400%  BM1688 Worker systemd CPU 上限
  LABELU_BM1688_WORKER_TASKS_MAX=1024  BM1688 Worker systemd 进程/线程上限
  LABELU_ENABLE_EDGE_VALIDATION_WORKER=0  独立边端验证 Worker（默认关闭，需真实设备和迁移证据）
  LABELU_EDGE_VALIDATION_WORKER_INTERVAL=5  边端验证 Worker 扫描间隔秒数
  LABELU_EDGE_VALIDATION_WORKER_BATCH_LIMIT=1  边端验证 Worker 每轮最大领取数量
  LABELU_ENABLE_EXPERIMENT_DATASET_WORKER=0  第三方 S3 回流 Worker（默认关闭，需迁移/S3/MySQL证据）
  EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS=5  S3 回流 Worker 扫描间隔秒数
  EXPERIMENT_DATASET_WORKER_LEASE_SECONDS=300  S3 回流 Worker 租约秒数
  EXPERIMENT_DATASET_WORKER_BATCH_SIZE=1  S3 回流 Worker 每轮最大领取数量
  LABELU_DEPLOY_SKIP_DISK_CLEAN=1  跳过安装前自动磁盘清理
  LABELU_DEPLOY_DISK_CLEAN_MAX_PASSES=6  自动清理最多轮数
  LABELU_DEPLOY_DOCKER_PRUNE=1    允许 docker system prune -f（本机有 Docker 时）
  GIT_CLONE_RETRIES=5             GitHub 克隆失败时的重试次数（TPU-MLIR / sophon-demo）

示例:
  sudo env INSTALL_EXTRAS=yolo,image-pipeline bash deploy.sh reset-clean
USAGE
  exit 1
}

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "请使用 root 执行，例如: sudo $0 $*" >&2
    exit 1
  fi
}

detect_ubuntu_jammy() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    . /etc/os-release
    if [[ "${ID:-}" != "ubuntu" ]] || [[ "${VERSION_ID:-}" != "22.04" ]]; then
      log "警告: 当前系统为 ${ID:-unknown} ${VERSION_ID:-unknown}，脚本按 Ubuntu 22.04 编写。"
    fi
  fi
}

# apt / pip / venv 多在根分区；空间不足时 pip 会报 No space left on device。
deploy_rootfs_avail_mb() {
  local avail_kb
  avail_kb="$(df -Pk / 2>/dev/null | awk 'NR==2 {print $4}')"
  if [[ -z "${avail_kb}" ]] || ! [[ "${avail_kb}" =~ ^[0-9]+$ ]]; then
    echo 0
    return 0
  fi
  echo $((avail_kb / 1024))
}

require_rootfs_free_mb() {
  local min_mb="${1:-512}"
  local avail_mb
  avail_mb="$(deploy_rootfs_avail_mb)"
  if [[ "${avail_mb}" == "0" ]]; then
    log "警告: 无法检测根分区 (/) 可用空间，跳过预检。"
    return 0
  fi
  if (( avail_mb < min_mb )); then
    log "错误: 根分区 (/) 可用约 ${avail_mb} MiB，低于需要的 ${min_mb} MiB。"
    log "已尝试自动清理仍不足时请扩容，或执行: df -h；du -xh / --max-depth=1 2>/dev/null | sort -h | tail"
    log "可选: LABELU_DEPLOY_DOCKER_PRUNE=1 允许清理未使用 Docker 镜像（若本机有 Docker）"
    exit 1
  fi
  log "根分区 (/) 可用约 ${avail_mb} MiB"
}

# 单轮「安全」清理：仅缓存、临时文件、旧备份、不完整 venv，不删 upload/数据库业务目录
deploy_free_root_space_pass() {
  local n

  log_step "磁盘：apt clean / autoclean"
  export DEBIAN_FRONTEND=noninteractive
  apt-get clean -y 2>/dev/null || apt-get clean 2>/dev/null || true
  apt-get autoclean -y 2>/dev/null || apt-get autoclean 2>/dev/null || true
  rm -rf /var/lib/apt/lists/* 2>/dev/null || true

  log_step "磁盘：pip 缓存（/root、/tmp、运行目录 .pip_cache）"
  rm -rf /root/.cache/pip /root/.cache/pypoetry 2>/dev/null || true
  rm -rf /tmp/pip-* /tmp/pip_ephem_wheel_cache-* 2>/dev/null || true
  if [[ -d "${SERVER_RUNTIME_DIR}/.pip_cache" ]]; then
    find "${SERVER_RUNTIME_DIR}/.pip_cache" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
  fi

  if command -v journalctl >/dev/null 2>&1; then
    log_step "磁盘：systemd 日志 vacuum（保留约 200MB）"
    journalctl --vacuum-size=200M 2>/dev/null || journalctl --vacuum-time=3d 2>/dev/null || true
  fi

  log_step "磁盘：/tmp、/var/tmp 中超过 3 天的文件"
  find /tmp -mindepth 1 -maxdepth 2 -type f -mtime +3 -delete 2>/dev/null || true
  find /var/tmp -mindepth 1 -maxdepth 2 -type f -mtime +3 -delete 2>/dev/null || true

  if [[ -d "${SERVER_TPU_TMP}" ]]; then
    log_step "磁盘：清空 TPU 临时目录 ${SERVER_TPU_TMP}"
    find "${SERVER_TPU_TMP}" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
  fi

  if [[ -d "${SERVER_RUNTIME_DIR}" ]]; then
    log_step "磁盘：清理运行目录缓存与旧 .env 备份（保留最近 2 份）"
    find "${SERVER_RUNTIME_DIR}" -maxdepth 3 -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    n="$(find "${SERVER_RUNTIME_DIR}" -maxdepth 1 -name '.env.bak.*' 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "${n:-0}" -gt 2 ]]; then
      find "${SERVER_RUNTIME_DIR}" -maxdepth 1 -name '.env.bak.*' -printf '%T@ %p\n' 2>/dev/null |
        sort -rn | tail -n +3 | cut -d' ' -f2- | xargs -r rm -f 2>/dev/null || true
    fi
    if [[ -d "${VENV_DIR}" ]] && [[ ! -x "${VENV_DIR}/bin/labelu" ]]; then
      log_step "磁盘：删除不完整 venv ${VENV_DIR}"
      rm -rf "${VENV_DIR}"
    fi
  fi

  if [[ -d "${SERVER_PROJECT_DIR}" ]]; then
    find "${SERVER_PROJECT_DIR}" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    find "${SERVER_PROJECT_DIR}" -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
  fi

  if [[ "${LABELU_DEPLOY_DOCKER_PRUNE:-}" == "1" ]] && command -v docker >/dev/null 2>&1; then
    log_step "磁盘：docker system prune -f（已设置 LABELU_DEPLOY_DOCKER_PRUNE=1）"
    docker system prune -f 2>/dev/null || true
  fi
}

# 多轮安全清理直到达到 min_mb 或达到轮数上限；第二参数 soft 时不足仅警告不 exit
deploy_ensure_rootfs_space() {
  local min_mb="${1:-2048}"
  local mode="${2:-}"
  if [[ "${LABELU_DEPLOY_SKIP_DISK_CLEAN:-}" == "1" ]]; then
    require_rootfs_free_mb "${min_mb}"
    return 0
  fi

  local max_passes="${LABELU_DEPLOY_DISK_CLEAN_MAX_PASSES:-6}"
  local pass avail

  avail="$(deploy_rootfs_avail_mb)"
  if [[ "${avail}" == "0" ]]; then
    log "警告: 无法检测根分区空间，跳过自动清理预检"
    return 0
  fi
  if (( avail >= min_mb )); then
    log "根分区 (/) 可用约 ${avail} MiB（需要 ≥ ${min_mb} MiB）"
    return 0
  fi

  log "根分区 (/) 可用约 ${avail} MiB，低于 ${min_mb} MiB，开始自动安全清理（最多 ${max_passes} 轮）…"
  for ((pass = 1; pass <= max_passes; pass++)); do
    deploy_free_root_space_pass
    avail="$(deploy_rootfs_avail_mb)"
    log "第 ${pass}/${max_passes} 轮清理后，根分区可用约 ${avail} MiB"
    if (( avail >= min_mb )); then
      log_step "磁盘：已满足安装所需空间"
      return 0
    fi
  done

  avail="$(deploy_rootfs_avail_mb)"
  if (( avail < min_mb )); then
    if [[ "${mode}" == "soft" ]]; then
      log "警告: 自动清理后根分区仍约 ${avail} MiB（建议 ${min_mb} MiB），继续执行后续步骤"
      return 1
    fi
    require_rootfs_free_mb "${min_mb}"
  fi
  return 0
}

apt_install_base() {
  export DEBIAN_FRONTEND=noninteractive
  log_step "apt：更新软件源索引…"
  local packages=(ca-certificates curl gnupg software-properties-common openssl build-essential git nodejs npm)
  if [[ "${LABELU_ENABLE_NGINX_FRONTEND}" == "1" ]]; then
    packages+=(nginx)
  fi
  if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
    apt-get update -y
    log_step "apt：安装基础包（ca-certificates、curl、git、build-essential 等）…"
    apt-get install -y --no-install-recommends "${packages[@]}"
  else
    apt-get update -y -qq
    log_step "apt：安装基础包（静默模式，可用 DEPLOY_VERBOSE=1 查看详情）…"
    apt-get install -y -qq --no-install-recommends "${packages[@]}"
  fi
  log_step "apt：基础包安装完成"
}

deploy_node_cmd() {
  if command -v node >/dev/null 2>&1; then
    command -v node
    return 0
  fi
  if command -v nodejs >/dev/null 2>&1; then
    if [[ ! -e /usr/bin/node ]] && [[ -e /usr/bin/nodejs ]]; then
      ln -sf /usr/bin/nodejs /usr/bin/node 2>/dev/null || true
    fi
    command -v node 2>/dev/null || command -v nodejs
    return 0
  fi
  return 1
}

deploy_node_major() {
  local node_bin
  if ! node_bin="$(deploy_node_cmd 2>/dev/null)"; then
    echo "0"
    return 0
  fi
  if [[ -z "${node_bin}" ]]; then
    echo "0"
    return 0
  fi
  "${node_bin}" -e 'process.stdout.write(String(process.versions.node.split(".")[0]))' 2>/dev/null || echo "0"
}

# 与 Node 20 兼容的 pnpm（10+ 需 Node ≥ 22）
deploy_pin_pnpm() {
  local pv pmajor
  if command -v corepack >/dev/null 2>&1; then
    corepack enable 2>/dev/null || true
    corepack prepare pnpm@9.15.0 --activate 2>/dev/null || true
    hash -r 2>/dev/null || true
  fi
  if ! command -v pnpm >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    npm install -g pnpm@9.15.0 2>/dev/null || true
    hash -r 2>/dev/null || true
  fi
  if ! command -v pnpm >/dev/null 2>&1; then
    return 1
  fi
  pv="$(pnpm --version 2>/dev/null | tr -d '\r' | head -n1 || true)"
  pmajor="${pv%%.*}"
  if [[ -n "${pmajor}" ]] && [[ "${pmajor}" =~ ^[0-9]+$ ]] && [[ "${pmajor}" -ge 10 ]]; then
    log "pnpm ${pv} 与 Node 20 不兼容，降级至 9.15.0…"
    npm install -g pnpm@9.15.0 2>/dev/null || true
    corepack prepare pnpm@9.15.0 --activate 2>/dev/null || true
    hash -r 2>/dev/null || true
  fi
  return 0
}

deploy_install_nodesource_20() {
  log_step "Node：安装 Node.js 20 LTS（NodeSource，Vite 需 Node ≥ 18）…"
  export DEBIAN_FRONTEND=noninteractive
  if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
  else
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1
    apt-get install -y -qq nodejs
  fi
  hash -r 2>/dev/null || true
  deploy_node_cmd >/dev/null || true
}

deploy_ensure_node_pnpm() {
  local node_bin major
  node_bin="$(deploy_node_cmd)" || true
  major="$(deploy_node_major)"
  if [[ -z "${node_bin}" ]] || [[ "${major}" -lt 18 ]]; then
    log "当前 Node 不可用或版本过旧（major=${major:-无}），升级至 20 LTS…"
    deploy_install_nodesource_20
    node_bin="$(deploy_node_cmd)" || true
    major="$(deploy_node_major)"
  fi
  if [[ -z "${node_bin}" ]] || [[ "${major}" -lt 18 ]]; then
    log "错误: 无法获得 Node.js ≥ 18（当前 major=${major:-无}）"
    return 1
  fi
  log "Node: $("${node_bin}" --version 2>/dev/null || echo '?')"

  log_step "前端构建：锁定 pnpm 9.15.0（与 Node 20 兼容）…"
  deploy_pin_pnpm || {
    log "错误: pnpm 不可用。可手动: corepack enable && corepack prepare pnpm@9.15.0 --activate"
    return 1
  }
  log "pnpm: $(pnpm --version 2>/dev/null | tr -d '\r' | head -n1 || echo '?')"
  return 0
}

ensure_python311() {
  if command -v python3.11 >/dev/null 2>&1; then
    log_step "Python：已存在 python3.11，跳过 deadsnakes 安装"
    return 0
  fi
  log_step "Python：通过 deadsnakes 安装 3.11（含 venv、dev 头文件）…"
  if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
    add-apt-repository -y "${DEADSNAKES_PPA}"
    apt-get update -y
    apt-get install -y --no-install-recommends python3.11 python3.11-venv python3.11-dev
  else
    add-apt-repository -y "${DEADSNAKES_PPA}"
    apt-get update -y -qq
    apt-get install -y -qq --no-install-recommends python3.11 python3.11-venv python3.11-dev
  fi
  log_step "Python：3.11 安装完成"
}

resolve_project_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if [[ -n "${LABELU_PROJECT_ROOT:-}" ]] && [[ -f "${LABELU_PROJECT_ROOT}/pyproject.toml" ]]; then
    echo "$(cd "${LABELU_PROJECT_ROOT}" && pwd)"
    return 0
  fi
  if [[ -f "${here}/pyproject.toml" ]]; then
    echo "${here}"
    return 0
  fi
  if [[ -f "${SERVER_PROJECT_DIR}/pyproject.toml" ]]; then
    echo "${SERVER_PROJECT_DIR}"
    return 0
  fi
  log "错误: 未找到本仓库根目录（需要 pyproject.toml）。"
  log "请将代码放到 ${SERVER_PROJECT_DIR}，或与 deploy.sh 同目录，或设置 LABELU_PROJECT_ROOT / LABELU_DEPLOY_BASE。"
  exit 1
}

migrate_sqlite_to_appdata_if_needed() {
  mkdir -p "${SERVER_APPDATA_DIR}"
  local old="${SERVER_RUNTIME_DIR}/labelu.sqlite"
  local new="${SERVER_APPDATA_DIR}/labelu.sqlite"
  if [[ -f "${old}" ]] && [[ ! -f "${new}" ]]; then
    log "迁移数据库: ${old} -> ${new}"
    mv "${old}" "${new}"
  fi
}

ensure_env_kv() {
  local file="$1" key="$2" val="$3"
  if [[ ! -f "${file}" ]]; then
    echo "${key}=${val}" >>"${file}"
    return 0
  fi
  if grep -q "^${key}=" "${file}" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${file}"
  else
    echo "${key}=${val}" >>"${file}"
  fi
}

# 仅当 .env 中尚无该键时追加（保留运维已改过的值）
ensure_env_kv_if_missing() {
  local file="$1" key="$2" val="$3"
  [[ -f "${file}" ]] || return 0
  if grep -q "^${key}=" "${file}" 2>/dev/null; then
    return 0
  fi
  echo "${key}=${val}" >>"${file}"
}

# deploy.sh 会用 bash source 读取 .env；对空格、$ 等 shell 特殊字符做一次兼容清洗，
# 避免 secret 在部署检查阶段被 shell 展开成错误值。
deploy_sanitize_env_shell_unsafe_values() {
  local file="${1:-${ENV_FILE}}"
  [[ -f "${file}" ]] || return 0
  local changed
  changed="$(
    python3 - "${file}" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
lines = text.splitlines()
assign_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
unsafe_re = re.compile(r"[\s$`#;&|<>()!]")


def shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


changed = 0
normalized: list[str] = []
for raw in lines:
    match = assign_re.match(raw)
    if not match or raw.lstrip().startswith("#"):
        normalized.append(raw)
        continue
    key, value = match.groups()
    if value.startswith(("'", '"')):
        normalized.append(raw)
        continue
    if unsafe_re.search(value):
        normalized.append(f"{key}={shell_single_quote(value)}")
        changed += 1
        continue
    normalized.append(raw)

if changed:
    trailing_newline = "\n" if text.endswith("\n") else ""
    path.write_text("\n".join(normalized) + trailing_newline, encoding="utf-8")

print(changed)
PY
  )"
  if [[ "${changed:-0}" =~ ^[0-9]+$ ]] && [[ "${changed}" -gt 0 ]]; then
    log_step "配置：已为 .env 中 shell 特殊字符值补安全引号（兼容 bash source .env）"
  fi
}

append_labelu_production_env_defaults() {
  [[ -f "${ENV_FILE}" ]] || return 0
  log_step "配置：合并生产默认项到 ${ENV_FILE}（仅补缺键）"
  ensure_env_kv_if_missing "${ENV_FILE}" "AI_AUTO_LABEL_ENABLED" "${LABELU_DEPLOY_AI_AUTO_LABEL:-true}"
  # 默认 vision_llm_chain；勿自动写入 127.0.0.1:5000，避免误连不存在的 model_server
  ensure_env_kv_if_missing "${ENV_FILE}" "AI_PROVIDER" "${LABELU_DEPLOY_AI_PROVIDER:-vision_llm_chain}"
  if [[ -n "${LABELU_DEPLOY_AI_MODEL_ENDPOINT:-}" ]]; then
    ensure_env_kv_if_missing "${ENV_FILE}" "AI_MODEL_ENDPOINT" "${LABELU_DEPLOY_AI_MODEL_ENDPOINT}"
  fi
  ensure_env_kv_if_missing "${ENV_FILE}" "LA_AUTO_LABEL_ENABLED" "${LABELU_DEPLOY_LA_AUTO_LABEL:-false}"
  ensure_env_kv_if_missing "${ENV_FILE}" "LA_MODEL_ENDPOINT" "${LABELU_DEPLOY_LA_MODEL_ENDPOINT:-http://127.0.0.1:5001/}"
  ensure_env_kv_if_missing "${ENV_FILE}" "LA_MODEL_TIMEOUT_SECONDS" "${LABELU_DEPLOY_LA_MODEL_TIMEOUT_SECONDS:-120}"
  ensure_env_kv_if_missing "${ENV_FILE}" "LA_MODEL_NAME" "${LABELU_DEPLOY_LA_MODEL_NAME:-nvidia/LocateAnything-3B}"
  ensure_env_kv_if_missing "${ENV_FILE}" "LA_MAX_IMAGE_SIDE" "${LABELU_DEPLOY_LA_MAX_IMAGE_SIDE:-1536}"
  ensure_env_kv_if_missing "${ENV_FILE}" "LA_GPU_IDLE_UNLOAD_SECONDS" "${LABELU_DEPLOY_LA_GPU_IDLE_UNLOAD_SECONDS:-300}"
  ensure_env_kv_if_missing "${ENV_FILE}" "YOLO_EVAL_ENABLED" "${LABELU_DEPLOY_YOLO_EVAL:-true}"
  ensure_env_kv_if_missing "${ENV_FILE}" "YOLO_TRAIN_GPU_MIN_FREE_FRACTION" "0.15"
  ensure_env_kv_if_missing "${ENV_FILE}" "YOLO_TRAIN_GPU_MIN_FREE_MB_SMALL" "4096"
  ensure_env_kv_if_missing "${ENV_FILE}" "YOLO_TRAIN_GPU_PREFER_DUAL" "true"
  ensure_env_kv_if_missing "${ENV_FILE}" "LOG_FILE_ENABLED" "true"
  ensure_env_kv_if_missing "${ENV_FILE}" "LOG_LEVEL" "INFO"
  ensure_env_kv_if_missing "${ENV_FILE}" "LOG_RETENTION" '"30 days"'
  ensure_env_kv_if_missing "${ENV_FILE}" "LOG_ERROR_RETENTION" '"90 days"'
  ensure_env_kv_if_missing "${ENV_FILE}" "LOG_ROTATION" "00:00"
  deploy_sanitize_env_shell_unsafe_values "${ENV_FILE}"
  if [[ "${INSTALL_EXTRAS}" == *yolo* ]]; then
    local sophon_det="${LABELU_DEPLOY_RESOLVED_BASE}/sophon-demo/sample/YOLOv8_plus_det"
    ensure_env_kv_if_missing "${ENV_FILE}" "TPU_MLIR_ROOT" "${SERVER_TPU_MLIR_ROOT}"
    ensure_env_kv_if_missing "${ENV_FILE}" "SOPHON_DET_ROOT" "${sophon_det}"
    ensure_env_kv_if_missing "${ENV_FILE}" "YOLO_BM1688_COMPILE_PLATFORMS" "${YOLO_BM1688_COMPILE_PLATFORMS:-bm1688}"
    if [[ -d "${sophon_det}/datasets/coco128" ]]; then
      ensure_env_kv_if_missing "${ENV_FILE}" "YOLO_BM1688_CALIBRATION_DATASET_DIR" \
        "${sophon_det}/datasets/coco128"
    fi
    if [[ -f "${sophon_det}/models/onnx/yolov11s_qtable" ]]; then
      ensure_env_kv_if_missing "${ENV_FILE}" "YOLO_BM1688_QTABLE_PATH" \
        "${sophon_det}/models/onnx/yolov11s_qtable"
    fi
  fi
}

maybe_bootstrap_sophon_demo() {
  local project_root="${1:-}"
  project_root="${project_root:-$(resolve_project_root)}"
  local want="${LABELU_BOOTSTRAP_SOPHON_DEMO:-}"
  if [[ -z "${want}" ]]; then
    if [[ "${INSTALL_EXTRAS}" == *yolo* ]]; then
      want=y
    else
      want=n
    fi
  fi
  case "${want}" in
    n|N|0|false|no)
      log_step "sophon-demo 资源：已跳过（LABELU_BOOTSTRAP_SOPHON_DEMO=n）"
      return 0
      ;;
  esac
  local script="${project_root}/labelu/scripts/bootstrap_sophon_demo_assets.sh"
  if [[ ! -f "${script}" ]]; then
    log "警告: 未找到 ${script}，跳过 sophon-demo 资源下载"
    return 0
  fi
  sed -i 's/\r$//' "${script}" 2>/dev/null || true
  if ! bash -n "${script}"; then
    log "错误: ${script} 语法检查失败（bash -n）"
    exit 1
  fi
  log_step "sophon-demo：下载 coco128 / qtable（供 BM1688 INT8 全自动编译）…"
  if deploy_is_strict; then
    deploy_run_required "sophon-demo 资源" \
      env LABELU_DEPLOY_STRICT=1 LABELU_DEPLOY_RESOLVED_BASE="${LABELU_DEPLOY_RESOLVED_BASE}" \
        LABELU_DEPLOY_OFFLINE_DIR="${LABELU_DEPLOY_OFFLINE_DIR}" \
        MEDIA_ROOT="${SERVER_RUNTIME_DIR}" \
        bash "${script}"
  else
    LABELU_DEPLOY_STRICT=0 LABELU_DEPLOY_RESOLVED_BASE="${LABELU_DEPLOY_RESOLVED_BASE}" \
      LABELU_DEPLOY_OFFLINE_DIR="${LABELU_DEPLOY_OFFLINE_DIR}" \
      MEDIA_ROOT="${SERVER_RUNTIME_DIR}" \
      bash "${script}" || deploy_optional_warn "sophon-demo 资源引导未完全成功"
  fi
}

maybe_bootstrap_bm1688_docker() {
  if [[ "${INSTALL_EXTRAS}" != *yolo* ]]; then
    return 0
  fi
  local project_root="${1:-}"
  project_root="${project_root:-$(resolve_project_root)}"
  local script="${project_root}/scripts/bm1688_docker_bootstrap.sh"
  if [[ ! -f "${script}" ]]; then
    deploy_optional_warn "未找到 ${script}，跳过 BM1688 Docker"
    return 0
  fi
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${LABELU_BM1688_CONTAINER:-labelu_bm1688}"; then
    log_step "BM1688 Docker：容器已运行，跳过引导"
    return 0
  fi
  log_step "BM1688 Docker：安装 Engine / 加载 sophgo 镜像 / 初始化容器（首次可能较慢）…"
  sed -i 's/\r$//' "${script}" 2>/dev/null || true
  if deploy_is_strict; then
    deploy_run_required "BM1688 Docker bootstrap" \
      env LABELU_DEPLOY_RESOLVED_BASE="${LABELU_DEPLOY_RESOLVED_BASE}" \
        LABELU_DEPLOY_OFFLINE_DIR="${LABELU_DEPLOY_OFFLINE_DIR}" \
        bash "${script}" "${LABELU_DEPLOY_RESOLVED_BASE}"
  else
    env LABELU_DEPLOY_RESOLVED_BASE="${LABELU_DEPLOY_RESOLVED_BASE}" \
      LABELU_DEPLOY_OFFLINE_DIR="${LABELU_DEPLOY_OFFLINE_DIR}" \
      bash "${script}" "${LABELU_DEPLOY_RESOLVED_BASE}" \
      || deploy_optional_warn "BM1688 Docker 未完全就绪（可放置 offline tar 或检查网络后重跑 deploy.sh update）"
  fi
}

seed_bm1688_compiler_config() {
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    return 0
  fi
  log_step "BM1688：写入自动编译配置（页面无需手工填 TPU/校准集）…"
  deploy_source_production_env
  if deploy_is_strict; then
    deploy_run_required "BM1688 编译配置种子" \
      "${VENV_DIR}/bin/python" -m labelu.scripts.seed_bm1688_compiler_config
  else
    set +e
    "${VENV_DIR}/bin/python" -m labelu.scripts.seed_bm1688_compiler_config
    local rc=$?
    set -e
    if [[ "${rc}" -ne 0 ]]; then
      deploy_optional_warn "BM1688 配置种子返回 ${rc}（见 labelu doctor）"
    fi
  fi
  append_labelu_production_env_defaults
}

deploy_media_host_hostname() {
  printf '%s' "${1:-}" | sed -E 's|^https?://([^:/]+).*|\1|'
}

deploy_is_private_media_hostname() {
  local host="$1"
  [[ -z "${host}" ]] && return 1
  [[ "${host}" =~ ^10\. ]] || [[ "${host}" =~ ^192\.168\. ]] \
    || [[ "${host}" =~ ^172\.(1[6-9]|2[0-9]|3[0-1])\. ]]
}

deploy_read_media_host_from_file() {
  local f="$1"
  [[ -f "${f}" ]] || return 1
  { grep -E '^[[:space:]]*MEDIA_HOST=' "${f}" 2>/dev/null || true; } |
    head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^[[:space:]]*//'
}

# merge/restart 时：显式 LABELU_MEDIA_HOST 优先；否则内网 MEDIA_HOST 自动对齐仓库 .env.server
deploy_sync_media_host_on_merge() {
  [[ -f "${ENV_FILE}" ]] || return 0
  if [[ -n "${LABELU_MEDIA_HOST:-}" ]]; then
    ensure_env_kv "${ENV_FILE}" "MEDIA_HOST" "${LABELU_MEDIA_HOST}"
    MEDIA_HOST="${LABELU_MEDIA_HOST}"
    log_step "配置：MEDIA_HOST 已设为 LABELU_MEDIA_HOST=${LABELU_MEDIA_HOST}"
    return 0
  fi
  if [[ -n "${LABELU_DEPLOY_PUBLIC_HOST:-}" ]]; then
    local pub_mh="http://${LABELU_DEPLOY_PUBLIC_HOST}:${LABELU_PORT}"
    ensure_env_kv "${ENV_FILE}" "MEDIA_HOST" "${pub_mh}"
    MEDIA_HOST="${pub_mh}"
    log_step "配置：MEDIA_HOST 已设为公网 ${pub_mh}"
    return 0
  fi
  local current template_mh cur_host tpl_host
  current="$(deploy_read_media_host_from_file "${ENV_FILE}" || true)"
  [[ -n "${current}" ]] || return 0
  cur_host="$(deploy_media_host_hostname "${current}")"
  deploy_is_private_media_hostname "${cur_host}" || return 0
  [[ -f "${DEPLOY_ENV_SERVER_TEMPLATE}" ]] || return 0
  template_mh="$(deploy_read_media_host_from_file "${DEPLOY_ENV_SERVER_TEMPLATE}" || true)"
  [[ -n "${template_mh}" ]] || return 0
  tpl_host="$(deploy_media_host_hostname "${template_mh}")"
  deploy_is_private_media_hostname "${tpl_host}" && return 0
  if [[ "${current}" == "${template_mh}" ]]; then
    return 0
  fi
  ensure_env_kv "${ENV_FILE}" "MEDIA_HOST" "${template_mh}"
  MEDIA_HOST="${template_mh}"
  log_step "配置：内网 MEDIA_HOST 已同步为仓库模板 ${template_mh}"
}

warn_media_host_if_likely_wrong() {
  if [[ -n "${LABELU_MEDIA_HOST:-}" ]] || [[ -n "${LABELU_DEPLOY_PUBLIC_HOST:-}" ]]; then
    return 0
  fi
  local host mh
  mh="$(deploy_read_media_host_from_file "${ENV_FILE}" 2>/dev/null || true)"
  [[ -n "${mh}" ]] && MEDIA_HOST="${mh}"
  host="$(deploy_media_host_hostname "${MEDIA_HOST}")"
  if deploy_is_private_media_hostname "${host}"; then
    log "警告: MEDIA_HOST=${MEDIA_HOST} 似为内网地址；公网访问请在 ${ENV_FILE} 设置 LABELU_MEDIA_HOST 或 LABELU_DEPLOY_PUBLIC_HOST"
  fi
}

deploy_source_migrate_lib() {
  # shellcheck source=scripts/ops/migrate_deploy.sh
  source "${SCRIPT_DIR}/scripts/ops/migrate_deploy.sh"
}

deploy_optional_warn() {
  DEPLOY_OPTIONAL_WARNINGS+=("$*")
  log "警告: $*"
}

# yolo 完整部署默认严格：TPU-MLIR / sophon / BM1688 种子任一失败则中止（LABELU_DEPLOY_STRICT=0 可关闭）
deploy_is_strict() {
  case "${LABELU_DEPLOY_STRICT:-}" in
    0|n|N|false|no|off)
      return 1
      ;;
    1|y|Y|true|yes|on)
      return 0
      ;;
  esac
  [[ "${INSTALL_EXTRAS}" == *yolo* ]]
}

deploy_run_required() {
  local title="$1"
  shift
  log_step "${title}（必须成功）"
  if ! "$@"; then
    log "错误: ${title} 失败，中止部署"
    exit 1
  fi
}

deploy_run_optional() {
  local title="$1"
  shift
  log_step "${title}（可选，失败不阻断主服务）"
  set +e
  "$@"
  local rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    deploy_optional_warn "${title} 未完成 (exit ${rc})"
  fi
  return 0
}

deploy_run_migrate() {
  local project_root="$1"
  if [[ "${LABELU_SKIP_MIGRATE:-}" == "1" ]]; then
    log "跳过数据库迁移（LABELU_SKIP_MIGRATE=1）"
    return 0
  fi
  if [[ ! -f "${SCRIPT_DIR}/scripts/ops/migrate_deploy.sh" ]]; then
    deploy_optional_warn "未找到 migrate_deploy.sh，已跳过数据库迁移"
    return 0
  fi
  deploy_source_migrate_lib
  export LABELU_ENV_FILE="${ENV_FILE}"
  export LABELU_DEPLOY_RUNTIME_DIR="${SERVER_RUNTIME_DIR}"
  export VENV_DIR
  log_step "数据库：alembic upgrade head"
  set +e
  labelu_migrate_upgrade_head "${project_root}"
  local rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    deploy_optional_warn "alembic upgrade head 失败 (exit ${rc})"
    return 1
  fi
  return 0
}

# 部署结束检查清单（由 deploy_finalize 在 install/restart/update 末尾统一打印）
declare -a DEPLOY_CHECK_ITEMS=()
DEPLOY_CHK_OK=0
DEPLOY_CHK_WARN=0
DEPLOY_CHK_FAIL=0
DEPLOY_CHK_SKIP=0

deploy_checklist_add() {
  local status="$1" name="$2" detail="${3:-}"
  DEPLOY_CHECK_ITEMS+=("${status}|${name}|${detail}")
}

deploy_checklist_import_lines() {
  local line st name detail
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -n "${line}" ]] || continue
    st="${line%%|*}"
    line="${line#*|}"
    name="${line%%|*}"
    detail="${line#*|}"
    deploy_checklist_add "${st}" "${name}" "${detail}"
  done
}

deploy_checklist_database() {
  local dburl
  dburl="$(deploy_read_database_url_from_file "${ENV_FILE}")"
  if [[ -z "${dburl}" ]]; then
    deploy_checklist_add warn "数据库 DATABASE_URL" "未配置"
    return 0
  fi
  if printf '%s' "${dburl}" | grep -qi 'mysql'; then
    if deploy_mysql_can_connect_quiet; then
      deploy_checklist_add ok "MySQL 连接" "DATABASE_URL 可连接"
    else
      deploy_checklist_add fail "MySQL 连接" "无法连接，请检查 ${ENV_FILE}"
    fi
    return 0
  fi
  if printf '%s' "${dburl}" | grep -qi 'sqlite'; then
    local sqlite_path="${dburl#sqlite:///}"
    sqlite_path="${sqlite_path%%\?*}"
    if [[ -f "${sqlite_path}" ]]; then
      deploy_checklist_add ok "SQLite 数据库" "${sqlite_path}"
    else
      deploy_checklist_add warn "SQLite 数据库" "文件尚未创建（首次启动会自动创建）"
    fi
    return 0
  fi
  deploy_checklist_add ok "DATABASE_URL" "已配置（非 MySQL/SQLite 探针跳过）"
}

deploy_checklist_http_api() {
  if ! command -v curl >/dev/null 2>&1; then
    deploy_checklist_add skip "HTTP API 探活 (/docs)" "未安装 curl"
    return 0
  fi
  local code port_note
  if labelu_nginx_enabled; then
    port_note="127.0.0.1:${LABELU_PORT} (Nginx)"
  else
    port_note="127.0.0.1:${LABELU_PORT} (直连)"
  fi
  code="$(deploy_curl_http_code "http://127.0.0.1:${LABELU_PORT}/docs")"
  if [[ "${code}" == "200" ]]; then
    deploy_checklist_add ok "HTTP API 探活 (/docs)" "${port_note} → ${code}"
  else
    deploy_checklist_add fail "HTTP API 探活 (/docs)" "${port_note} → ${code}（journalctl -u ${SYSTEMD_UNIT} -n 50）"
  fi
  local path
  for path in "/healthz" "/readyz" "/version" "/openapi.json"; do
    if deploy_probe_json_url_expect_200 "http://127.0.0.1:${LABELU_PORT}${path}" 3; then
      deploy_checklist_add ok "JSON 探针 ${path}" "${port_note} → ${DEPLOY_LAST_HTTP_CODE}, ${DEPLOY_LAST_HTTP_CONTENT_TYPE}"
    else
      deploy_checklist_add fail "JSON 探针 ${path}" "${port_note} → ${DEPLOY_LAST_HTTP_CODE}, ${DEPLOY_LAST_HTTP_CONTENT_TYPE}, body=${DEPLOY_LAST_HTTP_BODY_PREFIX}（禁止把前端 HTML 当作服务就绪）"
    fi
  done
  # The localized API documentation is a separate backend endpoint.  Probe it
  # explicitly because a stale Nginx SPA fallback can return HTTP 200 HTML
  # while /openapi.json itself still appears healthy.
  if deploy_probe_json_url_expect_200 "http://127.0.0.1:${LABELU_PORT}/api-docs/openapi.json" 3; then
    deploy_checklist_add ok "JSON 探针 /api-docs/openapi.json" "${port_note} → ${DEPLOY_LAST_HTTP_CODE}, ${DEPLOY_LAST_HTTP_CONTENT_TYPE}"
  else
    deploy_checklist_add fail "JSON 探针 /api-docs/openapi.json" "${port_note} → ${DEPLOY_LAST_HTTP_CODE}, ${DEPLOY_LAST_HTTP_CONTENT_TYPE}, body=${DEPLOY_LAST_HTTP_BODY_PREFIX}（禁止把前端 HTML 当作服务就绪）"
  fi
  deploy_checklist_canonical_openapi "${port_note}"
}

# 健康接口 200 只能证明进程存活，不能证明部署的是目标路由版本。
# 这里读取唯一规范入口 /openapi.json，校验一期主链路径和退休控制面，
# 失败记为硬失败并由 deploy_finalize 返回非零，禁止把旧路由部署判定为成功。
deploy_checklist_canonical_openapi() {
  local port_note="${1:-127.0.0.1:${LABELU_PORT}}"
  local contract_file="${SCRIPT_DIR}/scripts/ops/7gai_openapi_contract.json"
  if [[ ! -f "${contract_file}" ]]; then
    deploy_checklist_add fail "canonical OpenAPI 路由契约" "缺少 ${contract_file}"
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    deploy_checklist_add fail "canonical OpenAPI 路由契约" "未安装 curl，无法读取 /openapi.json"
    return 0
  fi
  local python_bin=""
  if command -v python3 >/dev/null 2>&1; then
    python_bin="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    python_bin="$(command -v python)"
  fi
  if [[ -z "${python_bin}" ]]; then
    deploy_checklist_add fail "canonical OpenAPI 路由契约" "未找到 Python 解析器"
    return 0
  fi

  local header_file body_file code content_type detail
  header_file="$(mktemp)"
  body_file="$(mktemp)"
  code="$(curl -sS -D "${header_file}" -o "${body_file}" -w "%{http_code}" \
    --connect-timeout 8 --max-time 20 "http://127.0.0.1:${LABELU_PORT}/openapi.json" 2>/dev/null || true)"
  code="${code//$'\r'/}"
  code="${code//$'\n'/}"
  content_type="$(awk 'BEGIN {IGNORECASE=1} /^Content-Type:/ {sub(/^[^:]*:[[:space:]]*/, ""); sub(/[[:space:]]*;.*/, ""); print; exit}' "${header_file}" | tr -d '\r')"
  if [[ "${code}" != "200" || "${content_type,,}" != *"application/json"* ]]; then
    deploy_checklist_add fail "canonical OpenAPI 路由契约" "${port_note} /openapi.json → ${code:-000}, ${content_type:-unknown}（必须是 JSON 规范入口）"
    rm -f "${header_file}" "${body_file}"
    return 0
  fi
  if detail="$("${python_bin}" - "${body_file}" "${contract_file}" <<'PY'
import json
import sys

body_path, contract_path = sys.argv[1:]
with open(body_path, encoding="utf-8") as stream:
    payload = json.load(stream)
with open(contract_path, encoding="utf-8") as stream:
    contract = json.load(stream)
paths = payload.get("paths") if isinstance(payload, dict) else None
if not isinstance(paths, dict):
    print("response has no paths object")
    raise SystemExit(1)
required = set(contract.get("required_paths", []))
retired_prefixes = tuple(contract.get("retired_prefixes", []))
allowed_paths = set(contract.get("allowed_paths", []))
exposed = set(paths)
missing = sorted(required - exposed)
retired = sorted(path for path in exposed if path not in allowed_paths and any(path.startswith(prefix) for prefix in retired_prefixes))
details = []
if missing:
    details.append("缺少: " + ", ".join(missing))
if retired:
    details.append("已退休路径仍暴露: " + ", ".join(retired))
if details:
    print("; ".join(details))
    raise SystemExit(1)
print("一期主链路由齐全，未暴露退休控制面")
PY
)"; then
    deploy_checklist_add ok "canonical OpenAPI 路由契约" "${port_note} /openapi.json → ${code}, ${content_type}; ${detail}"
  else
    deploy_checklist_add fail "canonical OpenAPI 路由契约" "${port_note} /openapi.json → ${code}, ${content_type}; ${detail:-解析或契约校验失败}"
  fi
  rm -f "${header_file}" "${body_file}"
}

deploy_training_worker_images_valid() {
  local raw="${LABELU_TRAINING_WORKER_ALLOWED_IMAGES:-}"
  [[ -n "${raw}" ]] || return 1
  local image
  IFS=',' read -r -a images <<<"${raw}"
  [[ "${#images[@]}" -gt 0 ]] || return 1
  for image in "${images[@]}"; do
    image="$(printf '%s' "${image}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -n "${image}" ]] || return 1
    [[ "${image}" =~ ^[A-Za-z0-9][A-Za-z0-9._/@:-]*$ ]] || return 1
  done
}

deploy_bm1688_worker_values_valid() {
  local interval="${LABELU_BM1688_WORKER_INTERVAL:-2}"
  local lease="${LABELU_BM1688_WORKER_LEASE_SECONDS:-300}"
  local limit="${LABELU_BM1688_WORKER_BATCH_LIMIT:-1}"
  local memory_max="${LABELU_BM1688_WORKER_MEMORY_MAX:-16G}"
  local cpu_quota="${LABELU_BM1688_WORKER_CPU_QUOTA:-400%}"
  local tasks_max="${LABELU_BM1688_WORKER_TASKS_MAX:-1024}"
  [[ "${interval}" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1
  [[ "${interval}" != "0" ]] && [[ "${interval}" != "0.0" ]] || return 1
  [[ "${lease}" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "${limit}" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "${memory_max}" =~ ^[1-9][0-9]*G$ ]] || return 1
  [[ "${cpu_quota}" =~ ^[1-9][0-9]*%$ ]] || return 1
  [[ "${tasks_max}" =~ ^[1-9][0-9]*$ ]] || return 1
}

deploy_edge_validation_worker_values_valid() {
  local interval="${LABELU_EDGE_VALIDATION_WORKER_INTERVAL:-5}"
  local limit="${LABELU_EDGE_VALIDATION_WORKER_BATCH_LIMIT:-1}"
  [[ "${interval}" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1
  [[ "${interval}" != "0" ]] && [[ "${interval}" != "0.0" ]] || return 1
  [[ "${limit}" =~ ^[1-9][0-9]*$ ]] || return 1
}

deploy_experiment_dataset_worker_values_valid() {
  local interval="${EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS:-5}"
  local lease="${EXPERIMENT_DATASET_WORKER_LEASE_SECONDS:-300}"
  local limit="${EXPERIMENT_DATASET_WORKER_BATCH_SIZE:-1}"
  [[ "${interval}" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1
  [[ "${interval}" != "0" ]] && [[ "${interval}" != "0.0" ]] || return 1
  [[ "${lease}" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "${limit}" =~ ^[1-9][0-9]*$ ]] || return 1
}

deploy_checklist_training_worker() {
  if [[ "${LABELU_ENABLE_TRAINING_WORKER}" != "1" ]]; then
    deploy_checklist_add skip "独立训练 Worker" "LABELU_ENABLE_TRAINING_WORKER=0（生产默认关闭）"
    return 0
  fi
  if ! deploy_training_worker_images_valid; then
    deploy_checklist_add fail "训练 Worker 镜像白名单" "启用 Worker 必须配置 LABELU_TRAINING_WORKER_ALLOWED_IMAGES（精确逗号分隔）"
    return 0
  fi
  if [[ -f "/etc/systemd/system/${WORKER_SYSTEMD_UNIT}" ]]; then
    deploy_checklist_add ok "systemd Worker 单元" "/etc/systemd/system/${WORKER_SYSTEMD_UNIT}"
  else
    deploy_checklist_add fail "systemd Worker 单元" "未安装 ${WORKER_SYSTEMD_UNIT}"
  fi
  if systemctl is-active --quiet "${WORKER_SYSTEMD_UNIT}" 2>/dev/null; then
    deploy_checklist_add ok "服务 ${WORKER_SYSTEMD_UNIT}" "active (running)"
  else
    deploy_checklist_add fail "服务 ${WORKER_SYSTEMD_UNIT}" "未运行或未 enable"
  fi
}

deploy_checklist_bm1688_worker() {
  if [[ "${LABELU_ENABLE_BM1688_WORKER}" != "1" ]]; then
    deploy_checklist_add skip "BM1688 独立 Worker" "LABELU_ENABLE_BM1688_WORKER=0（生产默认关闭）"
    return 0
  fi
  if [[ ! -f "${SERVER_PROJECT_DIR}/labelu/scripts/bm1688_worker.py" ]]; then
    deploy_checklist_add fail "BM1688 Worker 入口" "缺少 labelu/scripts/bm1688_worker.py"
    return 0
  fi
  if ! deploy_bm1688_worker_values_valid; then
    deploy_checklist_add fail "BM1688 Worker 参数" "interval、lease-seconds、batch-limit 必须为正数"
    return 0
  fi
  if [[ -f "/etc/systemd/system/${BM1688_WORKER_SYSTEMD_UNIT}" ]]; then
    deploy_checklist_add ok "BM1688 systemd Worker 单元" "/etc/systemd/system/${BM1688_WORKER_SYSTEMD_UNIT}"
  else
    deploy_checklist_add fail "BM1688 systemd Worker 单元" "未安装 ${BM1688_WORKER_SYSTEMD_UNIT}"
  fi
  if systemctl is-active --quiet "${BM1688_WORKER_SYSTEMD_UNIT}" 2>/dev/null; then
    deploy_checklist_add ok "服务 ${BM1688_WORKER_SYSTEMD_UNIT}" "active (running)"
  else
    deploy_checklist_add fail "服务 ${BM1688_WORKER_SYSTEMD_UNIT}" "未运行或未 enable"
  fi
}

deploy_checklist_edge_validation_worker() {
  if [[ "${LABELU_ENABLE_EDGE_VALIDATION_WORKER}" != "1" ]]; then
    deploy_checklist_add skip "独立边端验证 Worker" "LABELU_ENABLE_EDGE_VALIDATION_WORKER=0（生产默认关闭）"
    return 0
  fi
  if [[ ! -f "${SERVER_PROJECT_DIR}/labelu/scripts/edge_validation_worker.py" ]]; then
    deploy_checklist_add fail "边端验证 Worker 入口" "缺少 labelu/scripts/edge_validation_worker.py"
    return 0
  fi
  if ! deploy_edge_validation_worker_values_valid; then
    deploy_checklist_add fail "边端验证 Worker 参数" "interval、batch-limit 必须为正数"
    return 0
  fi
  if [[ -f "/etc/systemd/system/${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}" ]]; then
    deploy_checklist_add ok "边端验证 systemd Worker 单元" "/etc/systemd/system/${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}"
  else
    deploy_checklist_add fail "边端验证 systemd Worker 单元" "未安装 ${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}"
  fi
  if systemctl is-active --quiet "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}" 2>/dev/null; then
    deploy_checklist_add ok "服务 ${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}" "active (running)"
  else
    deploy_checklist_add fail "服务 ${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}" "未运行或未 enable"
  fi
}

deploy_checklist_experiment_dataset_worker() {
  if [[ "${LABELU_ENABLE_EXPERIMENT_DATASET_WORKER}" != "1" ]]; then
    deploy_checklist_add skip "第三方 S3 回流 Worker" "LABELU_ENABLE_EXPERIMENT_DATASET_WORKER=0（生产默认关闭）"
    return 0
  fi
  if [[ ! -f "${SERVER_PROJECT_DIR}/labelu/scripts/experiment_dataset_import_worker.py" ]]; then
    deploy_checklist_add fail "S3 回流 Worker 入口" "缺少 labelu/scripts/experiment_dataset_import_worker.py"
    return 0
  fi
  if ! deploy_experiment_dataset_worker_values_valid; then
    deploy_checklist_add fail "S3 回流 Worker 参数" "interval、lease-seconds、batch-size 必须为正数"
    return 0
  fi
  if [[ -f "/etc/systemd/system/${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}" ]]; then
    deploy_checklist_add ok "S3 回流 systemd Worker 单元" "/etc/systemd/system/${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"
  else
    deploy_checklist_add fail "S3 回流 systemd Worker 单元" "未安装 ${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"
  fi
  if systemctl is-active --quiet "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}" 2>/dev/null; then
    deploy_checklist_add ok "服务 ${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}" "active (running)"
  else
    deploy_checklist_add fail "服务 ${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}" "未运行或未 enable"
  fi
}

deploy_checklist_frontend_statics() {
  local project_root="$1"
  local statics="${project_root}/labelu/internal/statics"
  if [[ "${LABELU_SKIP_FRONTEND_BUILD:-}" == "1" ]]; then
    deploy_checklist_add skip "前端静态资源 (statics)" "LABELU_SKIP_FRONTEND_BUILD=1"
    return 0
  fi
  if [[ -f "${statics}/index.html" ]]; then
    deploy_checklist_add ok "前端静态资源 (statics)" "${statics}/index.html"
  elif [[ -d "${statics}/assets" ]] && [[ -n "$(ls -A "${statics}/assets" 2>/dev/null || true)" ]]; then
    deploy_checklist_add warn "前端静态资源 (statics)" "有 assets 但缺少 index.html"
  else
    deploy_checklist_add fail "前端静态资源 (statics)" "未构建，请 deploy.sh update 或 frontend-build"
  fi
}

deploy_checklist_yolo_bm1688_stack() {
  local project_root="$1"
  if [[ "${INSTALL_EXTRAS}" != *yolo* ]]; then
    deploy_checklist_add skip "YOLO / BM1688 编译栈" "INSTALL_EXTRAS 未含 yolo"
    return 0
  fi
  if tpu_mlir_stack_ready; then
    deploy_checklist_add ok "TPU-MLIR 工具链" "${SERVER_TPU_MLIR_ROOT}"
  else
    deploy_checklist_add warn "TPU-MLIR 工具链" "未就绪（INT8 宿主机回退可能失败）"
  fi
  if [[ -x "${SERVER_RUNTIME_DIR}/venv-tpu-mlir/bin/python3" ]]; then
    deploy_checklist_add ok "venv-tpu-mlir (Py3.10)" "${SERVER_RUNTIME_DIR}/venv-tpu-mlir/bin/python3"
  else
    deploy_checklist_add warn "venv-tpu-mlir (Py3.10)" "未创建"
  fi
  local sophon_det="${LABELU_DEPLOY_RESOLVED_BASE}/sophon-demo/sample/YOLOv8_plus_det"
  if [[ -d "${sophon_det}/scripts" ]]; then
    deploy_checklist_add ok "sophon-demo (YOLOv8_plus_det)" "${sophon_det}"
  else
    deploy_checklist_add warn "sophon-demo (YOLOv8_plus_det)" "未下载完整"
  fi
  if [[ -d "${sophon_det}/datasets/coco128" ]]; then
    deploy_checklist_add ok "BM1688 默认校准集 coco128" "${sophon_det}/datasets/coco128"
  else
    deploy_checklist_add warn "BM1688 默认校准集 coco128" "缺失（可用任务导出校准集）"
  fi
  if [[ -f "${sophon_det}/models/onnx/yolov11s_qtable" ]]; then
    deploy_checklist_add ok "BM1688 qtable" "yolov11s_qtable"
  else
    deploy_checklist_add warn "BM1688 qtable" "yolov11s_qtable 缺失"
  fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    deploy_checklist_add ok "Docker Engine" "可用"
  else
    deploy_checklist_add warn "Docker Engine" "未安装或未启动"
  fi
  local bm_container="${LABELU_BM1688_CONTAINER:-labelu_bm1688}"
  local bm_image="${LABELU_BM1688_IMAGE:-sophgo/tpuc_dev:v3.2}"
  if command -v docker >/dev/null 2>&1; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${bm_container}"; then
      deploy_checklist_add ok "BM1688 Docker 容器" "${bm_container} running"
    elif docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "${bm_container}"; then
      deploy_checklist_add warn "BM1688 Docker 容器" "${bm_container} 已创建但未运行"
    else
      deploy_checklist_add warn "BM1688 Docker 容器" "${bm_container} 未创建"
    fi
    if docker image inspect "${bm_image}" >/dev/null 2>&1; then
      deploy_checklist_add ok "BM1688 Docker 镜像" "${bm_image}"
    else
      deploy_checklist_add warn "BM1688 Docker 镜像" "${bm_image} 未加载"
    fi
  fi
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    deploy_source_production_env
    deploy_checklist_import_lines < <(
      LABELU_PROJECT_ROOT="${project_root}" PYTHONPATH="${project_root}" \
      "${VENV_DIR}/bin/python" - <<'PY' 2>/dev/null || true
import os
import sys

sys.path.insert(0, os.environ.get("LABELU_PROJECT_ROOT", os.environ.get("PYTHONPATH", ".")))
try:
    from labelu.internal.common.bm1688_deploy_bootstrap import (
        ensure_bm1688_compiler_ready,
        ensure_bm1688_docker_deploy,
    )
    from labelu.internal.common.yolo_bm1688_docker import quantize_backend_from_cfg

    cfg = ensure_bm1688_compiler_ready()
    backend = quantize_backend_from_cfg(cfg)
    print(f"ok|BM1688 quantize_backend|{backend}")

    for key, label in (
        ("tpu_mlir_root", "BM1688 配置 tpu_mlir_root"),
        ("calibration_dataset_dir", "BM1688 配置 calibration"),
        ("quantize_table_path", "BM1688 配置 qtable"),
    ):
        val = (cfg.get(key) or "").strip()
        if val:
            short = val if len(val) <= 72 else val[:32] + "…" + val[-28:]
            print(f"ok|{label}|{short}")
        else:
            print(f"warn|{label}|未写入 bm1688_compiler_config.json")

    if backend == "docker":
        ok, note = ensure_bm1688_docker_deploy()
        st = "ok" if ok else "warn"
        print(f"{st}|BM1688 Docker 量化就绪|{(note or '')[:120]}")
except Exception as exc:
    print(f"warn|BM1688 配置探测|{exc}")
PY
    )
  fi
  local cfg_file="${SERVER_APPDATA_DIR}/bm1688_compiler_config.json"
  if [[ ! -f "${cfg_file}" ]] && [[ -f "${SERVER_RUNTIME_DIR}/bm1688_compiler_config.json" ]]; then
    cfg_file="${SERVER_RUNTIME_DIR}/bm1688_compiler_config.json"
  fi
  if [[ -f "${cfg_file}" ]]; then
    deploy_checklist_add ok "bm1688_compiler_config.json" "${cfg_file}"
  else
    deploy_checklist_add warn "bm1688_compiler_config.json" "尚未生成（seed 未成功）"
  fi
}

deploy_checklist_edge_ssh_stack() {
  local project_root="$1"
  if [[ "${INSTALL_EXTRAS}" != *yolo* ]]; then
    deploy_checklist_add skip "边端 SSH 验证栈" "INSTALL_EXTRAS 未含 yolo"
    return 0
  fi
  local statics="${project_root}/labelu/internal/statics"
  local ext_page="${project_root}/frontend/extensions/pages/yolo_bm1688_exports.html"
  local edge_registry_page="${project_root}/frontend/extensions/pages/edge_device_registry.html"
  local edge_registry_static="${statics}/edge_device_registry.html"
  if [[ -f "${edge_registry_page}" ]] && grep -q 'fieldDockerContainer' "${edge_registry_page}" 2>/dev/null; then
    deploy_checklist_add ok "BM1688 页边端 Docker 配置 UI" "extensions/edge_device_registry.html"
  elif [[ -f "${edge_registry_static}" ]] && grep -q 'fieldDockerContainer' "${edge_registry_static}" 2>/dev/null; then
    deploy_checklist_add ok "BM1688 页边端 Docker 配置 UI" "statics/edge_device_registry.html"
  elif [[ -f "${ext_page}" ]] && grep -q 'edgeSshDockerContainer' "${ext_page}" 2>/dev/null; then
    deploy_checklist_add ok "BM1688 页边端 Docker 配置 UI" "extensions/yolo_bm1688_exports.html"
  elif [[ -f "${statics}/yolo_bm1688_exports.html" ]] && grep -q 'edgeSshDockerContainer' "${statics}/yolo_bm1688_exports.html" 2>/dev/null; then
    deploy_checklist_add ok "BM1688 页边端 Docker 配置 UI" "statics/yolo_bm1688_exports.html"
  else
    deploy_checklist_add warn "BM1688 页边端 Docker 配置 UI" "未找到 edgeSshDockerContainer，请 deploy.sh update 同步前端"
  fi
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    deploy_source_production_env
    deploy_checklist_import_lines < <(
      LABELU_PROJECT_ROOT="${project_root}" PYTHONPATH="${project_root}" \
      "${VENV_DIR}/bin/python" - <<'PY' 2>/dev/null || true
import os
import sys

sys.path.insert(0, os.environ.get("LABELU_PROJECT_ROOT", os.environ.get("PYTHONPATH", ".")))
try:
    from labelu.internal.common.edge_ssh_deploy_checklist import iter_deploy_checklist_lines

    for line in iter_deploy_checklist_lines(probe_remote=True):
        print(line)
except Exception as exc:
    print(f"warn|边端 SSH 配置探测|{exc}")
PY
    )
  else
    deploy_checklist_add warn "边端 SSH 配置探测" "venv python 不可用"
  fi
}

deploy_run_post_deploy_checklist() {
  local project_root="${1:-}"
  project_root="${project_root:-$(resolve_project_root)}"
  DEPLOY_CHECK_ITEMS=()
  DEPLOY_CHK_OK=0
  DEPLOY_CHK_WARN=0
  DEPLOY_CHK_FAIL=0
  DEPLOY_CHK_SKIP=0

  log_step "收尾：生成部署检查清单…"

  if [[ -f "${ENV_FILE}" ]]; then
    deploy_checklist_add ok "运行配置 .env" "${ENV_FILE}"
  else
    deploy_checklist_add fail "运行配置 .env" "缺失 ${ENV_FILE}"
  fi

  if [[ -d "${SERVER_RUNTIME_DIR}" ]]; then
    deploy_checklist_add ok "运行目录 (MEDIA/venv)" "${SERVER_RUNTIME_DIR}"
  else
    deploy_checklist_add fail "运行目录 (MEDIA/venv)" "${SERVER_RUNTIME_DIR}"
  fi

  if [[ -d "${SERVER_APPDATA_DIR}" ]]; then
    deploy_checklist_add ok "应用数据目录" "${SERVER_APPDATA_DIR}"
  else
    deploy_checklist_add warn "应用数据目录" "${SERVER_APPDATA_DIR} 不存在"
  fi

  if [[ -x "${VENV_DIR}/bin/labelu" ]]; then
    deploy_checklist_add ok "Python 虚拟环境 (labelu CLI)" "${VENV_DIR}/bin/labelu"
  else
    deploy_checklist_add fail "Python 虚拟环境 (labelu CLI)" "未安装或不可执行"
  fi

  if [[ -f "${project_root}/pyproject.toml" ]]; then
    deploy_checklist_add ok "代码仓库根" "${project_root}"
  else
    deploy_checklist_add warn "代码仓库根" "未找到 pyproject.toml"
  fi

  deploy_checklist_database

  if [[ -f "/etc/systemd/system/${SYSTEMD_UNIT}" ]]; then
    deploy_checklist_add ok "systemd 单元文件" "/etc/systemd/system/${SYSTEMD_UNIT}"
  else
    deploy_checklist_add fail "systemd 单元文件" "未安装 ${SYSTEMD_UNIT}"
  fi

  if systemctl is-active --quiet "${SYSTEMD_UNIT}" 2>/dev/null; then
    deploy_checklist_add ok "服务 ${SYSTEMD_UNIT}" "active (running)"
  elif systemctl is-enabled --quiet "${SYSTEMD_UNIT}" 2>/dev/null; then
    deploy_checklist_add fail "服务 ${SYSTEMD_UNIT}" "已 enable 但未 active"
  else
    deploy_checklist_add fail "服务 ${SYSTEMD_UNIT}" "未运行或未 enable"
  fi

  if [[ "${LABELU_ENABLE_TRAINING_SCHEDULER}" == "1" ]]; then
    if [[ -f "/etc/systemd/system/${SCHEDULER_SYSTEMD_UNIT}" ]]; then
      deploy_checklist_add ok "systemd 调度器单元" "/etc/systemd/system/${SCHEDULER_SYSTEMD_UNIT}"
    else
      deploy_checklist_add fail "systemd 调度器单元" "未安装 ${SCHEDULER_SYSTEMD_UNIT}"
    fi
    if systemctl is-active --quiet "${SCHEDULER_SYSTEMD_UNIT}" 2>/dev/null; then
      deploy_checklist_add ok "服务 ${SCHEDULER_SYSTEMD_UNIT}" "active (running)"
    else
      deploy_checklist_add fail "服务 ${SCHEDULER_SYSTEMD_UNIT}" "未运行或未 enable"
    fi
  else
    deploy_checklist_add skip "独立训练调度器" "LABELU_ENABLE_TRAINING_SCHEDULER=0（使用 API watchdog）"
  fi

  if labelu_nginx_enabled; then
    if command -v nginx >/dev/null 2>&1; then
      if systemctl is-active --quiet nginx 2>/dev/null; then
        deploy_checklist_add ok "Nginx 前端分发" "active，外部端口 ${LABELU_PORT} → 后端 ${LABELU_BACKEND_PORT}"
      else
        deploy_checklist_add fail "Nginx 前端分发" "nginx 未 active"
      fi
      if [[ -f "${NGINX_LABELU_CONF}" ]]; then
        deploy_checklist_add ok "Nginx 配置" "${NGINX_LABELU_CONF}"
      else
        deploy_checklist_add warn "Nginx 配置" "${NGINX_LABELU_CONF} 缺失"
      fi
    else
      deploy_checklist_add fail "Nginx 前端分发" "未安装 nginx"
    fi
  else
    deploy_checklist_add skip "Nginx 前端分发" "LABELU_ENABLE_NGINX_FRONTEND=0（LabelU 直连 ${LABELU_PORT}）"
  fi

  deploy_checklist_http_api
  deploy_checklist_training_worker
  deploy_checklist_bm1688_worker
  deploy_checklist_edge_validation_worker
  deploy_checklist_experiment_dataset_worker
  deploy_checklist_frontend_statics "${project_root}"
  deploy_checklist_yolo_bm1688_stack "${project_root}"
  deploy_checklist_edge_ssh_stack "${project_root}"
}

deploy_print_checklist_summary() {
  local item st name detail tag
  log "========================================"
  log "部署检查清单"
  log "========================================"
  if [[ "${#DEPLOY_CHECK_ITEMS[@]}" -eq 0 ]]; then
    log "  （无检查项，请确认已执行 deploy_finalize）"
    log "========================================"
    return 0
  fi
  for item in "${DEPLOY_CHECK_ITEMS[@]}"; do
    st="${item%%|*}"
    item="${item#*|}"
    name="${item%%|*}"
    detail="${item#*|}"
    case "${st}" in
      ok) tag=" OK "; DEPLOY_CHK_OK=$((DEPLOY_CHK_OK + 1)) ;;
      warn) tag="WARN"; DEPLOY_CHK_WARN=$((DEPLOY_CHK_WARN + 1)) ;;
      fail) tag="FAIL"; DEPLOY_CHK_FAIL=$((DEPLOY_CHK_FAIL + 1)) ;;
      skip) tag="SKIP"; DEPLOY_CHK_SKIP=$((DEPLOY_CHK_SKIP + 1)) ;;
      *) tag=" ?? "; DEPLOY_CHK_WARN=$((DEPLOY_CHK_WARN + 1)) ;;
    esac
    if [[ -n "${detail}" ]]; then
      log "$(printf '  [%s] %-34s %s' "${tag}" "${name}" "${detail}")"
    else
      log "$(printf '  [%s] %-34s' "${tag}" "${name}")"
    fi
  done
  log "----------------------------------------"
  log "汇总: 通过 ${DEPLOY_CHK_OK} 项 | 警告 ${DEPLOY_CHK_WARN} 项 | 失败 ${DEPLOY_CHK_FAIL} 项 | 跳过 ${DEPLOY_CHK_SKIP} 项"
  if [[ "${#DEPLOY_OPTIONAL_WARNINGS[@]}" -gt 0 ]]; then
    log "部署过程可选步骤告警:"
    local w
    for w in "${DEPLOY_OPTIONAL_WARNINGS[@]}"; do
      log "  · ${w}"
    done
  fi
  log "========================================"
}

deploy_finalize() {
  local project_root="${1:-}"
  DEPLOY_FINALIZE_DONE=1
  trap - EXIT 2>/dev/null || true
  sleep 2
  deploy_run_post_deploy_checklist "${project_root}"
  deploy_print_checklist_summary
  deploy_print_finish_summary
  [[ "${DEPLOY_CHK_FAIL}" -eq 0 ]]
}

deploy_print_finish_summary() {
  local media
  media="$(deploy_media_host_for_unit)"
  if [[ "${DEPLOY_CHK_FAIL}" -gt 0 ]]; then
    log "结论: 存在 ${DEPLOY_CHK_FAIL} 项失败，服务可能不可用或功能受限。"
    log "  数据库: grep DATABASE_URL ${ENV_FILE}"
    log "  日志: journalctl -u ${SYSTEMD_UNIT} -n 80 --no-pager"
    log "  修复后: sudo bash deploy.sh restart"
  elif [[ "${DEPLOY_CHK_WARN}" -gt 0 || "${#DEPLOY_OPTIONAL_WARNINGS[@]}" -gt 0 ]]; then
    log "结论: 核心服务已就绪，但有 ${DEPLOY_CHK_WARN} 项警告或可选步骤未完全成功。"
    if [[ "${INSTALL_EXTRAS}" == *yolo* ]]; then
      log "  BM1688/YOLO: sudo env INSTALL_EXTRAS=yolo,image-pipeline bash deploy.sh restart"
    fi
  else
    log "结论: 检查清单全部通过，部署步骤均已成功完成。"
  fi
  log "浏览器访问: ${media}"
  log "详细配置: ${VENV_DIR}/bin/labelu doctor"
  log "服务状态: systemctl status ${SYSTEMD_UNIT}"
}

deploy_ensure_service_running() {
  log_step "服务：确保 ${SYSTEMD_UNIT} 已启动"
  if [[ -f "${SCRIPT_DIR}/scripts/ops/deploy_yolo_train_guard.sh" ]]; then
    # shellcheck source=scripts/ops/deploy_yolo_train_guard.sh
    source "${SCRIPT_DIR}/scripts/ops/deploy_yolo_train_guard.sh"
    load_deploy_media_hints_from_envfile
    if ! deploy_yolo_train_prepare_for_restart; then
      log "已中止 systemctl restart（YOLO 训练自动暂停未完成）。见上方说明。"
      exit 1
    fi
  fi
  set +e
  systemctl daemon-reload
  systemctl enable "${SYSTEMD_UNIT}" 2>/dev/null
  systemctl restart "${SYSTEMD_UNIT}"
  local rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    deploy_optional_warn "systemctl restart 返回 ${rc}，请执行: journalctl -u ${SYSTEMD_UNIT} -n 80 --no-pager"
    return 0
  fi
  sleep 2
  if ! systemctl is-active --quiet "${SYSTEMD_UNIT}"; then
    deploy_optional_warn "${SYSTEMD_UNIT} 未处于 active，请检查 journalctl -u ${SYSTEMD_UNIT}"
    return 0
  fi
  if [[ "${LABELU_ENABLE_TRAINING_SCHEDULER}" == "1" ]]; then
    systemctl enable "${SCHEDULER_SYSTEMD_UNIT}" 2>/dev/null || true
    systemctl restart "${SCHEDULER_SYSTEMD_UNIT}" || \
      deploy_optional_warn "${SCHEDULER_SYSTEMD_UNIT} 启动失败，请检查 journalctl -u ${SCHEDULER_SYSTEMD_UNIT}"
  fi
  if [[ "${LABELU_ENABLE_TRAINING_WORKER}" == "1" ]]; then
    systemctl enable "${WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
    systemctl restart "${WORKER_SYSTEMD_UNIT}" || \
      deploy_optional_warn "${WORKER_SYSTEMD_UNIT} 启动失败，请检查 journalctl -u ${WORKER_SYSTEMD_UNIT}"
  fi
  if [[ "${LABELU_ENABLE_BM1688_WORKER}" == "1" ]]; then
    systemctl enable "${BM1688_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
    systemctl restart "${BM1688_WORKER_SYSTEMD_UNIT}" || \
      deploy_optional_warn "${BM1688_WORKER_SYSTEMD_UNIT} 启动失败，请检查 journalctl -u ${BM1688_WORKER_SYSTEMD_UNIT}"
  fi
  if [[ "${LABELU_ENABLE_EDGE_VALIDATION_WORKER}" == "1" ]]; then
    systemctl enable "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
    systemctl restart "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}" || \
      deploy_optional_warn "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT} 启动失败，请检查 journalctl -u ${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}"
  fi
  if [[ "${LABELU_ENABLE_EXPERIMENT_DATASET_WORKER}" == "1" ]]; then
    systemctl enable "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
    systemctl restart "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}" || \
      deploy_optional_warn "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT} 启动失败，请检查 journalctl -u ${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"
  fi
  if [[ -f "${SCRIPT_DIR}/scripts/ops/deploy_yolo_train_guard.sh" ]]; then
    # shellcheck source=scripts/ops/deploy_yolo_train_guard.sh
    source "${SCRIPT_DIR}/scripts/ops/deploy_yolo_train_guard.sh"
    deploy_wait_backend_ready "${LABELU_BACKEND_PORT}" 15 || true
    deploy_yolo_train_resume_after_restart || true
  fi
}

# 供 python -m / labelu CLI 使用：通过 LABELU_ENV_FILE 让 Python 进程自行读取 .env，
# 避免 deploy shell 解释 secret（如含 $ 的密码）后与应用运行时产生偏差。
deploy_source_production_env() {
  export LABELU_ENV_FILE="${ENV_FILE}"
  export TPU_MLIR_ROOT="${TPU_MLIR_ROOT:-${SERVER_TPU_MLIR_ROOT}}"
  local sophon_det="${SOPHON_DET_ROOT:-${LABELU_DEPLOY_RESOLVED_BASE}/sophon-demo/sample/YOLOv8_plus_det}"
  export SOPHON_DET_ROOT="${sophon_det}"
  export YOLO_BM1688_CALIBRATION_DATASET_DIR="${YOLO_BM1688_CALIBRATION_DATASET_DIR:-${sophon_det}/datasets/coco128}"
  export YOLO_BM1688_QTABLE_PATH="${YOLO_BM1688_QTABLE_PATH:-${sophon_det}/models/onnx/yolov11s_qtable}"
}

deploy_run_doctor() {
  if [[ ! -x "${VENV_DIR}/bin/labelu" ]]; then
    return 0
  fi
  log_step "自检：labelu doctor"
  deploy_source_production_env
  if ! "${VENV_DIR}/bin/labelu" doctor; then
    log "警告: labelu doctor 返回非零，请检查 .env（MEDIA_HOST、AI 密钥、DATABASE_URL）"
  fi
}

deploy_run_http_probe() {
  : # HTTP 探活已并入部署检查清单（deploy_checklist_http_api）
}

labelu_nginx_enabled() {
  [[ "${LABELU_ENABLE_NGINX_FRONTEND:-1}" == "1" ]]
}

labelu_backend_port_for_unit() {
  if labelu_nginx_enabled; then
    printf '%s' "${LABELU_BACKEND_PORT}"
  else
    printf '%s' "${LABELU_PORT}"
  fi
}

labelu_backend_host_for_unit() {
  if labelu_nginx_enabled; then
    printf '%s' "127.0.0.1"
  else
    printf '%s' "0.0.0.0"
  fi
}

deploy_disable_nginx_frontend() {
  if [[ -f "${NGINX_LABELU_CONF}" ]]; then
    local disabled="${NGINX_LABELU_CONF}.disabled"
    log_step "Nginx：禁用 LabelU 前端分发 ${NGINX_LABELU_CONF}"
    mv -f "${NGINX_LABELU_CONF}" "${disabled}"
  fi
  if command -v nginx >/dev/null 2>&1; then
    nginx -t && systemctl reload nginx 2>/dev/null || true
  fi
}

deploy_nginx_proxy_headers() {
  cat <<'EOF'
    proxy_http_version 1.1;
    proxy_set_header Host $host:$server_port;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
EOF
}

deploy_write_nginx_proxy_location() {
  local location_expr="$1"
  local read_timeout="${2:-}"
  local read_timeout_line=""
  if [[ -n "${read_timeout}" ]]; then
    read_timeout_line="    proxy_read_timeout ${read_timeout};"
  fi
  cat <<EOF
  location ${location_expr} {
    proxy_pass http://127.0.0.1:${LABELU_BACKEND_PORT};
$(deploy_nginx_proxy_headers)
${read_timeout_line}
  }

EOF
}

deploy_nginx_extension_page_routes() {
  local project_root="$1"
  local routes=""
  local py_candidates=()
  if [[ -n "${SERVER_RUNTIME_DIR:-}" && -x "${SERVER_RUNTIME_DIR}/venv/bin/python" ]]; then
    py_candidates+=("${SERVER_RUNTIME_DIR}/venv/bin/python")
  fi
  py_candidates+=("python3")
  local py
  for py in "${py_candidates[@]}"; do
    routes="$("${py}" -c "
import sys
sys.path.insert(0, '${project_root}')
from labelu.internal.common.labelu_frontend_integration import EXTENSION_PAGE_FILES
print('\\n'.join(sorted(EXTENSION_PAGE_FILES.keys())))
" 2>/dev/null)" && [[ -n "${routes}" ]] && break
  done
  if [[ -z "${routes}" ]]; then
    routes="/annotation-settings
/yolo-training
/ai-labeling-settings
/yolo-eval-prompts
/yolo-eval-reports-hub
/yolo-bm1688-exports
/yolo-preannotate
/yolo-model-workflow
/annotation-stats
/yolo-model-publish
/yolo-train-detail-mockup
/yolo-gt-pred-compare"
  fi
  printf '%s\n' "${routes}"
}

deploy_write_nginx_exact_asset() {
  local name="$1"
  local path="$2"
  cat <<EOF
  location = /assets/${name} {
    alias ${path};
    add_header Cache-Control "no-cache" always;
  }

EOF
}

deploy_write_nginx_frontend_config() {
  local project_root="$1"
  local statics_dir="${project_root}/labelu/internal/statics"
  local statics_assets_dir="${statics_dir}/assets"
  local extension_assets_dir="${project_root}/frontend/extensions/assets"
  mkdir -p "$(dirname "${NGINX_LABELU_CONF}")"
  if [[ -f "${NGINX_LABELU_CONF}" ]]; then
    cp -a "${NGINX_LABELU_CONF}" "${NGINX_LABELU_CONF}.bak.$(date +%Y%m%d%H%M%S)" || true
  fi
  cat >"${NGINX_LABELU_CONF}" <<EOF
server {
  listen ${LABELU_PORT};
  server_name _;

  client_max_body_size 4096m;
  root ${statics_dir};

  gzip on;
  gzip_static on;
  gzip_vary on;
  gzip_types text/plain text/css application/javascript application/json image/svg+xml;

EOF
  deploy_write_nginx_proxy_location "= /api/v1/yolo-training-tasks" "1800s" >>"${NGINX_LABELU_CONF}"
  deploy_write_nginx_proxy_location "/api/" >>"${NGINX_LABELU_CONF}"
  cat >>"${NGINX_LABELU_CONF}" <<EOF
  location /internal/ {
    proxy_pass http://127.0.0.1:${LABELU_BACKEND_PORT};
$(deploy_nginx_proxy_headers)
  }

  location /internal/yolo-remote-payload/ {
    internal;
    alias ${SERVER_RUNTIME_DIR}/yolo_train/;
    sendfile on;
    tcp_nopush on;
  }

EOF
  cat >>"${NGINX_LABELU_CONF}" <<EOF
  location /ws/ {
    proxy_pass http://127.0.0.1:${LABELU_BACKEND_PORT};
    proxy_http_version 1.1;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host \$host:\$server_port;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
  }

EOF
  # Keep backend-only observability and API documentation paths ahead of the
  # frontend SPA fallback.  The Chinese docs also serve Swagger/ReDoc assets
  # below /api-docs/assets/.
  for route in "/docs" "/redoc" "/openapi.json" "/healthz" "/readyz" "/version" "/api-docs" "/api-docs/redoc" "/api-docs/openapi.json"; do
    deploy_write_nginx_proxy_location "= ${route}" >>"${NGINX_LABELU_CONF}"
  done
  deploy_write_nginx_proxy_location "^~ /api-docs/" >>"${NGINX_LABELU_CONF}"
  while IFS= read -r route; do
    [[ -z "${route}" ]] && continue
    deploy_write_nginx_proxy_location "= ${route}" >>"${NGINX_LABELU_CONF}"
  done < <(deploy_nginx_extension_page_routes "${project_root}")
  local asset
  for asset in \
    labelu_auth_bridge.js labelu_embedded_boot.js labelu_portal.js labelu_pager.js labelu_standalone_shell.css labelu_toolkit_compact.css \
    task_rect_preset_tabs.js task_list_yolo_train_link.js task_yolo_preannotate_entry.js \
    task_tagtool_preannotate_entry.js task_vlx_seek_preannotate_entry.js \
    task_toolbar_extensions.js task_ai_label_geometry_toggle.js; do
    deploy_write_nginx_exact_asset "${asset}" "${extension_assets_dir}/${asset}" >>"${NGINX_LABELU_CONF}"
  done
  cat >>"${NGINX_LABELU_CONF}" <<EOF
  location /assets/ {
    alias ${statics_assets_dir}/;
    expires 1y;
    add_header Cache-Control "public, max-age=31536000, immutable" always;
  }

  location = /favicon.svg {
    try_files /favicon.svg @labelu_backend;
  }

  location = /favicon.ico {
    try_files /favicon.ico @labelu_backend;
  }

  location / {
    try_files \$uri \$uri/ /index.html;
    add_header Cache-Control "no-cache" always;
  }

  location @labelu_backend {
    proxy_pass http://127.0.0.1:${LABELU_BACKEND_PORT};
$(deploy_nginx_proxy_headers)
  }
}
EOF
}

deploy_configure_nginx_frontend() {
  local project_root="$1"
  if ! labelu_nginx_enabled; then
    deploy_disable_nginx_frontend
    return 0
  fi
  if ! command -v nginx >/dev/null 2>&1; then
    log_step "Nginx：安装 nginx"
    export DEBIAN_FRONTEND=noninteractive
    if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
      apt-get update -y && apt-get install -y --no-install-recommends nginx
    else
      apt-get update -y -qq && apt-get install -y -qq --no-install-recommends nginx
    fi
  fi
  log_step "Nginx：写入 LabelU 前端分发配置（外部 ${LABELU_PORT} → 后端 127.0.0.1:${LABELU_BACKEND_PORT}）"
  deploy_write_nginx_frontend_config "${project_root}"
  if ! nginx -t; then
    log "错误: nginx -t 失败，自动禁用 LabelU Nginx 配置并回退旧模式"
    deploy_disable_nginx_frontend
    LABELU_ENABLE_NGINX_FRONTEND=0 write_and_enable_systemd
    deploy_ensure_service_running
    deploy_optional_warn "nginx -t 失败，已回退 LabelU 直连 ${LABELU_PORT}"
    return 0
  fi
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl reload nginx 2>/dev/null || systemctl restart nginx
  deploy_wait_backend_ready "${LABELU_BACKEND_PORT}" 25 || true
  local path probe_fail=0
  for path in "/docs" "/yolo-training" "/yolo-gt-pred-compare" "/assets/labelu_auth_bridge.js" "/"; do
    if deploy_probe_url_expect_200 "http://127.0.0.1:${LABELU_PORT}${path}" 8; then
      log "Nginx 探活: http://127.0.0.1:${LABELU_PORT}${path} → 200"
    else
      log "错误: Nginx 模式探活失败 (${path} → ${DEPLOY_LAST_HTTP_CODE})，自动禁用 LabelU Nginx 配置并回退旧模式"
      probe_fail=1
      break
    fi
  done
  for path in "/healthz" "/readyz" "/version" "/openapi.json"; do
    if deploy_probe_json_url_expect_200 "http://127.0.0.1:${LABELU_PORT}${path}" 8; then
      log "Nginx JSON 探活: http://127.0.0.1:${LABELU_PORT}${path} → ${DEPLOY_LAST_HTTP_CODE}, ${DEPLOY_LAST_HTTP_CONTENT_TYPE}"
    else
      log "错误: Nginx JSON 探活失败 (${path} → ${DEPLOY_LAST_HTTP_CODE}, ${DEPLOY_LAST_HTTP_CONTENT_TYPE}, body=${DEPLOY_LAST_HTTP_BODY_PREFIX})，自动禁用 LabelU Nginx 配置并回退旧模式"
      probe_fail=1
      break
    fi
  done
  if [[ "${probe_fail}" -eq 0 ]]; then
    if deploy_probe_json_url_expect_200 "http://127.0.0.1:${LABELU_PORT}/api-docs/openapi.json" 8; then
      log "Nginx JSON 探活: http://127.0.0.1:${LABELU_PORT}/api-docs/openapi.json → ${DEPLOY_LAST_HTTP_CODE}, ${DEPLOY_LAST_HTTP_CONTENT_TYPE}"
    else
      log "错误: Nginx JSON 探活失败 (/api-docs/openapi.json → ${DEPLOY_LAST_HTTP_CODE}, ${DEPLOY_LAST_HTTP_CONTENT_TYPE}, body=${DEPLOY_LAST_HTTP_BODY_PREFIX})，自动禁用 Nginx 配置并回退旧模式"
      probe_fail=1
    fi
  fi
  if [[ "${probe_fail}" -eq 1 ]]; then
    deploy_disable_nginx_frontend
    LABELU_ENABLE_NGINX_FRONTEND=0 write_and_enable_systemd
    deploy_ensure_service_running
    deploy_wait_backend_ready "${LABELU_PORT}" 15 || true
    deploy_optional_warn "Nginx 探活未通过，已回退 LabelU 直连 ${LABELU_PORT}（检查清单仍会列出 HTTP 状态）"
  fi
  return 0
}

deploy_reload_nginx_frontend_only() {
  local project_root="$1"
  if ! labelu_nginx_enabled; then
    log "Nginx 前端分发未开启；已跳过 Nginx reload，后端进程保持不变。"
    return 0
  fi
  if ! command -v nginx >/dev/null 2>&1; then
    log "错误: 未找到 nginx，纯前端快速发版无法刷新入口配置。"
    log "请执行完整部署完成 Nginx 安装。"
    exit 1
  fi
  log_step "Nginx：刷新前端分发配置并重载（不重启 LabelU 后端）"
  deploy_write_nginx_frontend_config "${project_root}"
  if ! nginx -t; then
    log "错误: nginx -t 失败；为避免影响后端服务，已中止纯前端快速发版。"
    exit 1
  fi
  systemctl reload nginx 2>/dev/null || systemctl restart nginx
}

DEPLOY_ENV_SERVER_TEMPLATE="${SCRIPT_DIR}/config/env/.env.server"

# 从仓库 config/env/.env.server 安装运行目录 .env（路径与 MEDIA_HOST 按当前部署根覆盖）
install_env_from_server_template() {
  [[ -f "${DEPLOY_ENV_SERVER_TEMPLATE}" ]] || return 1

  mkdir -p "${SERVER_RUNTIME_DIR}"
  log_step "配置：仓库模板 ${DEPLOY_ENV_SERVER_TEMPLATE} → ${ENV_FILE}"
  cp "${DEPLOY_ENV_SERVER_TEMPLATE}" "${ENV_FILE}"

  umask 077
  ensure_env_kv "${ENV_FILE}" "BASE_DATA_DIR" "${SERVER_APPDATA_DIR}"
  ensure_env_kv "${ENV_FILE}" "MEDIA_ROOT" "${SERVER_RUNTIME_DIR}"
  # 保留 .env.server 中的公网 MEDIA_HOST；仅当显式指定部署变量时才覆盖
  if [[ -n "${LABELU_MEDIA_HOST:-}" ]]; then
    ensure_env_kv "${ENV_FILE}" "MEDIA_HOST" "${LABELU_MEDIA_HOST}"
  elif [[ -n "${LABELU_DEPLOY_PUBLIC_HOST:-}" ]]; then
    ensure_env_kv "${ENV_FILE}" "MEDIA_HOST" "http://${LABELU_DEPLOY_PUBLIC_HOST}:${LABELU_PORT}"
  fi
  append_labelu_production_env_defaults
  deploy_sanitize_env_shell_unsafe_values "${ENV_FILE}"
  chmod 600 "${ENV_FILE}" || true
  umask 022
  log_step "配置：已从仓库 .env.server 安装（已同步 BASE_DATA_DIR / MEDIA_ROOT；MEDIA_HOST 以模板为准除非设置 LABELU_MEDIA_HOST）"
  warn_media_host_if_likely_wrong
  return 0
}

# restart/update 时保留已有 DATABASE_URL、MEDIA_HOST、密钥等，仅同步路径与补缺键
merge_runtime_env_in_place() {
  [[ -f "${ENV_FILE}" ]] || return 1
  log_step "配置：保留 ${ENV_FILE}，仅同步 BASE_DATA_DIR / MEDIA_ROOT 与补缺键"
  umask 077
  ensure_env_kv "${ENV_FILE}" "BASE_DATA_DIR" "${SERVER_APPDATA_DIR}"
  ensure_env_kv "${ENV_FILE}" "MEDIA_ROOT" "${SERVER_RUNTIME_DIR}"
  deploy_sync_media_host_on_merge
  append_labelu_production_env_defaults
  chmod 600 "${ENV_FILE}" || true
  umask 022
  warn_media_host_if_likely_wrong
  return 0
}

write_env_file() {
  if [[ -f "${ENV_FILE}" ]] && [[ "${LABELU_DEPLOY_OVERWRITE_ENV:-1}" == "0" ]]; then
    merge_runtime_env_in_place
    return 0
  fi
  if [[ "${LABELU_DEPLOY_USE_REPO_ENV:-1}" != "0" ]]; then
    if install_env_from_server_template; then
      return 0
    fi
    log "警告: 未找到 ${DEPLOY_ENV_SERVER_TEMPLATE}，回退为脚本生成 .env"
  fi

  mkdir -p "${SERVER_RUNTIME_DIR}"
  log_step "配置：写入/合并 ${ENV_FILE}"
  local secret
  if [[ -f "${ENV_FILE}" ]] && grep -q '^PASSWORD_SECRET_KEY=.\+' "${ENV_FILE}" 2>/dev/null; then
    log "保留已有 ${ENV_FILE} 中的 PASSWORD_SECRET_KEY"
    secret=""
  else
    secret="$(openssl rand -hex 32)"
    log "生成新的 PASSWORD_SECRET_KEY"
  fi

  umask 077
  if [[ -n "${secret}" ]]; then
    cat >"${ENV_FILE}" <<EOF
# 由 deploy.sh 生成（可改用 config/env/.env.server 提交到仓库）
# BASE_DATA_DIR：SQLite、bm1688_compiler_config.json 等
# MEDIA_ROOT：上传媒体、YOLO 工作目录、BM1688 导出 ONNX/bmodel
BASE_DATA_DIR=${SERVER_APPDATA_DIR}
MEDIA_ROOT=${SERVER_RUNTIME_DIR}
MEDIA_HOST=${MEDIA_HOST}
PASSWORD_SECRET_KEY=${secret}
EOF
    if [[ "${INSTALL_EXTRAS}" == *yolo* ]]; then
      echo "YOLO_TRAIN_ENABLED=true" >>"${ENV_FILE}"
    fi
    append_labelu_production_env_defaults
  else
    ensure_env_kv "${ENV_FILE}" "BASE_DATA_DIR" "${SERVER_APPDATA_DIR}"
    ensure_env_kv "${ENV_FILE}" "MEDIA_ROOT" "${SERVER_RUNTIME_DIR}"
    ensure_env_kv "${ENV_FILE}" "MEDIA_HOST" "${MEDIA_HOST}"
    if [[ "${INSTALL_EXTRAS}" == *yolo* ]] && ! grep -q '^YOLO_TRAIN_ENABLED=' "${ENV_FILE}" 2>/dev/null; then
      echo "YOLO_TRAIN_ENABLED=true" >>"${ENV_FILE}"
    fi
    append_labelu_production_env_defaults
  fi
  chmod 600 "${ENV_FILE}" || true
  umask 022
  log_step "配置：.env 已更新（含 BASE_DATA_DIR / MEDIA_ROOT / MEDIA_HOST 等）"
  warn_media_host_if_likely_wrong
}

create_venv_and_install_project() {
  local project_root="$1"
  log_step "Python venv：目录 ${VENV_DIR}"

  mkdir -p "${SERVER_TPU_TMP}" "${SERVER_RUNTIME_DIR}/.pip_cache"
  export TMPDIR="${SERVER_TPU_TMP}"
  export PIP_CACHE_DIR="${SERVER_RUNTIME_DIR}/.pip_cache"
  log_step "pip 缓存与临时目录: PIP_CACHE_DIR=${PIP_CACHE_DIR}  TMPDIR=${TMPDIR}"

  log_step "预检：pip 安装前再次检查磁盘"
  deploy_ensure_rootfs_space "$(deploy_install_min_free_mb)"

  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    log "创建虚拟环境…"
    python3.11 -m venv "${VENV_DIR}"
  else
    log "复用已有虚拟环境"
  fi
  # shellcheck source=/dev/null
  source "${VENV_DIR}/bin/activate"
  log_step "pip：升级 pip / setuptools / wheel…"
  if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
    pip install --upgrade pip setuptools wheel
  else
    pip install -q --upgrade pip setuptools wheel
  fi

  local pip_ok=0
  if [[ -n "${INSTALL_EXTRAS}" ]]; then
    log_step "pip：可编辑安装项目（extras: ${INSTALL_EXTRAS}），耗时可能较长…"
    if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
      pip install --no-cache-dir -e "${project_root}[${INSTALL_EXTRAS}]" && pip_ok=1
    else
      pip install -q --no-cache-dir -e "${project_root}[${INSTALL_EXTRAS}]" && pip_ok=1
    fi
  else
    log_step "pip：可编辑安装项目（无 extras）…"
    if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
      pip install --no-cache-dir -e "${project_root}" && pip_ok=1
    else
      pip install -q --no-cache-dir -e "${project_root}" && pip_ok=1
    fi
  fi

  if [[ "${pip_ok}" -ne 1 ]]; then
    log "pip 安装失败，尝试自动清理磁盘后重试一次…"
    deploy_ensure_rootfs_space "$(deploy_install_min_free_mb)"
    rm -rf "${VENV_DIR}" 2>/dev/null || true
    python3.11 -m venv "${VENV_DIR}"
    # shellcheck source=/dev/null
    source "${VENV_DIR}/bin/activate"
    pip install -q --upgrade pip setuptools wheel
    if [[ -n "${INSTALL_EXTRAS}" ]]; then
      pip install -q --no-cache-dir -e "${project_root}[${INSTALL_EXTRAS}]" || {
        log "错误: pip 仍失败，请检查 df -h 与根分区 / 是否已满"
        exit 1
      }
    else
      pip install -q --no-cache-dir -e "${project_root}" || {
        log "错误: pip 仍失败，请检查 df -h 与根分区 / 是否已满"
        exit 1
      }
    fi
  fi

  if ! command -v labelu >/dev/null 2>&1; then
    log "错误: 未找到 labelu 命令"
    exit 1
  fi
  log_step "pip：labelu 命令行已就绪"
  deactivate || true
}

# 从指定 .env 文件读取 DATABASE_URL（stdout 输出；无则空）
deploy_read_database_url_from_file() {
  local f="$1"
  [[ -f "${f}" ]] || return 0
  local dburl
  dburl="$(
    { grep -E '^[[:space:]]*DATABASE_URL=' "${f}" 2>/dev/null || true; } |
      head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*#.*$//'
  )"
  dburl="${dburl#\"}"
  dburl="${dburl%\"}"
  dburl="${dburl#\'}"
  dburl="${dburl%\'}"

  printf '%s' "${dburl}"
}

# 将 .env 中 DATABASE_URL 规范为 URL 编码密码 + 127.0.0.1（避免 @ 截断与 localhost socket 差异）
deploy_normalize_database_url_in_env() {
  [[ -f "${ENV_FILE}" ]] || return 0
  local dburl project_root normalized
  dburl="$(deploy_read_database_url_from_file "${ENV_FILE}")"
  [[ -n "${dburl}" ]] || return 0
  if ! printf '%s' "${dburl}" | grep -qi 'mysql'; then
    return 0
  fi
  project_root="$(resolve_project_root)"
  normalized="$(
    DEPLOY_DBURL="${dburl}" PROJECT_ROOT="${project_root}" PYTHONPATH="${project_root}" \
      python3 -c "import os,sys; sys.path.insert(0, os.environ.get('PROJECT_ROOT','')); \
from labelu.internal.common.mysql_url import normalize_mysql_database_url; \
print(normalize_mysql_database_url(os.environ['DEPLOY_DBURL']))"
  )" || return 0
  if [[ -n "${normalized}" && "${normalized}" != "${dburl}" ]]; then
    log_step "配置：已规范化 DATABASE_URL（密码 URL 编码 / localhost→127.0.0.1）"
    ensure_env_kv "${ENV_FILE}" "DATABASE_URL" "${normalized}"
  fi
}

# reset-clean 前尽量装上 PyMySQL（venv 仍存在时优先写入 venv）
reset_clean_ensure_pymysql() {
  if [[ -x "${VENV_DIR}/bin/pip" ]]; then
    if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
      "${VENV_DIR}/bin/pip" install --no-cache-dir "pymysql>=1.1.0" || true
    else
      "${VENV_DIR}/bin/pip" install -q --no-cache-dir "pymysql>=1.1.0" 2>/dev/null || true
    fi
  fi
  if python3 -c "import pymysql" 2>/dev/null; then
    return 0
  fi
  python3 -m pip install -q --no-cache-dir "pymysql>=1.1.0" 2>/dev/null \
    || python3 -m pip install -q --break-system-packages --no-cache-dir "pymysql>=1.1.0" 2>/dev/null \
    || return 1
}

# reset-clean：按 .env 中 DATABASE_URL 重建 MySQL 库（仅 mysql 连接串）
reset_clean_drop_mysql_database() {
  local env_source="$1"
  [[ -f "${env_source}" ]] || return 0
  if [[ "${LABELU_RESET_SKIP_MYSQL:-}" == "1" ]]; then
    log "已设置 LABELU_RESET_SKIP_MYSQL=1，跳过 MySQL 清库"
    return 0
  fi

  local dburl
  dburl="$(deploy_read_database_url_from_file "${env_source}")"
  [[ -n "${dburl}" ]] || return 0
  if ! printf '%s' "${dburl}" | grep -qi 'mysql'; then
    log_step "数据库：非 MySQL，跳过 DROP/CREATE"
    return 0
  fi

  reset_clean_ensure_pymysql || log "提示: 未能预装 PyMySQL，将尝试系统 mysql 客户端"

  local py=""
  if [[ -x "${VENV_DIR}/bin/python" ]] && "${VENV_DIR}/bin/python" -c "import pymysql" 2>/dev/null; then
    py="${VENV_DIR}/bin/python"
  elif python3 -c "import pymysql" 2>/dev/null; then
    py="$(command -v python3)"
  elif [[ -x "${VENV_DIR}/bin/python" ]]; then
    py="${VENV_DIR}/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    py="$(command -v python3)"
  else
    log "警告: 未找到 python3，无法自动清 MySQL 库，请手工 DROP/CREATE"
    return 1
  fi

  log_step "数据库：MySQL 将 DROP 并 CREATE（来自 ${env_source}）…"
  if DEPLOY_DBURL="${dburl}" "${py}" - <<'PY'
import os
import re
import sys
from urllib.parse import unquote, urlparse

url = os.environ.get("DEPLOY_DBURL", "")
if not url:
    sys.exit(0)
lower = url.lower()
for prefix in ("mysql+pymysql://", "mysql+mysqldb://", "mysql://"):
    if lower.startswith(prefix):
        url = "mysql://" + url[len(prefix) :]
        break
else:
    print("not a mysql url", file=sys.stderr)
    sys.exit(0)

parsed = urlparse(url)
user = unquote(parsed.username or "")
password = unquote(parsed.password or "")
host = parsed.hostname or "127.0.0.1"
port = int(parsed.port or 3306)
db = (parsed.path or "").lstrip("/").split("/")[0].split("?")[0]
if not db or not re.match(r"^[A-Za-z0-9_]+$", db):
    print(f"invalid mysql database name: {db!r}", file=sys.stderr)
    sys.exit(1)

drop_sql = f"DROP DATABASE IF EXISTS `{db}`"
create_sql = (
    f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
)

def run_pymysql():
    import pymysql

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(drop_sql)
            cur.execute(create_sql)
        conn.commit()
    finally:
        conn.close()
    print(f"mysql: {drop_sql}; {create_sql}")

def _which(cmd):
    import shutil

    return shutil.which(cmd)

def run_mysql_cli():
    import subprocess
    import tempfile

    if not _which("mysql"):
        return False
    cnf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".cnf", delete=False, encoding="utf-8"
    )
    try:
        cnf.write("[client]\n")
        cnf.write(f"host={host}\n")
        cnf.write(f"port={port}\n")
        cnf.write(f"user={user}\n")
        cnf.write(f"password={password}\n")
        cnf.close()
        os.chmod(cnf.name, 0o600)
        r = subprocess.run(
            [
                "mysql",
                f"--defaults-extra-file={cnf.name}",
                "-e",
                f"{drop_sql}; {create_sql}",
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            if err:
                print(f"mysql client failed: {err}", file=sys.stderr)
            return False
        print(f"mysql-cli: {drop_sql}; {create_sql}")
        return True
    finally:
        try:
            os.unlink(cnf.name)
        except OSError:
            pass

def run_mysql_root_socket():
    """deploy 以 root 运行时，Ubuntu 默认 root@localhost 走 auth_socket，无需密码。"""
    import subprocess

    if not _which("mysql"):
        return False
    r = subprocess.run(
        ["mysql", "-e", f"{drop_sql}; {create_sql}"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if err:
            print(f"mysql root socket failed: {err}", file=sys.stderr)
        return False
    print(f"mysql-root-socket: {drop_sql}; {create_sql}")
    return True


def _try_all():
    try:
        import pymysql
    except ImportError:
        pymysql = None

    if pymysql is not None:
        try:
            run_pymysql()
            return 0
        except pymysql.err.OperationalError as e:
            code = e.args[0] if e.args else 0
            # 1698: Ubuntu root@localhost 使用 auth_socket，URL 密码无效
            if code not in (1698, 1045, 1044):
                print(f"pymysql: {e}", file=sys.stderr)
        except Exception as e:
            print(f"pymysql: {e}", file=sys.stderr)

    if run_mysql_cli():
        return 0
    if run_mysql_root_socket():
        return 0
    return 1


sys.exit(_try_all())
PY
  then
    log_step "数据库：MySQL 库已重建"
    return 0
  else
    local dbname
    dbname="$(
      DEPLOY_DBURL="${dburl}" python3 - <<'PY2'
import os, re
from urllib.parse import urlparse
u = os.environ.get("DEPLOY_DBURL", "")
for p in ("mysql+pymysql://", "mysql+mysqldb://", "mysql://"):
    if u.lower().startswith(p):
        u = "mysql://" + u[len(p):]
        break
db = urlparse(u).path.lstrip("/").split("/")[0].split("?")[0]
if db and re.match(r"^[A-Za-z0-9_]+$", db):
    print(db)
PY2
    )"
    if [[ -n "${dbname}" ]] && mysql -e "DROP DATABASE IF EXISTS \`${dbname}\`; CREATE DATABASE \`${dbname}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null; then
      log_step "数据库：MySQL 库已重建（root 本地 socket）"
      return 0
    fi
    log "警告: MySQL 清库失败。Ubuntu 上 root 常无法用密码登录，可手工执行:"
    log "  sudo mysql -e \"DROP DATABASE IF EXISTS ${dbname:-labelu}; CREATE DATABASE ${dbname:-labelu} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\""
    log "应用 DATABASE_URL 建议使用独立账号（如 labelu@127.0.0.1），勿依赖 root+密码，见 config/env/.env.server 注释"
    return 1
  fi
}

# 以 root 本地 socket 确保 .env 中 DATABASE_URL 对应 MySQL 用户存在（Ubuntu auth_socket）
ensure_mysql_app_user_from_env() {
  [[ -f "${ENV_FILE}" ]] || return 0
  local dburl
  dburl="$(deploy_read_database_url_from_file "${ENV_FILE}")"
  [[ -n "${dburl}" ]] || return 0
  if ! printf '%s' "${dburl}" | grep -qi 'mysql'; then
    return 0
  fi
  if [[ "${LABELU_SKIP_MYSQL_ENSURE_USER:-}" == "1" ]]; then
    return 0
  fi
  if deploy_mysql_can_connect_quiet; then
    log_step "数据库：DATABASE_URL 已可连接，跳过 MySQL 用户/密码同步（避免误触密码策略）"
    return 0
  fi
  if ! command -v mysql >/dev/null 2>&1; then
    log "警告: 未找到 mysql 客户端，跳过 MySQL 用户检查"
    return 0
  fi

  log_step "数据库：DATABASE_URL 暂不可连，尝试通过 root socket 创建/修复用户…"
  local project_root
  project_root="$(resolve_project_root)"
  if DEPLOY_DBURL="${dburl}" PROJECT_ROOT="${project_root}" PYTHONPATH="${project_root}" python3 - <<'PY'
import os
import re
import subprocess
import sys

sys.path.insert(0, os.environ.get("PROJECT_ROOT", ""))
from labelu.internal.common.mysql_url import parse_mysql_database_url

url = os.environ.get("DEPLOY_DBURL", "")
parsed = parse_mysql_database_url(url)
if not parsed:
    sys.exit(0)

user = parsed.username
password = parsed.password
host = parsed.host
db = parsed.database
if not db or not re.match(r"^[A-Za-z0-9_]+$", db):
    print("invalid mysql database name", file=sys.stderr)
    sys.exit(1)

parts = [
    f"CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
]

if user and user != "root":
    if not re.match(r"^[A-Za-z0-9_]+$", user):
        print("invalid mysql user name", file=sys.stderr)
        sys.exit(1)
    user_sql = user.replace("'", "''")
    pass_sql = password.replace("\\", "\\\\").replace("'", "''")
    hosts = []
    for h in (host, "127.0.0.1", "localhost"):
        if h and h not in hosts:
            hosts.append(h)
    for h in hosts:
        h_sql = h.replace("'", "''")
        parts.append(
            f"CREATE USER IF NOT EXISTS '{user_sql}'@'{h_sql}' IDENTIFIED BY '{pass_sql}';"
        )
        if os.environ.get("LABELU_MYSQL_FORCE_USER_RESET", "").strip() in ("1", "yes", "Y"):
            parts.append(
                f"ALTER USER '{user_sql}'@'{h_sql}' IDENTIFIED BY '{pass_sql}';"
            )
        parts.append(f"GRANT ALL PRIVILEGES ON `{db}`.* TO '{user_sql}'@'{h_sql}';")
    parts.append("FLUSH PRIVILEGES;")
else:
    user = user or "root"
    if password:
        pass_sql = password.replace("\\", "\\\\").replace("'", "''")

sql = " ".join(parts)


def run_socket_sql(statement: str) -> bool:
    """本机 mysql；root 已改为密码登录时回退 debian-sys-maint（/etc/mysql/debian.cnf）。"""
    debian_cnf = "/etc/mysql/debian.cnf"
    attempts = [["mysql", "-e", statement]]
    if os.path.isfile(debian_cnf):
        attempts.append(["mysql", f"--defaults-file={debian_cnf}", "-e", statement])
    last_err = ""
    for cmd in attempts:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return True
        last_err = (r.stderr or r.stdout or "").strip()
    print(last_err or "mysql admin failed", file=sys.stderr)
    return False


def relax_mysql_password_policy() -> None:
    """个人服务器：放宽策略以便使用含 @ 的短密码（原策略常因缺大写报 1819）。"""
    if os.environ.get("LABELU_MYSQL_RELAX_PASSWORD_POLICY", "1") in ("0", "n", "N", "no"):
        return
    for stmt in (
        "SET GLOBAL validate_password.policy=LOW",
        "SET GLOBAL validate_password.length=8",
        "SET GLOBAL validate_password.mixed_case_count=0",
        "SET GLOBAL validate_password.number_count=0",
        "SET GLOBAL validate_password.special_char_count=0",
        "SET PERSIST validate_password.policy=LOW",
        "SET PERSIST validate_password.length=8",
    ):
        run_socket_sql(stmt)


def can_connect_app() -> bool:
    try:
        import pymysql
        from labelu.internal.common.mysql_url import pymysql_connect_kwargs

        conn = pymysql.connect(**pymysql_connect_kwargs(url))
        conn.close()
        return True
    except Exception:
        return False


def ensure_root_tcp_password_auth() -> bool:
    """让 root 可通过 127.0.0.1 + 密码连接（供 PyMySQL / DATABASE_URL）。"""
    if user != "root" or not password:
        return True
    relax_mysql_password_policy()
    attempts = [
        [
            f"ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{pass_sql}';",
            f"CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY '{pass_sql}';",
            f"ALTER USER 'root'@'127.0.0.1' IDENTIFIED BY '{pass_sql}';",
            "FLUSH PRIVILEGES;",
        ],
        [
            f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{pass_sql}';",
            f"CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY '{pass_sql}';",
            f"ALTER USER 'root'@'127.0.0.1' IDENTIFIED BY '{pass_sql}';",
            "FLUSH PRIVILEGES;",
        ],
    ]
    last_err = ""
    for stmts in attempts:
        ok = True
        for stmt in stmts:
            if not run_socket_sql(stmt):
                last_err = "auth fix failed"
                ok = False
                break
        if ok:
            print(" enabled root TCP password auth (127.0.0.1)")
            return True
    print(last_err or "could not enable root TCP password auth", file=sys.stderr)
    return False


if can_connect_app():
    print(" skip: DATABASE_URL already connects")
    sys.exit(0)

if password:
    relax_mysql_password_policy()
if not run_socket_sql(sql):
    sys.exit(1)
if user == "root" and password and not ensure_root_tcp_password_auth():
    if can_connect_app():
        print(" warn: root TCP password sync failed but DATABASE_URL connects")
    else:
        sys.exit(1)
elif user and user != "root" and password and not can_connect_app():
    user_sql = user.replace("'", "''")
    pass_sql = password.replace("\\", "\\\\").replace("'", "''")
    for h in hosts:
        h_sql = h.replace("'", "''")
        run_socket_sql(
            f"ALTER USER '{user_sql}'@'{h_sql}' IDENTIFIED BY '{pass_sql}';"
        )
    run_socket_sql("FLUSH PRIVILEGES;")
    if not can_connect_app():
        print("auth fix failed: app user password still invalid", file=sys.stderr)
        sys.exit(1)
if user == "root":
    print(f" ensured database {db} (root via DATABASE_URL)")
else:
    print(f" ensured {user}@({','.join(hosts)}) on {db}")
sys.exit(0)
PY
  then
    log_step "数据库：MySQL 用户与库权限已就绪"
  else
    log "错误: 无法通过 root socket 创建/更新 MySQL 用户，请手工执行:"
    log "  请确认 MySQL root 可连且 ${ENV_FILE} 中 DATABASE_URL 账号密码正确（如 root:密码@127.0.0.1）"
    return 1
  fi
}

# 静默检测 DATABASE_URL 是否已可连（成功=0；非 MySQL/无配置=0；无法连=1）
deploy_mysql_can_connect_quiet() {
  [[ -f "${ENV_FILE}" ]] || return 0
  local dburl
  dburl="$(deploy_read_database_url_from_file "${ENV_FILE}")"
  [[ -n "${dburl}" ]] || return 0
  if ! printf '%s' "${dburl}" | grep -qi 'mysql'; then
    return 0
  fi
  local py=""
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    py="${VENV_DIR}/bin/python"
  elif python3 -c "import pymysql" 2>/dev/null; then
    py="python3"
  else
    return 1
  fi
  local project_root
  project_root="$(resolve_project_root)"
  DEPLOY_DBURL="${dburl}" PROJECT_ROOT="${project_root}" PYTHONPATH="${project_root}" \
    "${py}" - <<'PY' >/dev/null 2>&1
import os
import sys

sys.path.insert(0, os.environ.get("PROJECT_ROOT", ""))
from labelu.internal.common.mysql_url import pymysql_connect_kwargs

import pymysql

url = os.environ.get("DEPLOY_DBURL", "")
if not url.lower().startswith(("mysql://", "mysql+pymysql://", "mysql+mysqldb://")):
    sys.exit(0)
conn = pymysql.connect(**pymysql_connect_kwargs(url))
conn.close()
sys.exit(0)
PY
}

# 用 venv 校验 .env 中 DATABASE_URL 能否连接（核心步骤，失败应中止）
deploy_verify_mysql_connection() {
  [[ -f "${ENV_FILE}" ]] || return 0
  local dburl
  dburl="$(deploy_read_database_url_from_file "${ENV_FILE}")"
  [[ -n "${dburl}" ]] || return 0
  if ! printf '%s' "${dburl}" | grep -qi 'mysql'; then
    return 0
  fi
  [[ -x "${VENV_DIR}/bin/python" ]] || return 0

  log_step "数据库：校验 DATABASE_URL 连接…"
  local project_root
  project_root="$(resolve_project_root)"
  if DEPLOY_DBURL="${dburl}" PROJECT_ROOT="${project_root}" PYTHONPATH="${project_root}" \
    "${VENV_DIR}/bin/python" - <<'PY'
import os
import sys

sys.path.insert(0, os.environ.get("PROJECT_ROOT", ""))
from labelu.internal.common.mysql_url import pymysql_connect_kwargs

import pymysql

url = os.environ.get("DEPLOY_DBURL", "")
if not url.lower().startswith(("mysql://", "mysql+pymysql://", "mysql+mysqldb://")):
    sys.exit(0)

conn = pymysql.connect(**pymysql_connect_kwargs(url))
conn.close()
print("ok")
PY
  then
    log_step "数据库：连接校验通过"
    return 0
  fi
  log "错误: DATABASE_URL 无法连接 MySQL（常见: 用户未创建或密码与 .env 不一致）"
  return 1
}

deploy_media_host_for_unit() {
  if [[ -f "${ENV_FILE}" ]]; then
    local mh
    mh="$(
      { grep -E '^[[:space:]]*MEDIA_HOST=' "${ENV_FILE}" 2>/dev/null || true; } |
        head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^[[:space:]]*//'
    )"
    if [[ -n "${mh}" ]]; then
      printf '%s' "${mh}"
      return 0
    fi
  fi
  if [[ -n "${LABELU_MEDIA_HOST:-}" ]]; then
    printf '%s' "${LABELU_MEDIA_HOST}"
    return 0
  fi
  printf '%s' "${MEDIA_HOST}"
}

deploy_install_min_free_mb() {
  if [[ "${INSTALL_EXTRAS}" == *yolo* ]]; then
    echo 4096
  else
    echo 2048
  fi
}

# 从 .env 读取 DATABASE_URL；若使用 MySQL，则安装系统库与 Python 驱动（幂等）
ensure_database_python_deps() {
  [[ -f "${ENV_FILE}" ]] || return 0
  [[ -x "${VENV_DIR}/bin/pip" ]] || {
    log "警告: 未找到 ${VENV_DIR}/bin/pip，跳过数据库驱动检查"
    return 0
  }

  local dburl
  dburl="$(deploy_read_database_url_from_file "${ENV_FILE}")"

  [[ -n "${dburl}" ]] || return 0
  if ! printf '%s' "${dburl}" | grep -qi 'mysql'; then
    log_step "数据库：未使用 MySQL（或未配置 DATABASE_URL），跳过驱动安装"
    return 0
  fi

  log_step "数据库：检测到 MySQL，安装系统头文件与 Python 驱动…"
  if [[ "${DEPLOY_SKIP_MYSQL_APT:-}" != "1" ]]; then
    if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
      apt-get install -y --no-install-recommends \
        default-libmysqlclient-dev pkg-config
    else
      apt-get install -y -qq --no-install-recommends \
        default-libmysqlclient-dev pkg-config \
        >/dev/null 2>&1 || apt-get install -y -qq --no-install-recommends \
        default-libmysqlclient-dev pkg-config
    fi
  else
    log "已设置 DEPLOY_SKIP_MYSQL_APT=1，跳过 apt 中的 MySQL 开发库"
  fi

  log_step "pip：安装 PyMySQL…"
  if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
    "${VENV_DIR}/bin/pip" install --no-cache-dir "pymysql>=1.1.0"
  else
    "${VENV_DIR}/bin/pip" install -q --no-cache-dir "pymysql>=1.1.0"
  fi
  if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
    if "${VENV_DIR}/bin/pip" install --no-cache-dir "mysqlclient>=2.1.1"; then
      log_step "数据库：mysqlclient 与 PyMySQL 已安装"
    else
      log "提示: mysqlclient 安装失败；可改用 mysql+pymysql:// 连接串"
    fi
  else
    if "${VENV_DIR}/bin/pip" install -q --no-cache-dir "mysqlclient>=2.1.1" 2>/dev/null; then
      log_step "数据库：mysqlclient 与 PyMySQL 已安装"
    else
      log "提示: mysqlclient 编译失败或未安装；请确认 DATABASE_URL 使用 mysql+pymysql://… 或补齐 build-essential / libmysqlclient"
    fi
  fi

  if "${VENV_DIR}/bin/python" -c "import pymysql" 2>/dev/null; then
    log_step "数据库：PyMySQL 导入校验通过"
  else
    log "警告: PyMySQL 导入失败，请检查 venv 与 DATABASE_URL"
  fi
}

# 若仓库 deploy/offline/*.tar.gz 已存在（Git LFS pull 后），同步到部署离线目录
deploy_sync_offline_bundles_from_repo() {
  local project_root="${1:-}"
  [[ -n "${project_root}" ]] || return 0
  local src="${project_root}/deploy/offline"
  local dest="${LABELU_DEPLOY_OFFLINE_DIR}"
  [[ -d "${src}" ]] || return 0
  mkdir -p "${dest}"
  local f
  for f in tpu-mlir.tar.gz sophon-demo.tar.gz sophgo-tpuc_dev-v3.2_191a433358ad.tar.gz; do
    if [[ -f "${src}/${f}" ]] && [[ ! -f "${dest}/${f}" ]]; then
      log_step "离线包：${src}/${f} -> ${dest}/"
      cp -f "${src}/${f}" "${dest}/${f}"
    fi
  done
}

# BM1688 编译用 Python 3.10 venv；勿在 LabelU 3.11 venv 强装 tpu-mlir PyPI
tpu_mlir_stack_ready() {
  [[ -f "${SERVER_TPU_MLIR_ROOT}/envsetup.sh" ]] || return 1
  if [[ -f "${SERVER_TPU_MLIR_ROOT}/install/python/tools/model_transform.py" ]]; then
    return 0
  fi
  local project_root vpy lib
  project_root="$(resolve_project_root)"
  vpy="${SERVER_RUNTIME_DIR}/venv-tpu-mlir/bin/python3"
  lib="${project_root}/scripts/lib/tpu_mlir_pip_repair.sh"
  [[ -x "${vpy}" && -f "${lib}" ]] || return 1
  TPU_MLIR_ROOT="${SERVER_TPU_MLIR_ROOT}" \
    bash -c "source '${lib}'; tpu_mlir_tools_ok '${vpy}'" 2>/dev/null
}

maybe_ensure_tpu_py310_venv() {
  local project_root="$1"
  local ensure="${project_root}/scripts/ensure_tpu_mlir_py310_venv.sh"
  [[ -f "${ensure}" ]] || return 1
  sed -i 's/\r$//' "${ensure}" 2>/dev/null || true
  env VENV_TPU_MLIR="${SERVER_RUNTIME_DIR}/venv-tpu-mlir" \
    TPU_MLIR_ROOT="${SERVER_TPU_MLIR_ROOT}" \
    bash "${ensure}"
}

maybe_bootstrap_tpu_mlir() {
  local project_root="$1"
  if tpu_mlir_stack_ready; then
    log_step "TPU-MLIR：源码树/venv-tpu-mlir 已就绪，跳过 pip 引导"
    return 0
  fi
  if [[ "${LABELU_DEPLOY_RESTART:-}" == "1" ]] && [[ -f "${SERVER_TPU_MLIR_ROOT}/envsetup.sh" ]]; then
    log_step "TPU-MLIR：restart 且已有 envsetup.sh，仅尝试 venv-tpu-mlir（不往 LabelU 3.11 venv 装 tpu-mlir）"
    if maybe_ensure_tpu_py310_venv "${project_root}"; then
      if tpu_mlir_stack_ready; then
        log_step "TPU-MLIR：venv-tpu-mlir 已就绪"
        return 0
      fi
    fi
    deploy_optional_warn "TPU-MLIR venv-tpu-mlir 未完全就绪，BM1688 编译可能需手工: bash scripts/ensure_tpu_mlir_py310_venv.sh"
    return 0
  fi
  local want="${LABELU_BOOTSTRAP_TPU_MLIR:-}"
  if [[ -z "${want}" ]]; then
    if [[ "${INSTALL_EXTRAS}" == *yolo* ]]; then
      want=y
    else
      want=n
    fi
  fi
  case "${want}" in
    n|N|0|false|no)
      log_step "TPU-MLIR：已跳过（设置 LABELU_BOOTSTRAP_TPU_MLIR=n 可显式关闭）"
      return 0
      ;;
  esac
  local script="${project_root}/scripts/tpu_mlir_bootstrap.sh"
  if [[ ! -f "${script}" ]]; then
    if deploy_is_strict; then
      log "错误: 未找到 ${script}"
      exit 1
    fi
    log "警告: 未找到 ${script}，跳过 TPU-MLIR 引导"
    return 0
  fi
  sed -i 's/\r$//' "${script}" 2>/dev/null || true
  export GIT_CLONE_RETRIES="${GIT_CLONE_RETRIES:-8}"
  if deploy_is_strict; then
    deploy_run_required "TPU-MLIR 引导" \
      env LABELU_DEPLOY_STRICT=1 LABELU_DEPLOY_RESTART="${LABELU_DEPLOY_RESTART:-}" \
        VENV_DIR="${VENV_DIR}" VENV_TPU_MLIR="${SERVER_RUNTIME_DIR}/venv-tpu-mlir" \
        TPU_MLIR_ROOT="${SERVER_TPU_MLIR_ROOT}" \
        TPU_TMP_DIR="${SERVER_TPU_TMP}" LABELU_DEPLOY_RESOLVED_BASE="${LABELU_DEPLOY_RESOLVED_BASE}" \
        LABELU_DEPLOY_OFFLINE_DIR="${LABELU_DEPLOY_OFFLINE_DIR}" \
        bash "${script}"
  else
    deploy_run_optional "TPU-MLIR 引导" \
      env LABELU_DEPLOY_STRICT=0 LABELU_DEPLOY_RESTART="${LABELU_DEPLOY_RESTART:-}" \
        VENV_DIR="${VENV_DIR}" VENV_TPU_MLIR="${SERVER_RUNTIME_DIR}/venv-tpu-mlir" \
        TPU_MLIR_ROOT="${SERVER_TPU_MLIR_ROOT}" \
        TPU_TMP_DIR="${SERVER_TPU_TMP}" LABELU_DEPLOY_RESOLVED_BASE="${LABELU_DEPLOY_RESOLVED_BASE}" \
        LABELU_DEPLOY_OFFLINE_DIR="${LABELU_DEPLOY_OFFLINE_DIR}" \
        bash "${script}"
  fi
  if [[ ! -f "${SERVER_TPU_MLIR_ROOT}/envsetup.sh" ]]; then
    if deploy_is_strict; then
      log "错误: TPU-MLIR 未就绪（${SERVER_TPU_MLIR_ROOT}/envsetup.sh）"
      exit 1
    fi
    deploy_optional_warn "TPU-MLIR 未就绪（${SERVER_TPU_MLIR_ROOT}），BM1688 自动编译不可用"
  fi
}

write_and_enable_systemd() {
  local unit_path="/etc/systemd/system/${SYSTEMD_UNIT}"
  local unit_media unit_host unit_port
  unit_media="$(deploy_media_host_for_unit)"
  unit_host="$(labelu_backend_host_for_unit)"
  unit_port="$(labelu_backend_port_for_unit)"
  if [[ -f "${unit_path}" ]]; then
    cp -a "${unit_path}" "${unit_path}.bak.$(date +%Y%m%d%H%M%S)" || true
  fi
  log_step "systemd：写入单元 ${unit_path}（MEDIA_HOST=${unit_media}，监听 ${unit_host}:${unit_port}）"
  cat >"${unit_path}" <<EOF
[Unit]
Description=LabelU (${unit_media})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${SERVER_RUNTIME_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=TMPDIR=${SERVER_TPU_TMP}
EnvironmentFile=-${ENV_FILE}
$(if [[ "${LABELU_ENABLE_TRAINING_SCHEDULER}" == "1" ]]; then printf '%s\n' 'Environment=LABELU_AUTO_TRAINING_API_WATCHDOG_ENABLED=false'; fi)
$(if [[ "${LABELU_ENABLE_TRAINING_WORKER}" == "1" ]]; then printf '%s\n' 'Environment=LABELU_AUTO_TRAINING_WORKER_COMMAND_ENABLED=true'; else printf '%s\n' 'Environment=LABELU_AUTO_TRAINING_WORKER_COMMAND_ENABLED=false'; fi)
$(if [[ "${LABELU_ENABLE_BM1688_WORKER}" == "1" ]]; then printf '%s\n' 'Environment=LABELU_BM1688_DEDICATED_WORKER_ENABLED=true'; else printf '%s\n' 'Environment=LABELU_BM1688_DEDICATED_WORKER_ENABLED=false'; fi)
$(if [[ "${LABELU_ENABLE_EDGE_VALIDATION_WORKER}" == "1" ]]; then printf '%s\n' 'Environment=YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED=false'; fi)
$(if [[ "${LABELU_ENABLE_EXPERIMENT_DATASET_WORKER}" == "1" ]]; then printf '%s\n' 'Environment=EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=true'; else printf '%s\n' 'Environment=EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=false'; fi)
ExecStart=${VENV_DIR}/bin/labelu --static-only --host ${unit_host} --port ${unit_port} --media-host ${unit_media}
Restart=on-failure
RestartSec=5
TimeoutStopSec=120
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${SYSTEMD_UNIT}"
  write_and_enable_training_scheduler_systemd
  write_and_enable_training_worker_systemd
  write_and_enable_bm1688_worker_systemd
  write_and_enable_edge_validation_worker_systemd
  write_and_enable_experiment_dataset_worker_systemd
  log_step "systemd：已 daemon-reload 并 enable ${SYSTEMD_UNIT}"
}

write_and_enable_training_scheduler_systemd() {
  local unit_path="/etc/systemd/system/${SCHEDULER_SYSTEMD_UNIT}"
  if [[ "${LABELU_ENABLE_TRAINING_SCHEDULER}" != "1" ]]; then
    systemctl disable --now "${SCHEDULER_SYSTEMD_UNIT}" 2>/dev/null || true
    rm -f "${unit_path}"
    return 0
  fi
  if [[ -f "${unit_path}" ]]; then
    cp -a "${unit_path}" "${unit_path}.bak.$(date +%Y%m%d%H%M%S)" || true
  fi
  local interval="${LABELU_TRAINING_SCHEDULER_INTERVAL:-5}"
  log_step "systemd：写入独立训练调度器 ${unit_path}（interval=${interval}s）"
  cat >"${unit_path}" <<EOF
[Unit]
Description=7GAI training scheduler
After=network-online.target ${SYSTEMD_UNIT}
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${SERVER_RUNTIME_DIR}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -m labelu.scripts.training_scheduler --interval ${interval}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${SCHEDULER_SYSTEMD_UNIT}"
}

write_and_enable_training_worker_systemd() {
  local unit_path="/etc/systemd/system/${WORKER_SYSTEMD_UNIT}"
  if [[ "${LABELU_ENABLE_TRAINING_WORKER}" != "1" ]]; then
    systemctl disable --now "${WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
    rm -f "${unit_path}"
    return 0
  fi
  if ! deploy_training_worker_images_valid; then
    log "错误: LABELU_ENABLE_TRAINING_WORKER=1 但未配置合法 LABELU_TRAINING_WORKER_ALLOWED_IMAGES"
    return 1
  fi
  if [[ -f "${unit_path}" ]]; then
    cp -a "${unit_path}" "${unit_path}.bak.$(date +%Y%m%d%H%M%S)" || true
  fi
  local interval="${LABELU_TRAINING_WORKER_INTERVAL:-5}"
  local lease="${LABELU_TRAINING_WORKER_LEASE_SECONDS:-3600}"
  local limit="${LABELU_TRAINING_WORKER_BATCH_LIMIT:-20}"
  local attempts="${LABELU_TRAINING_WORKER_MAX_ATTEMPTS:-3}"
  local allow_args="" image
  IFS=',' read -r -a worker_images <<<"${LABELU_TRAINING_WORKER_ALLOWED_IMAGES}"
  for image in "${worker_images[@]}"; do
    image="$(printf '%s' "${image}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    allow_args+=" --allow-image ${image}"
  done
  log_step "systemd：写入独立训练 Worker ${unit_path}（interval=${interval}s）"
  cat >"${unit_path}" <<EOF
[Unit]
Description=7GAI training worker
After=network-online.target ${SCHEDULER_SYSTEMD_UNIT}
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${SERVER_RUNTIME_DIR}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-${ENV_FILE}
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${SERVER_RUNTIME_DIR} ${SERVER_APPDATA_DIR} ${SERVER_TPU_TMP}
ExecStart=${VENV_DIR}/bin/python -m labelu.scripts.training_worker --outbox --execute-container${allow_args} --interval ${interval} --lease-seconds ${lease} --batch-limit ${limit} --max-attempts ${attempts}
Restart=on-failure
RestartSec=5
TimeoutStopSec=120
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${WORKER_SYSTEMD_UNIT}"
}

write_and_enable_bm1688_worker_systemd() {
  local unit_path="/etc/systemd/system/${BM1688_WORKER_SYSTEMD_UNIT}"
  if [[ "${LABELU_ENABLE_BM1688_WORKER}" != "1" ]]; then
    systemctl disable --now "${BM1688_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
    rm -f "${unit_path}"
    return 0
  fi
  if [[ ! -f "${SERVER_PROJECT_DIR}/labelu/scripts/bm1688_worker.py" ]]; then
    log "错误: LABELU_ENABLE_BM1688_WORKER=1 但缺少 labelu/scripts/bm1688_worker.py"
    return 1
  fi
  if ! deploy_bm1688_worker_values_valid; then
    log "错误: BM1688 Worker interval、lease-seconds、batch-limit 必须为正数"
    return 1
  fi
  if [[ -f "${unit_path}" ]]; then
    cp -a "${unit_path}" "${unit_path}.bak.$(date +%Y%m%d%H%M%S)" || true
  fi
  local interval="${LABELU_BM1688_WORKER_INTERVAL:-2}"
  local lease="${LABELU_BM1688_WORKER_LEASE_SECONDS:-300}"
  local limit="${LABELU_BM1688_WORKER_BATCH_LIMIT:-1}"
  local memory_max="${LABELU_BM1688_WORKER_MEMORY_MAX:-16G}"
  local cpu_quota="${LABELU_BM1688_WORKER_CPU_QUOTA:-400%}"
  local tasks_max="${LABELU_BM1688_WORKER_TASKS_MAX:-1024}"
  log_step "systemd：写入 BM1688 独立 Worker ${unit_path}（interval=${interval}s）"
  cat >"${unit_path}" <<EOF
[Unit]
Description=7GAI BM1688 compile worker
After=network-online.target ${SYSTEMD_UNIT} ${SCHEDULER_SYSTEMD_UNIT}
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${SERVER_RUNTIME_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=TMPDIR=${SERVER_TPU_TMP}
EnvironmentFile=-${ENV_FILE}
Environment=LABELU_BM1688_DEDICATED_WORKER_ENABLED=true
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
MemoryMax=${memory_max}
CPUQuota=${cpu_quota}
TasksMax=${tasks_max}
ReadWritePaths=${SERVER_RUNTIME_DIR} ${SERVER_APPDATA_DIR} ${SERVER_TPU_TMP} ${SERVER_TPU_MLIR_ROOT}
ExecStart=${VENV_DIR}/bin/python -m labelu.scripts.bm1688_worker --interval ${interval} --lease-seconds ${lease} --limit ${limit}
Restart=on-failure
RestartSec=5
TimeoutStopSec=120
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${BM1688_WORKER_SYSTEMD_UNIT}"
}

write_and_enable_edge_validation_worker_systemd() {
  local unit_path="/etc/systemd/system/${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}"
  if [[ "${LABELU_ENABLE_EDGE_VALIDATION_WORKER}" != "1" ]]; then
    systemctl disable --now "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
    rm -f "${unit_path}"
    return 0
  fi
  if [[ ! -f "${SERVER_PROJECT_DIR}/labelu/scripts/edge_validation_worker.py" ]]; then
    log "错误: LABELU_ENABLE_EDGE_VALIDATION_WORKER=1 但缺少 labelu/scripts/edge_validation_worker.py"
    return 1
  fi
  if ! deploy_edge_validation_worker_values_valid; then
    log "错误: 边端验证 Worker interval、batch-limit 必须为正数"
    return 1
  fi
  if [[ -f "${unit_path}" ]]; then
    cp -a "${unit_path}" "${unit_path}.bak.$(date +%Y%m%d%H%M%S)" || true
  fi
  local interval="${LABELU_EDGE_VALIDATION_WORKER_INTERVAL:-5}"
  local limit="${LABELU_EDGE_VALIDATION_WORKER_BATCH_LIMIT:-1}"
  log_step "systemd：写入独立边端验证 Worker ${unit_path}（interval=${interval}s）"
  cat >"${unit_path}" <<EOF
[Unit]
Description=7GAI edge validation worker
After=network-online.target ${SYSTEMD_UNIT}
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${SERVER_RUNTIME_DIR}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-${ENV_FILE}
Environment=YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED=true
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${SERVER_RUNTIME_DIR} ${SERVER_APPDATA_DIR} ${SERVER_TPU_TMP}
ExecStart=${VENV_DIR}/bin/python -m labelu.scripts.edge_validation_worker --interval ${interval} --limit ${limit}
Restart=on-failure
RestartSec=5
TimeoutStopSec=120
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}"
}

write_and_enable_experiment_dataset_worker_systemd() {
  local unit_path="/etc/systemd/system/${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"
  if [[ "${LABELU_ENABLE_EXPERIMENT_DATASET_WORKER}" != "1" ]]; then
    systemctl disable --now "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
    rm -f "${unit_path}"
    return 0
  fi
  if [[ ! -f "${SERVER_PROJECT_DIR}/labelu/scripts/experiment_dataset_import_worker.py" ]]; then
    log "错误: LABELU_ENABLE_EXPERIMENT_DATASET_WORKER=1 但缺少 labelu/scripts/experiment_dataset_import_worker.py"
    return 1
  fi
  if ! deploy_experiment_dataset_worker_values_valid; then
    log "错误: S3 回流 Worker interval、lease-seconds、batch-size 必须为正数"
    return 1
  fi
  if [[ -f "${unit_path}" ]]; then
    cp -a "${unit_path}" "${unit_path}.bak.$(date +%Y%m%d%H%M%S)" || true
  fi
  local interval="${EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS:-5}"
  local lease="${EXPERIMENT_DATASET_WORKER_LEASE_SECONDS:-300}"
  local limit="${EXPERIMENT_DATASET_WORKER_BATCH_SIZE:-1}"
  log_step "systemd：写入第三方 S3 回流 Worker ${unit_path}（interval=${interval}s）"
  cat >"${unit_path}" <<EOF
[Unit]
Description=7GAI third-party S3 experiment dataset import worker
After=network-online.target ${SYSTEMD_UNIT}
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${SERVER_RUNTIME_DIR}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-${ENV_FILE}
Environment=EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=true
Environment=EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS=${interval}
Environment=EXPERIMENT_DATASET_WORKER_LEASE_SECONDS=${lease}
Environment=EXPERIMENT_DATASET_WORKER_BATCH_SIZE=${limit}
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${SERVER_RUNTIME_DIR} ${SERVER_APPDATA_DIR}
ExecStartPre=${VENV_DIR}/bin/python -m labelu.scripts.experiment_dataset_import_worker --preflight
ExecStart=${VENV_DIR}/bin/python -m labelu.scripts.experiment_dataset_import_worker --limit ${limit}
Restart=on-failure
RestartPreventExitStatus=78
RestartSec=5
TimeoutStopSec=120
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"
}

is_installed() {
  [[ -f "/etc/systemd/system/${SYSTEMD_UNIT}" ]] && [[ -x "${VENV_DIR}/bin/labelu" ]]
}

cmd_install() {
  need_root
  detect_ubuntu_jammy
  local project_root
  project_root="$(resolve_project_root)"
  deploy_mark_finalize_trap "${project_root}"
  log "========================================"
  log "  LabelU  完整安装 / 首次部署"
  log "========================================"
  log "提示: 需要完整 apt/pip 输出时: DEPLOY_VERBOSE=1 sudo $0 start"
  log "部署根 BASE: ${LABELU_DEPLOY_RESOLVED_BASE}"
  log "使用项目根: ${project_root}"
  log "运行与媒体目录: ${SERVER_RUNTIME_DIR}"
  log "应用数据目录: ${SERVER_APPDATA_DIR}"
  log "MEDIA_HOST: ${MEDIA_HOST}"
  log "监听端口: ${LABELU_PORT}"
  if [[ "${LABELU_ENABLE_NGINX_FRONTEND}" == "1" ]]; then
    log "Nginx 前端分发: 开启（外部 ${LABELU_PORT} → 后端 127.0.0.1:${LABELU_BACKEND_PORT}）"
  else
    log "Nginx 前端分发: 关闭（LabelU 直接监听 ${LABELU_PORT}）"
  fi

  log_step "预检：根分区空间（不足时自动多轮安全清理）"
  deploy_ensure_rootfs_space "$(deploy_install_min_free_mb)"
  log_step "系统：安装 apt 基础包"
  apt_install_base
  log_step "系统：确保 Python 3.11"
  ensure_python311
  log_step "目录：创建运行/代码/数据/临时路径"
  mkdir -p "${SERVER_RUNTIME_DIR}" "${SERVER_PROJECT_DIR}" "${SERVER_APPDATA_DIR}" "${SERVER_TPU_TMP}"
  migrate_sqlite_to_appdata_if_needed
  write_env_file
  deploy_normalize_database_url_in_env
  ensure_mysql_app_user_from_env || {
    log "错误: MySQL 用户/库初始化失败，中止安装"
    exit 1
  }
  create_venv_and_install_project "${project_root}"
  ensure_database_python_deps
  deploy_verify_mysql_connection || {
    log "错误: MySQL 连接校验失败，请修正 ${ENV_FILE} 中 DATABASE_URL 后执行: sudo bash deploy.sh restart"
    exit 1
  }
  DEPLOY_OPTIONAL_WARNINGS=()
  deploy_sync_offline_bundles_from_repo "${project_root}"
  maybe_bootstrap_tpu_mlir "${project_root}"
  maybe_bootstrap_sophon_demo "${project_root}"
  maybe_bootstrap_bm1688_docker "${project_root}"
  seed_bm1688_compiler_config
  write_and_enable_systemd
  if ! deploy_run_migrate "${project_root}"; then
    deploy_optional_warn "数据库迁移失败，尝试再次确保 MySQL 用户后重试迁移"
    ensure_mysql_app_user_from_env || true
    deploy_verify_mysql_connection || exit 1
    deploy_run_migrate "${project_root}" || exit 1
  fi
  deploy_try_frontend_build "${project_root}"
  if ! labelu_nginx_enabled; then
    deploy_disable_nginx_frontend
  fi
  deploy_ensure_service_running
  deploy_configure_nginx_frontend "${project_root}" || true
  deploy_run_doctor
  deploy_finalize "${project_root}"
  log "应用数据 (BASE_DATA_DIR): ${SERVER_APPDATA_DIR}"
  log "媒体与 venv (MEDIA_ROOT): ${SERVER_RUNTIME_DIR}"
  systemctl --no-pager -l status "${SYSTEMD_UNIT}" || true
}

cmd_start() {
  need_root
  if ! is_installed; then
    log "未检测到已安装服务，执行首次安装…"
    cmd_install
    return 0
  fi
  log_step "服务：启动 ${SYSTEMD_UNIT}"
  systemctl start "${SYSTEMD_UNIT}"
  if [[ "${LABELU_ENABLE_TRAINING_SCHEDULER}" == "1" ]]; then
    systemctl start "${SCHEDULER_SYSTEMD_UNIT}" || \
      deploy_optional_warn "${SCHEDULER_SYSTEMD_UNIT} 启动失败，请检查 journalctl -u ${SCHEDULER_SYSTEMD_UNIT}"
  fi
  if [[ "${LABELU_ENABLE_TRAINING_WORKER}" == "1" ]]; then
    systemctl start "${WORKER_SYSTEMD_UNIT}" || \
      deploy_optional_warn "${WORKER_SYSTEMD_UNIT} 启动失败，请检查 journalctl -u ${WORKER_SYSTEMD_UNIT}"
  fi
  if [[ "${LABELU_ENABLE_BM1688_WORKER}" == "1" ]]; then
    systemctl start "${BM1688_WORKER_SYSTEMD_UNIT}" || \
      deploy_optional_warn "${BM1688_WORKER_SYSTEMD_UNIT} 启动失败，请检查 journalctl -u ${BM1688_WORKER_SYSTEMD_UNIT}"
  fi
  if [[ "${LABELU_ENABLE_EDGE_VALIDATION_WORKER}" == "1" ]]; then
    systemctl start "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}" || \
      deploy_optional_warn "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT} 启动失败，请检查 journalctl -u ${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}"
  fi
  if [[ "${LABELU_ENABLE_EXPERIMENT_DATASET_WORKER}" == "1" ]]; then
    systemctl start "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}" || \
      deploy_optional_warn "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT} 启动失败，请检查 journalctl -u ${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"
  fi
  log "已启动 ${SYSTEMD_UNIT}  访问: ${MEDIA_HOST}"
  systemctl --no-pager -l status "${SYSTEMD_UNIT}" || true
}

cmd_stop() {
  need_root
  systemctl stop "${SCHEDULER_SYSTEMD_UNIT}" 2>/dev/null || true
  systemctl stop "${WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
  systemctl stop "${BM1688_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
  systemctl stop "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
  systemctl stop "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
  systemctl stop "${SYSTEMD_UNIT}" 2>/dev/null || log "服务当前未运行或尚未安装"
  log "已停止 ${SYSTEMD_UNIT}"
}

cmd_restart() {
  need_root
  if ! is_installed; then
    log "尚未安装，改为执行安装并启动…"
    cmd_install
    return 0
  fi
  log "========================================"
  log "  LabelU  重启（同步 .env / systemd）"
  log "========================================"
  local project_root
  project_root="$(resolve_project_root)"
  deploy_mark_finalize_trap "${project_root}"
  LABELU_DEPLOY_OVERWRITE_ENV=0 write_env_file
  deploy_normalize_database_url_in_env
  load_deploy_media_hints_from_envfile
  local mh
  mh="$(deploy_read_media_host_from_file "${ENV_FILE}" 2>/dev/null || true)"
  [[ -n "${mh}" ]] && MEDIA_HOST="${mh}"
  log "MEDIA_HOST=${MEDIA_HOST}  DEPLOY_VERBOSE=${DEPLOY_VERBOSE}（设为 1 可显示完整 apt/pip）"
  write_and_enable_systemd
  export LABELU_DEPLOY_RESTART=1
  if deploy_mysql_can_connect_quiet; then
    log_step "数据库：连接已可用，跳过 MySQL 用户/密码同步"
  else
    ensure_mysql_app_user_from_env || {
      log "错误: MySQL 用户/库同步失败"
      exit 1
    }
  fi
  deploy_verify_mysql_connection || {
    log "错误: MySQL 连接校验失败，请检查 ${ENV_FILE} 中 DATABASE_URL"
    log "  若库已正常：可先 LABELU_SKIP_MYSQL_ENSURE_USER=1 sudo bash deploy.sh restart"
    exit 1
  }
  ensure_database_python_deps
  maybe_bootstrap_tpu_mlir "${project_root}"
  maybe_bootstrap_sophon_demo "${project_root}"
  maybe_bootstrap_bm1688_docker "${project_root}"
  seed_bm1688_compiler_config
  DEPLOY_OPTIONAL_WARNINGS=()
  if ! deploy_run_migrate "${project_root}"; then
    deploy_optional_warn "数据库迁移失败，尝试再次确保 MySQL 用户后重试迁移"
    ensure_mysql_app_user_from_env || true
    deploy_verify_mysql_connection || exit 1
    deploy_run_migrate "${project_root}" || exit 1
  fi
  deploy_ensure_service_running
  deploy_configure_nginx_frontend "${project_root}" || true
  deploy_run_doctor
  deploy_finalize "${project_root}"
  systemctl --no-pager -l status "${SYSTEMD_UNIT}" || true
}

deploy_try_frontend_build() {
  local project_root="$1"
  local kit="${project_root}/frontend/labelU-Kit"
  if [[ "${LABELU_SKIP_FRONTEND_BUILD:-}" == "1" ]]; then
    log "跳过前端构建（LABELU_SKIP_FRONTEND_BUILD=1）"
    return 0
  fi
  if [[ ! -f "${kit}/package.json" ]] || [[ ! -f "${kit}/apps/frontend/package.json" ]]; then
    log "未在仓库中发现 frontend/labelU-Kit，尝试在服务器克隆官方 Kit（仅首次较慢）…"
    log "  若 GitHub 受限可: export LABELU_KIT_GH_PROXY=https://ghproxy.net/"
    bash -c "cd '${project_root}' && '${VENV_DIR}/bin/labelu' frontend-clone" || {
      log "错误: labelu frontend-clone 失败，无法构建主站 SPA。"
      log "  方案 A（服务器）: 配置代理后重试 deploy.sh update"
      log "  方案 B（开发机）: bash scripts/vendor_kit_into_repo.sh && git push frontend/labelU-Kit"
      log "  方案 C: 若 statics 已有旧构建产物，可 LABELU_SKIP_FRONTEND_BUILD=1 跳过"
      exit 1
    }
  fi
  if [[ ! -f "${kit}/package.json" ]] || [[ ! -f "${kit}/apps/frontend/package.json" ]]; then
    log "错误: frontend-clone 后仍缺少 labelU-Kit，请检查网络与磁盘权限。"
    exit 1
  fi
  deploy_ensure_node_pnpm || {
    log "错误: 需要 node + pnpm 以构建前端（apt 已装 nodejs/npm，请检查 corepack 或 npm install -g pnpm）"
    exit 1
  }
  deploy_pin_pnpm || {
    log "错误: 无法锁定 pnpm 9.15.0"
    exit 1
  }
  local fb_log="${SERVER_RUNTIME_DIR}/logs/frontend-build.log"
  mkdir -p "$(dirname "${fb_log}")"
  log_step "前端：labelu frontend-build（日志: ${fb_log}；DEPLOY_VERBOSE=1 时同步打印到终端）"
  local fb_rc=0
  if [[ "${DEPLOY_VERBOSE}" == "1" ]]; then
    bash -c "export HUSKY=0 CI=true COREPACK_ENABLE_DOWNLOAD_PROMPT=0; \
      corepack prepare pnpm@9.15.0 --activate 2>/dev/null || true; \
      cd '${project_root}' && LABELU_PROJECT_ROOT='${project_root}' PYTHONPATH='${project_root}' \
      '${VENV_DIR}/bin/labelu' frontend-build" || fb_rc=$?
  else
    bash -c "export HUSKY=0 CI=true COREPACK_ENABLE_DOWNLOAD_PROMPT=0; \
      corepack prepare pnpm@9.15.0 --activate 2>/dev/null || true; \
      cd '${project_root}' && LABELU_PROJECT_ROOT='${project_root}' PYTHONPATH='${project_root}' \
      '${VENV_DIR}/bin/labelu' frontend-build" >>"${fb_log}" 2>&1 || fb_rc=$?
    if [[ "${fb_rc}" -eq 0 ]]; then
      log "前端构建完成（Vite/antd 告警已写入日志，不在终端刷屏）"
      tail -n 8 "${fb_log}" 2>/dev/null | while IFS= read -r line; do
        log "  ${line}"
      done || true
    else
      log "错误: labelu frontend-build 失败，最近日志:"
      tail -n 40 "${fb_log}" 2>/dev/null | while IFS= read -r line; do
        log "  ${line}"
      done || true
    fi
  fi
  if [[ "${fb_rc}" -ne 0 ]]; then
    log "错误: labelu frontend-build 失败，完整日志: ${fb_log}"
    exit 1
  fi
}

cmd_frontend_build() {
  need_root
  local project_root
  project_root="$(resolve_project_root)"
  deploy_try_frontend_build "${project_root}"
}

cmd_nginx_disable() {
  need_root
  local project_root
  project_root="$(resolve_project_root)"
  LABELU_ENABLE_NGINX_FRONTEND=0
  deploy_disable_nginx_frontend
  write_and_enable_systemd
  deploy_ensure_service_running
  deploy_finalize "${project_root}"
  log "已回退为旧模式：LabelU 直接监听 ${LABELU_PORT}，访问地址仍为 $(deploy_media_host_for_unit)"
  systemctl --no-pager -l status "${SYSTEMD_UNIT}" || true
}

cmd_update() {
  need_root
  if ! is_installed; then
    log "尚未安装，改为执行完整安装…"
    cmd_install
    return 0
  fi
  local project_root old_head new_head changed_paths=()
  project_root="$(resolve_project_root)"
  deploy_mark_finalize_trap "${project_root}"
  log "========================================"
  log "  LabelU  发版更新（pull + pip + migrate + restart）"
  log "========================================"
  log "仓库: ${project_root}"
  log "运行目录: ${SERVER_RUNTIME_DIR}"
  write_and_enable_systemd
  if ! labelu_nginx_enabled; then
    deploy_disable_nginx_frontend
  fi
  export LABELU_ENV_FILE="${ENV_FILE}"
  export LABELU_DEPLOY_RUNTIME_DIR="${SERVER_RUNTIME_DIR}"
  export VENV_DIR
  export LABELU_PORT
  export LABELU_SKIP_SERVICE_RESTART=1
  old_head="$(git -C "${project_root}" rev-parse HEAD 2>/dev/null || true)"
  bash "${project_root}/scripts/labelu_server_update.sh" pull-only
  new_head="$(git -C "${project_root}" rev-parse HEAD 2>/dev/null || true)"
  if [[ -n "${old_head}" && -n "${new_head}" && "${old_head}" != "${new_head}" ]]; then
    mapfile -t changed_paths < <(git -C "${project_root}" diff --no-renames --name-only "${old_head}..${new_head}" --)
  fi
  if [[ "${LABELU_DISABLE_FRONTEND_ONLY_FAST_DEPLOY:-}" != "1" ]] &&
    labelu_deploy_changes_are_frontend_only "${changed_paths[@]}"; then
    log "检测到本次更新仅包含主站前端变更，跳过 pip、数据库迁移和后端重启。"
    printf '%s\n' "${changed_paths[@]}" | sed 's/^/[labelu-deploy]   - /'
    DEPLOY_OPTIONAL_WARNINGS=()
    deploy_try_frontend_build "${project_root}"
    deploy_reload_nginx_frontend_only "${project_root}"
    deploy_run_doctor
    deploy_finalize "${project_root}"
    return 0
  fi
  if [[ "${old_head}" == "${new_head}" ]]; then
    log "Git 代码无新增变更，执行完整部署检查路径。"
  elif [[ "${LABELU_DISABLE_FRONTEND_ONLY_FAST_DEPLOY:-}" == "1" ]]; then
    log "已关闭纯前端快速发版路径，执行完整部署。"
  else
    log "检测到后端、部署或混合变更，执行完整部署。"
  fi
  export LABELU_SKIP_GIT_PULL=1
  bash "${project_root}/scripts/labelu_server_update.sh" all
  export LABELU_DEPLOY_RESTART=1
  DEPLOY_OPTIONAL_WARNINGS=()
  deploy_sync_offline_bundles_from_repo "${project_root}"
  maybe_bootstrap_tpu_mlir "${project_root}"
  maybe_bootstrap_sophon_demo "${project_root}"
  maybe_bootstrap_bm1688_docker "${project_root}"
  seed_bm1688_compiler_config
  deploy_try_frontend_build "${project_root}"
  if ! labelu_nginx_enabled; then
    deploy_disable_nginx_frontend
  fi
  deploy_ensure_service_running
  deploy_configure_nginx_frontend "${project_root}" || true
  deploy_run_doctor
  deploy_finalize "${project_root}"
}

# 清空本机业务数据与虚拟环境后重新安装（不删代码仓库）
cmd_reset_clean() {
  need_root

  log "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  log "!!! reset-clean：将删除本机 LabelU 业务数据与 venv   !!!"
  log "!!! 不删除: ${SERVER_PROJECT_DIR}（代码）            !!!"
  log "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"

  systemctl stop "${SCHEDULER_SYSTEMD_UNIT}" 2>/dev/null || true
  systemctl stop "${WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
  systemctl stop "${BM1688_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
  systemctl stop "${EDGE_VALIDATION_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
  systemctl stop "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}" 2>/dev/null || true
  systemctl stop "${SYSTEMD_UNIT}" 2>/dev/null || log "服务当前未运行，继续清理"
  log_step "预检：reset-clean 前释放磁盘（安全清理）"
  deploy_ensure_rootfs_space "$(deploy_install_min_free_mb)" soft || true

  if [[ -f "${ENV_FILE}" ]]; then
    local bak="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    cp -a "${ENV_FILE}" "${bak}"
    log_step "已备份 .env → ${bak}（请从备份中找回 DATABASE_URL / 公网 MEDIA 等手工配置）"
    reset_clean_drop_mysql_database "${bak}" || true
    rm -f "${ENV_FILE}"
  fi

  log_step "清理：应用数据目录 ${SERVER_APPDATA_DIR}"
  if [[ -d "${SERVER_APPDATA_DIR}" ]]; then
    find "${SERVER_APPDATA_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  fi

  log_step "清理：媒体目录下的上传、导出、YOLO 工作目录等"
  rm -rf \
    "${SERVER_RUNTIME_DIR}/upload" \
    "${SERVER_RUNTIME_DIR}/export" \
    "${SERVER_RUNTIME_DIR}/.labelu_upload_staging" \
    "${SERVER_RUNTIME_DIR}/yolo_train" \
    "${SERVER_RUNTIME_DIR}/yolo_bm1688_exports" \
    "${SERVER_RUNTIME_DIR}/.image_pipeline_staging" \
    2>/dev/null || true
  rm -f "${SERVER_RUNTIME_DIR}/labelu.sqlite" 2>/dev/null || true

  log_step "清理：大临时目录 ${SERVER_TPU_TMP}"
  if [[ -d "${SERVER_TPU_TMP}" ]]; then
    find "${SERVER_TPU_TMP}" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
  fi

  log_step "清理：虚拟环境 ${VENV_DIR}"
  rm -rf "${VENV_DIR}"

  log "将执行完整安装（新生成密钥、重装依赖）…"
  cmd_install
}

# ----------------------------------------------------------------------------- main
case "${1:-}" in
  start)        cmd_start ;;
  stop)         cmd_stop ;;
  restart|preserve) cmd_restart ;;
  update)            cmd_update ;;
  frontend-build)    cmd_frontend_build ;;
  nginx-disable)     cmd_nginx_disable ;;
  reset-clean)       cmd_reset_clean ;;
  *)                 usage ;;
esac
