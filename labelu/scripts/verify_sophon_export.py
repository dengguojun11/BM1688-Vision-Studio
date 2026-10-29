#!/usr/bin/env python3
"""按 sophon-demo FAQ 验证 BM1688 导出目录或编译环境。

示例:
  python -m labelu.scripts.verify_sophon_export --config-only
  python -m labelu.scripts.verify_sophon_export --export-dir /path/to/media/yolo_bm1688_exports/1
  python -m labelu.scripts.verify_sophon_export --export-id 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sophon FAQ / YOLOv8_plus_det 导出验证")
    parser.add_argument(
        "--export-dir",
        type=Path,
        help="导出任务目录（含 models/onnx、models/BM1688 等）",
    )
    parser.add_argument("--export-id", type=int, help="按 export id 在 MEDIA_ROOT 下解析目录")
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="仅验证 LabelU 编译配置与环境（FAQ §1、§3 前置）",
    )
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument(
        "--no-env",
        action="store_true",
        help="跳过编译环境检查（仅验目录结构）",
    )
    args = parser.parse_args(argv)

    from labelu.internal.common.sophon_faq_verify import (
        SophonVerifyReport,
        print_report,
        verify_compiler_config,
        verify_export_tree,
    )
    from labelu.internal.common.config import settings

    if args.config_only:
        report = SophonVerifyReport()
        verify_compiler_config(report)
        return print_report(report, as_json=args.json)

    export_dir = args.export_dir
    if args.export_id is not None:
        export_dir = (
            Path(settings.MEDIA_ROOT)
            / settings.YOLO_BM1688_EXPORT_SUBDIR
            / str(args.export_id)
        )
    if export_dir is None:
        parser.error("请指定 --export-dir、--export-id 或 --config-only")
    report = verify_export_tree(
        export_dir.resolve(),
        check_env=not args.no_env,
    )
    return print_report(report, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
