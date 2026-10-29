"""边端验证独立 Worker 运行时。

Web 调度器只负责给待验证任务分配设备；开启本 Worker 后，SSH、上传、
BM1688 推理和结果回传均由独立进程执行。默认关闭，数据库结构未完成时
失败关闭，避免出现 Web 已停止消费但独立 Worker 尚未具备领取条件的状态。
"""

from __future__ import annotations

import argparse
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import inspect as sqlalchemy_inspect, or_

from labelu.internal.common.config import settings
from labelu.internal.common.db import SessionLocal
from labelu.internal.common import edge_device_registry
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


@dataclass(frozen=True)
class EdgeValidationWorkerCycleResult:
    enabled: bool
    ready: bool = False
    candidates: int = 0
    started: int = 0
    skipped: int = 0
    failed: int = 0
    reason: str = ""


def dedicated_worker_enabled() -> bool:
    return bool(
        getattr(settings, "YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED", False)
    )


def default_owner() -> str:
    host = socket.gethostname() or "unknown-host"
    return f"edge-validation-worker:{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def dedicated_worker_ready() -> tuple[bool, str]:
    if not dedicated_worker_enabled():
        return False, "独立边端验证 Worker 开关未开启"
    required = (
        "queue_lease_owner",
        "queue_lease_expires_at",
        "execution_lease_owner",
        "execution_lease_expires_at",
        "execution_fencing_token",
    )
    missing_orm = [
        name for name in required if not hasattr(YoloEdgeValidationJob, name)
    ]
    if missing_orm:
        return False, "边端验证表缺少执行租约字段：" + ",".join(missing_orm)
    try:
        with SessionLocal() as db:
            columns = {
                str(item["name"])
                for item in sqlalchemy_inspect(db.get_bind()).get_columns(
                    "yolo_edge_validation_job"
                )
            }
    except Exception as exc:
        return False, f"无法确认边端验证表执行租约字段：{exc}"
    missing_columns = sorted(set(required) - columns)
    if missing_columns:
        return False, "数据库缺少执行租约字段：" + ",".join(missing_columns)
    return True, "ok"


def candidate_ids(db, *, now: datetime | None = None, limit: int = 1) -> list[int]:
    current = now or datetime.now()
    target_limit = max(1, int(limit))
    # Registry ownership is persisted outside the jobs table, so Board Agent
    # rows cannot be excluded in SQL. Scan in ordered keyset batches and keep
    # going after pull-channel rows; applying LIMIT before that filter would
    # starve the SSH worker whenever the queue is led by Board Agent jobs.
    scan_batch_size = max(32, target_limit)
    last_seen_id = 0
    candidates: list[int] = []
    registry_cache: dict[str, dict | None] = {}
    while len(candidates) < target_limit:
        query = db.query(
            YoloEdgeValidationJob.id, YoloEdgeValidationJob.edge_device_id
        ).filter(
            YoloEdgeValidationJob.status == "PROCESSING",
            YoloEdgeValidationJob.edge_device_id.isnot(None),
            or_(
                YoloEdgeValidationJob.execution_lease_owner.is_(None),
                YoloEdgeValidationJob.execution_lease_expires_at.is_(None),
                YoloEdgeValidationJob.execution_lease_expires_at <= current,
            ),
        )
        if last_seen_id:
            query = query.filter(YoloEdgeValidationJob.id > last_seen_id)
        batch = (
            query.order_by(YoloEdgeValidationJob.id.asc())
            .limit(scan_batch_size)
            .all()
        )
        if not batch:
            break
        last_seen_id = int(batch[-1][0])
        # A Board Agent owns pull-channel jobs.  The dedicated SSH worker must
        # not take over those rows after a queue/execution lease expires;
        # otherwise two consumers can run the same validation and race the
        # final report.  Keep rows without a registry record for rolling
        # upgrades and old synthetic fixtures, where the historical SSH path
        # remains the only known consumer.
        for row in batch:
            device_id = str(row[1] or "").strip()
            if device_id:
                if device_id not in registry_cache:
                    try:
                        registry_cache[device_id] = (
                            edge_device_registry.find_device_record(device_id)
                        )
                    except Exception:
                        # Do not let a registry read outage hand a known/
                        # possibly pull-channel job to the SSH worker.  The
                        # next cycle can retry.
                        registry_cache[device_id] = None
                        continue
                record = registry_cache[device_id]
                if (
                    isinstance(record, dict)
                    and str(record.get("validation_channel") or "ssh")
                    .strip()
                    .lower()
                    in {"board_agent", "board-agent", "主动拉取", "板端主动拉取"}
                ):
                    continue
            candidates.append(int(row[0]))
            if len(candidates) >= target_limit:
                break
        if len(batch) < scan_batch_size:
            break
    # 查询结果只作为提示，真正的接管由 _run_manual_job_sync 的 execution
    # lease 原子领取完成；过期租约允许安全接管。
    return candidates[:target_limit]


def run_worker_once(
    db_factory: Callable[[], object] = SessionLocal,
    *,
    owner: str | None = None,
    limit: int | None = None,
    enabled: bool | None = None,
    run_job: Callable[[int], None] | None = None,
) -> EdgeValidationWorkerCycleResult:
    active = dedicated_worker_enabled() if enabled is None else bool(enabled)
    if not active:
        return EdgeValidationWorkerCycleResult(
            enabled=False,
            reason="独立边端验证 Worker 开关未开启",
        )
    ready, reason = dedicated_worker_ready()
    if not ready:
        return EdgeValidationWorkerCycleResult(
            enabled=True,
            ready=False,
            reason=reason,
        )
    batch_limit = int(
        limit
        if limit is not None
        else getattr(settings, "YOLO_EDGE_VALIDATION_DEDICATED_WORKER_BATCH_LIMIT", 1)
    )
    if batch_limit < 1:
        raise ValueError("limit must be positive")

    # 设备分配仍复用现有调度器，不复制任务状态机。开启独立 Worker 时
    # scheduler 的 start_workers=False 会阻止 Web 进程启动 SSH 线程。
    from labelu.internal.application.service.yolo_edge_validation_scheduler import (
        dispatch_pending_edge_validation_jobs,
    )

    dispatch_pending_edge_validation_jobs(start_workers=False)
    scan_db = db_factory()
    try:
        ids = candidate_ids(scan_db, limit=batch_limit)
    finally:
        close = getattr(scan_db, "close", None)
        if callable(close):
            close()

    active_run = run_job
    if active_run is None:
        from labelu.internal.application.service.yolo_edge_validation import (
            _run_manual_job_sync,
        )

        active_run = _run_manual_job_sync

    started = failed = 0
    for job_id in ids:
        try:
            active_run(int(job_id))
            started += 1
        except Exception:
            failed += 1
    return EdgeValidationWorkerCycleResult(
        enabled=True,
        ready=True,
        candidates=len(ids),
        started=started,
        failed=failed,
        reason=reason,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="7GAI 独立边端验证 Worker")
    parser.add_argument("--once", action="store_true", help="执行一轮后退出")
    parser.add_argument("--interval", type=float, default=None, help="轮询间隔秒数")
    parser.add_argument("--limit", type=int, default=None, help="每轮最多执行数量")
    args = parser.parse_args(argv)
    interval = float(
        args.interval
        if args.interval is not None
        else getattr(
            settings, "YOLO_EDGE_VALIDATION_DEDICATED_WORKER_INTERVAL_SECONDS", 5.0
        )
    )
    if interval <= 0:
        parser.error("--interval 必须大于 0")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit 必须大于 0")
    if not dedicated_worker_enabled():
        print("独立边端验证 Worker 未开启", flush=True)
        return 2
    while True:
        result = run_worker_once(limit=args.limit)
        if result.failed:
            print(f"边端验证 Worker 本轮失败 {result.failed} 个任务", flush=True)
        if args.once:
            return 0 if result.ready and result.failed == 0 else 1
        time.sleep(interval)


__all__ = [
    "EdgeValidationWorkerCycleResult",
    "candidate_ids",
    "dedicated_worker_enabled",
    "dedicated_worker_ready",
    "default_owner",
    "run_worker_once",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
