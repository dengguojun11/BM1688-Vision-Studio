"""模型仓库一期契约测试。

这些测试只使用当前两类用户和数据集血缘，不再创建或传递项目参数。
模型发布是平台模型仓库动作，不等同于设备发布；BM1688 模型必须同时
具备 FP32、FP16、INT8 三份 BModel。
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.adapter.persistence import crud_model_version, crud_user
from labelu.internal.domain.models.algorithm import AlgorithmDefinition, AlgorithmGroup
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
from labelu.internal.domain.models.dataset import DatasetVersion
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.yolo_imported_model import YoloImportedModel
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.domain.models.model_version import ModelArtifact, ModelVersion
from labelu.internal.application.service import model_version as model_version_service
from labelu.internal.common.error_code import LabelUException


def _seed_algorithm(db: Session) -> int:
    group = AlgorithmGroup(
        group_key="test_model_group_v2",
        name="测试算法组",
        status="ACTIVE",
        sort_order=1,
    )
    db.add(group)
    db.flush()
    algorithm = AlgorithmDefinition(
        algorithm_key="test_model_algorithm_v2",
        name="测试目标检测算法",
        task_type="object_detection",
        algorithm_group_id=group.id,
        required_tools_json="[]",
        required_attributes_json="[]",
        training_adapter="yolo_detection",
        evaluation_adapter="yolo_detection",
        supported_chips_json='["BM1688"]',
        status="ACTIVE",
    )
    db.add(algorithm)
    db.commit()
    db.refresh(algorithm)
    return algorithm.id


def _frozen_dataset_version(client: TestClient, headers: dict[str, str]) -> int:
    dataset = client.post(
        "/api/v1/datasets",
        json={"name": "模型评估数据集", "annotation_kind": "bbox"},
        headers=headers,
    )
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["data"]["id"]
    version = client.post(
        f"/api/v1/datasets/{dataset_id}/versions",
        json={
            "manifest_rel_path": "manifests/model.json",
            "manifest_sha256": "c" * 64,
            "sample_count": 10,
        },
        headers=headers,
    )
    assert version.status_code == 201, version.text
    version_id = version.json()["data"]["id"]
    for target in ("VALIDATING", "READY_TO_REVIEW"):
        response = client.patch(
            f"/api/v1/dataset-versions/{version_id}",
            json={"status": target},
            headers=headers,
        )
        assert response.status_code == 200, response.text
    reviewed = client.post(
        f"/api/v1/dataset-versions/{version_id}/review",
        json={"decision": "APPROVE", "comment": "模型仓库数据版本已复核"},
        headers=headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    frozen = client.post(
        f"/api/v1/dataset-versions/{version_id}/freeze", headers=headers
    )
    assert frozen.status_code == 200, frozen.text
    return version_id


def _artifact_payload(
    artifact_type: str, *, precision: str | None = None, digest: str = "a"
) -> dict:
    extension = {"pt": "pt", "onnx": "onnx", "bmodel": "bmodel"}.get(
        artifact_type, "json"
    )
    return {
        "artifact_type": artifact_type,
        "relative_path": f"models/test-{artifact_type.lower()}-{precision or 'base'}.{extension}",
        "sha256": digest * 64,
        "size_bytes": 100,
        "chip_type": "BM1688" if artifact_type == "bmodel" else None,
        "precision": precision,
    }


def _create_model(
    client: TestClient,
    headers: dict[str, str],
    algorithm_id: int,
    dataset_version_id: int,
) -> int:
    created = client.post(
        "/api/v1/model-versions",
        json={
            "algorithm_id": algorithm_id,
            "dataset_version_id": dataset_version_id,
            "display_name": "BM1688 安全帽候选模型",
            "chip_type": "BM1688",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    payload = created.json()["data"]
    assert "project_id" not in payload
    return payload["id"]


def _add_artifact(
    client: TestClient,
    headers: dict[str, str],
    model_id: int,
    artifact_type: str,
    *,
    precision: str | None = None,
    digest: str = "a",
) -> None:
    response = client.post(
        f"/api/v1/model-versions/{model_id}/artifacts",
        json=_artifact_payload(artifact_type, precision=precision, digest=digest),
        headers=headers,
    )
    assert response.status_code == 201, response.text


def _pass_gate(
    client: TestClient, headers: dict[str, str], model_id: int, gate: str
) -> dict:
    response = client.post(
        f"/api/v1/model-versions/{model_id}/quality-evidence",
        json={"gate_type": gate, "status": "PASS", "metrics": {"ok": True}},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_labeler_can_create_and_publish_bm1688_model(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    algorithm_id = _seed_algorithm(db)
    dataset_version_id = _frozen_dataset_version(client, testuser_token_headers)
    model_id = _create_model(
        client, testuser_token_headers, algorithm_id, dataset_version_id
    )

    _add_artifact(client, testuser_token_headers, model_id, "pt", digest="a")
    _add_artifact(client, testuser_token_headers, model_id, "onnx", digest="b")
    for precision, digest in (("FP32", "c"), ("FP16", "d"), ("INT8", "e")):
        _add_artifact(
            client,
            testuser_token_headers,
            model_id,
            "bmodel",
            precision=precision,
            digest=digest,
        )

    _pass_gate(client, testuser_token_headers, model_id, "training")
    _pass_gate(client, testuser_token_headers, model_id, "generalization")
    _pass_gate(client, testuser_token_headers, model_id, "conversion")
    _pass_gate(client, testuser_token_headers, model_id, "edge")
    release = _pass_gate(client, testuser_token_headers, model_id, "release")
    assert release["status"] == "VALIDATED"

    published = client.post(
        f"/api/v1/model-versions/{model_id}/publish",
        headers=testuser_token_headers,
    )
    assert published.status_code == 200, published.text
    payload = published.json()["data"]
    assert payload["status"] == "PUBLISHED"
    assert {
        item["precision"]
        for item in payload["artifacts"]
        if item["artifact_type"] == "bmodel"
    } == {"FP32", "FP16", "INT8"}


def test_passed_edge_validation_derives_publish_gates_without_manual_evidence():
    """Completed training/compile/edge facts enable the real publish action."""
    artifacts = [
        SimpleNamespace(
            artifact_type="pt", status="READY", chip_type="BM1688", precision=None
        ),
        SimpleNamespace(
            artifact_type="onnx", status="READY", chip_type=None, precision=None
        ),
        *[
            SimpleNamespace(
                artifact_type="bmodel",
                status="READY",
                chip_type="BM1688",
                precision=precision,
            )
            for precision in ("FP32", "FP16", "INT8")
        ],
    ]
    model = SimpleNamespace(
        status="CANDIDATE",
        chip_type="BM1688",
        artifacts=artifacts,
        quality_evidence=[],
        metrics_json=json.dumps(
            {
                "best_pt_val_metrics": {"status": "ok", "map50_95": 0.84},
                "test_val_metrics": {"status": "ok", "map50_95": 0.88},
            }
        ),
    )

    summary = model_version_service._effective_gate_summary(
        None,
        model,
        edge_validation_status={"status": "PASS"},
    )

    assert summary == {
        "training": "PASS",
        "generalization": "PASS",
        "conversion": "PASS",
        "edge": "PASS",
        "release": "PASS",
    }


@pytest.mark.parametrize(
    ("comparison_flags", "expected_status"),
    [
        (
            {"same_batch": True, "same_thresholds": True, "same_preprocessing": True},
            "IMPROVED",
        ),
        (
            {"same_batch": True, "same_thresholds": False, "same_preprocessing": True},
            None,
        ),
        (
            {"same_batch": False, "same_thresholds": True, "same_preprocessing": True},
            None,
        ),
    ],
)
def test_edge_improvement_requires_comparable_evidence(
    comparison_flags, expected_status
):
    status = SimpleNamespace(
        status="COMPLETED",
        edge_result_status="pass",
        validation_id=1,
        export_id=2,
        completed_at=None,
        report={
            "result": {
                "pt_eval": {"metrics": {"map50": 0.50}},
                "eval_summary": {"map50": 0.55},
                "comparison_pt_bmodel": {
                    "signed_delta_map50": 0.05,
                    "drop_map50": 0.0,
                    **comparison_flags,
                },
            }
        },
        report_summary="",
    )

    projection = model_version_service._edge_validation_projection(status)

    assert projection["comparison_comparable"] is (expected_status is not None)
    assert projection["improvement_status"] == expected_status


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [
        (0.55, 0.50, "IMPROVED"),
        (0.45, 0.50, "REGRESSED"),
        (0.50, 0.50, "UNCHANGED"),
    ],
)
def test_edge_version_comparison_uses_two_passed_edge_box_results(
    current, previous, expected
):
    current_status = {
        "validation_id": 20,
        "status": "PASS",
        "comparison_comparable": True,
        "inference_metrics": {"map50": current},
    }
    previous_status = {
        "validation_id": 19,
        "status": "PASS",
        "comparison_comparable": True,
        "inference_metrics": {"map50": previous},
    }

    result = model_version_service._edge_version_comparison(
        current_status,
        previous_status,
        previous_model_version_id=7,
        previous_version_no=3,
    )

    assert result["comparable"] is True
    assert result["status"] == expected
    assert result["previous_validation_id"] == 19
    assert result["signed_delta_map50"] == pytest.approx(current - previous)


def test_edge_version_comparison_is_not_inferred_without_previous_validation():
    result = model_version_service._edge_version_comparison(
        {
            "validation_id": 20,
            "status": "PASS",
            "comparison_comparable": True,
            "inference_metrics": {"map50": 0.55},
        },
        None,
        previous_model_version_id=7,
        previous_version_no=3,
    )

    assert result["comparable"] is False
    assert result["status"] is None
    assert "尚未完成" in result["reason"]


def test_edge_version_comparison_marks_first_version_without_baseline():
    result = model_version_service._edge_version_comparison(
        None,
        None,
        previous_model_version_id=None,
        previous_version_no=None,
    )

    assert result["comparable"] is False
    assert result["status"] is None
    assert result["reason"] == "当前版本没有上一版本"


def test_edge_version_comparison_distinguishes_previous_edge_validation_gap():
    result = model_version_service._edge_version_comparison(
        {"status": "PASS", "comparison_comparable": True, "inference_metrics": {"map50": 0.55}},
        {"status": "FAIL", "comparison_comparable": False},
        previous_model_version_id=5,
        previous_version_no=1,
    )

    assert result["baseline_state"] == "PREVIOUS_EDGE_UNAVAILABLE"
    assert "上一版本" in result["reason"]


def test_publish_with_aicm_id_materializes_platform_download_package(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    import shutil

    from labelu.internal.domain.models.published_model_catalog import (
        PublishedModelCatalog,
    )

    # The persistent test database may retain catalog rows from a previous
    # focused run; reserve the fixture id explicitly for this test.
    db.query(PublishedModelCatalog).filter(
        PublishedModelCatalog.public_model_id == "9534"
    ).delete(synchronize_session=False)
    db.commit()
    shutil.rmtree(
        Path(settings.BASE_DATA_DIR) / "published_model_packages" / "9534",
        ignore_errors=True,
    )
    algorithm_id = _seed_algorithm(db)
    dataset_version_id = _frozen_dataset_version(client, testuser_token_headers)
    model_id = _create_model(
        client, testuser_token_headers, algorithm_id, dataset_version_id
    )

    artifacts = [
        ("pt", None, b"pt-model"),
        ("onnx", None, b"onnx-model"),
        ("bmodel", "FP32", b"bmodel-fp32"),
        ("bmodel", "FP16", b"bmodel-fp16"),
        ("bmodel", "INT8", b"bmodel-int8"),
    ]
    import hashlib

    for artifact_type, precision, content in artifacts:
        relative_path = (
            f"models/publish-aicm-{artifact_type.lower()}-{precision or 'base'}"
        )
        target = Path(settings.MEDIA_ROOT) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        response = client.post(
            f"/api/v1/model-versions/{model_id}/artifacts",
            json={
                "artifact_type": artifact_type,
                "relative_path": relative_path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "chip_type": "BM1688" if artifact_type == "bmodel" else None,
                "precision": precision,
            },
            headers=testuser_token_headers,
        )
        assert response.status_code == 201, response.text

    for gate in ("training", "generalization", "conversion", "edge", "release"):
        _pass_gate(client, testuser_token_headers, model_id, gate)

    published = client.post(
        f"/api/v1/model-versions/{model_id}/publish",
        json={"aicm_model_set_id": 9534, "aicm_environment_id": "test-aicm"},
        headers=testuser_token_headers,
    )
    assert published.status_code == 200, published.text
    payload = published.json()["data"]
    assert payload["status"] == "PUBLISHED"
    assert payload["published_catalog_id"]

    catalog = client.get(
        "/api/v1/published-models/9534", headers=testuser_token_headers
    )
    assert catalog.status_code == 200, catalog.text
    catalog_payload = catalog.json()["data"]
    assert catalog_payload["aicm_model_set_id"] == 9534
    assert catalog_payload["local_package_available"] is True

    download = client.get(
        "/api/v1/published-models/9534/download", headers=testuser_token_headers
    )
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith("application/zip")


def test_bm1688_conversion_rejects_incomplete_precision_bundle(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    algorithm_id = _seed_algorithm(db)
    version_id = _frozen_dataset_version(client, testuser_token_headers)
    model_id = _create_model(client, testuser_token_headers, algorithm_id, version_id)
    _add_artifact(client, testuser_token_headers, model_id, "pt", digest="f")
    _add_artifact(client, testuser_token_headers, model_id, "onnx", digest="1")
    _add_artifact(
        client, testuser_token_headers, model_id, "bmodel", precision="INT8", digest="2"
    )
    _pass_gate(client, testuser_token_headers, model_id, "training")
    _pass_gate(client, testuser_token_headers, model_id, "generalization")
    rejected = client.post(
        f"/api/v1/model-versions/{model_id}/quality-evidence",
        json={"gate_type": "conversion", "status": "PASS"},
        headers=testuser_token_headers,
    )
    assert rejected.status_code == 409, rejected.text
    assert "FP32" in rejected.text and "FP16" in rejected.text


def test_management_user_can_view_candidate_model(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    algorithm_id = _seed_algorithm(db)
    version_id = _frozen_dataset_version(client, testuser_token_headers)
    model_id = _create_model(client, testuser_token_headers, algorithm_id, version_id)
    listing = client.get("/api/v1/models", headers=testadmin_token_headers)
    assert listing.status_code == 200, listing.text
    assert any(row["id"] == model_id for row in listing.json()["data"])


@pytest.mark.parametrize(
    ("source_type", "username", "superusers", "allowed"),
    [
        # Third-party lineage remains visible to ordinary authenticated users.
        ("third_party", "outsider@example.com", "", True),
        # A configured superuser is deliberately below the consistent-pool
        # boundary, even when the model has already been published.
        ("consistent_pool", "service@example.com", "service@example.com", False),
        # Only the built-in administrator can inspect internal pool lineage.
        ("consistent_pool", "admin@123.com", "service@example.com", True),
    ],
)
def test_training_model_detail_uses_task_visibility_contract(
    monkeypatch,
    source_type: str,
    username: str,
    superusers: str,
    allowed: bool,
):
    """Deep-link model details must match catalog/task visibility rules."""
    task = SimpleNamespace(
        id=71,
        created_by=99,
        source_type=source_type,
        config=json.dumps(
            {"consistent_pool": {"kind": "train_filtered_consistent"}}
            if source_type == "consistent_pool"
            else {}
        ),
        collaborators=[],
    )
    job = SimpleNamespace(task_id=task.id)
    model = SimpleNamespace(
        id=81,
        training_job_id=91,
        created_by=99,
        status="PUBLISHED",
    )
    user = SimpleNamespace(id=2, username=username)
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", superusers)
    monkeypatch.setattr(
        model_version_service.crud_model_version,
        "get",
        lambda _db, _model_version_id: model,
    )
    monkeypatch.setattr(
        model_version_service.crud_yolo_train_job,
        "get",
        lambda _db, _job_id: job,
    )
    monkeypatch.setattr(
        model_version_service.crud_task,
        "get",
        lambda db, task_id, lock=False: task,
    )

    if allowed:
        assert (
            model_version_service._model_or_raise(None, model.id, user, manage=True)
            is model
        )
    else:
        with pytest.raises(LabelUException) as exc_info:
            model_version_service._model_or_raise(None, model.id, user)
        assert exc_info.value.status_code == 403


def test_builtin_admin_model_catalog_excludes_deleted_training_tasks(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    """Admin visibility includes all active task lineages, never deleted ones."""
    algorithm_id = _seed_algorithm(db)
    admin = crud_user.get_user_by_username(db, username="admin@123.com")
    assert admin is not None
    active_task = Task(
        name="活动训练任务",
        media_type="IMAGE",
        status="FINISHED",
        created_by=admin.id,
        updated_by=admin.id,
    )
    deleted_task = Task(
        name="已删除训练任务",
        media_type="IMAGE",
        status="FINISHED",
        created_by=admin.id,
        updated_by=admin.id,
        deleted_at=datetime.now(),
    )
    db.add_all([active_task, deleted_task])
    db.flush()
    active_job = YoloTrainJob(
        task_id=active_task.id,
        created_by=admin.id,
        status="COMPLETED",
        model_weights="yolo11n.pt",
    )
    deleted_job = YoloTrainJob(
        task_id=deleted_task.id,
        created_by=admin.id,
        status="COMPLETED",
        model_weights="yolo11n.pt",
    )
    db.add_all([active_job, deleted_job])
    db.flush()
    active_model = ModelVersion(
        algorithm_id=algorithm_id,
        training_job_id=active_job.id,
        model_key=f"active-task-model-{active_job.id}",
        version_no=1,
        display_name="活动任务模型",
        status="CANDIDATE",
        created_by=admin.id,
    )
    deleted_model = ModelVersion(
        algorithm_id=algorithm_id,
        training_job_id=deleted_job.id,
        model_key=f"deleted-task-model-{deleted_job.id}",
        version_no=2,
        display_name="已删除任务模型",
        status="CANDIDATE",
        created_by=admin.id,
    )
    db.add_all([active_model, deleted_model])
    db.commit()

    rows, total = crud_model_version.list_accessible(
        db,
        user_id=admin.id,
        unrestricted=True,
        page=0,
        size=50,
        source="training",
        task_access_builtin_admin=True,
    )
    assert total == 1
    assert [row.id for row in rows] == [active_model.id]


def test_external_model_registration_has_no_project_field(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    algorithm_id = _seed_algorithm(db)
    label = AnnotationLabel(
        label_key="external_model_object",
        label_value="外部模型目标",
        normalized_label_value="external_model_object",
        annotation_kind="bbox",
        color="#1677ff",
        attributes_json="[]",
        status="active",
        version=1,
        created_by=1,
        updated_by=1,
    )
    db.add(label)
    db.commit()
    db.refresh(label)
    dataset = client.post(
        "/api/v1/datasets",
        json={
            "name": "外部模型关联数据集",
            "annotation_kind": "bbox",
            "label_ids": [label.id],
        },
        headers=testuser_token_headers,
    )
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["data"]["id"]
    uploaded = client.post(
        "/api/v1/yolo-imported-models",
        files={"file": ("external.pt", b"external-weight", "application/octet-stream")},
        data={"display_name": "外部基线", "category": "object_detection"},
        headers=testuser_token_headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    imported_id = uploaded.json()["data"]["id"]
    registered = client.post(
        "/api/v1/models/import",
        json={
            "algorithm_id": algorithm_id,
            "dataset_id": dataset_id,
            "imported_model_id": imported_id,
            "display_name": "外部基线登记版本",
            "chip_type": "BM1688",
            "precision": "FP32",
        },
        headers=testuser_token_headers,
    )
    assert registered.status_code == 201, registered.text
    payload = registered.json()["data"]
    assert "project_id" not in payload
    assert payload["dataset_id"] == dataset_id
    assert payload["dataset_version_id"] is None
    assert payload["manifest"]["source"] == "external_import"
    assert payload["manifest"]["dataset_id"] == dataset_id
    assert payload["manifest"]["dataset_label_contract"]["annotation_kind"] == "bbox"
    assert payload["artifacts"][0]["artifact_type"] == "pt"
    artifact_path = Path(settings.MEDIA_ROOT) / payload["artifacts"][0]["relative_path"]
    assert artifact_path.is_file()
    imported_list = client.get(
        "/api/v1/yolo-imported-models", headers=testuser_token_headers
    )
    assert imported_list.status_code == 200, imported_list.text
    imported_row = next(
        row for row in imported_list.json()["data"] if row["id"] == imported_id
    )
    assert imported_row["display_name"] == "外部基线"
    assert imported_row["registered_display_name"] == "外部基线登记版本"
    assert imported_row["can_delete"] is True
    assert imported_row["registered_model_count"] == 1
    registered_search = client.get(
        "/api/v1/yolo-imported-models",
        params={"q": "外部基线登记版本"},
        headers=testuser_token_headers,
    )
    assert registered_search.status_code == 200, registered_search.text
    assert [row["id"] for row in registered_search.json()["data"]] == [imported_id]

    deleted = client.delete(
        f"/api/v1/yolo-imported-models/{imported_id}",
        headers=testuser_token_headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {
        "deleted": True,
        "file_deleted": False,
        "retained_for_registered_models": True,
        "registered_model_count": 1,
    }
    assert artifact_path.is_file()
    model_detail = client.get(
        f"/api/v1/models/{payload['id']}", headers=testuser_token_headers
    )
    assert model_detail.status_code == 200, model_detail.text
    assert model_detail.json()["data"]["artifacts"][0]["status"] == "READY"
    versions = client.get(
        f"/api/v1/datasets/{dataset_id}/versions", headers=testuser_token_headers
    )
    assert versions.status_code == 200, versions.text
    assert versions.json()["data"] == []


def test_unregistered_external_model_delete_removes_record_and_weight(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    uploaded = client.post(
        "/api/v1/yolo-imported-models",
        files={"file": ("unused.pt", b"unused-weight", "application/octet-stream")},
        data={"display_name": "待删除外部模型"},
        headers=testuser_token_headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    imported_id = uploaded.json()["data"]["id"]
    row = db.query(YoloImportedModel).filter(YoloImportedModel.id == imported_id).one()
    artifact_path = Path(settings.MEDIA_ROOT) / row.weights_rel_path
    assert artifact_path.is_file()

    deleted = client.delete(
        f"/api/v1/yolo-imported-models/{imported_id}",
        headers=testuser_token_headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {
        "deleted": True,
        "file_deleted": True,
        "retained_for_registered_models": False,
        "registered_model_count": 0,
    }
    assert (
        db.query(YoloImportedModel).filter(YoloImportedModel.id == imported_id).first()
        is None
    )
    assert not artifact_path.exists()


def test_completed_training_job_registration_is_idempotent(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    algorithm_id = _seed_algorithm(db)
    version_id = _frozen_dataset_version(client, testuser_token_headers)
    task = Task(
        name="登记模型训练任务",
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
    )
    db.add(task)
    db.flush()
    relative_path = f"yolo_train/model-register-{task.id}.pt"
    artifact_path = Path(settings.MEDIA_ROOT) / relative_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"trained-best-weights")
    job = YoloTrainJob(
        task_id=task.id,
        project_id=None,
        created_by=1,
        status="COMPLETED",
        weights_path=relative_path,
        hyperparams_json=json.dumps(
            {
                "algorithm_id": algorithm_id,
                "algorithm_key": "test_model_algorithm_v2",
                "dataset_version_id": version_id,
                "chip_type": "BM1688",
                "precision": "FP32",
            }
        ),
        dataset_manifest_json=json.dumps({"dataset_version_id": version_id}),
    )
    db.add(job)
    db.commit()

    first = client.post(
        "/api/v1/models/from-training-job",
        json={"training_job_id": job.id},
        headers=testuser_token_headers,
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/models/from-training-job",
        json={"training_job_id": job.id},
        headers=testuser_token_headers,
    )
    assert second.status_code == 201, second.text
    assert second.json()["data"]["id"] == first.json()["data"]["id"]


def test_model_artifact_rejects_path_traversal(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    algorithm_id = _seed_algorithm(db)
    version_id = _frozen_dataset_version(client, testuser_token_headers)
    model_id = _create_model(client, testuser_token_headers, algorithm_id, version_id)
    rejected = client.post(
        f"/api/v1/model-versions/{model_id}/artifacts",
        json={
            "artifact_type": "pt",
            "relative_path": "../outside.pt",
            "sha256": "e" * 64,
        },
        headers=testuser_token_headers,
    )
    assert rejected.status_code == 400, rejected.text


def test_unpublished_model_can_download_ready_pt_artifact(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    import hashlib

    algorithm_id = _seed_algorithm(db)
    version_id = _frozen_dataset_version(client, testuser_token_headers)
    model_id = _create_model(client, testuser_token_headers, algorithm_id, version_id)
    relative_path = f"models/unpublished-pt-{model_id}.pt"
    content = b"unpublished-best-pt"
    artifact_path = Path(settings.MEDIA_ROOT) / relative_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(content)

    registered = client.post(
        f"/api/v1/model-versions/{model_id}/artifacts",
        json={
            "artifact_type": "pt",
            "relative_path": relative_path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        },
        headers=testuser_token_headers,
    )
    assert registered.status_code == 201, registered.text

    download = client.get(
        f"/api/v1/model-versions/{model_id}/artifacts/pt/download",
        headers=testuser_token_headers,
    )
    assert download.status_code == 200, download.text
    assert download.content == content
    assert "attachment" in download.headers.get("content-disposition", "")
    assert "-pt.pt" in download.headers["content-disposition"]

    detail = client.get(
        f"/api/v1/model-versions/{model_id}", headers=testuser_token_headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["status"] == "CANDIDATE"


def test_model_families_group_iterations_without_mixing_tasks(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    algorithm_id = _seed_algorithm(db)
    version_id = _frozen_dataset_version(client, testuser_token_headers)
    task = Task(
        name="人体识别模型系列",
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
    )
    other_task = Task(
        name="另一个模型系列",
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
    )
    db.add_all([task, other_task])
    db.flush()

    job_ids: dict[int, list[int]] = {task.id: [], other_task.id: []}
    model_ids: list[int] = []
    model_keys: list[str] = []
    for source_task, iteration in ((task, 1), (task, 2), (other_task, 1)):
        relative_path = f"yolo_train/family-{source_task.id}-{iteration}.pt"
        artifact_path = Path(settings.MEDIA_ROOT) / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(f"family-{source_task.id}-{iteration}".encode())
        job = YoloTrainJob(
            task_id=source_task.id,
            project_id=None,
            created_by=1,
            status="COMPLETED",
            model_weights="yolo11n.pt",
            weights_path=relative_path,
            hyperparams_json=json.dumps(
                {
                    "algorithm_id": algorithm_id,
                    "algorithm_key": "test_model_algorithm_v2",
                    "dataset_version_id": version_id,
                }
            ),
            dataset_manifest_json=json.dumps({"dataset_version_id": version_id}),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_ids[source_task.id].append(job.id)
        registered = client.post(
            "/api/v1/models/from-training-job",
            json={"training_job_id": job.id},
            headers=testuser_token_headers,
        )
        assert registered.status_code == 201, registered.text
        registered_payload = registered.json()["data"]
        model_ids.append(registered_payload["id"])
        model_keys.append(registered_payload["model_key"])

    _add_artifact(
        client,
        testuser_token_headers,
        model_ids[1],
        "bmodel",
        precision="INT8",
        digest="7",
    )
    listing = client.get(
        "/api/v1/models/families",
        params={"page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["meta_data"]["total"] == 2
    families = listing.json()["data"]
    family = next(item for item in families if item["task_id"] == task.id)
    assert family["display_name"] == "人体识别模型系列"
    assert family["version_count"] == 2
    assert [row["training_job_id"] for row in family["versions"]] == list(
        reversed(job_ids[task.id])
    )
    assert all(row["task_id"] == task.id for row in family["versions"])
    assert all(row["dataset_version_no"] == 1 for row in family["versions"])
    assert all(
        row["training_model_weights"] == "yolo11n.pt" for row in family["versions"]
    )
    other = next(item for item in families if item["task_id"] == other_task.id)
    assert other["version_count"] == 1
    assert other["versions"][0]["task_id"] == other_task.id

    # The grouped catalog must search the same user-visible identifiers as
    # the expanded model rows, including the derived family key.
    search_cases = (
        (task.name, 1),
        ("测试目标检测算法", 2),
        (model_keys[0], 1),
        (f"task:{task.id}:algorithm:{algorithm_id}", 1),
        (f"algorithm:{algorithm_id}", 2),
    )
    for keyword, expected_total in search_cases:
        searched = client.get(
            "/api/v1/models/families",
            params={"q": keyword, "page": 0, "size": 20},
            headers=testuser_token_headers,
        )
        assert searched.status_code == 200, searched.text
        assert searched.json()["meta_data"]["total"] == expected_total
    model_search = client.get(
        "/api/v1/models/families",
        params={"q": model_keys[0]},
        headers=testuser_token_headers,
    )
    assert [row["id"] for row in model_search.json()["data"][0]["versions"]] == [
        model_ids[0]
    ]

    focused = client.get(
        "/api/v1/models/families",
        params={"training_job_id": job_ids[task.id][0]},
        headers=testuser_token_headers,
    )
    assert focused.status_code == 200, focused.text
    assert focused.json()["meta_data"]["total"] == 1
    assert focused.json()["data"][0]["version_count"] == 2

    linked_model = client.get(
        "/api/v1/models/families",
        params={"model_id": model_ids[0]},
        headers=testuser_token_headers,
    )
    assert linked_model.status_code == 200, linked_model.text
    assert linked_model.json()["meta_data"]["total"] == 1
    assert linked_model.json()["data"][0]["version_count"] == 2
    assert [row["id"] for row in linked_model.json()["data"][0]["versions"]] == [
        model_ids[1],
        model_ids[0],
    ]

    bmodels = client.get(
        "/api/v1/models/families",
        params={"artifact_type": "bmodel"},
        headers=testuser_token_headers,
    )
    assert bmodels.status_code == 200, bmodels.text
    assert bmodels.json()["meta_data"]["total"] == 1
    assert bmodels.json()["data"][0]["version_count"] == 1
    assert bmodels.json()["data"][0]["versions"][0]["id"] == model_ids[1]


def test_model_families_artifact_filter_requires_ready_status(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    """The training picker must hide a model whose PT artifact is not usable."""

    algorithm_id = _seed_algorithm(db)
    dataset_version_id = _frozen_dataset_version(client, testuser_token_headers)
    task = Task(
        name="不可用 PT 模型系列",
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
    )
    db.add(task)
    db.flush()

    relative_path = "yolo_train/not-ready-family.pt"
    artifact_path = Path(settings.MEDIA_ROOT) / relative_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"not-ready-family")
    job = YoloTrainJob(
        task_id=task.id,
        project_id=None,
        created_by=1,
        status="COMPLETED",
        model_weights="yolo11n.pt",
        weights_path=relative_path,
        hyperparams_json=json.dumps(
            {
                "algorithm_id": algorithm_id,
                "algorithm_key": "test_model_algorithm_v2",
                "dataset_version_id": dataset_version_id,
            }
        ),
        dataset_manifest_json=json.dumps({"dataset_version_id": dataset_version_id}),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    registered = client.post(
        "/api/v1/models/from-training-job",
        json={"training_job_id": job.id},
        headers=testuser_token_headers,
    )
    assert registered.status_code == 201, registered.text
    model_id = registered.json()["data"]["id"]

    artifact = (
        db.query(ModelArtifact).filter(ModelArtifact.model_version_id == model_id).one()
    )
    artifact.status = "FAILED"
    db.commit()

    listing = client.get(
        "/api/v1/models/families",
        params={"artifact_type": "pt"},
        headers=testuser_token_headers,
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["meta_data"]["total"] == 0
    assert listing.json()["data"] == []


def test_model_families_recipe_filter_reads_nested_legacy_manifest(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    """Recipe-filtered catalogs retain jobs whose columns were never backfilled."""

    algorithm_id = _seed_algorithm(db)
    dataset_version_id = _frozen_dataset_version(client, testuser_token_headers)
    task = Task(
        name="历史配方模型系列",
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
    )
    db.add(task)
    db.flush()

    relative_path = "yolo_train/legacy-nested-recipe.pt"
    artifact_path = Path(settings.MEDIA_ROOT) / relative_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"legacy-nested-recipe")
    job = YoloTrainJob(
        task_id=task.id,
        project_id=None,
        created_by=1,
        status="COMPLETED",
        model_weights="yolo11n.pt",
        weights_path=relative_path,
        # This is the historical shape: the denormalized recipe columns are
        # empty while the immutable nested manifest retains the lineage.
        hyperparams_json=json.dumps(
            {
                "algorithm_id": algorithm_id,
                "algorithm_key": "test_model_algorithm_v2",
                "dataset_version_id": dataset_version_id,
                "dataset_manifest": {
                    "training_recipe": {
                        "key": "yolov11_detection",
                        "version": 1,
                    }
                },
            }
        ),
        dataset_manifest_json=json.dumps({"dataset_version_id": dataset_version_id}),
        training_recipe_key=None,
        training_recipe_version=None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    registered = client.post(
        "/api/v1/models/from-training-job",
        json={"training_job_id": job.id},
        headers=testuser_token_headers,
    )
    assert registered.status_code == 201, registered.text
    model_id = registered.json()["data"]["id"]

    listing = client.get(
        "/api/v1/models/families",
        params={
            "training_recipe_key": "yolo_detection_v1",
            "training_recipe_version": 1,
        },
        headers=testuser_token_headers,
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["meta_data"]["total"] == 1
    family = listing.json()["data"][0]
    assert family["task_id"] == task.id
    assert family["version_count"] == 1
    assert family["versions"][0]["id"] == model_id


def test_model_families_contract_filter_uses_training_job_signatures(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    """Contract-filtered model selection must not access nonexistent ModelVersion columns."""

    algorithm_id = _seed_algorithm(db)
    dataset_version_id = _frozen_dataset_version(client, testuser_token_headers)
    task = Task(
        name="契约筛选模型系列",
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
    )
    db.add(task)
    db.flush()

    relative_path = "yolo_train/contract-filter.pt"
    artifact_path = Path(settings.MEDIA_ROOT) / relative_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"contract-filter")
    job = YoloTrainJob(
        task_id=task.id,
        project_id=None,
        created_by=1,
        status="COMPLETED",
        model_weights="yolo11n.pt",
        weights_path=relative_path,
        label_set_signature="labels-contract-a",
        class_order_signature="order-contract-a",
        hyperparams_json=json.dumps(
            {
                "algorithm_id": algorithm_id,
                "algorithm_key": "test_model_algorithm_v2",
                "dataset_version_id": dataset_version_id,
            }
        ),
        dataset_manifest_json=json.dumps({"dataset_version_id": dataset_version_id}),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    registered = client.post(
        "/api/v1/models/from-training-job",
        json={"training_job_id": job.id},
        headers=testuser_token_headers,
    )
    assert registered.status_code == 201, registered.text

    matching = client.get(
        "/api/v1/models/families",
        params={
            "artifact_type": "pt",
            "label_set_signature": "labels-contract-a",
            "class_order_signature": "order-contract-a",
        },
        headers=testuser_token_headers,
    )
    assert matching.status_code == 200, matching.text
    assert matching.json()["meta_data"]["total"] == 1
    assert matching.json()["data"][0]["versions"][0]["training_job_id"] == job.id

    mismatched = client.get(
        "/api/v1/models/families",
        params={
            "artifact_type": "pt",
            "label_set_signature": "labels-contract-other",
            "class_order_signature": "order-contract-a",
        },
        headers=testuser_token_headers,
    )
    assert mismatched.status_code == 200, mismatched.text
    assert mismatched.json()["meta_data"]["total"] == 0


def test_model_families_contract_filter_recovers_legacy_snapshot_signatures(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    """Legacy jobs remain selectable when signatures are outside job columns."""

    algorithm_id = _seed_algorithm(db)
    dataset_version_id = _frozen_dataset_version(client, testuser_token_headers)
    dataset_version = (
        db.query(DatasetVersion).filter(DatasetVersion.id == dataset_version_id).one()
    )
    dataset_version.label_set_signature = "labels-legacy"
    dataset_version.class_order_signature = "order-legacy"
    db.commit()

    jobs: list[YoloTrainJob] = []
    legacy_manifests = [
        {
            "dataset_version_id": dataset_version_id,
            "label_set_signature": "labels-legacy",
            "class_order_signature": "order-legacy",
        },
        # The real legacy shape kept the immutable version on ModelVersion,
        # while the job manifest itself had no dataset-version identifier.
        {"manifest_version": 1, "class_names": ["person", "vehicle"]},
    ]
    for index, manifest in enumerate(legacy_manifests, start=1):
        task = Task(
            name=f"历史契约模型系列 {index}",
            media_type="IMAGE",
            status="FINISHED",
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        relative_path = f"yolo_train/legacy-contract-{index}.pt"
        artifact_path = Path(settings.MEDIA_ROOT) / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(f"legacy-contract-{index}".encode())
        job = YoloTrainJob(
            task_id=task.id,
            project_id=None,
            created_by=1,
            status="COMPLETED",
            model_weights="yolo11n.pt",
            weights_path=relative_path,
            hyperparams_json=json.dumps(
                {
                    "algorithm_id": algorithm_id,
                    "algorithm_key": "test_model_algorithm_v2",
                    "dataset_version_id": dataset_version_id,
                }
            ),
            dataset_manifest_json=json.dumps(manifest),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        jobs.append(job)

        registered = client.post(
            "/api/v1/models/from-training-job",
            json={"training_job_id": job.id},
            headers=testuser_token_headers,
        )
        assert registered.status_code == 201, registered.text

    query = {
        "artifact_type": "pt",
        "label_set_signature": "labels-legacy",
        "class_order_signature": "order-legacy",
        "size": 1,
    }
    first_page = client.get(
        "/api/v1/models/families",
        params={**query, "page": 0},
        headers=testuser_token_headers,
    )
    second_page = client.get(
        "/api/v1/models/families",
        params={**query, "page": 1},
        headers=testuser_token_headers,
    )
    assert first_page.status_code == 200, first_page.text
    assert second_page.status_code == 200, second_page.text
    assert first_page.json()["meta_data"]["total"] == 2
    assert second_page.json()["meta_data"]["total"] == 2
    returned_job_ids = {
        page.json()["data"][0]["versions"][0]["training_job_id"]
        for page in (first_page, second_page)
    }
    assert returned_job_ids == {job.id for job in jobs}
    returned_versions = {
        page.json()["data"][0]["versions"][0]["training_job_id"]: page.json()["data"][
            0
        ]["versions"][0]
        for page in (first_page, second_page)
    }
    assert all(
        version["label_set_signature"] == "labels-legacy"
        and version["class_order_signature"] == "order-legacy"
        for version in returned_versions.values()
    )
