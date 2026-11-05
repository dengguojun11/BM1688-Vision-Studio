from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from labelu.internal.application.service import strategy_model_training as service
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.annotation_contract import TaskAnnotationLabel
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
from labelu.internal.domain.models.published_model_catalog import (
    PublishedModelCatalog,
)
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob

MODEL_LABELS = [
    "nonvehicle_electricbike",
    "nonvehicle_bicycle",
    "nonvehicle_tricycle",
    "nonvehicle_strolle",
    "nonvehicle_skateboard",
    "nonvehicle_balanceBike",
    "nonvehicle_other",
    "nonvehicle_wheelchair",
    "vehicle_motorbike",
]


def _catalog(db, *, public_model_id: str = "591", labels=None) -> PublishedModelCatalog:
    with begin_transaction(db):
        row = PublishedModelCatalog(
            public_model_id=public_model_id,
            aicm_model_set_id=(
                int(public_model_id) if public_model_id.isdigit() else None
            ),
            name="非机动车违停_bm1688_v13",
            display_name="非机动车违停_bm1688_v13",
            source_type="remote_legacy",
            source_system="aicm",
            status="active",
            availability_status="available",
            labels_json=json.dumps(
                labels if labels is not None else MODEL_LABELS, ensure_ascii=False
            ),
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        return row


def _label_color(value: str) -> str:
    return "#" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:6]


def _annotated_task(
    db,
    *,
    name: str,
    labels: list[str],
    done_samples: int,
) -> Task:
    with begin_transaction(db):
        task = Task(
            name=name,
            media_type="IMAGE",
            status="INPROGRESS",
            config=json.dumps({"tool": [{"toolName": "rectTool"}]}),
        )
        db.add(task)
        db.flush()
        for value in labels:
            label = (
                db.query(AnnotationLabel)
                .filter(AnnotationLabel.label_value == value)
                .one_or_none()
            )
            if label is None:
                label = AnnotationLabel(
                    label_key=f"{value}-中文",
                    label_value=value,
                    normalized_label_value=value.lower(),
                    annotation_kind="bbox",
                    color=_label_color(value),
                    status="active",
                )
                db.add(label)
                db.flush()
            db.add(
                TaskAnnotationLabel(
                    task_id=int(task.id),
                    contract_id=1000 + int(task.id),
                    label_id=int(label.id),
                    status="active",
                )
            )
        for _ in range(done_samples):
            db.add(
                TaskSample(
                    task_id=int(task.id),
                    state=SampleState.DONE.value,
                    data="{}",
                )
            )
        db.flush()
        db.refresh(task)
        return task


def _strategy_row(db, external_task_id: str = "strategy-model-train-1"):
    with begin_transaction(db):
        row = StrategyTaskIntegration(
            external_task_id=external_task_id,
            schema_version="v1",
            task_name="非机动车违停高评分误报模型优化",
            task_type="model_capability",
            source_type="third_party",
            source_system="strategy_evolution",
            dataset_s3_uri="",
            dataset_image_prefix="",
            metadata_key="",
            bucket="",
            prefix="",
            labeling_required=False,
            labeling_labels_json="[]",
            target_json=json.dumps(
                {
                    "device_type": "normal",
                    "event_type": "illegalParkingofNonMotorVehicle",
                    "model_id": "591",
                    "scene_type": "common_ai_alarm",
                    "strategy_id": "strategy-1",
                }
            ),
            data_window_json=json.dumps(
                {
                    "start_time": "2026-10-15 00:00:00",
                    "end_time": "2026-10-16 00:00:00",
                }
            ),
            experiment_json="{}",
            request_payload_json="{}",
            request_sha256="b" * 64,
            import_status="PENDING",
            platform_status="waiting_for_model",
            automation_source="strategy_evolution",
            automation_stage="model_resolution",
            automation_status="running",
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        return row


def test_resolve_model_contract_uses_remote_legacy_catalog_labels(db):
    _catalog(db)

    contract = service.resolve_model_contract(db, "591")

    assert contract is not None
    assert contract["catalog_id"] is not None
    assert contract["display_name"].startswith("非机动车违停")
    assert contract["source_type"] == "remote_legacy"
    assert contract["labels"] == MODEL_LABELS


def test_select_label_matched_task_allows_partial_overlap(db):
    _catalog(db)
    task = _annotated_task(
        db,
        name="非机动车违停",
        labels=MODEL_LABELS[:4],
        done_samples=12,
    )

    selection = service.select_label_matched_task(db, MODEL_LABELS)

    assert selection is not None
    assert int(selection["task"].id) == int(task.id)
    assert selection["matched_count"] == 4
    assert selection["done_sample_count"] == 12


def test_select_label_matched_task_rejects_below_minimum_overlap(db):
    _catalog(db)
    _annotated_task(
        db,
        name="仅命中一个标签",
        labels=[MODEL_LABELS[0]],
        done_samples=20,
    )

    assert service.select_label_matched_task(db, MODEL_LABELS) is None


def test_select_label_matched_task_prefers_more_matched_labels(db):
    _catalog(db)
    _annotated_task(
        db,
        name="命中三个标签",
        labels=MODEL_LABELS[:3],
        done_samples=100,
    )
    better = _annotated_task(
        db,
        name="命中五个标签",
        labels=MODEL_LABELS[:5],
        done_samples=10,
    )

    selection = service.select_label_matched_task(db, MODEL_LABELS)

    assert selection is not None
    assert int(selection["task"].id) == int(better.id)
    assert selection["matched_count"] == 5


def test_resolve_initial_weights_uses_preset_without_local_training_job(db):
    task = _annotated_task(
        db, name="无训练作业任务", labels=MODEL_LABELS[:3], done_samples=5
    )

    weights = service.resolve_initial_weights(db, int(task.id))

    assert weights["weights_source"] == "preset"
    assert weights["model_weights"].endswith(".pt")
    assert weights["weights_ref_id"] is None


def test_resolve_initial_weights_uses_completed_local_job(db):
    task = _annotated_task(
        db, name="有训练作业任务", labels=MODEL_LABELS[:3], done_samples=5
    )
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=int(task.id),
            status="COMPLETED",
            model_weights="yolo11n.pt",
            weights_path="/tmp/weights/best.pt",
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    weights = service.resolve_initial_weights(db, int(task.id))

    assert weights["weights_source"] == "trained"
    assert weights["weights_ref_id"] == job_id


def test_model_lookup_due_respects_retry_interval(db):
    assert service.model_lookup_due({}) is True
    assert (
        service.model_lookup_due({"model_lookup_attempted_at": service._now_text()})
        is False
    )


def test_ensure_training_job_uses_manual_entrypoint_with_preset_weights(
    db, monkeypatch
):
    _catalog(db)
    task = _annotated_task(
        db, name="非机动车违停", labels=MODEL_LABELS[:4], done_samples=20
    )
    row = _strategy_row(db)
    captured = {}

    def fake_create(db, *, current_user, cmd, idempotency_key=None):
        captured["cmd"] = cmd
        captured["idempotency_key"] = idempotency_key
        job = YoloTrainJob(
            task_id=int(task.id),
            status="PENDING",
            created_by=int(current_user.id),
        )
        with begin_transaction(db):
            db.add(job)
            db.flush()
        return SimpleNamespace(latest_job_id=int(job.id), training_task_id=999)

    monkeypatch.setattr(
        service.training_task_service,
        "create_label_dimension_training_task",
        fake_create,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.gpu_training_available",
        lambda: (True, 1),
    )
    admin = User(username="open@system-test", hashed_password="x")
    with begin_transaction(db):
        db.add(admin)
        db.flush()
    admin = db.query(User).filter(User.id == int(admin.id)).one()

    created, payload = service.ensure_model_training_job_sync(
        db, row=row, result={}, admin=admin
    )

    assert created is True
    assert payload["derived_train_job_id"]
    assert payload["matched_task_id"] == int(task.id)
    assert payload["matched_labels"] == MODEL_LABELS[:4]
    command = captured["cmd"]
    assert command.selected_task_ids == [int(task.id)]
    assert command.weights_source == "preset"
    assert command.model_weights.endswith(".pt")
    assert captured["idempotency_key"] == f"strategy:{row.external_task_id}:1"
    job = (
        db.query(YoloTrainJob)
        .filter(YoloTrainJob.id == payload["derived_train_job_id"])
        .one()
    )
    assert job.source_system == "strategy_evolution"
    assert job.source_external_task_id == row.external_task_id
    assert job.source_integration_id == int(row.id)


def test_ensure_training_job_waits_for_gpu_instead_of_cpu(db, monkeypatch):
    _catalog(db)
    _annotated_task(db, name="非机动车违停", labels=MODEL_LABELS[:4], done_samples=20)
    row = _strategy_row(db)
    admin = User(username="open@system-gpu-wait", hashed_password="x")
    with begin_transaction(db):
        db.add(admin)
        db.flush()
    admin = db.query(User).filter(User.id == int(admin.id)).one()
    monkeypatch.setattr(settings, "YOLO_TRAIN_REQUIRE_GPU", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.gpu_training_available",
        lambda: (False, 0),
    )

    created, payload = service.ensure_model_training_job_sync(
        db, row=row, result={}, admin=admin
    )

    assert created is False
    assert payload["model_training_status"] == "waiting_for_gpu"
    assert "禁止 CPU 训练" in payload["message"]
    assert (
        db.query(YoloTrainJob)
        .filter(YoloTrainJob.source_integration_id == int(row.id))
        .count()
        == 0
    )
