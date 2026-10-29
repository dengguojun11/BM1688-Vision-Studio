#!/usr/bin/env python3
"""部署脚本调用：发版前暂停 YOLO 训练，发版后自动续训。

用法:
  python -m labelu.scripts.yolo_train_deploy pause-and-wait
  python -m labelu.scripts.yolo_train_deploy resume
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="YOLO 训练部署暂停/续训")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pause = sub.add_parser(
        "pause-and-wait",
        help="暂停进行中的训练并等待完成（写入续训队列）",
    )
    p_pause.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="等待暂停完成的最长秒数（默认 3600）",
    )

    sub.add_parser("resume", help="恢复部署前暂停的训练作业")

    args = parser.parse_args()

    if args.cmd == "pause-and-wait":
        from labelu.internal.common.yolo_train_deploy import deploy_pause_and_wait_cli

        try:
            result = deploy_pause_and_wait_cli(wait_timeout_seconds=args.timeout)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False))
        paused = result.get("paused") or []
        if paused:
            print(
                f"[yolo-train-deploy] 已暂停 {len(paused)} 个训练作业，等待 deploy 重启后续训: {paused}",
                file=sys.stderr,
            )
        else:
            print("[yolo-train-deploy] 无进行中的训练，跳过暂停", file=sys.stderr)
        return 0

    if args.cmd == "resume":
        from labelu.internal.common.yolo_train_deploy import deploy_resume_cli

        result = deploy_resume_cli()
        print(json.dumps(result, ensure_ascii=False))
        resumed = int(result.get("resumed") or 0)
        remaining = result.get("queue_remaining") or []
        if resumed:
            print(
                f"[yolo-train-deploy] 已自动继续训练 {resumed} 个作业",
                file=sys.stderr,
            )
        if remaining:
            print(
                f"[yolo-train-deploy] 仍有 {len(remaining)} 个作业未恢复: {remaining}",
                file=sys.stderr,
            )
            return 1
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
