import asyncio
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.application.service import yolo_train as yolo_train_service
from labelu.internal.common.idempotency import request_payload_hash


def test_create_train_job_loads_primary_task_before_building_response(tmp_path: Path, monkeypatch):
    task = SimpleNamespace(id=18, name="垃圾暴露-训练筛选一致", config="{}")
    created_job = SimpleNamespace(id=88, task_id=18)
    current_user = SimpleNamespace(id=1)
    captured = {}

    @contextmanager
    def fake_begin_transaction(_db):
        yield

    monkeypatch.setattr(yolo_train_service.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(yolo_train_service.crud_task, "get", lambda db, task_id: task if task_id == 18 else None)
    monkeypatch.setattr(yolo_train_service, "_assert_task_access", lambda loaded_task, user: None)
    monkeypatch.setattr(
        yolo_train_service,
        "resolve_source_task_ids_for_create",
        lambda db, primary_task_id, cmd, current_user: ([primary_task_id], {primary_task_id: 9}),
    )
    monkeypatch.setattr(yolo_train_service, "ensure_ultralytics_installed", lambda reason=None: True)
    monkeypatch.setattr(yolo_train_service, "_validate_weights_source_at_create", lambda db, user, cmd: None)
    monkeypatch.setattr(yolo_train_service, "_model_weights_field_for_storage", lambda cmd: "yolo11n.pt")
    monkeypatch.setattr(yolo_train_service, "begin_transaction", fake_begin_transaction)
    monkeypatch.setattr(
        yolo_train_service.crud_yolo_train_job,
        "create",
        lambda **kwargs: created_job,
    )
    monkeypatch.setattr(yolo_train_service, "_job_work_root", lambda job_id: tmp_path / f"job-{job_id}")
    monkeypatch.setattr(yolo_train_service, "_append_train_process_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(yolo_train_service, "_submit_yolo_train_job", lambda job_id, task_id, cmd: None)

    def fake_job_to_response(db, job, response_task):
        captured["job"] = job
        captured["task"] = response_task
        return SimpleNamespace(
            id=job.id,
            task_id=job.task_id,
            merge_task_ids=None,
            merge_task_label=None,
            merge_task_sample_counts=None,
        )

    monkeypatch.setattr(yolo_train_service, "_job_to_response", fake_job_to_response)

    resp = asyncio.run(
        yolo_train_service.create_train_job(
            db=object(),
            task_id=18,
            cmd=CreateYoloTrainJobCommand(),
            current_user=current_user,
        )
    )

    assert resp.id == 88
    assert captured["job"] is created_job
    assert captured["task"] is task


def test_create_train_job_forces_local_execution_mode(tmp_path: Path, monkeypatch):
    task = SimpleNamespace(id=18, name="pytest-local-only", config="{}")
    created_job = SimpleNamespace(id=89, task_id=18)
    current_user = SimpleNamespace(id=1)
    captured = {}

    @contextmanager
    def fake_begin_transaction(_db):
        yield

    monkeypatch.setattr(yolo_train_service.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(yolo_train_service.crud_task, "get", lambda db, task_id: task if task_id == 18 else None)
    monkeypatch.setattr(yolo_train_service, "_assert_task_access", lambda loaded_task, user: None)
    monkeypatch.setattr(
        yolo_train_service,
        "resolve_source_task_ids_for_create",
        lambda db, primary_task_id, cmd, current_user: ([primary_task_id], {primary_task_id: 9}),
    )
    monkeypatch.setattr(yolo_train_service, "ensure_ultralytics_installed", lambda reason=None: True)
    monkeypatch.setattr(yolo_train_service, "_validate_weights_source_at_create", lambda db, user, cmd: None)
    monkeypatch.setattr(yolo_train_service, "_model_weights_field_for_storage", lambda cmd: "yolo11n.pt")
    monkeypatch.setattr(yolo_train_service, "begin_transaction", fake_begin_transaction)

    def fake_create(**kwargs):
        captured["create_kwargs"] = kwargs
        return created_job

    monkeypatch.setattr(yolo_train_service.crud_yolo_train_job, "create", fake_create)
    monkeypatch.setattr(yolo_train_service, "_job_work_root", lambda job_id: tmp_path / f"job-{job_id}")
    monkeypatch.setattr(yolo_train_service, "_append_train_process_log", lambda *args, **kwargs: None)

    def fake_submit(job_id, task_id, cmd):
        captured["submit_cmd"] = cmd

    monkeypatch.setattr(yolo_train_service, "_submit_yolo_train_job", fake_submit)
    monkeypatch.setattr(
        yolo_train_service,
        "_job_to_response",
        lambda db, job, response_task: SimpleNamespace(
            id=job.id,
            task_id=job.task_id,
            merge_task_ids=None,
            merge_task_label=None,
            merge_task_sample_counts=None,
        ),
    )

    asyncio.run(
        yolo_train_service.create_train_job(
            db=object(),
            task_id=18,
            cmd=CreateYoloTrainJobCommand(
                execution_mode="remote",
                train_node_id="gpu-54-99",
            ),
            current_user=current_user,
        )
    )

    assert captured["create_kwargs"]["execution_mode"] == "local"
    assert captured["create_kwargs"]["train_node_id"] is None
    assert captured["submit_cmd"].execution_mode == "local"
    assert captured["submit_cmd"].train_node_id is None


def test_create_train_job_idempotency_replays_without_dispatch(monkeypatch):
    task = SimpleNamespace(id=18, name="幂等训练任务", config="{}")
    current_user = SimpleNamespace(id=1)
    command = CreateYoloTrainJobCommand()
    request_hash = request_payload_hash({"task_id": 18, "command": command.dict()})
    existing = SimpleNamespace(
        id=901,
        task_id=18,
        idempotency_request_hash=request_hash,
        idempotency_key="train-create-retry-001",
    )
    dispatched = []

    monkeypatch.setattr(yolo_train_service.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(yolo_train_service.crud_task, "get", lambda db, task_id: task)
    monkeypatch.setattr(yolo_train_service, "_assert_task_access", lambda loaded_task, user: None)
    monkeypatch.setattr(
        yolo_train_service.crud_yolo_train_job,
        "get_by_idempotency",
        lambda db, creator_id, idempotency_key: existing,
    )
    monkeypatch.setattr(
        yolo_train_service,
        "_submit_yolo_train_job",
        lambda *args, **kwargs: dispatched.append(args),
    )
    monkeypatch.setattr(
        yolo_train_service,
        "_job_to_response",
        lambda db, job, response_task, include_training_task_detail=False: SimpleNamespace(
            id=job.id, task_id=job.task_id
        ),
    )

    response = asyncio.run(
        yolo_train_service.create_train_job(
            db=object(),
            task_id=18,
            cmd=command,
            current_user=current_user,
            idempotency_key="train-create-retry-001",
        )
    )

    assert response.id == 901
    assert dispatched == []


def test_create_train_job_for_plate_preset_preserves_detection_target(monkeypatch):
    task = SimpleNamespace(id=122, name="plate-task", config="{}")
    preset = SimpleNamespace(
        slug="plateRecognition",
        training_profile="plate_recognition_v1",
        items=[SimpleNamespace(label_value="doubleEP")],
    )
    created_job = SimpleNamespace(id=265, task_id=122)
    current_user = SimpleNamespace(id=1)
    captured = {}

    monkeypatch.setattr(yolo_train_service.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        yolo_train_service.crud_task,
        "get",
        lambda db, task_id: task if task_id == task.id else None,
    )
    monkeypatch.setattr(
        yolo_train_service, "_assert_task_access", lambda loaded_task, user: None
    )
    monkeypatch.setattr(
        yolo_train_service.crud_annotation_taxonomy,
        "label_preset_get_by_slug",
        lambda db, slug: preset if slug == preset.slug else None,
    )
    monkeypatch.setattr(
        yolo_train_service.training_task_service,
        "check_training_task_name_available",
        lambda db, user_id, name: True,
    )

    def fake_create_training_task(db, current_user, cmd):
        captured["cmd"] = cmd
        return SimpleNamespace(latest_job_id=created_job.id)

    monkeypatch.setattr(
        yolo_train_service.training_task_service,
        "create_label_dimension_training_task",
        fake_create_training_task,
    )
    monkeypatch.setattr(
        yolo_train_service.crud_yolo_train_job,
        "get",
        lambda db, job_id: created_job if job_id == created_job.id else None,
    )
    monkeypatch.setattr(
        yolo_train_service,
        "_job_to_response",
        lambda db, job, response_task, include_training_task_detail=False: SimpleNamespace(
            id=job.id,
            task_id=job.task_id,
        ),
    )

    response = asyncio.run(
        yolo_train_service.create_train_job(
            db=object(),
            task_id=task.id,
            cmd=CreateYoloTrainJobCommand(
                model_weights="yolov8n-pose.pt",
                weights_source="preset",
                rect_preset_slug=preset.slug,
                plate_training_target="plate_detection",
            ),
            current_user=current_user,
        )
    )

    assert response.id == 265
    assert captured["cmd"].selected_task_ids == [122]
    assert captured["cmd"].plate_training_target == "plate_detection"
