"""训练作业/模型列表返回创建者登录账号。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from labelu.internal.adapter.persistence import crud_user
from labelu.internal.application.command.yolo_quality_policy import (
    CreateOrUpdateYoloQualityPolicyCommand,
)
from labelu.internal.application.service import yolo_quality_policy as yolo_quality_policy_service
from labelu.internal.domain.models.task import MediaType, TaskStatus
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


@pytest.fixture
def yolo_train_enabled(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def test_user_id(db):
    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None
    user_id = int(user.id)
    db.rollback()
    db.expunge_all()
    return user_id


@pytest.fixture
def finished_task(db, test_user_id):
    from labelu.internal.domain.models.task import Task

    with db.begin():
        t = Task(
            name="pytest-creator-col",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=test_user_id,
        )
        db.add(t)
        db.flush()
        task_id = t.id
    yield task_id


def test_train_job_list_includes_creator_email(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    test_user_id,
    db,
):
    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path="/tmp/best.pt",
        )
        db.add(job)
        db.flush()
        job_id = job.id

    r = client.get("/api/v1/yolo-train-jobs?page=0&size=100", headers=testuser_token_headers)
    assert r.status_code == 200
    rows = r.json()["data"]
    row = next(x for x in rows if x["id"] == job_id)
    assert row["creator_email"] == "test@example.com"


def test_trained_models_list_includes_creator_email(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    media_root,
    test_user_id,
    db,
):
    work = media_root / "yolo_train" / "job-creator"
    weights = work / "train" / "weights" / "best.pt"
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(b"pt")

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=str(weights),
        )
        db.add(job)
        db.flush()
        job_id = job.id

    r = client.get("/api/v1/yolo-trained-models?page=0&size=100", headers=testuser_token_headers)
    assert r.status_code == 200
    row = next(x for x in r.json()["data"] if x["job_id"] == job_id)
    assert row["creator_email"] == "test@example.com"
    assert "task_rect_classes" in row
    assert "task_rect_preset_slug" in row
    summary = r.json()["meta_data"]["summary"]
    assert summary["total"] >= 1
    assert (
        summary["publishable"]
        + summary["review_required"]
        + summary["blocked"]
        == summary["total"]
    )


def test_trained_models_filter_by_dataset_category_and_return_frozen_contract(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    media_root,
    test_user_id,
    db,
):
    weights = media_root / "yolo_train" / "category-contract" / "best.pt"
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(b"pt")

    with db.begin():
        category = DatasetCategory(
            category_key="road_governance",
            name="Road governance",
            status="active",
            created_by=test_user_id,
            updated_by=test_user_id,
        )
        db.add(category)
        db.flush()
        matching = YoloTrainJob(
            task_id=finished_task,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=str(weights),
            dataset_category_id=category.id,
            class_names_json='["person", "bicycle"]',
            label_set_signature="set-signature",
            class_order_signature="order-signature",
            training_recipe_key="yolov11_detection",
            training_recipe_version=1,
            runtime_profile="yolov11_detection",
            runtime_interface_version=1,
        )
        unmatched = YoloTrainJob(
            task_id=finished_task,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=str(weights),
        )
        db.add_all([matching, unmatched])
        db.flush()
        db.add(
            YoloBm1688Export(
                user_id=test_user_id,
                task_id=finished_task,
                yolo_train_job_id=matching.id,
                status="PROCESSING",
                imgsz=640,
            )
        )
        db.flush()
        category_id = int(category.id)
        matching_id = int(matching.id)

    response = client.get(
        f"/api/v1/yolo-trained-models?page=0&size=100&dataset_category_id={category_id}",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert [row["job_id"] for row in rows] == [matching_id]
    assert rows[0]["dataset_category_key"] == "road_governance"
    assert rows[0]["dataset_category_name"] == "Road governance"
    assert rows[0]["class_names"] == ["person", "bicycle"]
    assert rows[0]["label_set_signature"] == "set-signature"
    assert rows[0]["class_order_signature"] == "order-signature"
    assert rows[0]["bm1688_compile_status"] == "compiling"
    assert rows[0]["bm1688_compile_status_label"] == "编译中"
    summary = response.json()["meta_data"]["summary"]
    assert summary["total"] == 1
    assert (
        summary["training_passed"]
        + summary["training_review_required"]
        + summary["training_failed"]
        == 1
    )

    search_response = client.get(
        "/api/v1/yolo-trained-models",
        params={"q": "Road governance", "page": 0, "size": 100},
        headers=testuser_token_headers,
    )
    assert search_response.status_code == 200, search_response.text
    assert [row["job_id"] for row in search_response.json()["data"]] == [matching_id]
    assert search_response.json()["meta_data"]["total"] == 1

    training_status = next(
        key
        for key, summary_key in (
            ("passed", "training_passed"),
            ("review_required", "training_review_required"),
            ("failed", "training_failed"),
        )
        if summary[summary_key] == 1
    )
    quality_response = client.get(
        "/api/v1/yolo-trained-models"
        f"?page=0&size=100&dataset_category_id={category_id}"
        f"&training_status={training_status}&sort_by=training_priority",
        headers=testuser_token_headers,
    )
    assert quality_response.status_code == 200, quality_response.text
    assert [row["job_id"] for row in quality_response.json()["data"]] == [matching_id]


def test_train_job_list_quality_assessment_uses_task_rect_preset_slug(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    test_user_id,
    db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))
    yolo_quality_policy_service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="strict-scene",
            scene_name="strict-scene",
            rect_preset_slug="strict_scene",
            status="active",
            source="manual",
            training_quality={"map50_95_pass": 0.70, "map50_warn": 0.65},
        )
    )

    with db.begin():
        task = Task(
            name="pytest-quality-scene",
            description="",
            tips="",
            config=json.dumps({"defaultRectPresetId": "strict_scene"}, ensure_ascii=False),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=test_user_id,
        )
        db.add(task)
        db.flush()

        job = YoloTrainJob(
            task_id=task.id,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path="/tmp/best.pt",
            metrics_summary=json.dumps(
                {
                    "final_metrics": {
                        "map50_95": 0.66,
                        "map50": 0.76,
                        "precision": 0.8,
                        "recall": 0.7,
                    },
                    "dataset": {"train": 100, "val": 20, "test": 0},
                },
                ensure_ascii=False,
            ),
        )
        db.add(job)
        db.flush()
        job_id = job.id

    r = client.get("/api/v1/yolo-train-jobs?page=0&size=20", headers=testuser_token_headers)
    assert r.status_code == 200
    row = next(x for x in r.json()["data"] if x["id"] == job_id)
    assert row["quality_assessment"]["training_quality"]["status"] == "条件达标"
    assert any("0.70" in item for item in row["quality_assessment"]["training_quality"]["reasons"])
