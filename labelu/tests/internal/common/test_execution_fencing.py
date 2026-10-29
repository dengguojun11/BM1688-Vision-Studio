from datetime import datetime, timedelta
import multiprocessing as mp

from sqlalchemy import Column, DateTime, Integer, String, create_engine
from contextlib import contextmanager

from sqlalchemy.orm import declarative_base, sessionmaker

from labelu.internal.common.execution_fencing import (
    ExecutionLeaseMonitor,
    claim_execution_lease,
    conditional_execution_update,
    execution_lease_is_active,
    execution_lease_is_current,
    release_execution_lease,
    renew_execution_lease,
    ExecutionLeaseMonitor,
)


Base = declarative_base()


def test_execution_lease_is_active_requires_owner_and_unexpired_datetime():
    now = datetime(2026, 9, 18, 12, 0, 0)

    assert not execution_lease_is_active(type("Record", (), {})(), now=now)
    assert not execution_lease_is_active(
        type(
            "Record",
            (),
            {"execution_lease_owner": "worker-a", "execution_lease_expires_at": None},
        )(),
        now=now,
    )
    assert execution_lease_is_active(
        type(
            "Record",
            (),
            {
                "execution_lease_owner": "worker-a",
                "execution_lease_expires_at": now + timedelta(seconds=1),
            },
        )(),
        now=now,
    )
    assert not execution_lease_is_active(
        type(
            "Record",
            (),
            {
                "execution_lease_owner": "worker-a",
                "execution_lease_expires_at": now,
            },
        )(),
        now=now,
    )


def test_execution_lease_is_active_fails_closed_for_invalid_time_value():
    assert not execution_lease_is_active(
        type(
            "Record",
            (),
            {
                "execution_lease_owner": "worker-a",
                "execution_lease_expires_at": "not-a-datetime",
            },
        )(),
        now=datetime(2026, 9, 18, 12, 0, 0),
    )


class _Work(Base):
    __tablename__ = "execution_fencing_work"

    id = Column(Integer, primary_key=True)
    status = Column(String(32), nullable=False, default="PENDING")
    execution_lease_owner = Column(String(128), nullable=True, index=True)
    execution_lease_expires_at = Column(DateTime(), nullable=True, index=True)
    execution_fencing_token = Column(Integer, nullable=False, default=0)


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fencing.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    db.add(_Work(id=1))
    db.commit()
    return engine, db


def _process_claim_then_stale_update(
    db_path: str,
    owner: str,
    claim_now_iso: str,
    takeover_now_iso: str,
    ready,
    proceed,
    result_queue,
) -> None:
    """独立进程持有旧 token，等待另一进程接管后尝试迟到写入。"""
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        token = claim_execution_lease(
            db,
            _Work,
            1,
            owner=owner,
            lease_seconds=30,
            now=datetime.fromisoformat(claim_now_iso),
        )
        result_queue.put(("claimed", owner, token.fencing_token if token else None))
        ready.set()
        proceed.wait(timeout=10)
        updated = conditional_execution_update(
            db,
            token,
            {"status": "OLD_WORKER_WRITE"},
            now=datetime.fromisoformat(takeover_now_iso),
        ) if token is not None else False
        result_queue.put(("stale_update", owner, bool(updated)))
    except Exception as exc:  # pragma: no cover - surfaced through the queue
        result_queue.put(("error", owner, repr(exc)))
    finally:
        db.close()
        engine.dispose()


def _process_takeover_and_update(
    db_path: str,
    owner: str,
    takeover_now_iso: str,
    result_queue,
) -> None:
    """独立进程接管过期租约并写入新状态。"""
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        now = datetime.fromisoformat(takeover_now_iso)
        token = claim_execution_lease(
            db, _Work, 1, owner=owner, lease_seconds=30, now=now
        )
        updated = conditional_execution_update(
            db,
            token,
            {"status": "NEW_WORKER_WRITE"},
            now=now + timedelta(seconds=1),
        ) if token is not None else False
        result_queue.put(
            ("takeover", owner, token.fencing_token if token else None, bool(updated))
        )
    except Exception as exc:  # pragma: no cover - surfaced through the queue
        result_queue.put(("error", owner, repr(exc)))
    finally:
        db.close()
        engine.dispose()


def test_execution_lease_takeover_fences_old_worker(tmp_path):
    engine, db = _session(tmp_path)
    try:
        first_now = datetime(2026, 9, 18, 10, 0, 0)
        first = claim_execution_lease(
            db, _Work, 1, owner="worker-a", lease_seconds=30, now=first_now
        )
        assert first is not None
        assert first.fencing_token == 1
        assert claim_execution_lease(
            db,
            _Work,
            1,
            owner="worker-b",
            lease_seconds=30,
            now=first_now + timedelta(seconds=1),
        ) is None
        assert conditional_execution_update(
            db,
            first,
            {"status": "PROCESSING"},
            now=first_now + timedelta(seconds=2),
        )

        takeover_now = first_now + timedelta(seconds=31)
        second = claim_execution_lease(
            db, _Work, 1, owner="worker-b", lease_seconds=30, now=takeover_now
        )
        assert second is not None
        assert second.fencing_token == 2
        assert not execution_lease_is_current(
            db, first, now=takeover_now + timedelta(seconds=1)
        )
        assert not conditional_execution_update(
            db,
            first,
            {"status": "COMPLETED"},
            now=takeover_now + timedelta(seconds=1),
        )
        assert conditional_execution_update(
            db,
            second,
            {"status": "COMPLETED"},
            now=takeover_now + timedelta(seconds=1),
        )
    finally:
        db.close()
        engine.dispose()


def test_execution_lease_takeover_fences_across_processes(tmp_path):
    """两个独立进程接管同一任务时，旧进程不能迟到写入。"""
    engine, db = _session(tmp_path)
    db.close()
    engine.dispose()

    context = mp.get_context("spawn")
    result_queue = context.Queue()
    ready = context.Event()
    proceed = context.Event()
    db_path = str(tmp_path / "fencing.sqlite")
    first_now = datetime(2026, 9, 18, 14, 0, 0)
    takeover_now = first_now + timedelta(seconds=31)
    old_worker = context.Process(
        target=_process_claim_then_stale_update,
        args=(
            db_path,
            "process-a",
            first_now.isoformat(),
            (takeover_now + timedelta(seconds=1)).isoformat(),
            ready,
            proceed,
            result_queue,
        ),
    )
    new_worker = None
    try:
        old_worker.start()
        assert ready.wait(timeout=10)
        assert result_queue.get(timeout=10) == ("claimed", "process-a", 1)

        new_worker = context.Process(
            target=_process_takeover_and_update,
            args=(db_path, "process-b", takeover_now.isoformat(), result_queue),
        )
        new_worker.start()
        assert result_queue.get(timeout=10) == ("takeover", "process-b", 2, True)

        proceed.set()
        assert result_queue.get(timeout=10) == ("stale_update", "process-a", False)
    finally:
        proceed.set()
        old_worker.join(timeout=10)
        if new_worker is not None:
            new_worker.join(timeout=10)
        if old_worker.is_alive():
            old_worker.terminate()
        if new_worker is not None and new_worker.is_alive():
            new_worker.terminate()
        result_queue.close()
        result_queue.join_thread()

    engine = create_engine(f"sqlite:///{db_path}")
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        row = db.query(_Work).filter(_Work.id == 1).one()
        assert row.status == "NEW_WORKER_WRITE"
        assert row.execution_lease_owner == "process-b"
        assert row.execution_fencing_token == 2
    finally:
        db.close()
        engine.dispose()


def test_execution_lease_renew_and_release_require_current_token(tmp_path):
    engine, db = _session(tmp_path)
    try:
        now = datetime(2026, 9, 18, 11, 0, 0)
        token = claim_execution_lease(
            db, _Work, 1, owner="worker-a", lease_seconds=10, now=now
        )
        assert token is not None
        renewed = renew_execution_lease(
            db, token, lease_seconds=20, now=now + timedelta(seconds=5)
        )
        assert renewed is not None
        assert renewed.fencing_token == token.fencing_token
        assert execution_lease_is_current(
            db, renewed, now=now + timedelta(seconds=6)
        )
        assert release_execution_lease(
            db, renewed, now=now + timedelta(seconds=7)
        )
        assert not execution_lease_is_current(
            db, renewed, now=now + timedelta(seconds=8)
        )
        assert renew_execution_lease(
            db, renewed, lease_seconds=20, now=now + timedelta(seconds=8)
        ) is None
    finally:
        db.close()
        engine.dispose()


def test_conditional_update_can_join_existing_transaction(tmp_path):
    """进度回写位于业务事务内时，不应另开会话造成锁竞争。"""
    engine, db = _session(tmp_path)
    try:
        now = datetime(2026, 9, 18, 12, 0, 0)
        token = claim_execution_lease(
            db, _Work, 1, owner="worker-a", lease_seconds=30, now=now
        )
        assert token is not None
        with db.begin():
            assert conditional_execution_update(
                db,
                token,
                {"status": "PROCESSING"},
                now=now + timedelta(seconds=1),
                manage_transaction=False,
            )
            assert db.query(_Work).filter_by(id=1).one().status == "PROCESSING"
        assert db.query(_Work).filter_by(id=1).one().status == "PROCESSING"
    finally:
        db.close()
        engine.dispose()


def test_execution_lease_monitor_joins_business_session(tmp_path):
    engine, db = _session(tmp_path)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def _scope():
        scoped = factory()
        try:
            yield scoped
        finally:
            scoped.close()

    try:
        now = datetime.now()
        token = claim_execution_lease(
            db,
            _Work,
            1,
            owner="worker-a",
            lease_seconds=30,
            now=now,
        )
        assert token is not None
        monitor = ExecutionLeaseMonitor(
            token, session_scope_factory=_scope, interval_seconds=60
        )
        with db.begin():
            monitor.conditional_update({"status": "PROCESSING"}, db=db)
        assert db.query(_Work).filter_by(id=1).one().status == "PROCESSING"
    finally:
        db.close()
        engine.dispose()


def test_monitor_conditional_update_reuses_session_and_commits(tmp_path):
    """BM1688 长任务更新不能为同一行再开一个写 Session。"""
    engine, db = _session(tmp_path)
    factory = sessionmaker(bind=engine)
    scope_calls = []

    from contextlib import contextmanager

    @contextmanager
    def _scope():
        scope_calls.append(True)
        scoped_db = factory()
        try:
            yield scoped_db
        finally:
            scoped_db.close()

    try:
        now = datetime.now()
        token = claim_execution_lease(
            db, _Work, 1, owner="worker-a", lease_seconds=30, now=now
        )
        assert token is not None
        monitor = ExecutionLeaseMonitor(
            token,
            session_scope_factory=_scope,
            interval_seconds=60,
        )
        monitor.conditional_update(
            {"status": "PROCESSING"},
            db=db,
            manage_transaction=True,
        )
        assert len(scope_calls) == 1, "更新不应再开启第二个独立写 Session"
        observer = factory()
        try:
            assert observer.query(_Work).filter_by(id=1).one().status == "PROCESSING"
        finally:
            observer.close()
    finally:
        db.close()
        engine.dispose()
