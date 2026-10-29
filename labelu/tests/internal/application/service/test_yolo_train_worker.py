"""独立 YOLO/车牌 Worker 的领取边界测试。"""

import multiprocessing as mp
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus
from labelu.internal.application.service.yolo_train_worker import (
    claim_pending_train_job,
    dedicated_worker_ready,
    release_queue_claim,
    run_worker_once,
)


def _claim_worker_process(db_path, owner, output):
    """独立进程领取入口，验证跨进程条件更新而不是线程锁。"""
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": 30},
    )
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            claim = claim_pending_train_job(
                db,
                owner=owner,
                lease_seconds=120,
                now=datetime(2026, 9, 18, 10, 0, 0),
            )
            output.put(owner if claim is not None else None)
    finally:
        engine.dispose()


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.sqlite'}")
    YoloTrainJob.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    db.add(
        YoloTrainJob(
            task_id=1,
            created_by=1,
            status=YoloTrainStatus.PENDING.value,
            model_weights="yolo11n.pt",
        )
    )
    db.commit()
    return engine, factory, db


def test_two_workers_only_one_can_claim_and_expired_lease_can_be_taken_over(tmp_path):
    engine, factory, db = _session(tmp_path)
    try:
        now = datetime(2026, 9, 18, 10, 0, 0)
        first = claim_pending_train_job(
            db, owner="worker-a", lease_seconds=30, now=now
        )
        assert first is not None
        assert claim_pending_train_job(
            db, owner="worker-b", lease_seconds=30, now=now + timedelta(seconds=1)
        ) is None
        with factory() as takeover_db:
            second = claim_pending_train_job(
                takeover_db,
                owner="worker-b",
                lease_seconds=30,
                now=now + timedelta(seconds=31),
            )
        assert second is not None
        assert second.job_id == first.job_id
    finally:
        db.close()
        engine.dispose()


def test_release_queue_claim_is_owner_scoped(tmp_path):
    engine, factory, db = _session(tmp_path)
    try:
        claim = claim_pending_train_job(db, owner="worker-a", lease_seconds=30)
        assert claim is not None
        with factory() as other_db:
            assert not release_queue_claim(
                other_db,
                claim.__class__(
                    job_id=claim.job_id,
                    owner="worker-b",
                    expires_at=claim.expires_at,
                ),
            )
            assert release_queue_claim(other_db, claim)
    finally:
        db.close()
        engine.dispose()


def test_dedicated_switch_runs_after_schema_and_security_are_ready(monkeypatch, tmp_path):
    from labelu.internal.application.service import yolo_train_worker as worker

    engine, factory, db = _session(tmp_path)
    monkeypatch.setattr(worker.settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(worker.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr("labelu.internal.common.db.SessionLocal", factory)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._resolve_train_start_slot",
        lambda _job_id, _command: (True, "ok", "cpu"),
    )
    monkeypatch.setattr(worker, "_pause_or_stop_requested", lambda _job_id: False)
    try:
        ready, reason = dedicated_worker_ready()
        assert ready is True
        assert reason == "ok"
        result = run_worker_once(
            factory,
            owner="worker-a",
            runner=lambda *_args, **_kwargs: None,
        )
        assert result.disabled is False
        assert result.claimed == 1
        assert result.started == 1
        assert db.query(YoloTrainJob).first().queue_lease_owner is None
    finally:
        db.close()
        engine.dispose()


def test_default_runner_passes_execution_lease_check_to_subprocess(monkeypatch):
    from types import SimpleNamespace

    from labelu.internal.application.service import yolo_train_worker as worker

    calls = []

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._run_yolo_train_subprocess",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    check = lambda: None
    job = SimpleNamespace(id=7, task_id=8, job_type="yolo_detection")
    command = SimpleNamespace()
    worker._default_runner(
        job,
        command,
        resume=True,
        train_device="0",
        lease_check=check,
    )
    assert calls and calls[0][1]["lease_check"] is check


def test_default_runner_passes_runtime_pid_update_to_subprocess(monkeypatch):
    from types import SimpleNamespace

    from labelu.internal.application.service import yolo_train_worker as worker

    calls = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._run_yolo_train_subprocess",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    update = lambda _pid: None
    worker._default_runner(
        SimpleNamespace(id=9, task_id=10, job_type="yolo_detection"),
        SimpleNamespace(),
        resume=False,
        train_device="cpu",
        runtime_update=update,
    )
    assert calls and calls[0][1]["runtime_update"] is update


def test_default_runner_passes_execution_fence_context_to_subprocess(monkeypatch):
    from types import SimpleNamespace

    from labelu.internal.application.service import yolo_train_worker as worker

    calls = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._run_yolo_train_subprocess",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    worker._default_runner(
        SimpleNamespace(id=11, task_id=12, job_type="yolo_detection"),
        SimpleNamespace(),
        resume=False,
        train_device="cpu",
        execution_fencing_token=7,
        execution_owner="worker-a",
    )
    assert calls
    assert calls[0][1]["execution_fencing_token"] == 7
    assert calls[0][1]["execution_owner"] == "worker-a"


def test_default_runner_keeps_plate_subprocess_signature_compatible(monkeypatch):
    from types import SimpleNamespace

    from labelu.internal.application.service import yolo_train_worker as worker

    calls = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._run_plate_recognition_train_subprocess",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    worker._default_runner(
        SimpleNamespace(id=13, task_id=14, job_type="plate_recognition"),
        SimpleNamespace(),
        resume=False,
        train_device="cpu",
        execution_fencing_token=8,
        execution_owner="worker-b",
    )
    assert calls and calls[0][1]["execution_owner"] == "worker-b"


def test_train_result_write_fence_rejects_stale_token(tmp_path):
    from labelu.internal.application.service.yolo_train import (
        TrainExecutionFenceLost,
        _assert_train_execution_fence,
    )

    engine, factory, db = _session(tmp_path)
    try:
        row = db.query(YoloTrainJob).one()
        row.execution_lease_owner = "worker-a"
        row.execution_lease_expires_at = datetime.now() + timedelta(minutes=5)
        row.execution_fencing_token = 3
        db.commit()

        _assert_train_execution_fence(
            db,
            job_id=row.id,
            execution_owner="worker-a",
            execution_fencing_token=3,
        )
        try:
            _assert_train_execution_fence(
                db,
                job_id=row.id,
                execution_owner="worker-a",
                execution_fencing_token=2,
            )
        except TrainExecutionFenceLost:
            pass
        else:
            raise AssertionError("stale training token was accepted")
    finally:
        db.close()
        engine.dispose()


def test_legacy_result_write_rejects_active_execution_lease(tmp_path):
    from labelu.internal.application.service.yolo_train import (
        TrainExecutionFenceLost,
        _assert_train_result_write_fence,
    )

    engine, factory, db = _session(tmp_path)
    try:
        row = db.query(YoloTrainJob).one()
        row.execution_lease_owner = "worker-a"
        row.execution_lease_expires_at = datetime.now() + timedelta(minutes=5)
        row.execution_fencing_token = 4
        db.commit()

        with pytest.raises(TrainExecutionFenceLost, match="有效执行租约"):
            _assert_train_result_write_fence(
                db,
                job_id=row.id,
                execution_owner=None,
                execution_fencing_token=None,
            )
    finally:
        db.close()
        engine.dispose()


def test_stale_recovery_status_update_is_atomic_against_new_execution_lease(tmp_path):
    """旧回收线程不能覆盖已被独立 Worker 接管的作业。"""
    from labelu.internal.adapter.persistence import crud_yolo_train_job

    engine, factory, db = _session(tmp_path)
    try:
        row = db.query(YoloTrainJob).one()
        row.status = YoloTrainStatus.PROCESSING.value
        row.execution_lease_owner = "worker-new"
        row.execution_lease_expires_at = datetime.now() + timedelta(minutes=5)
        row.execution_fencing_token = 9
        db.commit()

        assert (
            crud_yolo_train_job.update_status_if_execution_lease_free(
                db,
                row.id,
                YoloTrainStatus.PENDING.value,
                error_message=None,
            )
            is None
        )
        db.refresh(row)
        assert row.status == YoloTrainStatus.PROCESSING.value

        row.execution_lease_expires_at = datetime.now() - timedelta(seconds=1)
        db.commit()
        updated = crud_yolo_train_job.update_status_if_execution_lease_free(
            db,
            row.id,
            YoloTrainStatus.PENDING.value,
            error_message=None,
        )
        assert updated is not None
        assert updated.status == YoloTrainStatus.PENDING.value
    finally:
        db.close()
        engine.dispose()


def test_twenty_processes_only_one_can_claim_same_training_job(tmp_path):
    """20 个独立进程并发领取时只能有一个成功，其他进程必须无副作用。"""
    engine, factory, db = _session(tmp_path)
    db.close()
    engine.dispose()

    ctx = mp.get_context("spawn")
    output = ctx.Queue()
    processes = [
        ctx.Process(
            target=_claim_worker_process,
            args=(str(tmp_path / "worker.sqlite"), f"worker-{index}", output),
        )
        for index in range(20)
    ]
    for process in processes:
        process.start()
    for process in processes:
        # Windows spawn 同时拉起 20 个进程时，SQLite 文件锁等待和模块启动
        # 可能超过 30 秒；这里放宽测试等待上限，不改变领取逻辑或生产租约。
        process.join(90)
        assert process.exitcode == 0

    claimed = [output.get(timeout=5) for _ in processes]
    successful = [owner for owner in claimed if owner is not None]
    assert len(successful) == 1

    verify_engine = create_engine(f"sqlite:///{tmp_path / 'worker.sqlite'}")
    verify_factory = sessionmaker(bind=verify_engine)
    try:
        with verify_factory() as verify_db:
            row = verify_db.query(YoloTrainJob).one()
            assert row.queue_lease_owner == successful[0]
            assert row.status == YoloTrainStatus.PENDING.value
    finally:
        verify_engine.dispose()
