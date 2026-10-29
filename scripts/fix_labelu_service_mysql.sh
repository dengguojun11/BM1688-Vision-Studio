#!/usr/bin/env bash
# 修复 labelu.service 因 MySQL 1045 反复重启：规范化 DATABASE_URL 并校验连接后重启。
# 用法（服务器 root）:
#   cd /data/labeIU/labeIuproject/labeIU
#   sudo bash scripts/fix_labelu_service_mysql.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${LABELU_ENV_FILE:-/data/labeIU/labeIuMedia/.env}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 sudo 运行" >&2
  exit 1
fi

cd "${PROJECT_ROOT}"
export LABELU_ENV_FILE="${ENV_FILE}"

systemctl stop labelu 2>/dev/null || true
systemctl reset-failed labelu 2>/dev/null || true

echo "==> 规范化 ${ENV_FILE} 中的 DATABASE_URL"
sudo bash deploy.sh restart 2>&1 | tee /tmp/labelu_mysql_fix.log || {
  echo "deploy.sh restart 失败，请检查 ${ENV_FILE} 中账号密码是否与 MySQL 一致" >&2
  echo "密码含 @ 时须 URL 编码为 %40，主机建议 127.0.0.1 而非 localhost" >&2
  exit 1
}

echo "==> 服务状态"
systemctl --no-pager -l status labelu || true
curl -fsS -o /dev/null -w "HTTP %{http_code}\n" "http://127.0.0.1:10000/docs" || true
