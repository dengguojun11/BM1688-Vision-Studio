"""第三方 S3 问题数据回流独立 Worker。

接口只负责写入 PENDING 记录；Worker 通过数据库租约领取并复用既有导入
状态机。租约失效或 fencing token 变化时，旧进程不得继续写状态。
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

from sqlalchemy import inspect as sqlalchemy_inspect

from labelu.internal.adapter.persistence import crud_experiment_dataset_import
from labelu.internal.application.service.experiment_dataset_import import (
    run_import_job_now,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import SessionLocal, begin_transaction
from labelu.internal.common.production_security import enforce_production_security
from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)


_REQUIRED_WORKER_COLUMNS = (
    "worker_lease_owner",
    "worker_lease_expires_at",
    "worker_fencing_token",
    "attempt_count",
)
WORKER_CONFIG_EXIT_CODE = 78


def default_owner() -> str:
    host = socket.gethostname() or "unknown-host"
    return f"experiment-import-worker:{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class ImportWorkerCycleResult:
    enabled: bool
    ready: bool = True
    candidates: int = 0
    requeued: int = 0
    claimed: int = 0
    executed: int = 0
    failed: int = 0
    reason: str = ""


def dedicated_worker_ready(
    db_factory: Callable[[], object] = SessionLocal,
    *,
    enabled: bool | None = None,
) -> tuple[bool, str]:
    """Fail closed before enabling the Web-side handoff contract.

    The API is configured to enqueue only when this worker is enabled.  A
    missing migration or production credential must therefore stop the unit
    before it starts consuming rows; otherwise the system would accept data
    with no reliable consumer or repeatedly crash while holding leases.
    """
    active = (
        settings.EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED
        if enabled is None
        else bool(enabled)
    )
    if not bool(active):
        return False, "第三方 S3 回流 Worker 开关未开启"
    missing_orm = [
        name
        for name in _REQUIRED_WORKER_COLUMNS
        if not hasattr(ExperimentDatasetImport, name)
    ]
    if missing_orm:
        return False, "回流表缺少 Worker 租约字段：" + ",".join(missing_orm)
    try:
        db = db_factory()
        try:
            columns = {
                str(item["name"])
                for item in sqlalchemy_inspect(db.get_bind()).get_columns(
                    "experiment_dataset_import"
                )
            }
        finally:
            close = getattr(db, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        return False, f"无法确认回流表 Worker 字段：{exc}"
    missing_columns = sorted(set(_REQUIRED_WORKER_COLUMNS) - columns)
    if missing_columns:
        return False, "数据库缺少 Worker 租约字段：" + ",".join(missing_columns)
    try:
        enforce_production_security(
            jwt_from_env=bool((settings.PASSWORD_SECRET_KEY or "").strip())
        )
    except RuntimeError as exc:
        return False, str(exc)
    return True, "ok"


def run_import_worker_once(
    db_factory: Callable[[], object] = SessionLocal,
    *,
    owner: str | None = None,
    limit: int | None = None,
    lease_seconds: int | None = None,
    enabled: bool | None = None,
) -> ImportWorkerCycleResult:
    active = (
        settings.EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED
        if enabled is None
        else bool(enabled)
    )
    if not active:
        return ImportWorkerCycleResult(
            enabled=False,
            ready=False,
            reason="第三方 S3 回流 Worker 开关未开启",
        )
    ready, reason = dedicated_worker_ready(db_factory, enabled=active)
    if not ready:
        return ImportWorkerCycleResult(
            enabled=True,
            ready=False,
            reason=reason,
        )
    batch_size = int(limit or settings.EXPERIMENT_DATASET_WORKER_BATCH_SIZE)
    lease = int(lease_seconds or settings.EXPERIMENT_DATASET_WORKER_LEASE_SECONDS)
    worker_owner = owner or default_owner()
    scan_db = db_factory()
    try:
        with begin_transaction(scan_db):
            requeued_rows = crud_experiment_dataset_import.requeue_waiting(
                scan_db,
                limit=batch_size,
                # Missing models/categories are external configuration
                # prerequisites.  They are woken explicitly when the model or
                # category is registered, so the periodic worker must not
                # repeatedly download and reprocess the same package.  Only
                # an incomplete sample aggregate needs a periodic retry.
                status="WAITING_FOR_SAMPLES",
                min_age_seconds=int(
                    settings.EXPERIMENT_DATASET_WAITING_REQUEUE_SECONDS
                ),
                now=datetime.now(),
            )
            claimed_rows = crud_experiment_dataset_import.claim_pending(
                scan_db,
                owner=worker_owner,
                lease_seconds=lease,
                limit=batch_size,
                now=datetime.now(),
            )
    finally:
        scan_db.close()

    executed = 0
    failed = 0
    for job_id, fencing_token in claimed_rows:
        try:
            run_import_job_now(
                int(job_id),
                lease_owner=worker_owner,
                fencing_token=int(fencing_token),
            )
            executed += 1
        except Exception:
            # One bad S3 package must not prevent the next claimed package from
            # being processed.  The import service owns terminal error writes;
            # this guard only keeps the worker loop alive for the next item.
            failed += 1
    return ImportWorkerCycleResult(
        enabled=True,
        ready=True,
        candidates=len(claimed_rows),
        requeued=len(requeued_rows),
        claimed=len(claimed_rows),
        executed=executed,
        failed=failed,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LabelU 第三方 S3 数据回流 Worker")
    parser.add_argument("--once", action="store_true", help="只执行一轮")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="只检查生产配置和回流表租约字段，不领取任务",
    )
    args = parser.parse_args(argv)
    if not settings.EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED:
        return 0
    ready, reason = dedicated_worker_ready()
    if args.preflight:
        return 0 if ready else WORKER_CONFIG_EXIT_CODE
    if not ready:
        print(f"第三方 S3 回流 Worker 启动门禁失败：{reason}", flush=True)
        return WORKER_CONFIG_EXIT_CODE
    while True:
        run_import_worker_once(limit=args.limit)
        if args.once:
            return 0
        time.sleep(float(settings.EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS))


__all__ = [
    "ImportWorkerCycleResult",
    "WORKER_CONFIG_EXIT_CODE",
    "dedicated_worker_ready",
    "run_import_worker_once",
    "main",
]
