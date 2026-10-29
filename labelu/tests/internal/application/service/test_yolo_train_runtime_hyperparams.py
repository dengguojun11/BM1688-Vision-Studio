import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


@pytest.fixture(autouse=True)
def _clear_active_train_jobs():
    """Keep process-wide training markers isolated between tests."""
    from labelu.internal.application.service import yolo_train as yolo_train_service

    yolo_train_service._ACTIVE_TRAIN_JOBS.clear()
    yield
    yolo_train_service._ACTIVE_TRAIN_JOBS.clear()


def _create_finished_task(db, user_id: int) -> int:
    with db.begin():
        task = Task(
            name="pytest-runtime-hp",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user_id,
        )
        db.add(task)
        db.flush()
        return int(task.id)


def _test_user(db):
    from labelu.tests.conftest import TEST_USERNAME
    from labelu.internal.adapter.persistence import crud_user

    return crud_user.get_user_by_username(db, username=TEST_USERNAME)


def _test_user_id(db) -> int:
    from labelu.tests.conftest import TestingSessionLocal

    lookup_db = TestingSessionLocal()
    try:
        return int(_test_user(lookup_db).id)
    finally:
        lookup_db.close()


def test_apply_runtime_hyperparams_restarts_failed_job_from_checkpoint(
    db, tmp_path, monkeypatch
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(yolo_train_service, "ensure_ultralytics_installed", lambda reason=None: True)
    launched = []
    monkeypatch.setattr(
        yolo_train_service,
        "_submit_yolo_train_job",
        lambda job_id, task_id, cmd, *, resume=False: launched.append((job_id, task_id, cmd, resume)),
    )
    user_id = _test_user_id(db)
    task_id = _create_finished_task(db, user_id)
    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / "job-9120"
    last_pt = work / "train" / "weights" / "last.pt"
    last_pt.parent.mkdir(parents=True, exist_ok=True)
    last_pt.write_bytes(b"fake")
    with db.begin():
        db.add(
            YoloTrainJob(
                id=9120,
                task_id=task_id,
                created_by=user_id,
                status=YoloTrainStatus.FAILED.value,
                model_weights="trained:83",
                epochs=150,
                imgsz=1280,
                hyperparams_json=json.dumps({"batch": 12, "optimizer": "AdamW"}, ensure_ascii=False),
            )
        )

    resp = yolo_train_service.apply_runtime_hyperparams(
        db=db,
        task_id=task_id,
        job_id=9120,
        current_user=_test_user(db),
        hyperparams={"batch": "6", "imgsz": "1024", "multi_scale": "false", "optimizer": "SGD"},
    )

    assert resp.status == YoloTrainStatus.PROCESSING.value
    assert resp.epochs == 150
    assert resp.imgsz == 1024
    assert resp.hyperparams == {"batch": 6, "optimizer": "AdamW", "multi_scale": False}
    assert launched and launched[0][3] is True
    assert launched[0][2].imgsz == 1024
    assert launched[0][2].hyperparams == {"batch": 6, "optimizer": "AdamW", "multi_scale": False}


class _MemoryTrainLog:
    def __init__(self):
        self.lines = []

    def write(self, line):
        self.lines.append(str(line))


def test_apply_runtime_hyperparams_waits_until_epoch_end_to_stop_running_job(
    db, tmp_path, monkeypatch
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    user_id = _test_user_id(db)
    task_id = _create_finished_task(db, user_id)
    with db.begin():
        db.add(
            YoloTrainJob(
                id=9121,
                task_id=task_id,
                created_by=user_id,
                status=YoloTrainStatus.PROCESSING.value,
                model_weights="trained:83",
                epochs=150,
                imgsz=1280,
                hyperparams_json=json.dumps({"batch": 12}, ensure_ascii=False),
            )
        )

    resp = yolo_train_service.apply_runtime_hyperparams(
        db=db,
        task_id=task_id,
        job_id=9121,
        current_user=_test_user(db),
        hyperparams={"batch": 6, "multi_scale": False},
    )

    pending = yolo_train_service.read_pending_runtime_hyperparams(9121)
    assert resp.status == YoloTrainStatus.PROCESSING.value
    assert pending["hyperparams"] == {"batch": 6, "multi_scale": False}
    assert pending["restart_after_epoch"] is True
    assert not yolo_train_service.is_stop_requested(9121)

    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / "job-9121"
    callbacks = yolo_train_service._make_train_process_callbacks(
        9121,
        _MemoryTrainLog(),
        work_root=work,
        target_epochs=150,
    )
    trainer = SimpleNamespace(epoch=8, stop=False)
    callbacks[2](trainer)

    assert trainer.stop is True
    assert yolo_train_service.is_stop_requested(9121)


def test_pending_runtime_hyperparams_restart_launches_even_while_job_is_active(
    db, tmp_path, monkeypatch
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(yolo_train_service, "ensure_ultralytics_installed", lambda reason=None: True)
    launched = []
    def _fake_submit(job_id, task_id, cmd, *, resume=False):
        yolo_train_service._ACTIVE_TRAIN_JOBS.add(int(job_id))
        launched.append((job_id, task_id, cmd, resume))

    monkeypatch.setattr(
        yolo_train_service,
        "_submit_yolo_train_job",
        _fake_submit,
    )
    user_id = _test_user_id(db)
    task_id = _create_finished_task(db, user_id)
    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / "job-9122"
    last_pt = work / "train" / "weights" / "last.pt"
    last_pt.parent.mkdir(parents=True, exist_ok=True)
    last_pt.write_bytes(b"fake")
    with db.begin():
        db.add(
            YoloTrainJob(
                id=9122,
                task_id=task_id,
                created_by=user_id,
                status=YoloTrainStatus.PROCESSING.value,
                model_weights="trained:83",
                epochs=150,
                imgsz=1280,
                hyperparams_json=json.dumps({"batch": 12}, ensure_ascii=False),
            )
        )

    yolo_train_service._write_pending_runtime_hyperparams(
        9122,
        {"batch": 8, "imgsz": 1024, "multi_scale": False},
    )
    yolo_train_service._ACTIVE_TRAIN_JOBS.add(9122)

    assert yolo_train_service._apply_pending_runtime_hyperparams_and_restart(9122)

    assert launched and launched[0][3] is True
    assert launched[0][2].imgsz == 1024
    assert launched[0][2].hyperparams == {"batch": 8, "multi_scale": False}
    assert 9122 in yolo_train_service._ACTIVE_TRAIN_JOBS


def test_train_callbacks_recheck_execution_fence_before_progress_write(tmp_path):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    calls = []
    callbacks = yolo_train_service._make_train_process_callbacks(
        9123,
        _MemoryTrainLog(),
        work_root=tmp_path / "job-9123",
        target_epochs=2,
        execution_guard=lambda: calls.append("checked"),
    )
    trainer = SimpleNamespace(epoch=0, stop=False)
    callbacks[0](trainer)
    callbacks[1](trainer)
    callbacks[2](trainer)
    callbacks[3](trainer)
    callbacks[4](trainer)

    assert calls == ["checked"] * 5


def test_pending_runtime_hyperparams_can_be_consumed_before_train_starts(
    db, tmp_path, monkeypatch
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    user_id = _test_user_id(db)
    task_id = _create_finished_task(db, user_id)
    with db.begin():
        db.add(
            YoloTrainJob(
                id=9123,
                task_id=task_id,
                created_by=user_id,
                status=YoloTrainStatus.PROCESSING.value,
                model_weights="trained:83",
                epochs=150,
                imgsz=1280,
                hyperparams_json=json.dumps({"batch": 12, "optimizer": "AdamW"}, ensure_ascii=False),
            )
        )

    yolo_train_service._write_pending_runtime_hyperparams(
        9123,
        {"batch": 8, "imgsz": 1024, "multi_scale": False},
    )

    cmd = yolo_train_service._consume_pending_runtime_hyperparams_for_current_run(9123)

    assert cmd is not None
    assert cmd.epochs == 150
    assert cmd.imgsz == 1024
    assert cmd.hyperparams == {"batch": 8, "optimizer": "AdamW", "multi_scale": False}
    assert yolo_train_service.read_pending_runtime_hyperparams(9123) is None
    assert not yolo_train_service.is_stop_requested(9123)


def test_pending_runtime_hyperparams_can_be_consumed_before_resume_starts(
    db, tmp_path, monkeypatch
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    user_id = _test_user_id(db)
    task_id = _create_finished_task(db, user_id)
    with db.begin():
        db.add(
            YoloTrainJob(
                id=9125,
                task_id=task_id,
                created_by=user_id,
                status=YoloTrainStatus.PROCESSING.value,
                model_weights="trained:83",
                epochs=150,
                imgsz=1280,
                hyperparams_json=json.dumps({"batch": 8, "multi_scale": True}, ensure_ascii=False),
            )
        )

    yolo_train_service._write_pending_runtime_hyperparams(
        9125,
        {"batch": 12, "imgsz": 1280, "multi_scale": False},
    )

    cmd = yolo_train_service._consume_pending_runtime_hyperparams_for_current_run(9125)

    assert cmd is not None
    assert cmd.hyperparams == {"batch": 12, "multi_scale": False}
    assert yolo_train_service.read_pending_runtime_hyperparams(125) is None


def test_pending_runtime_hyperparams_reject_stale_execution_owner(
    db, tmp_path, monkeypatch
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    user_id = _test_user_id(db)
    task_id = _create_finished_task(db, user_id)
    with db.begin():
        db.add(
            YoloTrainJob(
                id=9126,
                task_id=task_id,
                created_by=user_id,
                status=YoloTrainStatus.PROCESSING.value,
                model_weights="trained:83",
                epochs=150,
                imgsz=1280,
                hyperparams_json=json.dumps({"batch": 12}, ensure_ascii=False),
                execution_lease_owner="new-worker",
                execution_lease_expires_at=datetime.now() + timedelta(minutes=5),
                execution_fencing_token=2,
            )
        )

    yolo_train_service._write_pending_runtime_hyperparams(
        9126,
        {"batch": 8, "imgsz": 1024},
    )

    with pytest.raises(yolo_train_service.TrainExecutionFenceLost):
        yolo_train_service._consume_pending_runtime_hyperparams_for_current_run(
            9126,
            execution_owner="old-worker",
            execution_fencing_token=1,
        )

    assert yolo_train_service.read_pending_runtime_hyperparams(9126) is not None


def test_resume_train_kwargs_patch_checkpoint_args(tmp_path, monkeypatch):
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
    from labelu.internal.application.service import yolo_train as yolo_train_service

    saved = {}

    class _TorchStub:
        @staticmethod
        def load(path, **kwargs):
            saved["load_path"] = path
            saved["load_kwargs"] = kwargs
            return {
                "train_args": {
                    "batch": 8,
                    "imgsz": 1280,
                    "multi_scale": True,
                    "workers": 0,
                    "amp": False,
                }
            }

        @staticmethod
        def save(ckpt, path):
            saved["ckpt"] = ckpt
            saved["save_path"] = path

    monkeypatch.setitem(__import__("sys").modules, "torch", _TorchStub)
    monkeypatch.setattr(yolo_train_service, "_append_train_process_log", lambda *args, **kwargs: None)
    last_pt = tmp_path / "last.pt"
    last_pt.write_bytes(b"fake")
    cmd = CreateYoloTrainJobCommand(
        model_weights="trained:83",
        epochs=200,
        imgsz=1280,
        hyperparams={
            "batch": 12,
            "multi_scale": False,
            "workers": 4,
            "amp": True,
            "mosaic": 0.7,
        },
    )

    train_kw = yolo_train_service._build_resume_train_kwargs(
        last_path=last_pt,
        cmd=cmd,
        train_device="0,1",
        job_id=124,
    )

    patched = saved["ckpt"]["train_args"]
    assert train_kw["resume"] == str(last_pt)
    assert train_kw["batch"] == 12
    assert train_kw["workers"] == 0
    assert "multi_scale" not in train_kw
    assert patched["batch"] == 12
    assert patched["multi_scale"] is False
    assert patched["workers"] == 0
    assert patched["amp"] is False
    assert patched["mosaic"] == 0.7
