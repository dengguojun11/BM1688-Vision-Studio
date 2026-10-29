from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from labelu.internal.adapter.persistence import (
    crud_annotation_taxonomy,
    crud_task,
    crud_user,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.training_task import TrainingTask
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _get_test_user(db) -> User:
    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None
    return user


def _get_or_create_user(db, username: str) -> User:
    user = crud_user.get_user_by_username(db, username=username)
    if user is not None:
        return user
    with begin_transaction(db):
        return crud_user.create(db, User(username=username, hashed_password="x"))


def _seed_label_preset(db, *, slug: str, name: str, items: list[dict]):
    with begin_transaction(db):
        return crud_annotation_taxonomy.label_preset_create(
            db,
            slug=slug,
            name=name,
            description=None,
            sort_order=0,
            items=items,
        )


def _task_config_for_preset(slug: str, items: list[dict]) -> str:
    return json.dumps(
        {
            "defaultRectPresetId": slug,
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {
                        "attributes": [
                            {"key": item["label_key"], "value": item["label_value"]}
                            for item in items
                        ]
                    },
                }
            ],
        },
        ensure_ascii=False,
    )


def _seed_task(db, *, owner_id: int, name: str, config: str) -> Task:
    with begin_transaction(db):
        return crud_task.create(
            db,
            Task(
                name=name,
                description="",
                tips="",
                config=config,
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.INPROGRESS.value,
                training_ready=False,
                created_by=owner_id,
                updated_by=owner_id,
            ),
        )


def _seed_done_sample(
    db,
    *,
    owner_id: int,
    task_id: int,
    filename: str,
    path: str,
    rect_labels: list[str],
):
    outer = {
        "result": {
            "rectTool": {
                "result": [
                    {
                        "label": label,
                        "x": 1,
                        "y": 1,
                        "width": 10,
                        "height": 10,
                    }
                    for label in rect_labels
                ]
            }
        }
    }
    with begin_transaction(db):
        attachment = TaskAttachment(
            filename=filename,
            url=f"/media/{filename}",
            path=path,
            task_id=task_id,
            created_by=owner_id,
            updated_by=owner_id,
        )
        db.add(attachment)
        db.flush()
        sample = TaskSample(
            inner_id=1,
            task_id=task_id,
            file_id=attachment.id,
            created_by=owner_id,
            updated_by=owner_id,
            annotated_count=len(rect_labels),
            data=json.dumps(
                {"result": json.dumps(outer["result"], ensure_ascii=False)},
                ensure_ascii=False,
            ),
            state=SampleState.DONE.value,
        )
        db.add(sample)
        db.flush()
        return sample


def _plate_task_config() -> str:
    return json.dumps(
        {
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {
                        "attributes": [
                            {
                                "key": "双层车牌",
                                "value": "doubleEP",
                                "attributes": [
                                    {"key": "车牌号码", "value": "plateNumber"},
                                    {"key": "车牌颜色", "value": "color"},
                                    {"key": "车牌类型", "value": " plateType"},
                                ],
                            }
                        ]
                    },
                },
                {
                    "tool": "polygonTool",
                    "config": {
                        "attributes": [{"key": "关键点", "value": " platePoint"}]
                    },
                },
            ]
        },
        ensure_ascii=False,
    )


def _seed_plate_sample(
    db,
    *,
    owner_id: int,
    task_id: int,
    filename: str,
    result: dict,
    training_sample_type: str | None = None,
):
    with begin_transaction(db):
        attachment = TaskAttachment(
            filename=filename,
            url=f"/media/{filename}",
            path=filename,
            task_id=task_id,
            created_by=owner_id,
            updated_by=owner_id,
        )
        db.add(attachment)
        db.flush()
        payload = {"result": json.dumps(result, ensure_ascii=False)}
        if training_sample_type:
            payload["training_sample_type"] = training_sample_type
        sample = TaskSample(
            inner_id=1,
            task_id=task_id,
            file_id=attachment.id,
            created_by=owner_id,
            updated_by=owner_id,
            annotated_count=1,
            data=json.dumps(payload, ensure_ascii=False),
            state=SampleState.DONE.value,
        )
        db.add(sample)
        db.flush()
        return sample


def _plate_positive_result() -> dict:
    return {
        "rectTool": {
            "result": [
                {
                    "label": "doubleEP",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 50,
                    "attributes": {
                        "plateNumber": "7670X5",
                        "color": "white",
                        "plateType": "whitePlate",
                    },
                }
            ]
        },
        "polygonTool": {
            "result": [
                {
                    "label": "platePoint",
                    "points": [
                        {"x": 10, "y": 10},
                        {"x": 90, "y": 8},
                        {"x": 92, "y": 40},
                        {"x": 9, "y": 42},
                        {"x": 10.5, "y": 10.2},
                    ],
                }
            ]
        },
    }


def _seed_training_preview_fixture(db):
    user = _get_test_user(db)
    preset_a_items = [
        {"label_key": "零散垃圾", "label_value": "scatter_a", "color": "#111111"},
        {"label_key": "打包垃圾", "label_value": "pack_a", "color": "#222222"},
    ]
    preset_b_items = [
        {"label_key": "零散垃圾", "label_value": "scatter_b", "color": "#333333"},
        {"label_key": "大件垃圾", "label_value": "large_b", "color": "#444444"},
    ]
    _seed_label_preset(db, slug="preset_a", name="预设A", items=preset_a_items)
    _seed_label_preset(db, slug="preset_b", name="预设B", items=preset_b_items)

    task_a = _seed_task(
        db,
        owner_id=user.id,
        name="任务A",
        config=_task_config_for_preset("preset_a", preset_a_items),
    )
    task_b = _seed_task(
        db,
        owner_id=user.id,
        name="任务B",
        config=_task_config_for_preset("preset_b", preset_b_items),
    )
    task_c = _seed_task(
        db,
        owner_id=user.id,
        name="任务C",
        config=_task_config_for_preset("preset_a", preset_a_items),
    )

    _seed_done_sample(
        db,
        owner_id=user.id,
        task_id=task_a.id,
        filename="a.jpg",
        path="a.jpg",
        rect_labels=["scatter_a", "pack_a"],
    )
    _seed_done_sample(
        db,
        owner_id=user.id,
        task_id=task_b.id,
        filename="b.jpg",
        path="b.jpg",
        rect_labels=["scatter_b"],
    )
    _seed_done_sample(
        db,
        owner_id=user.id,
        task_id=task_c.id,
        filename="c.jpg",
        path="c.jpg",
        rect_labels=["pack_a"],
    )
    return user, task_a, task_b, task_c


def test_training_task_model_is_registered():
    try:
        mod = importlib.import_module("labelu.internal.domain.models.training_task")
    except ModuleNotFoundError as exc:
        pytest.fail(f"training_task model module missing: {exc}")
    assert hasattr(mod, "TrainingTask")


def test_check_training_task_name_available_is_user_scoped(db):
    user = _get_test_user(db)
    other_user_name = "other-user@example.com"
    with begin_transaction(db):
        other = crud_user.get_user_by_username(db, username=other_user_name)
        if other is None:
            other = crud_user.create(
                db,
                User(username=other_user_name, hashed_password="x"),
            )
    try:
        svc = importlib.import_module(
            "labelu.internal.application.service.training_task"
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"training_task service missing: {exc}")

    with begin_transaction(db):
        svc.create_training_task_row(
            db,
            owner_id=user.id,
            name="零散垃圾训练任务",
            mode="label_dimension",
            status="PENDING",
            primary_task_id=None,
            selected_presets_snapshot=[],
            selected_label_items_snapshot=[],
            merged_class_names_snapshot=[],
            source_tasks_snapshot=[],
            ignored_tasks_snapshot=[],
        )

    assert (
        svc.check_training_task_name_available(db, user.id, "零散垃圾训练任务") is False
    )
    assert (
        svc.check_training_task_name_available(db, other.id, "零散垃圾训练任务") is True
    )


def test_preview_label_dimension_training_task_keeps_distinct_slug_classes_across_presets(
    db,
):
    user, task_a, task_b, task_c = _seed_training_preview_fixture(db)
    try:
        svc = importlib.import_module(
            "labelu.internal.application.service.training_task"
        )
        cmd_mod = importlib.import_module(
            "labelu.internal.application.command.training_task"
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"training_task modules missing: {exc}")

    cmd = cmd_mod.PreviewLabelDimensionTrainingTaskCommand(
        selected_presets=[{"preset_slug": "preset_a"}, {"preset_slug": "preset_b"}],
        selected_label_items=[
            {"preset_slug": "preset_a", "label_value": "scatter_a"},
            {"preset_slug": "preset_b", "label_value": "scatter_b"},
        ],
    )
    preview = svc.preview_label_dimension_training_task(db, current_user=user, cmd=cmd)

    assert preview.merged_class_count == 2
    assert preview.included_task_count == 2
    assert preview.included_image_count == 2
    assert preview.ignored_task_count == 1
    assert {item.class_name for item in preview.merged_classes} == {
        "scatter_a",
        "scatter_b",
    }
    assert {row.task_id for row in preview.included_tasks} == {task_a.id, task_b.id}
    assert preview.ignored_tasks[0].task_id == task_c.id


def test_admin_label_dimension_preview_scans_tasks_owned_by_other_users(db):
    user, _task_a, _task_b, _task_c = _seed_training_preview_fixture(db)
    with begin_transaction(db):
        admin = crud_user.get_user_by_username(db, username="admin@123.com")
        if admin is None:
            admin = crud_user.create(
                db,
                User(username="admin@123.com", hashed_password="x"),
            )
    foreign_task = _seed_task(
        db,
        owner_id=admin.id,
        name="管理员任务",
        config=_task_config_for_preset(
            "preset_a",
            [
                {"label_key": "闆舵暎鍨冨溇", "label_value": "scatter_a"},
                {"label_key": "鎵撳寘鍨冨溇", "label_value": "pack_a"},
            ],
        ),
    )
    _seed_done_sample(
        db,
        owner_id=admin.id,
        task_id=foreign_task.id,
        filename="admin-task.jpg",
        path="admin-task.jpg",
        rect_labels=["scatter_a"],
    )

    svc = importlib.import_module("labelu.internal.application.service.training_task")
    cmd_mod = importlib.import_module(
        "labelu.internal.application.command.training_task"
    )
    preview = svc.preview_label_dimension_training_task(
        db,
        current_user=admin,
        cmd=cmd_mod.PreviewLabelDimensionTrainingTaskCommand(
            selected_presets=[{"preset_slug": "preset_a"}],
            selected_label_items=[
                {"preset_slug": "preset_a", "label_value": "scatter_a"}
            ],
        ),
    )

    assert foreign_task.id in {row.task_id for row in preview.included_tasks}


def test_training_task_detail_and_iteration_allow_user_with_all_source_tasks(
    db, monkeypatch
):
    owner = _get_test_user(db)
    collaborator = _get_or_create_user(db, "training-task-collaborator@example.com")
    task_a = _seed_task(db, owner_id=owner.id, name="shared-a", config="{}")
    task_b = _seed_task(db, owner_id=owner.id, name="shared-b", config="{}")
    with begin_transaction(db):
        db.add_all(
            [
                TaskCollaborator(task_id=task_a.id, user_id=collaborator.id),
                TaskCollaborator(task_id=task_b.id, user_id=collaborator.id),
            ]
        )
        training_task = TrainingTask(
            owner_id=owner.id,
            name="shared-training-task",
            mode="task_dimension",
            status="PENDING",
            primary_task_id=task_a.id,
            selected_presets_snapshot_json="[]",
            selected_label_items_snapshot_json="[]",
            merged_class_names_snapshot_json="[]",
            source_tasks_snapshot_json=json.dumps(
                [
                    {"task_id": task_a.id, "task_name": task_a.name},
                    {"task_id": task_b.id, "task_name": task_b.name},
                ]
            ),
            ignored_tasks_snapshot_json="[]",
        )
        db.add(training_task)
        db.flush()

    svc = importlib.import_module("labelu.internal.application.service.training_task")
    detail = svc.get_training_task_detail(
        db,
        current_user=collaborator,
        training_task_id=training_task.id,
    )
    assert [item.task_id for item in detail.source_tasks] == [task_a.id, task_b.id]

    captured = {}

    def fake_create(_db, *, current_user, cmd):
        captured["user_id"] = current_user.id
        captured["cmd"] = cmd
        return cmd

    monkeypatch.setattr(svc, "create_label_dimension_training_task", fake_create)
    iteration_cmd = svc.iterate_training_task(
        db,
        current_user=collaborator,
        training_task_id=training_task.id,
    )
    assert captured["user_id"] == collaborator.id
    assert iteration_cmd.selected_task_ids == [task_a.id, task_b.id]


def test_training_task_detail_requires_access_to_every_source_task(db):
    owner = _get_test_user(db)
    partial_collaborator = _get_or_create_user(
        db, "training-task-partial-collaborator@example.com"
    )
    task_a = _seed_task(db, owner_id=owner.id, name="partial-a", config="{}")
    task_b = _seed_task(db, owner_id=owner.id, name="partial-b", config="{}")
    with begin_transaction(db):
        db.add(TaskCollaborator(task_id=task_a.id, user_id=partial_collaborator.id))
        training_task = TrainingTask(
            owner_id=owner.id,
            name="partial-shared-training-task",
            mode="task_dimension",
            status="PENDING",
            primary_task_id=task_a.id,
            selected_presets_snapshot_json="[]",
            selected_label_items_snapshot_json="[]",
            merged_class_names_snapshot_json="[]",
            source_tasks_snapshot_json=json.dumps(
                [
                    {"task_id": task_a.id, "task_name": task_a.name},
                    {"task_id": task_b.id, "task_name": task_b.name},
                ]
            ),
            ignored_tasks_snapshot_json="[]",
        )
        db.add(training_task)
        db.flush()

    svc = importlib.import_module("labelu.internal.application.service.training_task")
    with pytest.raises(LabelUException) as exc_info:
        svc.get_training_task_detail(
            db,
            current_user=partial_collaborator,
            training_task_id=training_task.id,
        )
    assert exc_info.value.status_code == 404
    with pytest.raises(LabelUException) as iteration_exc_info:
        svc.iterate_training_task(
            db,
            current_user=partial_collaborator,
            training_task_id=training_task.id,
        )
    assert iteration_exc_info.value.status_code == 404


def test_training_task_detail_allows_task_superuser(db):
    owner = _get_test_user(db)
    admin = _get_or_create_user(db, "admin@123.com")
    task = _seed_task(db, owner_id=owner.id, name="admin-visible", config="{}")
    with begin_transaction(db):
        training_task = TrainingTask(
            owner_id=owner.id,
            name="admin-visible-training-task",
            mode="task_dimension",
            status="PENDING",
            primary_task_id=task.id,
            selected_presets_snapshot_json="[]",
            selected_label_items_snapshot_json="[]",
            merged_class_names_snapshot_json="[]",
            source_tasks_snapshot_json=json.dumps(
                [{"task_id": task.id, "task_name": task.name}]
            ),
            ignored_tasks_snapshot_json="[]",
        )
        db.add(training_task)
        db.flush()

    svc = importlib.import_module("labelu.internal.application.service.training_task")
    detail = svc.get_training_task_detail(
        db,
        current_user=admin,
        training_task_id=training_task.id,
    )
    assert detail.training_task_id == training_task.id


def test_preview_excludes_unfinished_samples_from_selected_task(db):
    user, task_a, _task_b, _task_c = _seed_training_preview_fixture(db)
    unfinished = _seed_done_sample(
        db,
        owner_id=user.id,
        task_id=task_a.id,
        filename="unfinished.jpg",
        path="unfinished.jpg",
        rect_labels=["scatter_a"],
    )
    with begin_transaction(db):
        unfinished.state = SampleState.NEW.value
        db.add(unfinished)

    svc = importlib.import_module("labelu.internal.application.service.training_task")
    cmd_mod = importlib.import_module(
        "labelu.internal.application.command.training_task"
    )
    preview = svc.preview_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.PreviewLabelDimensionTrainingTaskCommand(
            selected_presets=[{"preset_slug": "preset_a"}],
            selected_label_items=[
                {"preset_slug": "preset_a", "label_value": "scatter_a"}
            ],
            selected_task_ids=[task_a.id],
        ),
    )

    assert preview.included_image_count == 1
    assert preview.included_tasks[0].matched_done_sample_count == 1


def test_create_label_dimension_training_task_creates_training_task_and_job(
    db, monkeypatch
):
    user, task_a, task_b, _task_c = _seed_training_preview_fixture(db)
    try:
        svc = importlib.import_module(
            "labelu.internal.application.service.training_task"
        )
        cmd_mod = importlib.import_module(
            "labelu.internal.application.command.training_task"
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"training_task modules missing: {exc}")

    monkeypatch.setattr(
        svc, "_enqueue_label_dimension_train_job", lambda *args, **kwargs: None
    )

    cmd = cmd_mod.CreateLabelDimensionTrainingTaskCommand(
        name="零散垃圾训练任务",
        selected_presets=[{"preset_slug": "preset_a"}, {"preset_slug": "preset_b"}],
        selected_label_items=[
            {"preset_slug": "preset_a", "label_value": "scatter_a"},
            {"preset_slug": "preset_b", "label_value": "scatter_b"},
        ],
        model_weights="yolo11n.pt",
        weights_source="preset",
        epochs=50,
        imgsz=640,
        hyperparams={},
    )
    created = svc.create_label_dimension_training_task(db, current_user=user, cmd=cmd)

    assert created.training_task_id > 0
    assert created.primary_task_id == min(task_a.id, task_b.id)
    assert created.latest_job_id > 0
    row = svc.get_training_task_detail(
        db, current_user=user, training_task_id=created.training_task_id
    )
    assert row.name == "零散垃圾训练任务"
    assert row.mode == "label_dimension"
    assert row.primary_task_id == min(task_a.id, task_b.id)
    assert len(row.selected_label_items) == 2


def test_create_label_dimension_training_task_rejects_selected_source_dropped_by_preview(
    db, monkeypatch
):
    """A stale/now-ineligible source must not be silently omitted on create."""

    user, task_a, _task_b, task_c = _seed_training_preview_fixture(db)
    svc = importlib.import_module("labelu.internal.application.service.training_task")
    cmd_mod = importlib.import_module(
        "labelu.internal.application.command.training_task"
    )
    monkeypatch.setattr(
        svc, "_enqueue_label_dimension_train_job", lambda *args, **kwargs: None
    )

    # task_c only contains ``pack_a`` while the selected contract asks for
    # ``scatter_a``.  Preview reports task_c as ignored; creation must return
    # a conflict instead of persisting a snapshot containing task_a alone.
    with pytest.raises(LabelUException) as exc_info:
        svc.create_label_dimension_training_task(
            db,
            current_user=user,
            cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
                name="拒绝被静默排除的来源",
                selected_presets=[{"preset_slug": "preset_a"}],
                selected_label_items=[
                    {"preset_slug": "preset_a", "label_value": "scatter_a"}
                ],
                selected_task_ids=[task_a.id, task_c.id],
                weights_source="preset",
            ),
        )

    assert exc_info.value.status_code == 409
    assert "未能全部纳入训练快照" in exc_info.value.msg
    assert f"任务 #{task_c.id}" in exc_info.value.msg


def test_create_label_dimension_training_task_idempotency_replays_and_rejects_conflict(
    db, monkeypatch
):
    user, _task_a, _task_b, _task_c = _seed_training_preview_fixture(db)
    svc = importlib.import_module(
        "labelu.internal.application.service.training_task"
    )
    cmd_mod = importlib.import_module(
        "labelu.internal.application.command.training_task"
    )
    monkeypatch.setattr(
        svc, "_enqueue_label_dimension_train_job", lambda *args, **kwargs: None
    )
    cmd = cmd_mod.CreateLabelDimensionTrainingTaskCommand(
        name="幂等标签训练",
        selected_presets=[{"preset_slug": "preset_a"}],
        selected_label_items=[
            {"preset_slug": "preset_a", "label_value": "scatter_a"}
        ],
    )
    first = svc.create_label_dimension_training_task(
        db, current_user=user, cmd=cmd, idempotency_key="ui-training-retry-1"
    )
    replay = svc.create_label_dimension_training_task(
        db, current_user=user, cmd=cmd, idempotency_key="ui-training-retry-1"
    )
    assert replay.training_task_id == first.training_task_id
    assert replay.latest_job_id == first.latest_job_id
    assert db.query(TrainingTask).count() == 1
    assert db.query(YoloTrainJob).count() == 1

    conflict = cmd_mod.CreateLabelDimensionTrainingTaskCommand(
        **{**cmd.dict(), "name": "幂等标签训练-不同请求"}
    )
    with pytest.raises(LabelUException) as exc_info:
        svc.create_label_dimension_training_task(
            db,
            current_user=user,
            cmd=conflict,
            idempotency_key="ui-training-retry-1",
        )
    assert exc_info.value.status_code == 409


def test_create_training_task_persists_trained_weight_reference(db, monkeypatch):
    user, _task_a, _task_b, _task_c = _seed_training_preview_fixture(db)
    svc = importlib.import_module("labelu.internal.application.service.training_task")
    train_svc = importlib.import_module("labelu.internal.application.service.yolo_train")
    cmd_mod = importlib.import_module(
        "labelu.internal.application.command.training_task"
    )
    submitted = {}

    monkeypatch.setattr(
        train_svc,
        "_validate_weights_source_at_create",
        lambda *_args, **_kwargs: None,
    )

    def capture_submit(job_id, task_id, cmd):
        submitted.update(job_id=job_id, task_id=task_id, cmd=cmd)

    monkeypatch.setattr(svc, "_enqueue_label_dimension_train_job", capture_submit)

    created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
            name="trained-weight-iteration",
            selected_presets=[{"preset_slug": "preset_a"}],
            selected_label_items=[
                {"preset_slug": "preset_a", "label_value": "scatter_a"}
            ],
            model_weights="yolo11n.pt",
            weights_source="trained",
            weights_ref_id=285,
            epochs=50,
            imgsz=640,
            hyperparams={},
        ),
    )

    job = db.query(YoloTrainJob).filter(YoloTrainJob.id == created.latest_job_id).one()
    assert job.model_weights == "trained:285"
    assert submitted["cmd"].model_weights == "trained:285"
    assert submitted["cmd"].weights_source is None
    assert submitted["cmd"].weights_ref_id is None


def test_create_training_task_rejects_incompatible_trained_parent(db):
    user, task_a, _task_b, _task_c = _seed_training_preview_fixture(db)
    svc = importlib.import_module("labelu.internal.application.service.training_task")
    cmd_mod = importlib.import_module(
        "labelu.internal.application.command.training_task"
    )
    parent = YoloTrainJob(
        task_id=task_a.id,
        created_by=user.id,
        model_weights="yolo11n.pt",
        weights_path="runs/incompatible/best.pt",
        epochs=50,
        imgsz=640,
        category="iteration",
        job_type="yolo_detection",
        status="COMPLETED",
        training_recipe_key="different_recipe",
        training_recipe_version=1,
    )
    db.add(parent)
    db.flush()
    weights_file = Path(settings.MEDIA_ROOT) / parent.weights_path
    weights_file.parent.mkdir(parents=True, exist_ok=True)
    weights_file.write_bytes(b"pytest-placeholder-weights")

    with pytest.raises(LabelUException) as error:
        svc.create_label_dimension_training_task(
            db,
            current_user=user,
            cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
                name="incompatible-trained-parent",
                selected_presets=[{"preset_slug": "preset_a"}],
                selected_label_items=[
                    {"preset_slug": "preset_a", "label_value": "scatter_a"}
                ],
                weights_source="trained",
                weights_ref_id=parent.id,
            ),
        )

    assert error.value.status_code == 400
    assert "训练配方" in str(error.value)
    assert db.query(TrainingTask).filter(
        TrainingTask.name == "incompatible-trained-parent"
    ).count() == 0


def test_yolo_job_status_updates_latest_training_task_status(db, monkeypatch):
    user, _task_a, _task_b, _task_c = _seed_training_preview_fixture(db)
    svc = importlib.import_module("labelu.internal.application.service.training_task")
    cmd_mod = importlib.import_module(
        "labelu.internal.application.command.training_task"
    )
    from labelu.internal.adapter.persistence import crud_yolo_train_job

    monkeypatch.setattr(
        svc, "_enqueue_label_dimension_train_job", lambda *_args, **_kwargs: None
    )
    created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
            name="status-sync",
            selected_presets=[{"preset_slug": "preset_a"}],
            selected_label_items=[
                {"preset_slug": "preset_a", "label_value": "scatter_a"}
            ],
        ),
    )
    job = db.query(YoloTrainJob).filter(YoloTrainJob.id == created.latest_job_id).one()

    crud_yolo_train_job.update_status(db, job, "PROCESSING")

    training_task = (
        db.query(TrainingTask)
        .filter(TrainingTask.id == created.training_task_id)
        .one()
    )
    assert training_task.latest_job_status == "PROCESSING"


def test_old_yolo_job_does_not_overwrite_latest_training_task_status(db):
    user, task_a, _task_b, _task_c = _seed_training_preview_fixture(db)
    training_task = TrainingTask(
        owner_id=user.id,
        name="latest-job-guard",
        mode="task_dimension",
        status="PENDING",
        primary_task_id=task_a.id,
        latest_job_id=999999,
        latest_job_status="PROCESSING",
    )
    db.add(training_task)
    db.flush()
    job = YoloTrainJob(
        training_task_id=training_task.id,
        task_id=task_a.id,
        created_by=user.id,
        status="PENDING",
        model_weights="yolo11n.pt",
    )
    db.add(job)
    db.flush()
    from labelu.internal.adapter.persistence import crud_yolo_train_job

    crud_yolo_train_job.update_status(db, job, "COMPLETED")

    db.refresh(training_task)
    assert training_task.latest_job_status == "PROCESSING"


def test_iterate_training_task_auto_increments_suffix(db, monkeypatch):
    user, _task_a, _task_b, _task_c = _seed_training_preview_fixture(db)
    try:
        svc = importlib.import_module(
            "labelu.internal.application.service.training_task"
        )
        cmd_mod = importlib.import_module(
            "labelu.internal.application.command.training_task"
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"training_task modules missing: {exc}")

    monkeypatch.setattr(
        svc, "_enqueue_label_dimension_train_job", lambda *args, **kwargs: None
    )

    base_cmd = cmd_mod.CreateLabelDimensionTrainingTaskCommand(
        name="零散垃圾训练任务",
        selected_presets=[{"preset_slug": "preset_a"}],
        selected_label_items=[{"preset_slug": "preset_a", "label_value": "scatter_a"}],
        model_weights="yolo11n.pt",
        weights_source="preset",
        epochs=50,
        imgsz=640,
        hyperparams={},
    )
    first = svc.create_label_dimension_training_task(
        db, current_user=user, cmd=base_cmd
    )
    svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
            **{
                **base_cmd.dict(),
                "name": "零散垃圾训练任务-v2",
            }
        ),
    )
    iterated = svc.iterate_training_task(
        db, current_user=user, training_task_id=first.training_task_id
    )
    assert iterated.name == "零散垃圾训练任务-v3"


def test_plate_profile_preview_includes_done_negative_and_selected_tasks_only(
    db, monkeypatch, tmp_path
):
    user = _get_test_user(db)
    from labelu.internal.common.plate_recognition_profile import (
        DEFAULT_PLATE_RECOGNITION_CONFIG,
        PLATE_RECOGNITION_PROFILE,
    )

    with begin_transaction(db):
        crud_annotation_taxonomy.label_preset_create(
            db,
            slug="plateRecognition",
            name="双层电动车牌识别",
            description=None,
            sort_order=0,
            items=[
                {"label_key": "双层车牌", "label_value": "doubleEP", "color": "#ff6600"}
            ],
            training_profile=PLATE_RECOGNITION_PROFILE,
            training_profile_config=json.dumps(DEFAULT_PLATE_RECOGNITION_CONFIG),
        )
    task_a = _seed_task(db, owner_id=user.id, name="车牌A", config=_plate_task_config())
    task_b = _seed_task(db, owner_id=user.id, name="车牌B", config=_plate_task_config())
    task_c = _seed_task(db, owner_id=user.id, name="车牌C", config=_plate_task_config())
    with begin_transaction(db):
        category = DatasetCategory(
            category_key="plate_training_test",
            name="车牌训练测试分类",
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(category)
        db.flush()
        task_a.dataset_category_id = category.id
        task_b.dataset_category_id = category.id
        task_c.dataset_category_id = category.id
        db.add_all([task_a, task_b, task_c])
    _seed_plate_sample(
        db,
        owner_id=user.id,
        task_id=task_a.id,
        filename="plate-positive.jpg",
        result=_plate_positive_result(),
    )
    _seed_plate_sample(
        db,
        owner_id=user.id,
        task_id=task_b.id,
        filename="plate-negative.jpg",
        result={"tagTool": {"result": [{"value": {"scene": ["road"]}}]}},
        training_sample_type="negative",
    )
    _seed_plate_sample(
        db,
        owner_id=user.id,
        task_id=task_c.id,
        filename="plate-unselected.jpg",
        result=_plate_positive_result(),
    )

    svc = importlib.import_module("labelu.internal.application.service.training_task")
    cmd_mod = importlib.import_module(
        "labelu.internal.application.command.training_task"
    )
    monkeypatch.setattr(
        svc.plate_recognition_train,
        "resolve_plate_pose_pretrained_model",
        lambda _model_weights: "C:/weights/yolov8n-pose.pt",
    )
    preview = svc.preview_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.PreviewLabelDimensionTrainingTaskCommand(
            selected_presets=[{"preset_slug": "plateRecognition"}],
            selected_label_items=[
                {"preset_slug": "plateRecognition", "label_value": "doubleEP"}
            ],
            selected_task_ids=[task_a.id, task_b.id],
        ),
    )

    assert preview.training_profile == PLATE_RECOGNITION_PROFILE
    assert preview.included_task_count == 2
    assert preview.included_image_count == 2
    assert preview.positive_image_count == 1
    assert preview.negative_image_count == 1
    assert preview.instance_count == 1
    assert preview.invalid_image_count == 0
    assert {item.task_id for item in preview.included_tasks} == {task_a.id, task_b.id}
    assert task_c.id not in {item.task_id for item in preview.included_tasks}

    task_dimension_preview = svc.preview_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.PreviewLabelDimensionTrainingTaskCommand(
            selected_task_ids=[task_a.id, task_b.id],
            dataset_category_id=category.id,
            dataset_category_ids=[category.id],
            training_recipe_key=PLATE_RECOGNITION_PROFILE,
        ),
    )
    assert task_dimension_preview.training_profile == PLATE_RECOGNITION_PROFILE
    assert task_dimension_preview.included_image_count == 2
    assert task_dimension_preview.positive_image_count == 1
    assert task_dimension_preview.negative_image_count == 1
    assert task_dimension_preview.dataset_category_id == category.id
    assert task_dimension_preview.training_recipe_key == PLATE_RECOGNITION_PROFILE

    dispatched = []
    monkeypatch.setattr(
        svc,
        "_enqueue_plate_recognition_train_job",
        lambda job_id, task_id, cmd: dispatched.append((job_id, task_id, cmd)),
    )
    task_dimension_created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
            name="车牌任务区训练",
            selected_task_ids=[task_a.id, task_b.id],
            dataset_category_id=category.id,
            dataset_category_ids=[category.id],
            training_recipe_key=PLATE_RECOGNITION_PROFILE,
            model_weights="yolov8n-pose.pt",
            weights_source="preset",
            epochs=1,
            imgsz=640,
            plate_training_target="plate_detection",
        ),
    )
    from labelu.internal.adapter.persistence import (
        crud_training_task,
        crud_yolo_train_job,
    )

    task_dimension_row = crud_training_task.get(
        db, task_dimension_created.training_task_id
    )
    task_dimension_presets = json.loads(
        task_dimension_row.selected_presets_snapshot_json
    )
    assert task_dimension_created.mode == "plate_detection"
    assert task_dimension_presets[0]["training_profile"] == PLATE_RECOGNITION_PROFILE
    assert task_dimension_row.dataset_category_id == category.id
    assert task_dimension_row.training_recipe_key == PLATE_RECOGNITION_PROFILE
    task_dimension_job = crud_yolo_train_job.get(
        db, task_dimension_created.latest_job_id
    )
    assert task_dimension_job.dataset_category_id == category.id
    assert task_dimension_job.training_recipe_key == PLATE_RECOGNITION_PROFILE

    created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
            name="复合车牌训练",
            selected_presets=[{"preset_slug": "plateRecognition"}],
            selected_label_items=[
                {"preset_slug": "plateRecognition", "label_value": "doubleEP"}
            ],
            selected_task_ids=[task_a.id, task_b.id],
            model_weights="yolov8n-pose.pt",
            weights_source="preset",
            epochs=1,
            imgsz=640,
        ),
    )
    job = crud_yolo_train_job.get(db, created.latest_job_id)
    assert job.job_type == "plate_recognition"
    assert job.model_weights == "yolov8n-pose.pt"
    assert any(item[0] == job.id for item in dispatched)

    detection_created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
            name="车牌检测单模型训练",
            selected_presets=[{"preset_slug": "plateRecognition"}],
            selected_label_items=[
                {"preset_slug": "plateRecognition", "label_value": "doubleEP"}
            ],
            selected_task_ids=[task_a.id, task_b.id],
            model_weights="yolov8n-pose.pt",
            weights_source="preset",
            epochs=1,
            imgsz=640,
            plate_training_target="plate_detection",
        ),
    )
    detection_job = crud_yolo_train_job.get(db, detection_created.latest_job_id)
    assert detection_created.mode == "plate_detection"
    assert detection_job.job_type == "plate_detection"
    assert json.loads(detection_job.component_status_json) == {
        "pose": {"status": "PENDING", "artifact": None, "error": None}
    }
    assert "车牌检测训练任务已创建" in detection_created.message

    component_status = {}
    component_artifacts = {}
    for component in svc.plate_recognition_train.PLATE_COMPONENTS:
        pt_path = tmp_path / f"{component}.pt"
        onnx_path = tmp_path / f"{component}.onnx"
        pt_path.write_bytes(b"pt")
        onnx_path.write_bytes(b"onnx")
        artifact = {"pt": str(pt_path), "onnx": str(onnx_path)}
        component_status[component] = {"status": "COMPLETED", "artifact": artifact}
        component_artifacts[component] = artifact
    with begin_transaction(db):
        crud_yolo_train_job.update_status(
            db,
            job,
            "COMPLETED",
            component_status_json=json.dumps(component_status),
            component_artifacts_json=json.dumps(component_artifacts),
        )

    detection_pt = tmp_path / "plate_detection_best.pt"
    detection_onnx = tmp_path / "plate_detection.onnx"
    detection_pt.write_bytes(b"pt")
    detection_onnx.write_bytes(b"onnx")
    detection_artifact = {
        "pt": str(detection_pt),
        "onnx": str(detection_onnx),
        "network": "plate_detection",
    }
    with begin_transaction(db):
        crud_yolo_train_job.update_status(
            db,
            detection_job,
            "COMPLETED",
            weights_path=str(detection_pt),
            component_status_json=json.dumps(
                {
                    "pose": {
                        "status": "COMPLETED",
                        "artifact": detection_artifact,
                        "error": None,
                    }
                }
            ),
            component_artifacts_json=json.dumps({"pose": detection_artifact}),
        )

    detection_iterated = svc.iterate_training_task(
        db,
        current_user=user,
        training_task_id=detection_created.training_task_id,
    )
    detection_iterated_job = crud_yolo_train_job.get(
        db, detection_iterated.latest_job_id
    )
    assert detection_iterated_job.job_type == "plate_detection"
    assert (
        json.loads(detection_iterated_job.hyperparams_json)[
            svc.plate_recognition_train.PLATE_ITERATION_SOURCE_JOB_ID_HYPERPARAM
        ]
        == detection_job.id
    )

    with pytest.raises(Exception, match="训练目标与当前车牌训练目标不一致"):
        svc.create_label_dimension_training_task(
            db,
            current_user=user,
            cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
                name="错误的跨模式迭代",
                selected_presets=[{"preset_slug": "plateRecognition"}],
                selected_label_items=[
                    {
                        "preset_slug": "plateRecognition",
                        "label_value": "doubleEP",
                    }
                ],
                selected_task_ids=[task_a.id, task_b.id],
                model_weights="yolov8n-pose.pt",
                weights_source="preset",
                epochs=1,
                imgsz=640,
                plate_training_target="plate_detection",
                iteration_source_job_id=job.id,
            ),
        )

    iterated = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
            name=created.name + "-v2",
            selected_presets=[{"preset_slug": "plateRecognition"}],
            selected_label_items=[
                {"preset_slug": "plateRecognition", "label_value": "doubleEP"}
            ],
            selected_task_ids=[task_a.id, task_b.id],
            model_weights="yolov8n-pose.pt",
            weights_source="preset",
            epochs=3,
            imgsz=640,
            hyperparams={"batch": 4},
            iteration_source_job_id=job.id,
        ),
    )
    iterated_job = crud_yolo_train_job.get(db, iterated.latest_job_id)
    stored_hyperparams = json.loads(iterated_job.hyperparams_json)
    assert stored_hyperparams["batch"] == 4
    assert (
        stored_hyperparams[
            svc.plate_recognition_train.PLATE_ITERATION_SOURCE_JOB_ID_HYPERPARAM
        ]
        == job.id
    )
    assert dispatched[-1][2].hyperparams == stored_hyperparams

    auto_iterated = svc.iterate_training_task(
        db,
        current_user=user,
        training_task_id=created.training_task_id,
    )
    auto_iterated_job = crud_yolo_train_job.get(db, auto_iterated.latest_job_id)
    assert auto_iterated.name.endswith("-v3")
    assert auto_iterated_job.epochs == job.epochs
    assert (
        json.loads(auto_iterated_job.hyperparams_json)[
            svc.plate_recognition_train.PLATE_ITERATION_SOURCE_JOB_ID_HYPERPARAM
        ]
        == job.id
    )


def test_plate_profile_requires_explicit_pose_weights():
    svc = importlib.import_module("labelu.internal.application.service.training_task")
    cmd_mod = importlib.import_module(
        "labelu.internal.application.command.training_task"
    )
    with pytest.raises(Exception, match="yolov8n-pose.pt"):
        svc._validate_plate_recognition_weights(
            cmd_mod.CreateLabelDimensionTrainingTaskCommand(
                name="复合车牌训练",
                model_weights="yolo11n.pt",
                weights_source="preset",
            )
        )


def test_plate_profile_requires_available_pose_weights(monkeypatch):
    svc = importlib.import_module("labelu.internal.application.service.training_task")
    cmd_mod = importlib.import_module(
        "labelu.internal.application.command.training_task"
    )
    monkeypatch.setattr(
        svc.plate_recognition_train,
        "resolve_plate_pose_pretrained_model",
        lambda _model_weights: (_ for _ in ()).throw(
            RuntimeError("缺少车牌 Pose 预训练权重 yolov8n-pose.pt")
        ),
    )

    with pytest.raises(Exception, match="车牌复合训练无法开始.*yolov8n-pose.pt"):
        svc._validate_plate_recognition_weights(
            cmd_mod.CreateLabelDimensionTrainingTaskCommand(
                name="复合车牌训练",
                model_weights="yolov8n-pose.pt",
                weights_source="preset",
            )
        )
