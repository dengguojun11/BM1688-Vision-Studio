from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from labelu.internal.application.service import edge_validation_worker_runtime as worker
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


class _FakeDb:
    def close(self):
        return None


def test_worker_disabled_does_not_dispatch(monkeypatch):
    monkeypatch.setattr(worker, "dedicated_worker_enabled", lambda: False)
    dispatch = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_scheduler.dispatch_pending_edge_validation_jobs",
        lambda **kwargs: dispatch.append(kwargs),
    )
    result = worker.run_worker_once(lambda: _FakeDb())
    assert result.enabled is False
    assert result.ready is False
    assert dispatch == []


def test_worker_fail_closes_when_schema_not_ready(monkeypatch):
    monkeypatch.setattr(worker, "dedicated_worker_enabled", lambda: True)
    monkeypatch.setattr(worker, "dedicated_worker_ready", lambda: (False, "缺少字段"))
    result = worker.run_worker_once(lambda: _FakeDb())
    assert result.enabled is True
    assert result.ready is False
    assert result.reason == "缺少字段"


def test_worker_dispatches_without_starting_web_threads(monkeypatch):
    monkeypatch.setattr(worker, "dedicated_worker_enabled", lambda: True)
    monkeypatch.setattr(worker, "dedicated_worker_ready", lambda: (True, "ok"))
    dispatch = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_scheduler.dispatch_pending_edge_validation_jobs",
        lambda **kwargs: dispatch.append(kwargs),
    )
    monkeypatch.setattr(worker, "candidate_ids", lambda db, **kwargs: [11, 12])
    started = []
    result = worker.run_worker_once(
        lambda: _FakeDb(),
        limit=2,
        run_job=lambda job_id: started.append(job_id),
    )
    assert result.ready is True
    assert result.candidates == 2
    assert result.started == 2
    assert result.failed == 0
    assert started == [11, 12]
    assert dispatch == [{"start_workers": False}]


def test_candidate_ids_include_expired_execution_lease(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'edge-worker.sqlite'}")
    YoloEdgeValidationJob.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(
            YoloEdgeValidationJob(
                user_id=1,
                task_id=1,
                export_id=1,
                status="PROCESSING",
                edge_device_id="edge-a",
                execution_lease_owner="old-worker",
                execution_lease_expires_at=datetime.now() - timedelta(seconds=1),
            )
        )
        db.commit()
        assert worker.candidate_ids(db, limit=1)
    engine.dispose()


def test_candidate_ids_scan_past_board_agent_rows_to_find_ssh_work(tmp_path, monkeypatch):
    """Board Agent rows must not consume the SSH worker's entire scan window."""

    engine = create_engine(f"sqlite:///{tmp_path / 'edge-worker-mixed.sqlite'}")
    YoloEdgeValidationJob.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for device_id in ("board-a", "board-b", "ssh-a"):
            db.add(
                YoloEdgeValidationJob(
                    user_id=1,
                    task_id=1,
                    export_id=1,
                    status="PROCESSING",
                    edge_device_id=device_id,
                )
            )
        db.commit()
        records = {
            "board-a": {"validation_channel": "board_agent"},
            "board-b": {"validation_channel": "板端主动拉取"},
            "ssh-a": {"validation_channel": "ssh"},
        }
        monkeypatch.setattr(
            worker.edge_device_registry,
            "find_device_record",
            lambda device_id: records.get(device_id),
        )

        assert worker.candidate_ids(db, limit=1) == [3]
    engine.dispose()


def test_candidate_ids_keeps_scanning_until_requested_ssh_batch(tmp_path, monkeypatch):
    """The scan must use the filtered count, not the raw SQL batch count."""

    engine = create_engine(f"sqlite:///{tmp_path / 'edge-worker-large-mixed.sqlite'}")
    YoloEdgeValidationJob.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        device_ids = [f"board-{index}" for index in range(40)] + ["ssh-a", "ssh-b"]
        for device_id in device_ids:
            db.add(
                YoloEdgeValidationJob(
                    user_id=1,
                    task_id=1,
                    export_id=1,
                    status="PROCESSING",
                    edge_device_id=device_id,
                )
            )
        db.commit()
        monkeypatch.setattr(
            worker.edge_device_registry,
            "find_device_record",
            lambda device_id: (
                {"validation_channel": "board_agent"}
                if device_id.startswith("board-")
                else {"validation_channel": "ssh"}
            ),
        )

        assert worker.candidate_ids(db, limit=2) == [41, 42]
    engine.dispose()
