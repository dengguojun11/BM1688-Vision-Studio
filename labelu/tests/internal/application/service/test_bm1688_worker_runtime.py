from __future__ import annotations

import multiprocessing
import os
import queue
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from labelu.internal.application.service import bm1688_worker_runtime as runtime


_PROCESS_WAIT_TIMEOUT_SECONDS = 90


def _sqlite_url(path: str) -> str:
    return f"sqlite:///{Path(path).resolve().as_posix()}"


def _prepare_worker_db(
    db_path: Path,
    *,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    execution_lease_owner: str | None = None,
    execution_lease_expires_at: datetime | None = None,
) -> None:
    engine = create_engine(_sqlite_url(str(db_path)))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE yolo_bm1688_export (
                        id INTEGER PRIMARY KEY,
                        status VARCHAR(32) NOT NULL,
                        auto_training_round_id INTEGER,
                        pipeline_origin VARCHAR(32),
                        queue_lease_owner VARCHAR(128),
                        queue_lease_expires_at DATETIME,
                        execution_lease_owner VARCHAR(128),
                        execution_lease_expires_at DATETIME,
                        execution_fencing_token INTEGER NOT NULL DEFAULT 0,
                        worker_action VARCHAR(32),
                        updated_at DATETIME
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO yolo_bm1688_export (
                        id, status, auto_training_round_id, pipeline_origin, worker_action,
                        queue_lease_owner, queue_lease_expires_at,
                        execution_lease_owner, execution_lease_expires_at
                    ) VALUES (
                        1, 'PENDING', 7, 'AUTO_QUALITY', 'export', :owner, :expires,
                        :execution_owner, :execution_expires
                    )
                    """
                ),
                {
                    "owner": lease_owner,
                    "expires": lease_expires_at,
                    "execution_owner": execution_lease_owner,
                    "execution_expires": execution_lease_expires_at,
                },
            )
    finally:
        engine.dispose()


def _worker_process(
    db_path: str,
    marker_path: str,
    owner: str,
    current: datetime,
    ready_queue,
    start_event,
    candidate_barrier,
    result_queue,
) -> None:
    engine = create_engine(
        _sqlite_url(db_path),
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    factory = sessionmaker(bind=engine, autoflush=False)
    original_candidate_ids = runtime._candidate_ids

    def _candidate_ids_with_barrier(db, *, now, limit):
        candidate_ids = original_candidate_ids(db, now=now, limit=limit)
        candidate_barrier.wait(timeout=_PROCESS_WAIT_TIMEOUT_SECONDS)
        return candidate_ids

    runtime._candidate_ids = _candidate_ids_with_barrier

    def _execute(export_id: int) -> None:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE yolo_bm1688_export "
                    "SET status = 'COMPLETED' WHERE id = :export_id"
                ),
                {"export_id": int(export_id)},
            )
        with Path(marker_path).open("a", encoding="ascii") as stream:
            stream.write(f"{owner}:{int(export_id)}\n")

    try:
        ready_queue.put(owner)
        if not start_event.wait(timeout=_PROCESS_WAIT_TIMEOUT_SECONDS):
            raise RuntimeError("worker start barrier timed out")
        result = runtime.run_bm1688_worker_once(
            factory,
            owner=owner,
            limit=1,
            lease_seconds=30,
            enabled=True,
            execute=_execute,
            now=current,
        )
        result_queue.put(
            {"owner": owner, "pid": os.getpid(), "result": asdict(result)}
        )
    except Exception as exc:  # pragma: no cover - returned to the parent assertion
        result_queue.put({"owner": owner, "pid": os.getpid(), "error": repr(exc)})
    finally:
        engine.dispose()


def _run_two_processes(
    db_path: Path, marker_path: Path, *, current: datetime
) -> list[dict]:
    context = multiprocessing.get_context("spawn")
    ready_queue = context.Queue()
    result_queue = context.Queue()
    start_event = context.Event()
    candidate_barrier = context.Barrier(2)
    processes = [
        context.Process(
            target=_worker_process,
            args=(
                str(db_path),
                str(marker_path),
                f"worker-{index}",
                current,
                ready_queue,
                start_event,
                candidate_barrier,
                result_queue,
            ),
        )
        for index in (1, 2)
    ]
    try:
        for process in processes:
            process.start()
        try:
            ready = {
                ready_queue.get(timeout=_PROCESS_WAIT_TIMEOUT_SECONDS)
                for _ in processes
            }
        except queue.Empty as exc:
            states = [
                {
                    "pid": process.pid,
                    "alive": process.is_alive(),
                    "exitcode": process.exitcode,
                }
                for process in processes
            ]
            raise AssertionError(
                f"子进程启动超时，状态={states}"
            ) from exc
        assert ready == {"worker-1", "worker-2"}
        start_event.set()
        try:
            results = [
                result_queue.get(timeout=_PROCESS_WAIT_TIMEOUT_SECONDS)
                for _ in processes
            ]
        except queue.Empty as exc:
            states = [
                {
                    "pid": process.pid,
                    "alive": process.is_alive(),
                    "exitcode": process.exitcode,
                }
                for process in processes
            ]
            raise AssertionError(
                f"子进程结果等待超时，状态={states}"
            ) from exc
        for process in processes:
            process.join(timeout=_PROCESS_WAIT_TIMEOUT_SECONDS)
            assert not process.is_alive()
            assert process.exitcode == 0
        child_pids = {item["pid"] for item in results}
        assert len(child_pids) == 2
        assert os.getpid() not in child_pids
        return results
    finally:
        if not start_event.is_set():
            start_event.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=5)


def _worker_row(db_path: Path) -> dict:
    engine = create_engine(_sqlite_url(str(db_path)))
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, queue_lease_owner, queue_lease_expires_at "
                    "FROM yolo_bm1688_export WHERE id = 1"
                )
            ).mappings().one()
            return dict(row)
    finally:
        engine.dispose()


def test_dedicated_worker_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LABELU_BM1688_DEDICATED_WORKER_ENABLED", raising=False)
    result = runtime.run_bm1688_worker_once(lambda: None)
    assert result.enabled is False
    assert result.claimed == 0


def test_dedicated_worker_fails_closed_when_execution_fencing_columns_are_missing(
    tmp_path,
):
    engine = create_engine(_sqlite_url(str(tmp_path / "missing-fencing.sqlite")))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE yolo_bm1688_export ("
                    "id INTEGER PRIMARY KEY, status VARCHAR(32) NOT NULL)"
                )
            )
        factory = sessionmaker(bind=engine)
        result = runtime.run_bm1688_worker_once(
            factory,
            enabled=True,
            execute=lambda _export_id: (_ for _ in ()).throw(
                AssertionError("must not execute without fencing columns")
            ),
        )
        assert result.enabled is True
        assert result.ready is False
        assert result.candidates == 0
        assert "执行租约字段" in result.reason
    finally:
        engine.dispose()


def test_candidate_and_claim_scope_pending_export_actions():
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    candidate = source[source.index("def _candidate_ids"): source.index("def _claim_export")]
    claim = source[source.index("def _claim_export"): source.index("def _release_failed_claim")]
    for section in (candidate, claim):
        assert 'YoloBm1688Export.status == "PENDING"' in section
        assert "worker_action.is_(None)" in section
        assert "worker_action.in_(_SUPPORTED_ACTIONS)" in section
        assert "queue_lease_expires_at" in section
        assert "execution_lease_expires_at" in section


def test_worker_claim_commits_before_execute_and_dispatches_unified_entrypoint():
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    run_once = source[source.index("def run_bm1688_worker_once"): source.index("def run_bm1688_worker_loop")]
    assert "claim_db.close()" in run_once
    assert "execute(export_id)" in run_once
    assert "_run_bm1688_export_sync" in run_once
    assert "_run_bm1688_recompile_sync" in run_once
    assert "_complete_claim" in run_once
    assert "_release_failed_claim" in run_once
    assert "Claim transaction has committed" in run_once
    assert "execution_fencing_ready" in run_once


def test_worker_loop_reuses_one_owner_and_validates_positive_limits():
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    loop = source[source.index("def run_bm1688_worker_loop"): source.index("def main")]
    assert "worker_owner = owner or default_owner()" in loop
    assert "owner=worker_owner" in loop
    assert "interval_seconds must be positive" in loop


def test_two_processes_claim_one_pending_export_and_run_one_side_effect(tmp_path):
    current = datetime(2026, 9, 18, 14, 0, 0)
    db_path = tmp_path / "two-process-claim.sqlite"
    marker_path = tmp_path / "compile-side-effect.log"
    _prepare_worker_db(db_path)

    results = _run_two_processes(db_path, marker_path, current=current)

    assert all("error" not in item for item in results), results
    assert sum(item["result"]["claimed"] for item in results) == 1
    assert sum(item["result"]["executed"] for item in results) == 1
    assert sum(item["result"]["candidates"] for item in results) == 2
    assert sum(item["result"]["skipped"] for item in results) == 1
    assert marker_path.read_text(encoding="ascii").splitlines() in (
        ["worker-1:1"],
        ["worker-2:1"],
    )
    row = _worker_row(db_path)
    assert row["status"] == "COMPLETED"
    assert row["queue_lease_owner"] is None


def test_two_processes_take_over_one_expired_queue_lease(tmp_path):
    current = datetime(2026, 9, 18, 15, 0, 0)
    db_path = tmp_path / "expired-lease.sqlite"
    marker_path = tmp_path / "expired-lease-side-effect.log"
    _prepare_worker_db(
        db_path,
        lease_owner="dead-worker",
        lease_expires_at=current - timedelta(seconds=1),
    )

    results = _run_two_processes(db_path, marker_path, current=current)

    assert all("error" not in item for item in results), results
    assert sum(item["result"]["claimed"] for item in results) == 1
    assert sum(item["result"]["executed"] for item in results) == 1
    assert sum(item["result"]["candidates"] for item in results) == 2
    assert sum(item["result"]["skipped"] for item in results) == 1
    assert len(marker_path.read_text(encoding="ascii").splitlines()) == 1
    assert _worker_row(db_path)["queue_lease_owner"] is None


def test_two_processes_reject_one_unexpired_queue_lease(tmp_path):
    current = datetime(2026, 9, 18, 16, 0, 0)
    db_path = tmp_path / "live-lease.sqlite"
    marker_path = tmp_path / "live-lease-side-effect.log"
    _prepare_worker_db(
        db_path,
        lease_owner="live-worker",
        lease_expires_at=current + timedelta(seconds=60),
    )

    results = _run_two_processes(db_path, marker_path, current=current)

    assert all("error" not in item for item in results), results
    assert sum(item["result"]["claimed"] for item in results) == 0
    assert sum(item["result"]["executed"] for item in results) == 0
    assert sum(item["result"]["candidates"] for item in results) == 0
    assert not marker_path.exists()
    row = _worker_row(db_path)
    assert row["status"] == "PENDING"
    assert row["queue_lease_owner"] == "live-worker"


def test_worker_rejects_one_unexpired_execution_lease(tmp_path):
    current = datetime(2026, 9, 18, 17, 0, 0)
    db_path = tmp_path / "execution-lease.sqlite"
    marker_path = tmp_path / "execution-lease-side-effect.log"
    _prepare_worker_db(
        db_path,
        execution_lease_owner="compiler-a",
        execution_lease_expires_at=current + timedelta(seconds=60),
    )

    results = _run_two_processes(db_path, marker_path, current=current)

    assert all("error" not in item for item in results), results
    assert sum(item["result"]["claimed"] for item in results) == 0
    assert sum(item["result"]["executed"] for item in results) == 0
    assert sum(item["result"]["candidates"] for item in results) == 0
    assert not marker_path.exists()
    row = _worker_row(db_path)
    assert row["status"] == "PENDING"
    assert row["queue_lease_owner"] is None
