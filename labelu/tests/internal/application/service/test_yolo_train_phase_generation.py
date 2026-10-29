import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from labelu.internal.application.service import yolo_train as service


class _SessionContext:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *_args):
        return False


class _Query:
    def __init__(self, row):
        self.row = row

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.row


class _Session:
    def __init__(self, row):
        self.row = row

    def query(self, *_args, **_kwargs):
        return _Query(self.row)


def test_unfenced_phase_writer_cannot_overwrite_active_execution(tmp_path: Path, monkeypatch):
    job_root = tmp_path / "job-11"
    row = SimpleNamespace(
        execution_lease_owner="new-worker",
        execution_fencing_token=2,
        execution_lease_expires_at=datetime.now() + timedelta(minutes=5),
    )
    monkeypatch.setattr(service, "_job_work_root", lambda _job_id: job_root)
    monkeypatch.setattr(
        service, "session_scope", lambda: _SessionContext(_Session(row))
    )

    assert service._write_train_phase(11, "failed", detail="late old worker") is False
    assert not (job_root / "phase.json").exists()


def test_fenced_phase_writer_rejects_old_token_and_accepts_current_generation(
    tmp_path: Path, monkeypatch
):
    job_root = tmp_path / "job-12"
    monkeypatch.setattr(service, "_job_work_root", lambda _job_id: job_root)
    monkeypatch.setattr(
        service,
        "session_scope",
        lambda: _SessionContext(_Session(None)),
    )

    def reject(*_args, **_kwargs):
        raise service.TrainExecutionFenceLost("旧 token")

    monkeypatch.setattr(service, "_assert_train_execution_fence", reject)
    assert (
        service._write_train_phase(
            12,
            "failed",
            detail="stale",
            execution_owner="old-worker",
            execution_fencing_token=1,
        )
        is False
    )
    assert not (job_root / "phase.json").exists()

    monkeypatch.setattr(service, "_assert_train_execution_fence", lambda *_a, **_k: None)
    assert (
        service._write_train_phase(
            12,
            "running",
            detail="current",
            execution_owner="new-worker",
            execution_fencing_token=2,
        )
        is True
    )
    payload = json.loads((job_root / "phase.json").read_text(encoding="utf-8"))
    assert payload["execution_owner"] == "new-worker"
    assert payload["execution_fencing_token"] == 2
    assert payload["phase_generation"] == "new-worker:2"
