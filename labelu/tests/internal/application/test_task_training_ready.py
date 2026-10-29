import json
from types import SimpleNamespace

from labelu.internal.application.service import task_training_ready as svc
from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus


class _NoopTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_refresh_task_training_ready_promotes_inprogress_task_back_to_finished(monkeypatch):
    task = Task()
    task.id = 14
    task.media_type = MediaType.IMAGE.value
    task.status = TaskStatus.INPROGRESS.value
    task.config = json.dumps({"defaultRectPresetId": "garbageStreet"}, ensure_ascii=False)

    monkeypatch.setattr(svc, "begin_transaction", lambda _db: _NoopTransaction())
    monkeypatch.setattr(
        svc.crud_sample,
        "list_by_task",
        lambda db, task_id: [SimpleNamespace(id=1)],
    )
    monkeypatch.setattr(
        svc,
        "audit_task_config_and_samples",
        lambda config, samples: {
            "training_ready": True,
            "total_samples": 1,
            "passed_samples": 1,
            "failed_samples_count": 0,
        },
    )
    monkeypatch.setattr(
        svc.crud_sample,
        "statics",
        lambda db, task_ids: {f"{task.id}_NEW": 0},
    )

    updates = {}

    def fake_update(db, db_obj, obj_in):
        updates.update(obj_in)
        for key, value in obj_in.items():
            setattr(db_obj, key, value)
        return db_obj

    monkeypatch.setattr(svc.crud_task, "update", fake_update)

    audit = svc.refresh_task_training_ready(db=None, task=task)

    assert audit["training_ready"] is True
    assert updates[Task.training_ready.key] is True
    assert updates[Task.status.key] == TaskStatus.FINISHED.value
    assert task.status == TaskStatus.FINISHED.value


def test_refresh_task_training_ready_promotes_configured_task_back_to_finished(monkeypatch):
    task = Task()
    task.id = 15
    task.media_type = MediaType.IMAGE.value
    task.status = TaskStatus.CONFIGURED.value
    task.config = json.dumps({"defaultRectPresetId": "garbageStreet"}, ensure_ascii=False)

    monkeypatch.setattr(svc, "begin_transaction", lambda _db: _NoopTransaction())
    monkeypatch.setattr(
        svc.crud_sample,
        "list_by_task",
        lambda db, task_id: [SimpleNamespace(id=1)],
    )
    monkeypatch.setattr(
        svc,
        "audit_task_config_and_samples",
        lambda config, samples: {
            "training_ready": True,
            "total_samples": 1,
            "passed_samples": 1,
            "failed_samples_count": 0,
        },
    )
    monkeypatch.setattr(
        svc.crud_sample,
        "statics",
        lambda db, task_ids: {f"{task.id}_NEW": 0},
    )

    updates = {}

    def fake_update(db, db_obj, obj_in):
        updates.update(obj_in)
        for key, value in obj_in.items():
            setattr(db_obj, key, value)
        return db_obj

    monkeypatch.setattr(svc.crud_task, "update", fake_update)

    audit = svc.refresh_task_training_ready(db=None, task=task)

    assert audit["training_ready"] is True
    assert updates[Task.training_ready.key] is True
    assert updates[Task.status.key] == TaskStatus.FINISHED.value
    assert task.status == TaskStatus.FINISHED.value


def test_refresh_task_training_ready_enqueues_dataset_plan_event(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)
    task = Task()
    task.id = 16
    task.media_type = MediaType.IMAGE.value
    task.status = TaskStatus.INPROGRESS.value
    task.config = json.dumps({"defaultRectPresetId": "garbageStreet"}, ensure_ascii=False)
    task.dataset_category_id = 8
    task.created_by = 1
    task.current_annotation_contract_id = 99

    class _Query:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return type(
                "Contract",
                (),
                {
                    "status": "active",
                    "label_set_signature": "a" * 64,
                    "class_order_signature": "b" * 64,
                },
            )()

    class _DB:
        def query(self, model):
            return _Query()

    monkeypatch.setattr(svc, "begin_transaction", lambda _db: _NoopTransaction())
    monkeypatch.setattr(
        svc.crud_sample,
        "list_by_task",
        lambda db, task_id: [SimpleNamespace(id=1)],
    )
    monkeypatch.setattr(
        svc,
        "audit_task_config_and_samples",
        lambda config, samples: {"training_ready": True},
    )
    monkeypatch.setattr(
        svc.crud_sample,
        "statics",
        lambda db, task_ids: {f"{task.id}_NEW": 0},
    )
    monkeypatch.setattr(
        svc.crud_task,
        "update",
        lambda db, db_obj, obj_in: (
            [setattr(db_obj, key, value) for key, value in obj_in.items()]
            and db_obj
        ),
    )
    events = []
    monkeypatch.setattr(
        svc,
        "enqueue_auto_training_event",
        lambda db, **kwargs: events.append(kwargs),
    )

    svc.refresh_task_training_ready(db=_DB(), task=task)

    assert len(events) == 1
    assert events[0]["event_name"] == "task.training_ready.v1"
    assert events[0]["payload"]["task_id"] == task.id


def test_training_ready_does_not_enqueue_automatic_event_while_phase_one_is_read_only(
    monkeypatch,
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    events = []
    monkeypatch.setattr(
        svc,
        "enqueue_auto_training_event",
        lambda db, **kwargs: events.append(kwargs),
    )
    task = Task()
    task.id = 17
    task.training_ready = True
    task.dataset_category_id = 2
    task.current_annotation_contract_id = 3

    svc._enqueue_training_ready_event(object(), task)

    assert events == []


def test_sample_change_updates_cached_audit_without_loading_all_samples(monkeypatch):
    task = Task()
    task.id = 17
    task.media_type = MediaType.IMAGE.value
    task.status = TaskStatus.INPROGRESS.value
    task.training_ready = False
    task.dataset_category_id = None
    task.current_annotation_contract_id = None
    task.annotation_audit_json = json.dumps(
        {
            "training_ready": False,
            "total_samples": 2,
            "passed_samples": 1,
            "failed_samples_count": 1,
            "failed_missing_tags_count": 1,
            "failed_missing_rect_count": 1,
            "failed_geometry_count": 0,
            "failed_incomplete_count": 1,
            "failed_samples": [{"sample_id": 2, "filename": "2.jpg"}],
        }
    )

    monkeypatch.setattr(svc, "begin_transaction", lambda _db: _NoopTransaction())
    monkeypatch.setattr(svc.crud_task, "get", lambda db, task_id, lock=False: task)
    monkeypatch.setattr(
        svc.crud_sample,
        "list_by_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("incremental refresh must not load the whole task")
        ),
    )
    monkeypatch.setattr(
        svc.crud_sample,
        "statics",
        lambda db, task_ids: {f"{task.id}_NEW": 0},
    )

    def fake_update(db, db_obj, obj_in):
        for key, value in obj_in.items():
            setattr(db_obj, key, value)
        return db_obj

    monkeypatch.setattr(svc.crud_task, "update", fake_update)
    previous = {
        "sample_id": 2,
        "filename": "2.jpg",
        "ok": False,
        "tags_ok": False,
        "rect_ok": False,
        "geometry_ok": True,
        "sample_quality_state": "INCOMPLETE",
        "missing_tag_keys": ["weather"],
    }
    current = {
        "sample_id": 2,
        "filename": "2.jpg",
        "ok": True,
        "tags_ok": True,
        "rect_ok": True,
        "geometry_ok": True,
        "sample_quality_state": "DONE",
        "missing_tag_keys": [],
    }

    audit = svc.refresh_task_training_ready_after_sample_change(
        db=None,
        task=task,
        previous_audit=previous,
        current_audit=current,
        previous_state="DONE",
        current_state="DONE",
    )

    assert audit["training_ready"] is True
    assert audit["passed_samples"] == 2
    assert audit["failed_samples_count"] == 0
    assert audit["failed_samples"] == []
    assert task.status == TaskStatus.FINISHED.value
