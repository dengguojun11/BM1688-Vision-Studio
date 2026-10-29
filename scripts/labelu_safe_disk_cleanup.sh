#!/usr/bin/env bash
# LabelU 服务器安全磁盘清理（仅删可重建/日志/失败残留；业务与 venv 永不触碰）
#
# 用法:
#   bash scripts/labelu_safe_disk_cleanup.sh              # 仅检查 + 模拟（默认）
#   bash scripts/labelu_safe_disk_cleanup.sh --apply      # 真正执行可删项
#   bash scripts/labelu_safe_disk_cleanup.sh --apply --migrate-docker
#
# 环境变量（可选）:
#   LABELU_BASE=/data/labeIU
#   LABELU_MEDIA=/data/labeIU/labeIuMedia
set -euo pipefail

APPLY=0
MIGRATE_DOCKER=0
LABELU_BASE="${LABELU_BASE:-/data/labeIU}"
LABELU_MEDIA="${LABELU_MEDIA:-${LABELU_BASE}/labeIuMedia}"
LABELU_PROJECT="${LABELU_PROJECT:-${LABELU_BASE}/labeIuproject/labeIU}"
TPU_MLIR_ROOT="${TPU_MLIR_ROOT:-${LABELU_BASE}/tpu-mlir}"
DOCKER_DATA_ROOT="${DOCKER_DATA_ROOT:-${LABELU_BASE}/docker-data}"
MIN_ROOT_AVAIL_GB="${MIN_ROOT_AVAIL_GB:-10}"
MIN_DATA_AVAIL_GB="${MIN_DATA_AVAIL_GB:-20}"

# ---------- 日志 ----------
log() { echo "[labelu-cleanup] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# ---------- 参数 ----------
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --migrate-docker) MIGRATE_DOCKER=1 ;;
    --dry-run) APPLY=0 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) die "未知参数: $arg（支持 --dry-run | --apply | --migrate-docker）" ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || log "WARN: 非 root；部分项（journal、containerd ingest）可能跳过"

# ---------- 绝对保护路径（前缀匹配即禁止删除）----------
readonly -a PROTECTED_PREFIXES=(
  "${LABELU_PROJECT}"
  "${TPU_MLIR_ROOT}"
  "${LABELU_MEDIA}/venv"
  "${LABELU_MEDIA}/venv-tpu-mlir"
  "${LABELU_MEDIA}/yolo_bm1688_exports"
  "${LABELU_MEDIA}/upload"
  "${LABELU_BASE}/labelu_appdata"
  "/etc"
  "/usr"
  "/bin"
  "/sbin"
  "/lib"
  "/lib64"
  "/boot"
  "/home"
)

# ---------- 允许删除的固定路径（必须完全相等或明确子路径）----------
# 格式: "path|reason"
readonly -a ALLOWED_EXACT=(
  "${LABELU_MEDIA}/wget-log|wget 日志"
  "${LABELU_MEDIA}/cache|空缓存目录（仅删目录内文件）"
  "${LABELU_MEDIA}/tmp|空临时目录（仅删目录内文件）"
)

readonly -a ALLOWED_DIR_CONTENTS_ONLY=(
  "${LABELU_MEDIA}/.pip_build/cache|pip 构建缓存"
  "${LABELU_MEDIA}/.pip_build/tmp|pip 临时文件"
  "/root/.cache/pip|root pip 缓存"
)

REMOVED_BYTES=0
SKIPPED=0
ACTIONS=0

human_size() {
  local n="${1:-0}"
  if [[ "$n" -ge 1073741824 ]]; then
    echo "$(( n / 1073741824 ))G"
  elif [[ "$n" -ge 1048576 ]]; then
    echo "$(( n / 1048576 ))M"
  elif [[ "$n" -ge 1024 ]]; then
    echo "$(( n / 1024 ))K"
  else
    echo "${n}B"
  fi
}

is_protected() {
  local target="$1"
  local p norm
  norm="$(readlink -f "$target" 2>/dev/null || realpath -m "$target" 2>/dev/null || echo "$target")"
  for p in "${PROTECTED_PREFIXES[@]}"; do
    local pn
    pn="$(readlink -f "$p" 2>/dev/null || realpath -m "$p" 2>/dev/null || echo "$p")"
    if [[ "$norm" == "$pn" || "$norm" == "$pn/"* ]]; then
      return 0
    fi
  done
  return 1
}

avail_gb() {
  local mp="$1"
  df -BG "$mp" 2>/dev/null | awk 'NR==2 {gsub(/G/,"",$4); print $4}' || echo 0
}

report_disk() {
  log "=== 磁盘 ==="
  df -h / /data "${LABELU_MEDIA}" 2>/dev/null | sed 's/^/[labelu-cleanup] /' >&2 || true
  log "根分区可用: $(avail_gb /) GB (建议 >= ${MIN_ROOT_AVAIL_GB} GB 再 docker load)"
  log "数据盘可用: $(avail_gb /data) GB"
}

# 删除前：路径必须在白名单、存在、非保护
safe_rm_file() {
  local f="$1" reason="$2"
  ACTIONS=$((ACTIONS + 1))
  if [[ ! -e "$f" ]]; then
    log "SKIP [$reason]: 不存在 $f"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if is_protected "$f"; then
    log "SKIP [$reason]: 受保护 $f"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  local sz=0
  sz="$(stat -c%s "$f" 2>/dev/null || echo 0)"
  if [[ "$APPLY" -eq 1 ]]; then
    rm -f "$f"
    REMOVED_BYTES=$((REMOVED_BYTES + sz))
    log "DEL  [$reason]: $f ($(human_size "$sz"))"
  else
    log "DRY  [$reason]: 将删除文件 $f ($(human_size "$sz"))"
  fi
}

safe_empty_dir_contents() {
  local d="$1" reason="$2"
  ACTIONS=$((ACTIONS + 1))
  if [[ ! -d "$d" ]]; then
    log "SKIP [$reason]: 非目录 $d"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if is_protected "$d"; then
    log "SKIP [$reason]: 受保护目录 $d"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  local total=0 f
  while IFS= read -r -d '' f; do
    if is_protected "$f"; then
      log "SKIP [$reason]: 子项受保护 $f"
      SKIPPED=$((SKIPPED + 1))
      continue
    fi
    local sz
    sz="$(du -sb "$f" 2>/dev/null | awk '{print $1}' || echo 0)"
    total=$((total + sz))
    if [[ "$APPLY" -eq 1 ]]; then
      rm -rf "$f"
      log "DEL  [$reason]: $f"
    else
      log "DRY  [$reason]: 将删除 $f"
    fi
  done < <(find "$d" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)
  REMOVED_BYTES=$((REMOVED_BYTES + total))
  if [[ "$total" -eq 0 ]]; then
    log "OK   [$reason]: $d 已空或无可删项"
  fi
}

# ---------- 1. 固定白名单文件 ----------
cleanup_allowed_exact() {
  log "=== 1. 白名单文件 ==="
  local entry path reason
  for entry in "${ALLOWED_EXACT[@]}"; do
    path="${entry%%|*}"
    reason="${entry#*|}"
    case "$reason" in
      *缓存目录*|*临时目录*)
        safe_empty_dir_contents "$path" "$reason"
        ;;
      *)
        safe_rm_file "$path" "$reason"
        ;;
    esac
  done
}

# ---------- 2. 白名单目录内容（不删目录本身）----------
cleanup_allowed_dir_contents() {
  log "=== 2. 白名单目录内容 ==="
  local entry d reason
  for entry in "${ALLOWED_DIR_CONTENTS_ONLY[@]}"; do
    d="${entry%%|*}"
    reason="${entry#*|}"
    safe_empty_dir_contents "$d" "$reason"
  done
}

# ---------- 3. /tmp 下 pip 临时（仅匹配前缀，且不碰保护路径）----------
cleanup_tmp_pip_artifacts() {
  log "=== 3. /tmp pip 临时 ==="
  local f
  shopt -s nullglob
  for f in /tmp/pip-* /tmp/tmp*; do
    [[ -e "$f" ]] || continue
    if is_protected "$f"; then
      log "SKIP [tmp pip]: 受保护 $f"
      SKIPPED=$((SKIPPED + 1))
      continue
    fi
    # 仅删明确 pip 前缀或空 tmp 命名
    case "$(basename "$f")" in
      pip-*|tmp*)
        if [[ "$APPLY" -eq 1 ]]; then
          local sz
          sz="$(du -sb "$f" 2>/dev/null | awk '{print $1}' || echo 0)"
          rm -rf "$f"
          REMOVED_BYTES=$((REMOVED_BYTES + sz))
          log "DEL  [tmp pip]: $f ($(human_size "$sz"))"
        else
          log "DRY  [tmp pip]: 将删除 $f"
        fi
        ;;
      *)
        log "SKIP [tmp pip]: 不匹配规则 $(basename "$f")"
        SKIPPED=$((SKIPPED + 1))
        ;;
    esac
  done
  shopt -u nullglob
}

# ---------- 4. Docker：仅 prune 悬空资源（不删正在使用的镜像名含 tpuc 且被容器引用）----------
cleanup_docker_safe_prune() {
  log "=== 4. Docker 安全 prune ==="
  if ! command -v docker >/dev/null 2>&1; then
    log "SKIP [docker]: 未安装 docker"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if ! docker info >/dev/null 2>&1; then
    log "SKIP [docker]: docker daemon 不可用"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  local running
  running="$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "${running:-0}" -gt 0 ]]; then
    log "SKIP [docker prune]: 有 ${running} 个运行中容器，不自动 prune（避免影响服务）"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if [[ "$APPLY" -eq 1 ]]; then
    log "RUN  [docker]: docker system prune -af（无运行容器）"
    docker system prune -af >&2 || log "WARN: docker prune 失败"
  else
    log "DRY  [docker]: 将执行 docker system prune -af（当前无运行容器）"
    docker system df >&2 || true
  fi
}

# ---------- 5. containerd 失败导入残留（仅 ingest，且 docker 已停、ingest > 50MB）----------
cleanup_containerd_ingest_orphans() {
  log "=== 5. containerd ingest 残留 ==="
  local ingest="/var/lib/containerd/io.containerd.content.v1.content/ingest"
  if [[ ! -d "$ingest" ]]; then
    log "SKIP [ingest]: 目录不存在"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  local sz_mb
  sz_mb="$(du -sm "$ingest" 2>/dev/null | awk '{print $1}' || echo 0)"
  if [[ "${sz_mb:-0}" -lt 50 ]]; then
    log "SKIP [ingest]: 仅 ${sz_mb}MB (<50MB)，不清理"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if [[ "$(id -u)" -ne 0 ]]; then
    log "SKIP [ingest]: 需要 root"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    if [[ -n "$(docker ps -q 2>/dev/null)" ]]; then
      log "SKIP [ingest]: 有运行中容器，不停止 docker"
      SKIPPED=$((SKIPPED + 1))
      return 0
    fi
  fi
  if [[ "$APPLY" -eq 1 ]]; then
    systemctl stop docker 2>/dev/null || true
    find "$ingest" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
    systemctl start docker 2>/dev/null || true
    log "DEL  [ingest]: 已清空 $ingest (原约 ${sz_mb}MB)"
  else
    log "DRY  [ingest]: 将 stop docker 并清空 $ingest (约 ${sz_mb}MB)"
  fi
}

# ---------- 6. journal（仅当占用 > 500MB 且 root）----------
cleanup_journal_if_large() {
  log "=== 6. systemd journal ==="
  if [[ "$(id -u)" -ne 0 ]]; then
    log "SKIP [journal]: 需要 root"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if ! command -v journalctl >/dev/null 2>&1; then
    log "SKIP [journal]: 无 journalctl"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  local line mb
  line="$(journalctl --disk-usage 2>/dev/null | tail -1 || true)"
  log "INFO [journal]: ${line:-unknown}"
  mb="$(echo "$line" | grep -oE '[0-9]+\.[0-9]+[[:space:]]*[MG]' | head -1 | awk '{gsub(/[^0-9.]/,"",$1); print $1}')"
  local unit
  unit="$(echo "$line" | grep -oE '[0-9]+\.[0-9]+[[:space:]]*[MG]' | head -1 | grep -oE '[MG]' || echo M)"
  if [[ -z "${mb:-}" ]]; then
    log "SKIP [journal]: 无法解析占用"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  local mb_int="${mb%.*}"
  [[ "$unit" == "G" ]] && mb_int=$(( mb_int * 1024 ))
  if [[ "${mb_int:-0}" -lt 500 ]]; then
    log "SKIP [journal]: 约 ${mb}${unit} (<500M 等效)，不清理"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if [[ "$APPLY" -eq 1 ]]; then
    journalctl --vacuum-size=500M >&2 || log "WARN: journal vacuum 失败"
    log "RUN  [journal]: vacuum-size=500M"
  else
    log "DRY  [journal]: 将执行 journalctl --vacuum-size=500M"
  fi
}

# ---------- 7. tpuc tar：仅当镜像已存在且显式 ALLOW_REMOVE_TPU_TAR=1 ----------
cleanup_tpuc_tar_if_loaded() {
  log "=== 7. tpuc_dev tar（默认不删）==="
  local tar="${LABELU_MEDIA}/tpuc_dev_v3.4.tar.gz"
  if [[ "${ALLOW_REMOVE_TPU_TAR:-0}" != "1" ]]; then
    log "SKIP [tpuc tar]: 未设置 ALLOW_REMOVE_TPU_TAR=1"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if [[ ! -f "$tar" ]]; then
    log "SKIP [tpuc tar]: 不存在"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    log "SKIP [tpuc tar]: docker 不可用，无法确认镜像已 load"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if ! docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -qE 'tpuc_dev|sophgo/tpuc'; then
    log "SKIP [tpuc tar]: 未找到 tpuc_dev/sophgo/tpuc 镜像，禁止删 tar"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  safe_rm_file "$tar" "tpuc tar（镜像已存在且 ALLOW_REMOVE_TPU_TAR=1）"
}

# ---------- 8. 迁移 Docker 到 /data（可选，需 --migrate-docker + --apply）----------
migrate_docker_to_data() {
  log "=== 8. Docker data-root 迁移 ==="
  if [[ "$MIGRATE_DOCKER" -ne 1 ]]; then
    log "SKIP [migrate]: 未指定 --migrate-docker"
    return 0
  fi
  if [[ "$APPLY" -ne 1 ]]; then
    log "DRY  [migrate]: 将设置 data-root=${DOCKER_DATA_ROOT}（需 --apply）"
    return 0
  fi
  if [[ "$(id -u)" -ne 0 ]]; then
    die "migrate-docker 需要 root"
  fi
  if [[ "$(avail_gb /data)" -lt "$MIN_DATA_AVAIL_GB" ]]; then
    die "/data 可用空间 < ${MIN_DATA_AVAIL_GB}GB，禁止迁移"
  fi
  if [[ -n "$(docker ps -q 2>/dev/null)" ]]; then
    die "有运行中容器，请先停止再 --migrate-docker"
  fi
  mkdir -p "${DOCKER_DATA_ROOT}"
  if [[ -f /etc/docker/daemon.json ]] && grep -q '"data-root"' /etc/docker/daemon.json 2>/dev/null; then
    log "SKIP [migrate]: daemon.json 已含 data-root"
    return 0
  fi
  systemctl stop docker
  if [[ -f /etc/docker/daemon.json ]]; then
    cp -a /etc/docker/daemon.json "/etc/docker/daemon.json.bak.$(date +%Y%m%d%H%M%S)"
    log "WARN [migrate]: 已有 daemon.json，请手动合并 data-root=${DOCKER_DATA_ROOT} 后重启 docker"
    log "      备份: /etc/docker/daemon.json.bak.*"
    systemctl start docker 2>/dev/null || true
    return 0
  fi
  printf '%s\n' '{' "  \"data-root\": \"${DOCKER_DATA_ROOT}\"" '}' > /etc/docker/daemon.json
  systemctl start docker
  if docker info 2>/dev/null | grep -q "${DOCKER_DATA_ROOT}"; then
    log "OK   [migrate]: Docker Root Dir -> ${DOCKER_DATA_ROOT}"
  else
    log "WARN [migrate]: 请手动检查 docker info | grep 'Docker Root Dir'"
  fi
}

# ---------- 9. yolo_train：仅当 ALLOW_REMOVE_YOLO_TRAIN=1 且目录存在 ----------
cleanup_yolo_train_optional() {
  log "=== 9. yolo_train（默认不删）==="
  local d="${LABELU_MEDIA}/yolo_train"
  if [[ "${ALLOW_REMOVE_YOLO_TRAIN:-0}" != "1" ]]; then
    log "SKIP [yolo_train]: 未设置 ALLOW_REMOVE_YOLO_TRAIN=1"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if [[ ! -d "$d" ]]; then
    log "SKIP [yolo_train]: 不存在"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  log "WARN [yolo_train]: 将删除整个 $d（旧训练产物）"
  safe_empty_dir_contents "$d" "yolo_train（ALLOW_REMOVE_YOLO_TRAIN=1）"
}

# ---------- 主流程 ----------
main() {
  log "模式: $([[ "$APPLY" -eq 1 ]] && echo APPLY || echo DRY-RUN)"
  log "LABELU_MEDIA=${LABELU_MEDIA}"
  report_disk

  cleanup_allowed_exact
  cleanup_allowed_dir_contents
  cleanup_tmp_pip_artifacts
  cleanup_docker_safe_prune
  cleanup_containerd_ingest_orphans
  cleanup_journal_if_large
  cleanup_tpuc_tar_if_loaded
  cleanup_yolo_train_optional
  migrate_docker_to_data

  log "=== 完成 ==="
  log "检查项: ${ACTIONS}  跳过: ${SKIPPED}  释放约: $(human_size "$REMOVED_BYTES")"
  report_disk

  if [[ "$(avail_gb /)" -lt "$MIN_ROOT_AVAIL_GB" ]]; then
    log "WARN: 根分区仍 < ${MIN_ROOT_AVAIL_GB}GB，建议: --apply --migrate-docker 后再 docker load"
  else
    log "OK: 根分区可用空间尚可"
  fi

  if [[ "$APPLY" -eq 0 ]]; then
    log "未实际删除。确认后执行: bash $0 --apply"
  fi
}

main "$@"
