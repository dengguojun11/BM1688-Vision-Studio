#!/usr/bin/env python3
"""部署后写入 BM1688 编译配置（由 deploy.sh 调用）。"""
from __future__ import annotations

import sys

from labelu.internal.common.bm1688_deploy_bootstrap import (
    deploy_readiness_summary,
    ensure_bm1688_compiler_ready,
    ensure_bm1688_docker_deploy,
)


def main() -> int:
    ensure_bm1688_compiler_ready(force_paths=True)
    docker_ok, docker_msg = ensure_bm1688_docker_deploy(force=True)
    if not docker_ok:
        print(f"WARN: BM1688 Docker 未完全就绪: {docker_msg[:500]}", file=sys.stderr)
    print(deploy_readiness_summary())
    cfg = ensure_bm1688_compiler_ready()
    missing = []
    if not (cfg.get("tpu_mlir_root") or "").strip():
        missing.append("tpu_mlir")
    if not (cfg.get("calibration_dataset_dir") or "").strip():
        missing.append("coco128")
    if not (cfg.get("quantize_table_path") or "").strip():
        missing.append("qtable")
    if missing:
        print(
            f"ERROR: BM1688 部署未就绪，缺少: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    if not docker_ok:
        print(
            "WARN: BM1688 Docker 量化环境未就绪（下次 update 重试；"
            "或放置 sophgo-tpuc_dev-v3.2_*.tar.gz 到 offline/）",
            file=sys.stderr,
        )
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
