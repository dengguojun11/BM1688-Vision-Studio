from types import SimpleNamespace

from labelu.internal.application.service import sample as sample_service
from labelu.internal.common import db as db_module
from labelu.internal.domain.models.sample import SampleState, TaskSample


class _NoopTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_bounds_check_job_restores_previous_abnormal_sample_when_geometry_is_normal(monkeypatch):
    sample = TaskSample(id=48718, task_id=41, state=SampleState.ABNORMAL.value)
    job = SimpleNamespace(id=1, status="PENDING")
    updates = []

    monkeypatch.setattr(db_module, "session_scope", lambda: _FakeSession())
    monkeypatch.setattr(sample_service, "begin_transaction", lambda _db: _NoopTransaction())
    monkeypatch.setattr(
        sample_service.crud_task,
        "get",
        lambda db, task_id: SimpleNamespace(id=task_id, config="{}"),
    )
    monkeypatch.setattr(sample_service.crud_sample, "list_by_task", lambda db, task_id: [sample])
    monkeypatch.setattr(sample_service.crud_sample, "get", lambda db, sample_id: sample)
    monkeypatch.setattr(
        sample_service,
        "audit_one_sample",
        lambda _sample, _config: {"sample_quality_state": SampleState.DONE.value},
    )
    monkeypatch.setattr(sample_service, "refresh_task_training_ready", lambda db, task: {})
    monkeypatch.setattr(sample_service.crud_sample_bounds_check_job, "get", lambda db, job_id: job)
    monkeypatch.setattr(sample_service.crud_sample_bounds_check_job, "update_status", lambda *args, **kwargs: job)
    monkeypatch.setattr(sample_service.crud_sample_bounds_check_job, "update_progress", lambda *args, **kwargs: job)
    monkeypatch.setattr(sample_service, "set_request_context", lambda **kwargs: None)
    monkeypatch.setattr(sample_service, "biz_log", lambda *args, **kwargs: None)

    def fake_update(db, db_obj, obj_in):
        updates.append(obj_in)
        for key, value in obj_in.items():
            setattr(db_obj, key, value)
        return db_obj

    monkeypatch.setattr(sample_service.crud_sample, "update", fake_update)

    sample_service._run_bounds_check_sync(job_id=1, task_id=41, user_id=7)

    assert updates == [
        {
            TaskSample.state.key: SampleState.DONE.value,
            TaskSample.updated_by.key: 7,
        }
    ]


def test_bounds_check_job_marks_incomplete_sample(monkeypatch):
    sample = TaskSample(id=48719, task_id=41, state=SampleState.DONE.value)
    task = SimpleNamespace(id=41, config="{}")
    job = SimpleNamespace(id=1, status="PENDING")
    updates = []
    progress = []

    monkeypatch.setattr(db_module, "session_scope", lambda: _FakeSession())
    monkeypatch.setattr(sample_service, "begin_transaction", lambda _db: _NoopTransaction())
    monkeypatch.setattr(sample_service.crud_task, "get", lambda db, task_id: task)
    monkeypatch.setattr(sample_service.crud_sample, "list_by_task", lambda db, task_id: [sample])
    monkeypatch.setattr(sample_service.crud_sample, "get", lambda db, sample_id: sample)
    monkeypatch.setattr(
        sample_service,
        "audit_one_sample",
        lambda _sample, _config: {"sample_quality_state": SampleState.INCOMPLETE.value},
    )
    monkeypatch.setattr(sample_service, "refresh_task_training_ready", lambda db, task: {})
    monkeypatch.setattr(sample_service.crud_sample_bounds_check_job, "get", lambda db, job_id: job)
    monkeypatch.setattr(sample_service.crud_sample_bounds_check_job, "update_status", lambda *args, **kwargs: job)
    monkeypatch.setattr(sample_service, "set_request_context", lambda **kwargs: None)
    monkeypatch.setattr(sample_service, "biz_log", lambda *args, **kwargs: None)

    def fake_update(db, db_obj, obj_in):
        updates.append(obj_in)
        for key, value in obj_in.items():
            setattr(db_obj, key, value)
        return db_obj

    def fake_progress(*args, **kwargs):
        progress.append(kwargs)
        return job

    monkeypatch.setattr(sample_service.crud_sample, "update", fake_update)
    monkeypatch.setattr(sample_service.crud_sample_bounds_check_job, "update_progress", fake_progress)

    sample_service._run_bounds_check_sync(job_id=1, task_id=41, user_id=7)

    assert updates == [
        {
            TaskSample.state.key: SampleState.INCOMPLETE.value,
            TaskSample.updated_by.key: 7,
        }
    ]
    assert {"incomplete_delta": 1} in progress
