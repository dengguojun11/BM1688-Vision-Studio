#!/usr/bin/env bash
# 在 Sophon 编译机或导出完成后执行（对齐 FAQ + YOLOv8_plus_det）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "=== 1) LabelU 编译配置（FAQ §1、§3）==="
python -m labelu.scripts.verify_sophon_export --config-only || true

if [[ -n "${EXPORT_DIR:-}" ]]; then
  echo ""
  echo "=== 2) 导出目录: $EXPORT_DIR ==="
  python -m labelu.scripts.verify_sophon_export --export-dir "$EXPORT_DIR" --no-env
elif [[ -n "${EXPORT_ID:-}" ]]; then
  echo ""
  echo "=== 2) 导出 ID: $EXPORT_ID ==="
  python -m labelu.scripts.verify_sophon_export --export-id "$EXPORT_ID"
else
  echo ""
  echo "提示: 设置 EXPORT_DIR 或 EXPORT_ID 以验证 models/ 树，例如:"
  echo "  EXPORT_ID=1 ./scripts/ops/verify_sophon_faq.sh"
fi
