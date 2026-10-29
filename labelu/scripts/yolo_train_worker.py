"""YOLO/车牌独立训练 Worker 入口（默认关闭）。"""

from __future__ import annotations

import argparse
import time

from labelu.internal.application.service.yolo_train_worker import run_worker_once
from labelu.internal.common.config import settings
from labelu.internal.common.db import SessionLocal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LabelU 独立 YOLO/车牌训练 Worker")
    parser.add_argument("--once", action="store_true", help="只扫描并处理一轮")
    parser.add_argument("--loop", action="store_true", help="持续扫描")
    parser.add_argument("--interval", type=float, default=None, help="扫描间隔秒数")
    parser.add_argument("--lease-seconds", type=int, default=None, help="执行租约秒数")
    parser.add_argument("--limit", type=int, default=None, help="每轮最多领取数量")
    args = parser.parse_args(argv)

    interval = float(
        args.interval
        if args.interval is not None
        else getattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_INTERVAL_SECONDS", 5.0)
    )
    if interval <= 0:
        parser.error("--interval 必须大于 0")
    if args.once and args.loop:
        parser.error("--once 与 --loop 不能同时使用")
    continuous = bool(args.loop)
    while True:
        result = run_worker_once(
            SessionLocal,
            lease_seconds=args.lease_seconds,
            limit=args.limit,
        )
        if not continuous:
            return 0
        if result.disabled:
            # 默认关闭时不产生消费动作，但保留进程可观测性。
            time.sleep(interval)
            continue
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
