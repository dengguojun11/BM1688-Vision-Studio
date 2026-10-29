"""BM1688 独立 Worker 运行时。

消费自动训练和手工创建的 PENDING 导出。数据库 ``queue_lease_*`` 负责
跨进程派发领取；真正的编译写入仍由导出/重编译函数内的 execution fencing
保护。本模块不创建第二套任务状态机，也不改变 API 语义。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import inspect as sqlalchemy_inspect, or_
from sqlalchemy.orm import Session

from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export


_LEASE_SECONDS = 300
_TRUE_VALUES = {"1", "true", "yes", "on"}
_SUPPORTED_ACTIONS = ("export", "recompile")


def dedicated_worker_enabled() -> bool:
    return (
        os.environ.get("LABELU_BM1688_DEDICATED_WORKER_ENABLED", "")
        .strip()
        .lower()
        in _TRUE_VALUES
    )


def default_owner() -> str:
    host = socket.gethostname() or "unknown-host"
    return f"bm1688-worker:{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def execution_fencing_ready(db: Session) -> tuple[bool, str]:
    """Check the worker database before claiming any compile task.

    The opt-in BM1688 worker must not fall back to the legacy unfenced
    compiler when the execution-lease migration is incomplete.  The probe
    uses the supplied session so tests and production inspect the same DB.
    """
    required = (
        "queue_lease_owner",
        "queue_lease_expires_at",
        "execution_lease_owner",
        "execution_lease_expires_at",
        "execution_fencing_token",
    )
    missing_orm = [name for name in required if not hasattr(YoloBm1688Export, name)]
    if missing_orm:
        return False, "BM1688 导出模型缺少执行租约字段：" + ",".join(missing_orm)
    try:
        columns = {
            str(item["name"])
            for item in sqlalchemy_inspect(db.get_bind()).get_columns(
                YoloBm1688Export.__tablename__
            )
        }
    except Exception as exc:
        return False, f"无法确认 BM1688 执行租约字段：{exc}"
    missing_columns = sorted(set(required) - columns)
    if missing_columns:
        return False, "数据库缺少 BM1688 执行租约字段：" + ",".join(missing_columns)
    return True, "ok"


@dataclass(frozen=True)
class Bm1688WorkerCycleResult:
    enabled: bool
    ready: bool = False
    candidates: int = 0
    claimed: int = 0
    executed: int = 0
    skipped: int = 0
    failed: int = 0
    reason: str = ""


def _candidate_ids(db: Session, *, now: datetime, limit: int) -> list[int]:
    rows = (
        db.query(YoloBm1688Export.id)
        .filter(
            YoloBm1688Export.status == "PENDING",
            or_(
                YoloBm1688Export.worker_action.is_(None),
                YoloBm1688Export.worker_action.in_(_SUPPORTED_ACTIONS),
            ),
            or_(
                YoloBm1688Export.execution_lease_owner.is_(None),
                YoloBm1688Export.execution_lease_expires_at.is_(None),
                YoloBm1688Export.execution_lease_expires_at <= now,
            ),
            or_(
                YoloBm1688Export.queue_lease_expires_at.is_(None),
                YoloBm1688Export.queue_lease_expires_at <= now,
            ),
        )
        .order_by(YoloBm1688Export.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    return [int(row[0]) for row in rows]


def _claim_export(
    db: Session,
    *,
    export_id: int,
    owner: str,
    now: datetime,
    lease_seconds: int,
) -> bool:
    expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
    with begin_transaction(db):
        updated = (
            db.query(YoloBm1688Export)
            .filter(
                YoloBm1688Export.id == int(export_id),
                YoloBm1688Export.status == "PENDING",
                or_(
                    YoloBm1688Export.worker_action.is_(None),
                    YoloBm1688Export.worker_action.in_(_SUPPORTED_ACTIONS),
                ),
                or_(
                    YoloBm1688Export.execution_lease_owner.is_(None),
                    YoloBm1688Export.execution_lease_expires_at.is_(None),
                    YoloBm1688Export.execution_lease_expires_at <= now,
                ),
                or_(
                    YoloBm1688Export.queue_lease_expires_at.is_(None),
                    YoloBm1688Export.queue_lease_expires_at <= now,
                ),
            )
            .update(
                {
                    YoloBm1688Export.queue_lease_owner: owner,
                    YoloBm1688Export.queue_lease_expires_at: expires_at,
                },
                synchronize_session=False,
            )
        )
    return int(updated or 0) == 1


def _release_failed_claim(
    db: Session, *, export_id: int, owner: str, now: datetime
) -> None:
    with begin_transaction(db):
        db.query(YoloBm1688Export).filter(
            YoloBm1688Export.id == int(export_id),
            YoloBm1688Export.queue_lease_owner == owner,
        ).update(
            {
                YoloBm1688Export.queue_lease_owner: None,
                YoloBm1688Export.queue_lease_expires_at: now,
            },
            synchronize_session=False,
        )


def _complete_claim(db: Session, *, export_id: int, owner: str) -> None:
    """清理已同步执行完成的派发租约，只能清理本 Worker 的租约。"""
    with begin_transaction(db):
        db.query(YoloBm1688Export).filter(
            YoloBm1688Export.id == int(export_id),
            YoloBm1688Export.queue_lease_owner == owner,
            YoloBm1688Export.status != "PENDING",
        ).update(
            {
                YoloBm1688Export.queue_lease_owner: None,
                YoloBm1688Export.queue_lease_expires_at: datetime.now(),
            },
            synchronize_session=False,
        )


def run_bm1688_worker_once(
    db_factory: Callable[[], Session],
    *,
    owner: str | None = None,
    limit: int = 1,
    lease_seconds: int = _LEASE_SECONDS,
    enabled: bool | None = None,
    execute: Callable[[int], None] | None = None,
    now: datetime | None = None,
) -> Bm1688WorkerCycleResult:
    """领取并同步执行一批 BM1688 导出；每次数据库访问使用短 Session。"""
    active = dedicated_worker_enabled() if enabled is None else bool(enabled)
    if not active:
        return Bm1688WorkerCycleResult(enabled=False)
    if int(limit) < 1:
        raise ValueError("limit must be positive")
    if int(lease_seconds) < 1:
        raise ValueError("lease_seconds must be positive")

    readiness_db = db_factory()
    try:
        ready, readiness_reason = execution_fencing_ready(readiness_db)
    finally:
        readiness_db.close()
    if not ready:
        return Bm1688WorkerCycleResult(
            enabled=True,
            ready=False,
            reason=readiness_reason,
        )

    worker_owner = owner or default_owner()
    current = now or datetime.now()
    scan_db = db_factory()
    try:
        candidates = _candidate_ids(scan_db, now=current, limit=int(limit))
    finally:
        scan_db.close()

    if execute is None:
        def execute(export_id: int) -> None:
            from labelu.internal.application.service.yolo_bm1688_export import (
                _run_bm1688_export_sync,
                _run_bm1688_recompile_sync,
            )

            action_db = db_factory()
            try:
                row = action_db.query(YoloBm1688Export).filter(
                    YoloBm1688Export.id == int(export_id)
                ).first()
                action = str(getattr(row, "worker_action", None) or "export").strip().lower()
            finally:
                action_db.close()
            if action == "recompile":
                _run_bm1688_recompile_sync(int(export_id))
            else:
                _run_bm1688_export_sync(int(export_id))

    claimed = executed = skipped = failed = 0
    for export_id in candidates:
        claim_db = db_factory()
        try:
            won = _claim_export(
                claim_db,
                export_id=export_id,
                owner=worker_owner,
                now=current,
                lease_seconds=int(lease_seconds),
            )
        finally:
            claim_db.close()
        if not won:
            skipped += 1
            continue
        claimed += 1
        try:
            # Claim transaction has committed and its Session is closed before
            # any compiler side effect. The execution function owns its own
            # short sessions and execution fencing lifecycle.
            execute(export_id)
            complete_db = db_factory()
            try:
                _complete_claim(
                    complete_db, export_id=export_id, owner=worker_owner
                )
            finally:
                complete_db.close()
            executed += 1
        except Exception:
            failed += 1
            release_db = db_factory()
            try:
                _release_failed_claim(
                    release_db,
                    export_id=export_id,
                    owner=worker_owner,
                    now=datetime.now(),
                )
            finally:
                release_db.close()

    return Bm1688WorkerCycleResult(
        enabled=True,
        ready=True,
        candidates=len(candidates),
        claimed=claimed,
        executed=executed,
        skipped=skipped,
        failed=failed,
        reason="ok",
    )


def run_bm1688_worker_loop(
    db_factory: Callable[[], Session],
    *,
    owner: str | None = None,
    interval_seconds: float = 2.0,
    limit: int = 1,
    lease_seconds: int = _LEASE_SECONDS,
    enabled: bool | None = None,
    execute: Callable[[int], None] | None = None,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if float(interval_seconds) <= 0:
        raise ValueError("interval_seconds must be positive")
    should_stop = stop or (lambda: False)
    worker_owner = owner or default_owner()
    while not should_stop():
        run_bm1688_worker_once(
            db_factory,
            owner=worker_owner,
            limit=limit,
            lease_seconds=lease_seconds,
            enabled=enabled,
            execute=execute,
        )
        sleep(float(interval_seconds))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="7GAI BM1688 独立编译 Worker")
    parser.add_argument("--once", action="store_true", help="执行一轮后退出")
    parser.add_argument("--interval", type=float, default=2.0, help="轮询间隔秒数")
    parser.add_argument("--limit", type=int, default=1, help="每轮最多领取数量")
    parser.add_argument("--lease-seconds", type=int, default=_LEASE_SECONDS)
    args = parser.parse_args(argv)
    if args.interval <= 0 or args.limit < 1 or args.lease_seconds < 1:
        parser.error("interval、limit 和 lease-seconds 必须为正数")

    from labelu.internal.common.db import SessionLocal

    if not dedicated_worker_enabled():
        print(
            json.dumps(
                {
                    "enabled": False,
                    "message": "请设置 LABELU_BM1688_DEDICATED_WORKER_ENABLED=true",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 2

    owner = default_owner()
    while True:
        result = run_bm1688_worker_once(
            SessionLocal,
            owner=owner,
            limit=args.limit,
            lease_seconds=args.lease_seconds,
            enabled=True,
        )
        print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
        if args.once:
            return 0 if result.ready and result.failed == 0 else 1
        time.sleep(args.interval)


__all__ = [
    "Bm1688WorkerCycleResult",
    "dedicated_worker_enabled",
    "default_owner",
    "execution_fencing_ready",
    "run_bm1688_worker_once",
    "run_bm1688_worker_loop",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
