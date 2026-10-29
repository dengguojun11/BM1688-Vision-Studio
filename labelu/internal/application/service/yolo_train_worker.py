"""独立 YOLO/车牌训练 Worker 的持久化领取与执行边界。

一期只提供可本地演练的执行入口，默认开关关闭。训练作业领取顺序为：
短期 queue lease -> execution lease/fencing -> GPU 槽位探测 -> 训练子进程。
Web 进程在开关开启且字段齐全时不再消费训练队列，避免双消费者。
"""

from __future__ import annotations

import json
import inspect as python_inspect
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Callable

from sqlalchemy import inspect as sqlalchemy_inspect, or_

from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.execution_fencing import (
    ExecutionFencingUnavailable,
    ExecutionLeaseMonitor,
    ExecutionLeaseToken,
    claim_execution_lease,
)
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


@dataclass(frozen=True)
class QueueLeaseClaim:
    job_id: int
    owner: str
    expires_at: datetime


@dataclass(frozen=True)
class TrainWorkerCycleResult:
    claimed: int = 0
    started: int = 0
    skipped: int = 0
    disabled: bool = False
    reason: str = ""


def dedicated_worker_enabled() -> bool:
    return bool(getattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", False))


def dedicated_worker_ready() -> tuple[bool, str]:
    """开关和数据库/训练前置条件必须同时满足才切换消费路径。"""
    if not dedicated_worker_enabled():
        return False, "独立训练 Worker 开关未开启"
    if not bool(getattr(settings, "YOLO_TRAIN_ENABLED", False)):
        return False, "YOLO 训练未启用"
    required = (
        "execution_lease_owner",
        "execution_lease_expires_at",
        "execution_fencing_token",
    )
    missing = [name for name in required if not hasattr(YoloTrainJob, name)]
    if missing:
        return False, "训练表缺少执行租约字段：" + ",".join(missing)
    # ORM 属性存在不代表线上数据库已完成迁移。开启独立消费前做一次只读
    # 结构检查，失败时保留 Web 旧路径，不能让任务无人领取。
    try:
        from labelu.internal.common.db import SessionLocal

        with SessionLocal() as db:
            columns = {
                str(item["name"])
                for item in sqlalchemy_inspect(db.get_bind()).get_columns("yolo_train_job")
            }
    except Exception as exc:
        return False, f"无法确认训练表执行租约字段：{exc}"
    missing_columns = sorted(set(required) - columns)
    if missing_columns:
        return False, "数据库缺少执行租约字段：" + ",".join(missing_columns)
    limit = int(getattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_BATCH_LIMIT", 1) or 0)
    if limit < 1:
        return False, "独立训练 Worker 批量上限必须为正数"
    # 训练父进程把 owner/token 传入 YOLO/车牌子进程；子进程的阶段、进度、
    # 制品和终态写入均在写入前校验同一执行租约，父进程失租时会终止子进程。
    # 到这里前置条件已满足，可以把独立 Worker 作为唯一消费者。
    return True, "ok"


def default_worker_owner() -> str:
    return f"yolo-train:{socket.gethostname() or 'unknown'}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def claim_pending_train_job(
    db: Any,
    *,
    owner: str,
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> QueueLeaseClaim | None:
    """用数据库条件更新领取一个 PENDING 作业，支持过期接管。"""
    if not owner.strip():
        raise ValueError("worker owner is required")
    if int(lease_seconds) < 1:
        raise ValueError("lease_seconds must be positive")
    current = now or datetime.now()
    expires = current + timedelta(seconds=int(lease_seconds))
    queue_available = or_(
        YoloTrainJob.queue_lease_owner.is_(None),
        YoloTrainJob.queue_lease_expires_at.is_(None),
        YoloTrainJob.queue_lease_expires_at <= current,
    )
    execution_available = or_(
        YoloTrainJob.execution_lease_owner.is_(None),
        YoloTrainJob.execution_lease_expires_at.is_(None),
        YoloTrainJob.execution_lease_expires_at <= current,
    )
    with begin_transaction(db):
        candidate = (
            db.query(YoloTrainJob.id)
            .filter(
                YoloTrainJob.status == YoloTrainStatus.PENDING.value,
                queue_available,
                execution_available,
            )
            .order_by(YoloTrainJob.queue_priority.asc(), YoloTrainJob.id.asc())
            .first()
        )
        if candidate is None:
            return None
        job_id = int(candidate[0])
        updated = (
            db.query(YoloTrainJob)
            .filter(
                YoloTrainJob.id == job_id,
                YoloTrainJob.status == YoloTrainStatus.PENDING.value,
                queue_available,
                execution_available,
            )
            .update(
                {
                    YoloTrainJob.queue_lease_owner: owner,
                    YoloTrainJob.queue_lease_expires_at: expires,
                },
                synchronize_session=False,
            )
        )
        if int(updated or 0) != 1:
            return None
    return QueueLeaseClaim(job_id=job_id, owner=owner, expires_at=expires)


def release_queue_claim(db: Any, claim: QueueLeaseClaim) -> bool:
    with begin_transaction(db):
        updated = (
            db.query(YoloTrainJob)
            .filter(
                YoloTrainJob.id == int(claim.job_id),
                YoloTrainJob.queue_lease_owner == claim.owner,
            )
            .update(
                {
                    YoloTrainJob.queue_lease_owner: None,
                    YoloTrainJob.queue_lease_expires_at: datetime.now(),
                },
                synchronize_session=False,
            )
        )
    return int(updated or 0) == 1


def _pause_or_stop_requested(job_id: int) -> bool:
    try:
        from labelu.internal.application.service.yolo_train import (
            is_stop_requested,
            is_user_pause_requested,
        )

        return bool(is_stop_requested(job_id) or is_user_pause_requested(job_id))
    except Exception:
        # A missing marker must never prevent a queued job from running.
        return False


def _default_runner(
    job: Any,
    command: Any,
    *,
    resume: bool,
    train_device: str | None,
    lease_check: Callable[[], None] | None = None,
    runtime_update: Callable[[int], None] | None = None,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> None:
    from labelu.internal.application.service.yolo_train_queue import (
        _run_plate_recognition_train_subprocess,
        _run_yolo_train_subprocess,
    )

    job_type = str(getattr(job, "job_type", None) or "yolo_detection").strip()
    runner = (
        _run_plate_recognition_train_subprocess
        if job_type in {"plate_detection", "plate_recognition"}
        else _run_yolo_train_subprocess
    )
    runner(
        int(job.id),
        int(job.task_id),
        command,
        resume=bool(resume),
        train_device=train_device,
        lease_check=lease_check,
        runtime_update=runtime_update,
        execution_fencing_token=execution_fencing_token,
        execution_owner=execution_owner,
    )


def run_claimed_train_job(
    db_factory: Callable[[], Any],
    claim: QueueLeaseClaim,
    *,
    owner: str,
    lease_seconds: int,
    runner: Callable[..., None] | None = None,
) -> str:
    """执行一个已领取作业；runner 仅用于隔离测试和本地演练。"""
    with db_factory() as db:
        job = crud_yolo_train_job.get(db=db, job_id=claim.job_id)
        if job is None or str(job.status or "") != YoloTrainStatus.PENDING.value:
            release_queue_claim(db, claim)
            return "missing"
        job_snapshot = SimpleNamespace(
            id=int(job.id),
            task_id=int(job.task_id),
            job_type=str(getattr(job, "job_type", None) or "yolo_detection"),
        )
        try:
            token = claim_execution_lease(
                db,
                YoloTrainJob,
                claim.job_id,
                owner=owner,
                lease_seconds=lease_seconds,
            )
        except ExecutionFencingUnavailable:
            release_queue_claim(db, claim)
            return "fencing_unavailable"
        if token is None:
            release_queue_claim(db, claim)
            return "execution_busy"
        if _pause_or_stop_requested(claim.job_id):
            from labelu.internal.common.execution_fencing import release_execution_lease

            release_execution_lease(db, token)
            release_queue_claim(db, claim)
            return "paused"

        from labelu.internal.application.service.yolo_train_queue import (
            _resolve_train_start_slot,
            command_from_job,
        )

        command = command_from_job(job)
        from labelu.internal.application.service.yolo_train import _job_work_root

        resume = (_job_work_root(claim.job_id) / "train" / "weights" / "last.pt").is_file()
        ok, reason, train_device = _resolve_train_start_slot(claim.job_id, command)
        if not ok:
            from labelu.internal.common.execution_fencing import release_execution_lease

            release_execution_lease(db, token)
            release_queue_claim(db, claim)
            return "gpu_unavailable"
        assigned = {"device": train_device or "cpu", "owner": owner}
        monitor = ExecutionLeaseMonitor(
            token,
            session_scope_factory=db_factory,
            interval_seconds=max(1.0, float(lease_seconds) / 3.0),
            lease_seconds=lease_seconds,
            label=f"yolo-train-{claim.job_id}",
        )
        from labelu.internal.common.execution_fencing import conditional_execution_update

        if not conditional_execution_update(
            db,
            token,
            {
                YoloTrainJob.status.key: YoloTrainStatus.PROCESSING.value,
                YoloTrainJob.assigned_gpu_json.key: json.dumps(assigned, ensure_ascii=False),
                YoloTrainJob.execution_runtime_pid.key: None,
                YoloTrainJob.error_message.key: None,
            },
            manage_transaction=True,
        ):
            from labelu.internal.common.execution_fencing import release_execution_lease

            release_execution_lease(db, token)
            release_queue_claim(db, claim)
            from labelu.internal.common.yolo_train_gpu_allocator import release_job_gpu_allocation

            release_job_gpu_allocation(claim.job_id)
            return "fencing_lost"
        release_queue_claim(db, claim)

    try:
        monitor.start()
        try:
            active_runner = runner or _default_runner
            runner_kwargs = {
                "resume": resume,
                "train_device": train_device,
            }
            # Keep isolated/test runners that predate the lease callback
            # compatible; the production default runner always accepts it.
            try:
                accepts_lease_check = "lease_check" in python_inspect.signature(
                    active_runner
                ).parameters
            except (TypeError, ValueError):
                accepts_lease_check = False
            if accepts_lease_check:
                runner_kwargs["lease_check"] = monitor.ensure_current
            try:
                accepts_runtime_update = "runtime_update" in python_inspect.signature(
                    active_runner
                ).parameters
            except (TypeError, ValueError):
                accepts_runtime_update = False
            if accepts_runtime_update:
                runner_kwargs["runtime_update"] = lambda pid: monitor.conditional_update(
                    {YoloTrainJob.execution_runtime_pid.key: int(pid)}
                )
            try:
                accepts_fencing_token = "execution_fencing_token" in python_inspect.signature(
                    active_runner
                ).parameters
            except (TypeError, ValueError):
                accepts_fencing_token = False
            if accepts_fencing_token:
                runner_kwargs["execution_fencing_token"] = int(token.fencing_token)
            try:
                accepts_execution_owner = "execution_owner" in python_inspect.signature(
                    active_runner
                ).parameters
            except (TypeError, ValueError):
                accepts_execution_owner = False
            if accepts_execution_owner:
                runner_kwargs["execution_owner"] = owner
            active_runner(job_snapshot, command, **runner_kwargs)
        except Exception as exc:
            # 失败状态也必须受 fencing 保护；接管后旧 worker 只能停止，
            # 不能把新 owner 的状态覆盖成失败。
            try:
                with db_factory() as failure_db:
                    monitor.conditional_update(
                        {
                            YoloTrainJob.status.key: YoloTrainStatus.FAILED.value,
                            YoloTrainJob.error_message.key: str(exc)[:4000],
                        },
                        db=failure_db,
                        manage_transaction=True,
                    )
            except Exception:
                pass
            return "failed"
        monitor.ensure_current()
        with db_factory() as final_db:
            monitor.conditional_update(
                {
                    YoloTrainJob.assigned_gpu_json.key: None,
                    YoloTrainJob.execution_runtime_pid.key: None,
                },
                db=final_db,
                manage_transaction=True,
            )
        return "started"
    finally:
        monitor.stop()
        try:
            with db_factory() as release_db:
                # 只清理仍由当前 token 持有的运行时字段；fencing 失效时
                # 不得覆盖接管者已经写入的新作业状态。
                try:
                    monitor.conditional_update(
                        {
                            YoloTrainJob.assigned_gpu_json.key: None,
                            YoloTrainJob.execution_runtime_pid.key: None,
                        },
                        db=release_db,
                        manage_transaction=True,
                    )
                except Exception:
                    pass
                from labelu.internal.common.execution_fencing import release_execution_lease

                release_execution_lease(release_db, token)
        except Exception:
            pass
        from labelu.internal.common.yolo_train_gpu_allocator import release_job_gpu_allocation

        release_job_gpu_allocation(claim.job_id)


def run_worker_once(
    db_factory: Callable[[], Any],
    *,
    owner: str | None = None,
    lease_seconds: int | None = None,
    limit: int | None = None,
    runner: Callable[..., None] | None = None,
) -> TrainWorkerCycleResult:
    ready, reason = dedicated_worker_ready()
    if not ready:
        return TrainWorkerCycleResult(disabled=True, reason=reason)
    worker_owner = owner or default_worker_owner()
    lease = int(lease_seconds or getattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_LEASE_SECONDS", 900))
    batch_limit = int(limit or getattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_BATCH_LIMIT", 1))
    claimed = started = skipped = 0
    for _ in range(max(1, batch_limit)):
        with db_factory() as db:
            claim = claim_pending_train_job(db, owner=worker_owner, lease_seconds=lease)
        if claim is None:
            break
        claimed += 1
        result = run_claimed_train_job(
            db_factory,
            claim,
            owner=worker_owner,
            lease_seconds=lease,
            runner=runner,
        )
        if result == "started":
            started += 1
        else:
            skipped += 1
    return TrainWorkerCycleResult(claimed=claimed, started=started, skipped=skipped)


__all__ = [
    "QueueLeaseClaim",
    "TrainWorkerCycleResult",
    "claim_pending_train_job",
    "dedicated_worker_enabled",
    "dedicated_worker_ready",
    "release_queue_claim",
    "run_claimed_train_job",
    "run_worker_once",
]
