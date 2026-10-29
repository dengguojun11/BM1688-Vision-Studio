#!/usr/bin/env bash
# 打印脱敏配置，便于部署与排障（等价于 `labelu doctor`）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
exec python -m labelu.main doctor
