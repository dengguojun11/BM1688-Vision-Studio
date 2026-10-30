import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_attachment,
    crud_auto_label_job,
    crud_pre_annotation,
    crud_sample,
    crud_task,
    crud_yolo_train_job,
)
from labelu.internal.application.command.sample import CreateSampleCommand
from labelu.internal.application.service import (
    auto_sample_preannotate as auto_sample_service,
)
from labelu.internal.application.service import sample as sample_service
from labelu.internal.application.service import yolo_preannotate
from labelu.internal.application.service import yolo_dataset
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.auto_label_job import AutoLabelJob
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.user import User


def test_new_sample_without_annotation_result_is_safe_for_auto_preannotation():
    """A freshly uploaded sample must be eligible for the automatic worker."""
    sample = SimpleNamespace(data=json.dumps({"file": {"id": 123}}))

    assert yolo_dataset._parse_sample_annotation(sample) is None


def test_existing_preannotation_filters_tools_not_in_task_config():
    payload = {
        "annotations": {
            "rectTool": {"toolName": "rectTool", "result": [{"label": "dog"}]},
            "polygonTool": {
                "toolName": "polygonTool",
                "result": [{"label": "dog", "points": []}],
            },
        },
        "meta": {"source_type": "yolo_generated"},
    }

    normalized = auto_sample_service._normalize_existing_pre_annotation_result(
        payload,
        allowed_tool_names={"polygonTool", "tagTool"},
    )

    assert "rectTool" not in normalized
    assert "polygonTool" in normalized


def _create_image_task(
    db: Session,
    *,
    created_by: int,
    name: str,
    config: dict,
) -> Task:
    with begin_transaction(db):
        return crud_task.create(
            db=db,
            task=Task(
                name=name,
                description="",
                tips="",
                config=json.dumps(config, ensure_ascii=False),
                media_type=MediaType.IMAGE.value,
                created_by=created_by,
                updated_by=created_by,
            ),
        )


def _create_attachment(
    db: Session, *, task_id: int, created_by: int, filename: str
) -> TaskAttachment:
    with begin_transaction(db):
        return crud_attachment.create(
            db,
            TaskAttachment(
                filename=filename,
                url=f"/upload/{filename}",
                path=f"upload/{filename}",
                task_id=task_id,
                created_by=created_by,
                updated_by=created_by,
            ),
        )


def test_sample_create_schedules_auto_preannotate_for_all_new_samples(
    db: Session, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_ENABLED", False)
    scheduled: dict[str, object] = {}
    monkeypatch.setattr(
        sample_service,
        "schedule_auto_preannotate_for_new_samples",
        lambda **kwargs: scheduled.update(kwargs),
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="auto preannotate target",
        config={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "helmet", "value": "helmet"}]},
                }
            ]
        },
    )
    att1 = _create_attachment(db, task_id=task.id, created_by=user.id, filename="a.jpg")
    att2 = _create_attachment(db, task_id=task.id, created_by=user.id, filename="b.jpg")

    resp = asyncio.run(
        sample_service.create(
            db=db,
            task_id=task.id,
            cmd=[
                CreateSampleCommand(file_id=att1.id, data={}),
                CreateSampleCommand(file_id=att2.id, data={"result": []}),
            ],
            current_user=user,
        )
    )

    assert len(resp.ids) == 2
    assert scheduled["task_id"] == task.id
    assert scheduled["source_hint"] == "sample_create"
    assert scheduled["user_id"] == user.id
    assert scheduled["sample_ids"] == resp.ids


def test_sample_create_can_disable_auto_preannotate(db: Session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_ENABLED", False)
    called = {"value": False}
    monkeypatch.setattr(
        sample_service,
        "schedule_auto_preannotate_for_new_samples",
        lambda **kwargs: called.__setitem__("value", True),
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="manual only",
        config={"tools": []},
    )
    att = _create_attachment(db, task_id=task.id, created_by=user.id, filename="c.jpg")

    asyncio.run(
        sample_service.create(
            db=db,
            task_id=task.id,
            cmd=[CreateSampleCommand(file_id=att.id, data={})],
            current_user=user,
            auto_preannotate=False,
            auto_preannotate_source="test",
        )
    )

    assert called["value"] is False


def test_durable_auto_preannotate_enqueue_is_idempotent(
    db: Session,
    monkeypatch,
) -> None:
    submitted: list[int] = []
    monkeypatch.setattr(
        auto_sample_service,
        "_submit_auto_preannotate_job",
        lambda job_id: submitted.append(int(job_id)) or True,
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="durable automatic global tags",
        config={"tools": []},
    )

    first_job_id = auto_sample_service.schedule_auto_preannotate_for_new_samples(
        task_id=task.id,
        sample_ids=[31, 32, 31],
        user_id=user.id,
        source_hint="experiment_dataset_import_post_preannotation",
        requested_model_id="534",
        idempotency_key=f"experiment_dataset_import:72:task:{task.id}",
    )
    second_job_id = auto_sample_service.schedule_auto_preannotate_for_new_samples(
        task_id=task.id,
        sample_ids=[31, 32],
        user_id=user.id,
        source_hint="experiment_dataset_import_post_preannotation",
        requested_model_id="534",
        idempotency_key=f"experiment_dataset_import:72:task:{task.id}",
    )

    assert first_job_id == second_job_id
    rows = db.query(AutoLabelJob).filter(AutoLabelJob.task_id == task.id).all()
    assert len(rows) == 1
    payload = json.loads(rows[0].payload_json)
    assert payload["sample_ids"] == [31, 32]
    assert payload["processed_sample_ids"] == []
    assert payload["requested_model_id"] == "534"
    assert rows[0].job_kind == crud_auto_label_job.AUTO_SAMPLE_PREANNOTATE_JOB_KIND
    assert rows[0].status == "PENDING"
    assert submitted == [first_job_id, first_job_id]


def test_durable_auto_preannotate_recovers_expired_processing_job(
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        auto_sample_service,
        "_submit_auto_preannotate_job",
        lambda job_id: True,
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="recover automatic global tags",
        config={"tools": []},
    )
    job_id = auto_sample_service.schedule_auto_preannotate_for_new_samples(
        task_id=task.id,
        sample_ids=[41, 42],
        user_id=user.id,
        source_hint="pytest_restart",
        idempotency_key=f"pytest-restart:{task.id}",
    )
    with begin_transaction(db):
        job = crud_auto_label_job.get(db=db, job_id=job_id)
        payload = json.loads(job.payload_json)
        payload["processed_sample_ids"] = [41]
        crud_auto_label_job.update_status(
            db,
            job,
            "PROCESSING",
            payload_json=json.dumps(payload),
            processed_count=1,
            success_count=1,
            lease_owner="dead-worker",
            lease_expires_at=auto_sample_service.datetime(2000, 1, 1),
        )

    captured: dict[str, object] = {}

    def capture_run(
        task_id, sample_ids, user_id, source_hint, requested_model_id, **kwargs
    ):
        captured.update(
            {
                "task_id": task_id,
                "sample_ids": sample_ids,
                "user_id": user_id,
                "source_hint": source_hint,
                "requested_model_id": requested_model_id,
                **kwargs,
            }
        )

    monkeypatch.setattr(auto_sample_service, "_run_auto_preannotate_sync", capture_run)
    auto_sample_service._run_durable_auto_preannotate_job(job_id)

    db.expire_all()
    recovered = crud_auto_label_job.get(db=db, job_id=job_id)
    assert recovered.status == "PROCESSING"
    assert recovered.attempt_count == 1
    assert recovered.lease_owner != "dead-worker"
    assert captured["sample_ids"] == [41, 42]
    assert captured["processed_sample_ids"] == {41}


def test_durable_auto_preannotate_retries_only_failed_samples(
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        auto_sample_service,
        "_submit_auto_preannotate_job",
        lambda job_id: True,
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="retry automatic global tags",
        config={"tools": []},
    )
    job_id = auto_sample_service.schedule_auto_preannotate_for_new_samples(
        task_id=task.id,
        sample_ids=[51],
        user_id=user.id,
        source_hint="pytest_retry",
        idempotency_key=f"pytest-retry:{task.id}",
    )

    with begin_transaction(db):
        assert crud_auto_label_job.claim_auto_sample_job(
            db,
            job_id=job_id,
            lease_owner="worker-1",
            lease_seconds=60,
        )
    auto_sample_service._record_durable_job_sample(
        job_id,
        "worker-1",
        sample_id=51,
        success=False,
        error="temporary model timeout",
    )
    auto_sample_service._finish_durable_job(job_id, "worker-1")

    db.expire_all()
    retrying = crud_auto_label_job.get(db=db, job_id=job_id)
    retry_payload = json.loads(retrying.payload_json)
    assert retrying.status == "PENDING"
    assert retrying.processed_count == 0
    assert retrying.success_count == 0
    assert retrying.failed_count == 0
    assert retry_payload["processed_sample_ids"] == []
    assert retry_payload["failed_sample_ids"] == []

    with begin_transaction(db):
        assert crud_auto_label_job.claim_auto_sample_job(
            db,
            job_id=job_id,
            lease_owner="worker-2",
            lease_seconds=60,
        )
    auto_sample_service._record_durable_job_sample(
        job_id,
        "worker-2",
        sample_id=51,
        success=True,
    )
    auto_sample_service._finish_durable_job(job_id, "worker-2")

    db.expire_all()
    completed = crud_auto_label_job.get(db=db, job_id=job_id)
    assert completed.status == "COMPLETED"
    assert completed.attempt_count == 2
    assert completed.processed_count == 1
    assert completed.success_count == 1
    assert completed.failed_count == 0


def test_durable_auto_preannotate_skips_complete_global_tags(
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", False)
    monkeypatch.setattr(
        auto_sample_service,
        "_submit_auto_preannotate_job",
        lambda job_id: True,
    )
    monkeypatch.setattr(
        auto_sample_service,
        "refresh_task_training_ready",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_sample_global_tags_complete",
        lambda sample, task_config: True,
    )

    def fail_model_call(**kwargs):
        raise AssertionError("complete global tags must not call the model")

    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_process_single_sample",
        fail_model_call,
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="skip complete automatic global tags",
        config={
            "tools": [
                {
                    "tool": "tagTool",
                    "config": {
                        "attributes": [
                            {
                                "key": "场景类型",
                                "value": "sceneType",
                                "type": "enum",
                                "options": [{"key": "道路", "value": "road"}],
                            }
                        ]
                    },
                }
            ]
        },
    )
    attachment = _create_attachment(
        db,
        task_id=task.id,
        created_by=user.id,
        filename="complete-tags.jpg",
    )
    with begin_transaction(db):
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    inner_id=1,
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    state="NEW",
                    annotated_count=1,
                    data=json.dumps({}),
                )
            ],
        )[0]
    job_id = auto_sample_service.schedule_auto_preannotate_for_new_samples(
        task_id=task.id,
        sample_ids=[sample.id],
        user_id=user.id,
        source_hint="pytest_complete_tags",
        idempotency_key=f"pytest-complete-tags:{task.id}",
    )

    auto_sample_service._run_durable_auto_preannotate_job(job_id)

    db.expire_all()
    completed = crud_auto_label_job.get(db=db, job_id=job_id)
    assert completed.status == "COMPLETED"
    assert completed.processed_count == 1
    assert completed.success_count == 1
    assert completed.failed_count == 0


def test_resolve_auto_candidate_prefers_exact_preset_slug(
    db: Session, tmp_path: Path
) -> None:
    user = db.query(User).filter(User.username == "test@example.com").first()
    current_task = _create_image_task(
        db,
        created_by=user.id,
        name="current",
        config={
            "defaultRectPresetId": "helmetPreset",
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "helmet", "value": "helmet"}]},
                }
            ],
        },
    )
    class_match_task = _create_image_task(
        db,
        created_by=user.id,
        name="class match only",
        config={
            "defaultRectPresetId": "otherPreset",
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "helmet", "value": "helmet"}]},
                }
            ],
        },
    )
    exact_match_task = _create_image_task(
        db,
        created_by=user.id,
        name="exact preset",
        config={
            "defaultRectPresetId": "helmetPreset",
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "helmet", "value": "helmet"}]},
                }
            ],
        },
    )

    weights_root = Path(settings.MEDIA_ROOT) / "pytest-auto-preannotate"
    weights_root.mkdir(parents=True, exist_ok=True)
    weight1 = weights_root / "weights-1.pt"
    weight2 = weights_root / "weights-2.pt"
    weight1.write_bytes(b"1")
    weight2.write_bytes(b"2")
    with begin_transaction(db):
        job1 = crud_yolo_train_job.create(
            db=db,
            task_id=class_match_task.id,
            user_id=user.id,
            model_weights="m1.pt",
            epochs=1,
            imgsz=640,
            category="",
        )
        crud_yolo_train_job.update_status(
            db=db,
            job=job1,
            status="COMPLETED",
            weights_path=str(weight1),
        )
        job2 = crud_yolo_train_job.create(
            db=db,
            task_id=exact_match_task.id,
            user_id=user.id,
            model_weights="m2.pt",
            epochs=1,
            imgsz=672,
            category="",
        )
        crud_yolo_train_job.update_status(
            db=db,
            job=job2,
            status="COMPLETED",
            weights_path=str(weight2),
        )

    candidate = yolo_preannotate.resolve_auto_candidate_for_task(
        db=db,
        task=current_task,
        current_user=user,
    )

    assert candidate is not None
    assert candidate["model_kind"] == "trained"
    assert candidate["model_id"] == job2.id
    assert candidate["imgsz"] == 672


def test_resolve_auto_candidate_returns_none_without_matching_labels(
    db: Session, monkeypatch
) -> None:
    user = db.query(User).filter(User.username == "test@example.com").first()
    current_task = _create_image_task(
        db,
        created_by=user.id,
        name="current-no-match",
        config={
            "defaultRectPresetId": "helmetPreset",
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "helmet", "value": "helmet"}]},
                }
            ],
        },
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "_imported_model_label_signature",
        lambda row: ("person",),
    )

    candidate = yolo_preannotate.resolve_auto_candidate_for_task(
        db=db,
        task=current_task,
        current_user=user,
    )

    assert candidate is None


def test_sample_has_any_geometry_detects_experiment_dataset_preannotation(
    db: Session,
) -> None:
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="metadata geometry",
        config={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {
                        "attributes": [
                            {"key": "骑行电动车的人", "value": "person_electricbike"}
                        ]
                    },
                }
            ]
        },
    )
    att = _create_attachment(
        db, task_id=task.id, created_by=user.id, filename="meta.jpg"
    )
    with begin_transaction(db):
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=att.id,
                    created_by=user.id,
                    updated_by=user.id,
                    data=json.dumps({}, ensure_ascii=False),
                )
            ],
        )[0]
        crud_pre_annotation.batch(
            db=db,
            pre_annotations=[
                TaskPreAnnotation(
                    task_id=task.id,
                    file_id=None,
                    sample_name=att.filename,
                    data=json.dumps(
                        {
                            "sample_name": att.filename,
                            "annotations": {
                                "rectTool": {
                                    "toolName": "rectTool",
                                    "result": [
                                        {
                                            "id": "r1",
                                            "order": 0,
                                            "label": "person_electricbike",
                                            "x": 10,
                                            "y": 20,
                                            "width": 30,
                                            "height": 40,
                                        }
                                    ],
                                }
                            },
                            "meta": {"source_type": "experiment_dataset_metadata"},
                        },
                        ensure_ascii=False,
                    ),
                    created_by=user.id,
                    updated_by=user.id,
                )
            ],
        )

    assert (
        auto_sample_service._sample_has_any_geometry(
            db,
            task_id=task.id,
            sample_id=sample.id,
        )
        is True
    )


def test_sample_has_any_geometry_false_when_no_result_and_no_preannotation(
    db: Session,
) -> None:
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="no geometry",
        config={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {
                        "attributes": [
                            {"key": "骑行电动车的人", "value": "person_electricbike"}
                        ]
                    },
                }
            ]
        },
    )
    att = _create_attachment(
        db, task_id=task.id, created_by=user.id, filename="none.jpg"
    )
    with begin_transaction(db):
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=att.id,
                    created_by=user.id,
                    updated_by=user.id,
                    data=json.dumps(
                        {"image_pipeline": {"version": 1}}, ensure_ascii=False
                    ),
                )
            ],
        )[0]

    assert (
        auto_sample_service._sample_has_any_geometry(
            db,
            task_id=task.id,
            sample_id=sample.id,
        )
        is False
    )


def test_run_auto_preannotate_sync_applies_existing_preannotations_without_ai_or_yolo(
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", False)
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="existing preannotations only",
        config={"tools": []},
    )
    att = _create_attachment(
        db, task_id=task.id, created_by=user.id, filename="existing.jpg"
    )
    with begin_transaction(db):
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=att.id,
                    created_by=user.id,
                    updated_by=user.id,
                    state="NEW",
                    annotated_count=0,
                    data=json.dumps({}, ensure_ascii=False),
                )
            ],
        )[0]
        crud_pre_annotation.batch(
            db=db,
            pre_annotations=[
                TaskPreAnnotation(
                    task_id=task.id,
                    file_id=None,
                    sample_name=att.filename,
                    data=json.dumps(
                        {
                            "sample_name": att.filename,
                            "annotations": {
                                "rectTool": {
                                    "toolName": "rectTool",
                                    "result": [
                                        {
                                            "id": "rect-1",
                                            "order": 0,
                                            "label": "electric_bike",
                                            "x": 10,
                                            "y": 20,
                                            "width": 30,
                                            "height": 40,
                                        }
                                    ],
                                },
                                "tagTool": {
                                    "toolName": "tagTool",
                                    "result": [
                                        {
                                            "id": "tag-1",
                                            "label": "scene_type",
                                            "value": "night",
                                        }
                                    ],
                                },
                            },
                            "meta": {
                                "source_type": "experiment_dataset_metadata",
                                "width": 1280,
                                "height": 720,
                                "rotate": 0,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    created_by=user.id,
                    updated_by=user.id,
                )
            ],
        )

    auto_sample_service._run_auto_preannotate_sync(
        task.id,
        [sample.id],
        user.id,
        "pytest_existing_preannotation",
    )

    with session_scope() as verify_db:
        persisted = crud_sample.get(db=verify_db, sample_id=sample.id)
    assert persisted is not None
    sample_data = json.loads(persisted.data)
    result_payload = sample_data.get("result")
    if isinstance(result_payload, str):
        result_payload = json.loads(result_payload)
    assert result_payload["width"] == 1280
    assert result_payload["height"] == 720
    assert result_payload["rectTool"]["result"][0]["label"] == "electric_bike"
    assert result_payload["tagTool"]["result"][0]["value"] == "night"
    assert persisted.annotated_count == 2
    assert persisted.state == "NEW"


def test_resolve_auto_candidate_ignores_missing_unrelated_weights(
    db: Session,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", tmp_path)
    user = db.query(User).filter(User.username == "test@example.com").first()
    current_task = _create_image_task(
        db,
        created_by=user.id,
        name="current with stale model history",
        config={
            "defaultRectPresetId": "targetPreset",
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "helmet", "value": "helmet"}]},
                }
            ],
        },
    )
    unrelated_task = _create_image_task(
        db,
        created_by=user.id,
        name="unrelated stale model",
        config={
            "defaultRectPresetId": "unrelatedPreset",
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "person", "value": "person"}]},
                }
            ],
        },
    )
    class_match_task = _create_image_task(
        db,
        created_by=user.id,
        name="valid class match",
        config={
            "defaultRectPresetId": "otherPreset",
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "helmet", "value": "helmet"}]},
                }
            ],
        },
    )
    valid_weights = tmp_path / "valid.pt"
    valid_weights.write_bytes(b"valid")
    with begin_transaction(db):
        stale_job = crud_yolo_train_job.create(
            db=db,
            task_id=unrelated_task.id,
            user_id=user.id,
            model_weights="missing.pt",
            epochs=1,
            imgsz=640,
            category="",
        )
        crud_yolo_train_job.update_status(
            db=db,
            job=stale_job,
            status="COMPLETED",
            weights_path=str(tmp_path / "missing.pt"),
        )
        valid_job = crud_yolo_train_job.create(
            db=db,
            task_id=class_match_task.id,
            user_id=user.id,
            model_weights="valid.pt",
            epochs=1,
            imgsz=640,
            category="",
        )
        crud_yolo_train_job.update_status(
            db=db,
            job=valid_job,
            status="COMPLETED",
            weights_path=str(valid_weights),
        )

    candidate = yolo_preannotate.resolve_auto_candidate_for_task(
        db=db,
        task=current_task,
        current_user=user,
    )

    assert candidate is not None
    assert candidate["model_id"] == valid_job.id


def test_run_auto_preannotate_sync_keeps_ai_tags_when_yolo_candidate_fails(
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        auto_sample_service,
        "ensure_ultralytics_installed",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "resolve_auto_candidate_for_task",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("stale weights")),
    )
    monkeypatch.setattr(
        auto_sample_service,
        "refresh_task_training_ready",
        lambda *args: None,
    )
    processed_sample_ids: list[int] = []
    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_process_single_sample",
        lambda **kwargs: processed_sample_ids.append(kwargs["sample_id"]),
    )

    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="global tags survive stale yolo",
        config={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "helmet", "value": "helmet"}]},
                },
                {
                    "tool": "tagTool",
                    "config": {
                        "attributes": [
                            {
                                "key": "Scene",
                                "value": "sceneType",
                                "type": "enum",
                                "options": [{"key": "Road", "value": "road"}],
                            }
                        ]
                    },
                },
            ]
        },
    )
    attachment = _create_attachment(
        db,
        task_id=task.id,
        created_by=user.id,
        filename="stale-yolo.jpg",
    )
    with begin_transaction(db):
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    state="NEW",
                    annotated_count=0,
                    data=json.dumps({}, ensure_ascii=False),
                )
            ],
        )[0]

    auto_sample_service._run_auto_preannotate_sync(
        task.id,
        [sample.id],
        user.id,
        "pytest_stale_yolo",
    )

    assert processed_sample_ids == [sample.id]


def test_auto_candidate_prefers_compatible_imported_yolo_over_trained(
    db: Session,
    tmp_path: Path,
    monkeypatch,
) -> None:
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="prefer imported yolo",
        config={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "person", "value": "person"}]},
                }
            ]
        },
    )
    weights_path = tmp_path / "trained.pt"
    weights_path.write_bytes(b"trained")
    with begin_transaction(db):
        job = crud_yolo_train_job.create(
            db=db,
            task_id=task.id,
            user_id=user.id,
            model_weights="trained.pt",
            epochs=1,
            imgsz=640,
            category="",
        )
        crud_yolo_train_job.update_status(
            db=db,
            job=job,
            status="COMPLETED",
            weights_path=str(weights_path),
        )
    imported = SimpleNamespace(
        id=41,
        weights_rel_path="imported.pt",
        display_name="external-person",
        category="",
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_imported_model,
        "list_for_user",
        lambda *args, **kwargs: ([imported], 1),
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_imported_model,
        "public_owner_ids",
        lambda _db: [],
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "_imported_model_label_signature",
        lambda _row: ("person",),
    )
    monkeypatch.setattr(yolo_preannotate, "_imported_model_imgsz", lambda _row: 1280)
    monkeypatch.setattr(yolo_preannotate, "resolve_weights_path", lambda value: value)

    candidate = yolo_preannotate.resolve_auto_candidate_for_task(
        db=db,
        task=task,
        current_user=user,
        prefer_imported=True,
    )

    assert candidate is not None
    assert candidate["model_kind"] == "imported"
    assert candidate["model_id"] == 41
    assert candidate["imgsz"] == 1280


def test_tagtool_auto_candidate_requires_compatible_dimensions_and_options(
    db: Session,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", tmp_path)
    (tmp_path / "tag.pt").write_bytes(b"classifier")
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="imported tagtool compatibility",
        config={
            "tools": [
                {
                    "tool": "tagTool",
                    "config": {
                        "attributes": [
                            {
                                "key": "Scene",
                                "value": "sceneType",
                                "type": "enum",
                                "options": [{"key": "Road", "value": "road"}],
                            }
                        ]
                    },
                }
            ]
        },
    )
    label_map = {
        name: (["road"] if name == "sceneType" else [f"{name}-value"])
        for name in auto_sample_service.tagtool_preannotate.ATTRIBUTE_NAMES
    }
    row = SimpleNamespace(
        id=9,
        weights_rel_path="tag.pt",
        label_map_json=json.dumps(label_map),
    )
    monkeypatch.setattr(
        auto_sample_service.tagtool_preannotate.crud_tagtool_classification_model,
        "list_for_user",
        lambda *args, **kwargs: ([row], 1),
    )

    candidate = auto_sample_service.tagtool_preannotate.resolve_auto_candidate_for_task(
        db=db,
        task=task,
        current_user=user,
    )

    assert candidate is not None
    assert candidate["model_id"] == 9
    label_map["sceneType"] = ["night"]
    row.label_map_json = json.dumps(label_map)
    assert (
        auto_sample_service.tagtool_preannotate.resolve_auto_candidate_for_task(
            db=db,
            task=task,
            current_user=user,
        )
        is None
    )


def test_auto_preannotate_uses_imported_tagtool_and_yolo_without_generic_ai(
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        auto_sample_service,
        "ensure_ultralytics_installed",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        auto_sample_service,
        "refresh_task_training_ready",
        lambda *args: None,
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "resolve_auto_candidate_for_task",
        lambda **kwargs: {
            "model_kind": "imported",
            "model_id": 7,
            "weights_path": "detector.pt",
            "imgsz": 1280,
            "rect_labels": ["person"],
            "job_type": yolo_preannotate.PLATE_RECOGNITION_JOB_TYPE,
        },
    )
    monkeypatch.setattr(
        auto_sample_service.tagtool_preannotate,
        "resolve_auto_candidate_for_task",
        lambda **kwargs: {
            "model_kind": "imported",
            "model_id": 9,
            "weights_path": Path("classifier.pt"),
            "label_map": {"sceneType": ["road"]},
            "device": "",
        },
    )
    imported_tag_samples: list[int] = []
    imported_yolo_samples: list[int] = []
    generic_ai_samples: list[int] = []
    monkeypatch.setattr(
        auto_sample_service.tagtool_preannotate,
        "_process_one_sample",
        lambda **kwargs: imported_tag_samples.append(kwargs["sample_id"]),
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "_process_one_sample",
        lambda **kwargs: imported_yolo_samples.append(kwargs["sample_id"]),
    )
    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_process_single_sample",
        lambda **kwargs: generic_ai_samples.append(kwargs["sample_id"]),
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="both imported models",
        config={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "person", "value": "person"}]},
                },
                {
                    "tool": "tagTool",
                    "config": {
                        "attributes": [
                            {
                                "key": "Scene",
                                "value": "sceneType",
                                "type": "enum",
                                "options": [{"key": "Road", "value": "road"}],
                            }
                        ]
                    },
                },
            ]
        },
    )
    attachment = _create_attachment(
        db,
        task_id=task.id,
        created_by=user.id,
        filename="both-imported.jpg",
    )
    with begin_transaction(db):
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    state="NEW",
                    annotated_count=0,
                    data=json.dumps({}, ensure_ascii=False),
                )
            ],
        )[0]

    auto_sample_service._run_auto_preannotate_sync(
        task.id,
        [sample.id],
        user.id,
        "pytest_both_imported_models",
    )

    assert imported_tag_samples == [sample.id]
    assert imported_yolo_samples == [sample.id]
    assert generic_ai_samples == []


def test_auto_preannotate_falls_back_to_ai_detection_without_yolo_model(
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", False)
    monkeypatch.setattr(auto_sample_service, "refresh_task_training_ready", lambda *args: None)
    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_configured_bbox_vision_available",
        lambda: True,
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_process_single_sample",
        lambda **kwargs: calls.append(kwargs),
    )

    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="auto ai detection fallback",
        config={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "person", "value": "person"}]},
                }
            ]
        },
    )
    attachment = _create_attachment(
        db, task_id=task.id, created_by=user.id, filename="fallback.jpg"
    )
    with begin_transaction(db):
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    state="NEW",
                    annotated_count=0,
                    data=json.dumps({}, ensure_ascii=False),
                )
            ],
        )[0]

    auto_sample_service._run_auto_preannotate_sync(
        task.id,
        [sample.id],
        user.id,
        "pytest_ai_detection_fallback",
    )

    assert any(call.get("include_geometry_tools") is True for call in calls)
    assert not any(call.get("include_geometry_tools") is False for call in calls)


def test_auto_preannotate_does_not_persist_generated_geometry(
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", False)
    monkeypatch.setattr(auto_sample_service, "refresh_task_training_ready", lambda *args: None)
    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_configured_bbox_vision_available",
        lambda: False,
    )

    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="generated preview stays preview",
        config={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "person", "value": "person"}]},
                }
            ]
        },
    )
    attachment = _create_attachment(
        db, task_id=task.id, created_by=user.id, filename="preview.jpg"
    )
    with begin_transaction(db):
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    state="NEW",
                    annotated_count=0,
                    data=json.dumps({}, ensure_ascii=False),
                )
            ],
        )[0]
        crud_pre_annotation.batch(
            db=db,
            pre_annotations=[
                TaskPreAnnotation(
                    task_id=task.id,
                    file_id=None,
                    sample_name=attachment.filename,
                    data=json.dumps(
                        {
                            "sample_name": attachment.filename,
                            "annotations": {
                                "rectTool": {
                                    "toolName": "rectTool",
                                    "result": [
                                        {"label": "ai_preview", "x": 1, "y": 2, "width": 3, "height": 4}
                                    ],
                                }
                            },
                            "meta": {"source_type": "ai_generated", "preannotation_kind": "geometry"},
                        },
                        ensure_ascii=False,
                    ),
                    created_by=user.id,
                    updated_by=user.id,
                ),
                TaskPreAnnotation(
                    task_id=task.id,
                    file_id=None,
                    sample_name=attachment.filename,
                    data=json.dumps(
                        {
                            "sample_name": attachment.filename,
                            "annotations": {
                                "rectTool": {
                                    "toolName": "rectTool",
                                    "result": [
                                        {"label": "imported_label", "x": 5, "y": 6, "width": 7, "height": 8}
                                    ],
                                }
                            },
                            "meta": {"source_type": "experiment_dataset_metadata"},
                        },
                        ensure_ascii=False,
                    ),
                    created_by=user.id,
                    updated_by=user.id,
                ),
            ],
        )

    auto_sample_service._run_auto_preannotate_sync(
        task.id,
        [sample.id],
        user.id,
        "pytest_generated_preview",
    )

    with session_scope() as verify_db:
        persisted = crud_sample.get(db=verify_db, sample_id=sample.id)
    sample_data = json.loads(persisted.data)
    result_payload = sample_data.get("result")
    if isinstance(result_payload, str):
        result_payload = json.loads(result_payload)
    labels = [item["label"] for item in result_payload["rectTool"]["result"]]
    assert "imported_label" in labels
    assert "ai_preview" not in labels


def test_auto_preannotate_ai_detection_fallback_can_be_disabled(
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", False)
    monkeypatch.setattr(settings, "AUTO_PREANNOTATE_AI_DETECTION_ENABLED", False)
    monkeypatch.setattr(auto_sample_service, "refresh_task_training_ready", lambda *args: None)
    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_configured_bbox_vision_available",
        lambda: True,
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_process_single_sample",
        lambda **kwargs: calls.append(kwargs),
    )

    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="auto ai detection disabled",
        config={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "person", "value": "person"}]},
                }
            ]
        },
    )
    attachment = _create_attachment(db, task_id=task.id, created_by=user.id, filename="disabled.jpg")
    with begin_transaction(db):
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    state="NEW",
                    annotated_count=0,
                    data=json.dumps({}, ensure_ascii=False),
                )
            ],
        )[0]

    auto_sample_service._run_auto_preannotate_sync(task.id, [sample.id], user.id, "pytest_ai_disabled")

    assert calls == []


def test_auto_preannotate_ai_detection_fallback_respects_sample_cap(
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", False)
    monkeypatch.setattr(settings, "AUTO_PREANNOTATE_AI_DETECTION_ENABLED", True)
    monkeypatch.setattr(settings, "AUTO_PREANNOTATE_AI_DETECTION_MAX_SAMPLES", 1)
    monkeypatch.setattr(auto_sample_service, "refresh_task_training_ready", lambda *args: None)
    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_configured_bbox_vision_available",
        lambda: True,
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        auto_sample_service.auto_label_mod,
        "_process_single_sample",
        lambda **kwargs: calls.append(kwargs),
    )

    user = db.query(User).filter(User.username == "test@example.com").first()
    task = _create_image_task(
        db,
        created_by=user.id,
        name="auto ai detection capped",
        config={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "person", "value": "person"}]},
                }
            ]
        },
    )
    attachment = _create_attachment(db, task_id=task.id, created_by=user.id, filename="capped.jpg")
    with begin_transaction(db):
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    state="NEW",
                    annotated_count=0,
                    data=json.dumps({}, ensure_ascii=False),
                )
            ],
        )[0]

    auto_sample_service._run_auto_preannotate_sync(
        task.id,
        [sample.id, sample.id + 100000],
        user.id,
        "pytest_ai_capped",
    )

    assert calls == []
