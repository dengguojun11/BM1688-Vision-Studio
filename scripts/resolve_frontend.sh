#!/usr/bin/env bash
# 已弃用：请使用源码集成流程
#   labelu frontend-clone
#   labelu frontend-build   # 生产写入 statics
#   labelu                  # 开发：自动 Vite + 后端
echo "[deprecated] resolve_frontend.sh 已弃用。"
echo "  开发: labelu frontend-clone && labelu"
echo "  生产构建: labelu frontend-build"
if [[ "${1:-}" == "true" ]]; then
  echo "  若仍需 Release zip，可手动下载 .VERSION 中 frontend.zip 解压到 labelu/internal/statics"
  exit 1
fi
exit 0
