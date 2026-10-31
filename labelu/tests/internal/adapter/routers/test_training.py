import hashlib
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.adapter.routers import training as training_router
from labelu.internal.application.command.training import CreateTrainingJobCommand
from labelu.internal.application.response.training import TrainingIterationPreviewResponse
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.idempotency import request_payload_hash
from labelu.internal.domain.models.dataset import Dataset, DatasetVersion
from labelu.internal.domain.models.auto_training import AutoTrainingPlan
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def test_unified_plate_recipe_binds_pose_profile_and_preset():
    command = CreateTrainingJobCommand(
        task_id=11,
        algorithm_key="yolo_detection",
        training_recipe_key="plate_detection_v1",
    )
    normalized = training_router._normalize_plate_training_contract(
        command,
        SimpleNamespace(job_type="plate_detection"),
    )

    assert normalized.rect_preset_slug == "plateDetection"
    assert normalized.plate_training_target == "plate_detection"
    assert normalized.model_weights == "yolov8n-pose.pt"
    assert normalized.params["training_profile"] == "plate_detection_v1"
    assert normalized.params["runtime_profile"] == "plate_detection_v1"
    assert normalized.params["runtime_interface_version"] == 1


def test_unified_plate_recipe_rejects_label_dimension_contract():
    command = CreateTrainingJobCommand(
        task_id=11,
        algorithm_key="yolo_detection",
        training_recipe_key="plate_detection_v1",
        label_ids=[7],
    )

    with pytest.raises(LabelUException, match="不能按标签维度训练"):
        training_router._normalize_plate_training_contract(
            command,
            SimpleNamespace(job_type="plate_detection"),
        )


def _dataset(db: Session, user_id: int, name: str = "训练数据") -> Dataset:
    row = Dataset(
        owner_id=user_id,
        created_by=user_id,
        updated_by=user_id,
        name=name,
        status="ACTIVE",
    )
    db.add(row)
    db.flush()
    return row


def _task(
    db: Session,
    user_id: int,
    *,
    dataset_id: int | None = None,
    source_type: str = "internal",
) -> Task:
    row = Task(
        name="训练任务",
        media_type="IMAGE",
        status="FINISHED",
        created_by=user_id,
        updated_by=user_id,
        dataset_id=dataset_id,
        source_type=source_type,
    )
    db.add(row)
    db.flush()
    return row


def _version(db: Session, dataset: Dataset, user_id: int) -> DatasetVersion:
    row = DatasetVersion(
        dataset_id=dataset.id,
        version_no=1,
        status="FROZEN",
        manifest_rel_path="manifests/training.json",
        manifest_sha256="a" * 64,
        sample_count=2,
        created_by=user_id,
        frozen_by=user_id,
        frozen_at=datetime.now(),
    )
    db.add(row)
    db.flush()
    return row


def _plate_detection_manifest(*, points=None, labels=None):
    annotation = {
        "result": {
            "rectTool": {
                "result": [
                    {
                        "label": "doubleEP",
                        "x": 10,
                        "y": 10,
                        "width": 80,
                        "height": 40,
                    }
                ]
            },
            "polygonTool": {
                "result": [
                    {
                        "label": "platePoint",
                        "points": points
                        or [[10, 10], [90, 10], [90, 50], [10, 50]],
                    }
                ]
            },
        }
    }
    return {
        "format": "7gai.dataset_manifest.v1",
        "source_task_ids": [11],
        "annotation_contract": {
            "dataset_contract_id": 3,
            "dataset_contract_revision": 1,
            "label_set_signature": "label-signature",
            "class_order_signature": "class-signature",
            "labels": labels
            or [
                {
                    "label_id": 1,
                    "label_key": "车牌框",
                    "label_value": "doubleEP",
                    "annotation_kind": "bbox",
                },
                {
                    "label_id": 2,
                    "label_key": "车牌四角点",
                    "label_value": "platePoint",
                    "annotation_kind": "polygon",
                },
            ],
        },
        "samples": [
            {
                "sample_id": 101,
                "task_id": 11,
                "split": "train",
                "annotation": annotation,
                "asset_sha256": "a" * 64,
            }
        ],
    }


class _ManifestStorage:
    def __init__(self, raw: str):
        self.raw = raw

    def read_text(self, _path):
        return self.raw


def _plate_version_for_manifest(manifest):
    raw = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    return SimpleNamespace(
        id=7,
        dataset_id=3,
        version_no=1,
        status="FROZEN",
        manifest_rel_path="datasets/dataset-3/v-7/manifest.json",
        manifest_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        dataset_label_contract_id=3,
        dataset_contract_revision=1,
        label_set_signature="label-signature",
        class_order_signature="class-signature",
    ), raw


def test_plate_detection_frozen_contract_requires_mixed_geometry(monkeypatch):
    manifest = _plate_detection_manifest()
    version, raw = _plate_version_for_manifest(manifest)
    monkeypatch.setattr(
        training_router,
        "get_storage_backend",
        lambda: _ManifestStorage(raw),
    )

    assert training_router._validate_plate_detection_dataset_version(version) == manifest

    missing = _plate_detection_manifest(labels=[manifest["annotation_contract"]["labels"][0]])
    missing_version, missing_raw = _plate_version_for_manifest(missing)
    monkeypatch.setattr(
        training_router,
        "get_storage_backend",
        lambda: _ManifestStorage(missing_raw),
    )
    with pytest.raises(LabelUException, match="doubleEP.*platePoint"):
        training_router._validate_plate_detection_dataset_version(missing_version)


def test_plate_detection_frozen_contract_rejects_wrong_polygon_kind(monkeypatch):
    manifest = _plate_detection_manifest()
    manifest["annotation_contract"]["labels"][1]["annotation_kind"] = "bbox"
    version, raw = _plate_version_for_manifest(manifest)
    monkeypatch.setattr(
        training_router,
        "get_storage_backend",
        lambda: _ManifestStorage(raw),
    )

    with pytest.raises(LabelUException, match="platePoint.*polygon"):
        training_router._validate_plate_detection_dataset_version(version)


def test_plate_detection_frozen_contract_rejects_non_four_point_polygon(monkeypatch):
    manifest = _plate_detection_manifest(points=[[10, 10], [90, 10], [90, 50]])
    version, raw = _plate_version_for_manifest(manifest)
    monkeypatch.setattr(
        training_router,
        "get_storage_backend",
        lambda: _ManifestStorage(raw),
    )

    with pytest.raises(LabelUException, match="四角点"):
        training_router._validate_plate_detection_dataset_version(version)


def test_unified_training_contract_has_no_project_dimension():
    command = CreateTrainingJobCommand(
        task_id=11,
        algorithm_key="yolo_detection",
        dataset_version_id=12,
        execution_mode="local",
    )
    assert not hasattr(command, "project_id")
    assert "project_id" not in command.__class__.model_fields


def test_unified_training_contract_normalizes_dataset_scope():
    command = CreateTrainingJobCommand(
        task_id=11,
        algorithm_key="yolo_detection",
        dataset_id=7,
        dataset_ids=[9, 7, 9],
    )
    assert command.dataset_ids == [9, 7]


def test_unified_training_rejects_multi_dataset_scope_for_single_task(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    # The assertion is intentionally before algorithm/catalog resolution;
    # avoid reseeding the shared SQLite fixture while another test process may
    # still be using it.
    monkeypatch.setattr(training_router, "ensure_seeded", lambda _db: None)
    test_user = db.query(User).filter(User.username == "test@example.com").one()
    primary = _dataset(db, test_user.id, name="单任务主数据集")
    secondary = _dataset(db, test_user.id, name="单任务附加数据集")
    task = _task(db, test_user.id, dataset_id=primary.id)
    db.commit()
    response = client.post(
        "/api/v1/training/jobs",
        headers=testuser_token_headers,
        json={
            "task_id": task.id,
            "algorithm_key": "yolo_detection",
            "dataset_ids": [primary.id, secondary.id],
        },
    )
    assert response.status_code == 409, response.text
    assert "单任务训练不能合并多个逻辑数据集" in response.text


def test_training_eligible_tasks_forwards_multiple_dataset_scope(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    monkeypatch,
):
    calls = []

    async def fake_candidates(db, current_user, **kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(
        training_router.dataset_service,
        "_dataset_or_raise",
        lambda _db, dataset_id, _user: SimpleNamespace(id=dataset_id),
    )
    monkeypatch.setattr(
        training_router.task_service,
        "list_task_dimension_training_candidates",
        fake_candidates,
    )
    response = client.get(
        "/api/v1/training/eligible-tasks",
        params=[
            ("training_dimension", "task"),
            ("dataset_ids", "17"),
            ("dataset_ids", "19"),
            ("page", "0"),
            ("size", "20"),
        ],
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    assert calls == [{"page": 0, "size": 20, "dataset_ids": [17, 19]}]


def test_training_eligible_tasks_forwards_plate_training_recipe(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    monkeypatch,
):
    calls = []
    seeded = []

    async def fake_candidates(db, current_user, **kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(
        training_router.task_service,
        "list_task_dimension_training_candidates",
        fake_candidates,
    )
    monkeypatch.setattr(
        training_router,
        "ensure_seeded",
        lambda _db: seeded.append(True),
    )
    response = client.get(
        "/api/v1/training/eligible-tasks",
        params={
            "training_dimension": "task",
            "training_recipe_key": "  plate_detection_v1  ",
            "page": 0,
            "size": 20,
        },
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    assert seeded == [True]
    assert calls == [
        {"page": 0, "size": 20, "training_recipe_key": "plate_detection_v1"}
    ]


def test_unified_training_contract_rejects_retired_project_parameter():
    try:
        CreateTrainingJobCommand(
            project_id=7,
            task_id=11,
            algorithm_key="yolo_detection",
        )
    except ValueError as exc:
        assert "project_id" in str(exc)
    else:
        raise AssertionError("退役的项目参数不得被静默接受")


def test_training_create_api_rejects_retired_project_parameter(
    client: TestClient,
    testuser_token_headers: dict[str, str],
):
    response = client.post(
        "/api/v1/training/jobs",
        headers=testuser_token_headers,
        json={
            "project_id": 7,
            "task_id": 11,
            "algorithm_key": "yolo_detection",
        },
    )
    assert response.status_code == 422


def test_unified_yolo_idempotency_replays_complete_lineage_before_snapshot(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    dataset = _dataset(db, 1)
    version = _version(db, dataset, 1)
    task = _task(db, 1, dataset_id=dataset.id)
    command = CreateTrainingJobCommand(
        task_id=task.id,
        algorithm_key="yolo_detection",
        model_weights="yolo11n.pt",
    )
    retry_hash = request_payload_hash(
        {"route": "training/jobs", "command": command.dict()}
    )
    manifest = {
        "dataset_version_id": version.id,
        "dataset_id": version.dataset_id,
        "version_no": version.version_no,
        "manifest_rel_path": version.manifest_rel_path,
        "manifest_sha256": version.manifest_sha256,
    }
    existing = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        idempotency_key="unified-yolo-retry-001",
        idempotency_request_hash=retry_hash,
        model_weights="yolo11n.pt",
        epochs=command.epochs,
        imgsz=command.imgsz,
        category="yolo_detection",
        job_type="yolo_detection",
        hyperparams_json=json.dumps(
            {
                "algorithm_key": "yolo_detection",
                "dataset_version_id": version.id,
                "dataset_manifest": manifest,
            }
        ),
        dataset_manifest_json=json.dumps(manifest),
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)
    snapshot_calls = []

    async def fail_if_snapshot_runs(*args, **kwargs):
        snapshot_calls.append((args, kwargs))
        raise AssertionError("idempotent replay must return before snapshot creation")

    monkeypatch.setattr(
        training_router.dataset_service,
        "ensure_training_version_for_task",
        fail_if_snapshot_runs,
    )
    dispatch_calls = []
    monkeypatch.setattr(
        training_router.yolo_train_service,
        "_submit_yolo_train_job",
        lambda *args, **kwargs: dispatch_calls.append((args, kwargs)),
    )
    response = client.post(
        "/api/v1/training/jobs",
        headers={
            **testuser_token_headers,
            "Idempotency-Key": "unified-yolo-retry-001",
        },
        json=command.dict(exclude_none=True),
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["id"] == existing.id
    assert snapshot_calls == []
    assert len(dispatch_calls) == 1
    assert dispatch_calls[0][0][:2] == (existing.id, task.id)
    assert dispatch_calls[0][0][2].hyperparams["dataset_manifest"] == manifest


def test_unified_yolo_idempotency_rejects_conflicting_version_without_snapshot(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    dataset = _dataset(db, 1, name="版本冲突数据集")
    version = _version(db, dataset, 1)
    task = _task(db, 1, dataset_id=dataset.id)
    command = CreateTrainingJobCommand(
        task_id=task.id,
        algorithm_key="yolo_detection",
        model_weights="yolo11n.pt",
    )
    retry_hash = request_payload_hash(
        {"route": "training/jobs", "command": command.dict()}
    )
    conflicting_manifest = {
        "dataset_version_id": version.id,
        "dataset_id": dataset.id,
        "version_no": version.version_no + 1,
        "manifest_rel_path": version.manifest_rel_path,
        "manifest_sha256": version.manifest_sha256,
    }
    existing = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        idempotency_key="unified-yolo-conflict-version-001",
        idempotency_request_hash=retry_hash,
        model_weights="yolo11n.pt",
        epochs=command.epochs,
        imgsz=command.imgsz,
        category="yolo_detection",
        job_type="yolo_detection",
        hyperparams_json=json.dumps({"dataset_manifest": conflicting_manifest}),
        dataset_manifest_json=json.dumps(conflicting_manifest),
    )
    db.add(existing)
    db.commit()
    snapshot_calls = []

    async def fail_if_snapshot_runs(*args, **kwargs):
        snapshot_calls.append((args, kwargs))
        raise AssertionError("conflicting lineage must not create a new snapshot")

    monkeypatch.setattr(
        training_router.dataset_service,
        "ensure_training_version_for_task",
        fail_if_snapshot_runs,
    )
    response = client.post(
        "/api/v1/training/jobs",
        headers={
            **testuser_token_headers,
            "Idempotency-Key": "unified-yolo-conflict-version-001",
        },
        json=command.dict(exclude_none=True),
    )

    assert response.status_code == 409, response.text
    assert snapshot_calls == []


def test_unified_yolo_idempotency_rejects_task_dataset_rebind_without_snapshot(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    task_dataset = _dataset(db, 1, name="任务当前数据集")
    manifest_dataset = _dataset(db, 1, name="旧训练数据集")
    version = DatasetVersion(
        dataset_id=manifest_dataset.id,
        version_no=1,
        status="FROZEN",
        manifest_rel_path="manifests/rebound.json",
        manifest_sha256="b" * 64,
        sample_count=1,
        created_by=1,
        frozen_by=1,
        frozen_at=datetime.now(),
    )
    db.add(version)
    task = _task(db, 1, dataset_id=task_dataset.id)
    db.flush()
    command = CreateTrainingJobCommand(
        task_id=task.id,
        dataset_id=task_dataset.id,
        algorithm_key="yolo_detection",
        model_weights="yolo11n.pt",
    )
    retry_hash = request_payload_hash(
        {"route": "training/jobs", "command": command.dict()}
    )
    manifest = {
        "dataset_version_id": version.id,
        "dataset_id": manifest_dataset.id,
        "version_no": version.version_no,
        "manifest_rel_path": version.manifest_rel_path,
        "manifest_sha256": version.manifest_sha256,
    }
    existing = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        idempotency_key="unified-yolo-conflict-dataset-001",
        idempotency_request_hash=retry_hash,
        model_weights="yolo11n.pt",
        epochs=command.epochs,
        imgsz=command.imgsz,
        category="yolo_detection",
        job_type="yolo_detection",
        hyperparams_json=json.dumps({"dataset_manifest": manifest}),
        dataset_manifest_json=json.dumps(manifest),
    )
    db.add(existing)
    db.commit()
    snapshot_calls = []

    async def fail_if_snapshot_runs(*args, **kwargs):
        snapshot_calls.append((args, kwargs))
        raise AssertionError("rebound lineage must not create a new snapshot")

    monkeypatch.setattr(
        training_router.dataset_service,
        "ensure_training_version_for_task",
        fail_if_snapshot_runs,
    )
    response = client.post(
        "/api/v1/training/jobs",
        headers={
            **testuser_token_headers,
            "Idempotency-Key": "unified-yolo-conflict-dataset-001",
        },
        json=command.dict(exclude_none=True),
    )

    assert response.status_code == 409, response.text
    assert snapshot_calls == []


def test_unified_yolo_idempotency_repairs_incomplete_lineage(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    dataset = _dataset(db, 1)
    version = _version(db, dataset, 1)
    task = _task(db, 1, dataset_id=dataset.id)
    command = CreateTrainingJobCommand(
        task_id=task.id,
        algorithm_key="yolo_detection",
        model_weights="yolo11n.pt",
    )
    retry_hash = request_payload_hash(
        {"route": "training/jobs", "command": command.dict()}
    )
    existing = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        idempotency_key="unified-yolo-repair-001",
        idempotency_request_hash=retry_hash,
        model_weights="yolo11n.pt",
        epochs=command.epochs,
        imgsz=command.imgsz,
        category="yolo_detection",
        job_type="yolo_detection",
        hyperparams_json=json.dumps({"algorithm_key": "yolo_detection"}),
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)
    snapshot_calls = []

    async def repair_snapshot(*args, **kwargs):
        snapshot_calls.append((args, kwargs))
        return version

    monkeypatch.setattr(
        training_router.dataset_service,
        "ensure_training_version_for_task",
        repair_snapshot,
    )
    response = client.post(
        "/api/v1/training/jobs",
        headers={
            **testuser_token_headers,
            "Idempotency-Key": "unified-yolo-repair-001",
        },
        json=command.dict(exclude_none=True),
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["id"] == existing.id
    assert len(snapshot_calls) == 1
    db.refresh(existing)
    repaired_manifest = json.loads(existing.dataset_manifest_json or "{}")
    assert repaired_manifest["dataset_version_id"] == version.id
    assert repaired_manifest["manifest_sha256"] == version.manifest_sha256


def test_complete_lineage_requires_matching_dataset_version_and_task_binding(
    db: Session,
):
    dataset = _dataset(db, 1, name="主数据集")
    version = _version(db, dataset, 1)
    task = _task(db, 1, dataset_id=dataset.id)
    manifest = {
        "dataset_version_id": version.id,
        "dataset_id": dataset.id,
        "version_no": version.version_no,
        "manifest_rel_path": version.manifest_rel_path,
        "manifest_sha256": version.manifest_sha256,
    }
    job = SimpleNamespace(
        task_id=task.id,
        job_type="yolo_detection",
        category="yolo_detection",
        hyperparams_json=json.dumps(
            {"dataset_version_id": version.id, "dataset_manifest": manifest}
        ),
        dataset_manifest_json=json.dumps(manifest),
    )

    assert training_router._job_has_complete_dataset_lineage(
        db,
        job,
        json.loads(job.hyperparams_json),
        expected_task_id=task.id,
        expected_dataset_id=dataset.id,
        expected_dataset_version_id=version.id,
        expected_job_type="yolo_detection",
    ) is True

    for field, value in (
        ("dataset_id", dataset.id + 1000),
        ("version_no", version.version_no + 1),
    ):
        invalid_manifest = dict(manifest)
        invalid_manifest[field] = value
        job.hyperparams_json = json.dumps(
            {"dataset_version_id": version.id, "dataset_manifest": invalid_manifest}
        )
        job.dataset_manifest_json = json.dumps(invalid_manifest)
        assert training_router._job_has_complete_dataset_lineage(
            db,
            job,
            json.loads(job.hyperparams_json),
            expected_task_id=task.id,
            expected_dataset_id=dataset.id,
            expected_dataset_version_id=version.id,
            expected_job_type="yolo_detection",
        ) is False

    job.hyperparams_json = json.dumps(
        {"dataset_version_id": version.id, "dataset_manifest": manifest}
    )
    job.dataset_manifest_json = json.dumps([manifest])
    assert training_router._job_has_complete_dataset_lineage(
        db,
        job,
        json.loads(job.hyperparams_json),
        expected_task_id=task.id,
        expected_dataset_id=dataset.id,
        expected_dataset_version_id=version.id,
        expected_job_type="yolo_detection",
    ) is False


def test_complete_lineage_rejects_manifest_from_task_bound_dataset_mismatch(
    db: Session,
):
    task_dataset = _dataset(db, 1, name="任务绑定数据集")
    manifest_dataset = _dataset(db, 1, name="清单数据集")
    task = _task(db, 1, dataset_id=task_dataset.id)
    version = DatasetVersion(
        dataset_id=manifest_dataset.id,
        version_no=1,
        status="FROZEN",
        manifest_rel_path="manifests/other.json",
        manifest_sha256="b" * 64,
        sample_count=1,
        created_by=1,
        frozen_by=1,
        frozen_at=datetime.now(),
    )
    db.add(version)
    db.flush()
    manifest = {
        "dataset_version_id": version.id,
        "dataset_id": manifest_dataset.id,
        "version_no": version.version_no,
        "manifest_rel_path": version.manifest_rel_path,
        "manifest_sha256": version.manifest_sha256,
    }
    job = SimpleNamespace(
        task_id=task.id,
        job_type="yolo_detection",
        hyperparams_json=json.dumps({"dataset_manifest": manifest}),
        dataset_manifest_json=json.dumps(manifest),
    )

    assert training_router._job_has_complete_dataset_lineage(
        db,
        job,
        json.loads(job.hyperparams_json),
        expected_task_id=task.id,
        expected_dataset_id=task_dataset.id,
        expected_dataset_version_id=version.id,
        expected_job_type="yolo_detection",
    ) is False


def test_pending_generic_replay_requeues_worker_event_once(
    db: Session,
    monkeypatch,
):
    task = _task(db, 1)
    job = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        status="PENDING",
        job_type="classification",
        model_weights="classifier.pt",
        epochs=1,
        imgsz=320,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    published = []
    monkeypatch.setattr(
        training_router,
        "enqueue_training_worker_command",
        lambda _db, *, job: published.append(int(job.id)),
    )

    training_router._requeue_pending_training_job(
        db,
        job,
        adapter_key="classification",
    )

    assert published == [job.id]


def test_requeue_does_not_restart_processing_job(db: Session, monkeypatch):
    task = _task(db, 1)
    job = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        status="PROCESSING",
        job_type="classification",
        model_weights="classifier.pt",
        epochs=1,
        imgsz=320,
    )
    db.add(job)
    db.commit()
    published = []
    monkeypatch.setattr(
        training_router,
        "enqueue_training_worker_command",
        lambda _db, *, job: published.append(int(job.id)),
    )

    training_router._requeue_pending_training_job(
        db,
        job,
        adapter_key="classification",
    )

    assert published == []


def test_training_catalog_returns_only_user_visible_dataset_versions(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    user_id = 1
    dataset = _dataset(db, user_id)
    version = _version(db, dataset, user_id)
    db.commit()

    response = client.get(
        "/api/v1/training/catalog", headers=testuser_token_headers
    )
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert "project_id" not in payload
    assert any(item["id"] == dataset.id for item in payload["datasets"])
    assert any(item["id"] == version.id for item in payload["dataset_versions"])


def test_training_eligible_tasks_does_not_accept_project_filter(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    monkeypatch,
):
    calls = []

    async def fake_candidates(db, current_user, **kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(
        training_router.task_service,
        "list_task_dimension_training_candidates",
        fake_candidates,
    )
    response = client.get(
        "/api/v1/training/eligible-tasks",
        params={"page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    assert calls == [{"page": 0, "size": 20}]


def test_training_eligible_tasks_forwards_search_to_candidate_service(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    monkeypatch,
):
    calls = []

    async def fake_candidates(db, current_user, **kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(
        training_router.task_service,
        "list_task_dimension_training_candidates",
        fake_candidates,
    )
    response = client.get(
        "/api/v1/training/eligible-tasks",
        params={"page": 1, "size": 20, "search": "  目标任务  "},
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    assert calls == [{"page": 1, "size": 20, "search": "目标任务"}]


def test_training_eligible_tasks_prefers_canonical_q_over_legacy_search(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    monkeypatch,
):
    calls = []

    async def fake_candidates(db, current_user, **kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(
        training_router.task_service,
        "list_task_dimension_training_candidates",
        fake_candidates,
    )
    response = client.get(
        "/api/v1/training/eligible-tasks",
        params={"page": 0, "size": 20, "search": "旧查询", "q": "规范查询"},
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    assert calls == [{"page": 0, "size": 20, "search": "规范查询"}]


def test_training_eligible_tasks_forwards_dataset_scope_and_search(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    monkeypatch,
):
    calls = []

    async def fake_candidates(db, current_user, **kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(
        training_router.dataset_service,
        "_dataset_or_raise",
        lambda _db, _dataset_id, _user: SimpleNamespace(id=_dataset_id),
    )
    monkeypatch.setattr(
        training_router.task_service,
        "list_task_dimension_training_candidates",
        fake_candidates,
    )

    response = client.get(
        "/api/v1/training/eligible-tasks",
        params={
            "training_dimension": "task",
            "dataset_id": 17,
            "search": "  道路检测  ",
            "page": 2,
            "size": 20,
        },
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    assert calls == [
        {"page": 2, "size": 20, "search": "道路检测", "dataset_id": 17}
    ]


def test_label_training_eligible_tasks_forwards_dataset_scope_and_search(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    monkeypatch,
):
    calls = []

    async def fake_candidates(db, current_user, **kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(
        training_router.dataset_service,
        "_dataset_or_raise",
        lambda _db, _dataset_id, _user: SimpleNamespace(id=_dataset_id),
    )
    monkeypatch.setattr(
        training_router.task_service,
        "list_training_candidates_by_labels",
        fake_candidates,
    )

    response = client.get(
        "/api/v1/training/eligible-tasks",
        params=[
            ("training_dimension", "label"),
            ("dataset_id", "17"),
            ("label_ids", "3"),
            ("label_ids", "5"),
            ("search", "  车道  "),
            ("page", "1"),
            ("size", "20"),
        ],
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    assert calls == [
        {
            "label_ids": [3, 5],
            "page": 1,
            "size": 20,
            "dataset_id": 17,
            "dataset_category_ids": None,
            "search": "车道",
        }
    ]


def test_training_job_list_uses_task_access_and_hides_legacy_project_column(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    task = _task(db, 1)
    db.add(
        YoloTrainJob(
            task_id=task.id,
            project_id=None,
            created_by=1,
            model_weights="yolo11n.pt",
            epochs=1,
            imgsz=640,
            category="yolo_detection",
            status="COMPLETED",
            hyperparams_json=json.dumps({"algorithm_key": "yolo_detection"}),
        )
    )
    db.commit()

    response = client.get(
        "/api/v1/training/jobs", headers=testuser_token_headers
    )
    assert response.status_code == 200, response.text
    item = response.json()["data"][0]
    assert item["task_id"] == task.id
    assert "project_id" not in item
    assert item["current_epoch"] == 1
    assert item["progress_percent"] == 100


def test_training_progress_uses_worker_epoch_boundary():
    job = type(
        "Job",
        (),
        {
            "id": 88,
            "epochs": 50,
            "status": "PROCESSING",
            "weights_path": None,
            "metrics_summary": None,
        },
    )()
    assert training_router._training_progress(job, {"current_epoch": 17}) == (17, 34)


def test_training_job_detail_uses_task_access_without_project_row(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    task = _task(db, 1)
    job = YoloTrainJob(
        task_id=task.id,
        project_id=None,
        created_by=1,
        model_weights="yolo11n.pt",
        epochs=1,
        imgsz=640,
        category="yolo_detection",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    response = client.get(
        f"/api/v1/training/jobs/{job.id}", headers=testuser_token_headers
    )
    assert response.status_code == 200, response.text
    assert "project_id" not in response.json()["data"]


def test_training_summary_has_no_project_dimension(
    client: TestClient,
    testuser_token_headers: dict[str, str],
):
    response = client.get(
        "/api/v1/training/summary", headers=testuser_token_headers
    )
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert "project_id" not in payload
    assert payload["auto_training"]["plan_count"] == 0


def test_training_summary_includes_visible_automatic_training_plans(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    plan = AutoTrainingPlan(
        owner_id=1,
        name="统一摘要自动训练计划",
        series_type="yolo_detection",
        dataset_category_id=8,
        label_set_signature="a" * 64,
        class_order_signature="b" * 64,
        status="AUTO_QUALITY_REACHED",
        version=1,
        created_by=1,
        updated_by=1,
    )
    db.add(plan)
    db.commit()

    response = client.get(
        "/api/v1/training/summary", headers=testuser_token_headers
    )
    assert response.status_code == 200, response.text
    auto_training = response.json()["data"]["auto_training"]
    assert auto_training["plan_count"] == 1
    assert auto_training["active_plan_count"] == 0
    assert [item["name"] for item in auto_training["plans"]] == [
        "统一摘要自动训练计划"
    ]


def test_training_summary_search_and_pagination_for_history(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    for name in ("道路夜间迭代 A", "道路夜间迭代 B", "园区白天迭代"):
        db.add(
            AutoTrainingPlan(
                owner_id=1,
                name=name,
                series_type="yolo_detection",
                dataset_category_id=8,
                label_set_signature=(name.encode("utf-8").hex() + "a" * 64)[:64],
                class_order_signature="b" * 64,
                status="ACTIVE",
                version=1,
                created_by=1,
                updated_by=1,
            )
        )
    db.commit()

    response = client.get(
        "/api/v1/training/summary",
        params={"q": "夜间迭代", "page": 0, "size": 1},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["auto_training"]["plans_total"] == 2
    assert len(payload["auto_training"]["plans"]) == 1
    assert payload["pagination"] == {
        "page": 0,
        "size": 1,
        "plans_total": 2,
        "reports_total": 0,
    }

    legacy_response = client.get(
        "/api/v1/training/summary",
        params={"search": "园区白天迭代", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    assert legacy_response.status_code == 200, legacy_response.text
    legacy_payload = legacy_response.json()["data"]
    assert legacy_payload["auto_training"]["plans_total"] == 1
    assert [item["name"] for item in legacy_payload["auto_training"]["plans"]] == [
        "园区白天迭代"
    ]


def test_auto_training_plan_list_accepts_canonical_q_and_legacy_search(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)
    db.add(
        AutoTrainingPlan(
            owner_id=1,
            name="计划搜索兼容性",
            series_type="yolo_detection",
            dataset_category_id=8,
            label_set_signature="c" * 64,
            class_order_signature="d" * 64,
            status="ACTIVE",
            version=1,
            created_by=1,
            updated_by=1,
        )
    )
    db.commit()

    for key in ("q", "search"):
        response = client.get(
            "/api/v1/auto-training-plans",
            params={key: "兼容性", "page": 0, "size": 20},
            headers=testuser_token_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta_data"]["total"] == 1
        assert body["data"][0]["name"] == "计划搜索兼容性"


def test_training_job_detail_returns_real_compile_failure(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    task = _task(db, 1)
    job = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        model_weights="yolo11n.pt",
        weights_path="yolo_train/job-9/train/weights/best.pt",
        epochs=1,
        imgsz=640,
        category="yolo_detection",
        status="COMPLETED",
        hyperparams_json=json.dumps({"algorithm_key": "yolo_detection"}),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    quality = type(
        "Quality",
        (),
        {
            "bm1688_export_id": 19,
            "bm1688_export_status": "FAILED",
            "bm1688_compile_status": "failed",
            "bm1688_compile_status_label": "编译失败",
            "bm1688_compile_updated_at": None,
            "bm1688_bmodel_rel_path": None,
        },
    )()
    export = type("Export", (), {"error_message": "No module named 'onnxscript'"})()
    monkeypatch.setattr(
        training_router.yolo_train_service,
        "get_train_job_by_id",
        lambda **kwargs: quality,
    )
    monkeypatch.setattr(
        training_router.crud_yolo_bm1688_export,
        "list_all_for_job",
        lambda db, yolo_train_job_id: [export],
    )

    response = client.get(
        f"/api/v1/training/jobs/{job.id}", headers=testuser_token_headers
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["bm1688_compile_status"] == "failed"
    assert payload["bm1688_compile_status_label"] == "编译失败"
    assert payload["bm1688_compile_error"] == "No module named 'onnxscript'"


def test_training_job_detail_returns_previous_version_metric_comparison(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    task = _task(db, 1)
    previous = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        model_weights="yolo11n.pt",
        weights_path="runs/job-1/best.pt",
        epochs=50,
        imgsz=640,
        category="yolo_detection",
        status="COMPLETED",
        metrics_summary=json.dumps({
            "best_pt_val_metrics": {
                "map50_95": 0.4,
                "map50": 0.7,
                "precision": 0.8,
                "recall": 0.75,
            }
        }),
    )
    db.add(previous)
    db.flush()
    current = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        model_weights=f"trained:{previous.id}",
        weights_path="runs/job-2/best.pt",
        epochs=50,
        imgsz=640,
        category="yolo_detection",
        status="COMPLETED",
        metrics_summary=json.dumps({
            "best_pt_val_metrics": {
                "map50_95": 0.45,
                "map50": 0.73,
                "precision": 0.82,
                "recall": 0.77,
            }
        }),
    )
    db.add(current)
    db.commit()

    response = client.get(
        f"/api/v1/training/jobs/{current.id}", headers=testuser_token_headers
    )

    assert response.status_code == 200, response.text
    comparison = response.json()["data"]["previous_version_comparison"]
    assert comparison["available"] is True
    assert comparison["status"] == "improved"
    assert comparison["previous_job_id"] == previous.id
    assert comparison["current_job_id"] == current.id
    assert comparison["comparison_basis"] == "authoritative_val_metrics"
    assert round(comparison["metrics"][0]["delta"], 10) == 0.05


def test_iteration_preview_api_returns_readable_parent_without_creating_job(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    weights_file = tmp_path / "runs" / "job-1" / "best.pt"
    weights_file.parent.mkdir(parents=True, exist_ok=True)
    weights_file.write_bytes(b"pytest-placeholder-weights")
    dataset = _dataset(db, 1, "父模型数据集")
    task = _task(db, 1, dataset_id=dataset.id)
    parent = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        model_weights="yolo11n.pt",
        weights_path="runs/job-1/best.pt",
        epochs=50,
        imgsz=640,
        category="yolo_detection",
        job_type="yolo_detection",
        status="COMPLETED",
    )
    db.add(parent)
    db.commit()
    job_count = db.query(YoloTrainJob).count()

    response = client.get(
        f"/api/v1/training/iteration-preview?task_id={task.id}",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["weights_source"] == "trained"
    assert data["weights_ref_id"] == parent.id
    assert data["parent"]["display_name"] == task.name
    assert data["parent"]["dataset_id"] == dataset.id
    assert data["parent"]["dataset_name"] == dataset.name
    assert data["parent"]["weights_filename"] == "best.pt"
    assert data["suggested_params"] == {}
    assert db.query(YoloTrainJob).count() == job_count


def test_iteration_preview_api_passes_global_tag_conditions_to_resolver(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    task = _task(db, 1)
    db.commit()
    captured: dict[str, object] = {}

    def fake_validate(_db, conditions):
        return conditions

    def fake_preview(*args, **kwargs):
        captured.update(kwargs)
        return TrainingIterationPreviewResponse(
            weights_source="preset",
            model_weights="yolo11n.pt",
            sample_conditions=kwargs.get("sample_conditions") or {},
        )

    monkeypatch.setattr(
        training_router.dataset_service,
        "validate_sample_conditions",
        fake_validate,
    )
    monkeypatch.setattr(
        training_router.training_iteration_service,
        "build_iteration_preview",
        fake_preview,
    )

    response = client.get(
        f"/api/v1/training/iteration-preview?task_id={task.id}"
        "&sample_conditions=%7B%22weather%22%3A%5B%22rain%22%5D%7D",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    assert captured["sample_conditions"] == {"weather": ["rain"]}
    assert response.json()["data"]["sample_conditions"] == {"weather": ["rain"]}


def _search_job(
    db: Session,
    *,
    task_name: str,
    model_weights: str = "yolo11n.pt",
) -> tuple[Task, YoloTrainJob]:
    """Create the smallest visible task/job pair for training-list searches."""
    task = Task(
        name=task_name,
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
    )
    db.add(task)
    db.flush()
    job = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        model_weights=model_weights,
        epochs=1,
        imgsz=640,
        category="search-category",
        job_type="yolo_detection",
        status="PENDING",
        execution_mode="local",
    )
    db.add(job)
    db.flush()
    return task, job


def test_training_job_search_q_matches_job_task_and_id(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    task_by_name, job_by_name = _search_job(
        db,
        task_name="任务搜索唯一名称-7f4c",
        model_weights="weights-for-name-case.pt",
    )
    task_by_job, job_by_job = _search_job(
        db,
        task_name="普通任务-28a1",
        model_weights="weights-search-unique-91de.pt",
    )
    task_by_id, job_by_id = _search_job(
        db,
        task_name="另一个普通任务-6b2e",
        model_weights="weights-for-id-case.pt",
    )
    db.commit()

    cases = (
        ("任务搜索唯一名称-7f4c", task_by_name, job_by_name),
        ("weights-search-unique-91de", task_by_job, job_by_job),
        (str(job_by_id.id), task_by_id, job_by_id),
    )
    for query, expected_task, expected_job in cases:
        response = client.get(
            "/api/v1/training/jobs",
            params={"q": query, "page": 0, "size": 20},
            headers=testuser_token_headers,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["meta_data"]["total"] == 1
        assert [item["id"] for item in payload["data"]] == [expected_job.id]
        assert payload["data"][0]["task_id"] == expected_task.id
        assert payload["data"][0]["task_name"] == expected_task.name


def test_training_job_search_q_reports_total_and_applies_zero_based_pagination(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    jobs = [
        _search_job(
            db,
            task_name=f"分页任务-{index}",
            model_weights=f"pagination-target-{index}.pt",
        )[1]
        for index in range(3)
    ]
    db.commit()

    response = client.get(
        "/api/v1/training/jobs",
        params={"q": "pagination-target", "page": 1, "size": 1},
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta_data"] == {"total": 3, "page": 1, "size": 1}
    # The CRUD orders by descending job id; page=1 is the middle row.
    assert payload["data"][0]["id"] == jobs[-2].id


def test_training_job_search_q_treats_percent_and_underscore_as_literals(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    percent_task, percent_job = _search_job(
        db,
        task_name="包含%字面量-任务",
        model_weights="percent-literal.pt",
    )
    _search_job(
        db,
        task_name="包含X字面量-任务",
        model_weights="percent-control.pt",
    )
    underscore_task, underscore_job = _search_job(
        db,
        task_name="包含_字面量-任务",
        model_weights="underscore-literal.pt",
    )
    _search_job(
        db,
        task_name="包含X字面量-任务2",
        model_weights="underscore-control.pt",
    )
    db.commit()

    percent_response = client.get(
        "/api/v1/training/jobs",
        params={"q": "%", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    underscore_response = client.get(
        "/api/v1/training/jobs",
        params={"q": "_", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )

    assert percent_response.status_code == 200, percent_response.text
    assert underscore_response.status_code == 200, underscore_response.text
    percent_payload = percent_response.json()
    underscore_payload = underscore_response.json()
    assert percent_payload["meta_data"]["total"] == 1
    assert [item["id"] for item in percent_payload["data"]] == [percent_job.id]
    assert percent_payload["data"][0]["task_id"] == percent_task.id
    assert underscore_payload["meta_data"]["total"] == 1
    assert [item["id"] for item in underscore_payload["data"]] == [underscore_job.id]
    assert underscore_payload["data"][0]["task_id"] == underscore_task.id
