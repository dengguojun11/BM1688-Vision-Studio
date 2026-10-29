from __future__ import annotations

import json
import sys
import types
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from labelu.internal.adapter.persistence import crud_user
from labelu.internal.domain.models.task import MediaType, TaskStatus, Task
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
    with db.begin():
        task = Task(
            name="pytest-model-validate-task",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=test_user_id,
        )
        db.add(task)
        db.flush()
        task_id = int(task.id)
    return task_id


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 24), color=(240, 240, 240)).save(buf, format="PNG")
    return buf.getvalue()


def _task_config_with_rect_classes(*labels: str) -> str:
    return json.dumps(
        {
            "defaultRectPresetId": "pytest_validate_rect",
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {
                        "attributes": [
                            {"key": label, "value": label} for label in labels
                        ]
                    },
                }
            ],
        },
        ensure_ascii=False,
    )


def test_validate_single_image_returns_detection_payload(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    test_user_id,
    monkeypatch,
    db,
):
    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path="yolo_train/job-301/train/weights/best.pt",
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    def fake_validate(*, db, task_id, job_id, current_user, upload_file, conf, imgsz):
        assert task_id == finished_task
        assert job_id > 0
        assert abs(float(conf) - 0.35) < 1e-9
        assert int(imgsz) == 768
        assert upload_file.filename == "sample.png"
        return {
            "task_id": task_id,
            "job_id": job_id,
            "task_name": "pytest-model-validate-task",
            "image_width": 32,
            "image_height": 24,
            "box_count": 1,
            "max_confidence": 0.91,
            "detections": [
                {
                    "label": "plastic_bag",
                    "confidence": 0.91,
                    "x1": 2.0,
                    "y1": 3.0,
                    "x2": 20.0,
                    "y2": 16.0,
                }
            ],
            "result_image_data_url": "data:image/png;base64,ZmFrZQ==",
        }

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.validate_uploaded_image_for_job",
        fake_validate,
        raising=False,
    )

    resp = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/validate-image",
        headers=testuser_token_headers,
        files={"file": ("sample.png", _png_bytes(), "image/png")},
        data={"conf": "0.35", "imgsz": "768"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["box_count"] == 1
    assert data["max_confidence"] == 0.91
    assert data["detections"][0]["label"] == "plastic_bag"
    assert data["result_image_data_url"].startswith("data:image/png;base64,")


def test_validate_single_image_rejects_incomplete_job(
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
            status=YoloTrainStatus.PROCESSING.value,
            model_weights="yolo11n.pt",
            weights_path="",
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    resp = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/validate-image",
        headers=testuser_token_headers,
        files={"file": ("sample.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 404


def test_validate_single_image_rejects_missing_weights_file(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    test_user_id,
    media_root,
    db,
):
    missing_rel = "yolo_train/job-404/train/weights/best.pt"

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=missing_rel,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    resp = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/validate-image",
        headers=testuser_token_headers,
        files={"file": ("sample.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 404


def test_get_train_job_exposes_task_rect_classes_for_validate_page(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    test_user_id,
    db,
):
    with db.begin():
        task = Task(
            name="pytest-validate-meta-task",
            description="",
            tips="",
            config=_task_config_with_rect_classes("plastic_bag", "paper_box"),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=test_user_id,
        )
        db.add(task)
        db.flush()
        task_id = int(task.id)

        job = YoloTrainJob(
            task_id=task_id,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path="yolo_train/job-201/train/weights/best.pt",
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    resp = client.get(
        f"/api/v1/tasks/{task_id}/yolo-train-jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["task_name"] == "pytest-validate-meta-task"
    assert data["task_rect_classes"] == ["plastic_bag", "paper_box"]


def test_validate_single_image_uses_task_metadata_in_service_response(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    test_user_id,
    media_root,
    monkeypatch,
    db,
):
    with db.begin():
        task = Task(
            name="pytest-validate-runtime-task",
            description="",
            tips="",
            config=_task_config_with_rect_classes("plastic_bag", "paper_box"),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=test_user_id,
        )
        db.add(task)
        db.flush()
        task_id = int(task.id)

        rel_weights = "yolo_train/job-202/train/weights/best.pt"
        job = YoloTrainJob(
            task_id=task_id,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=rel_weights,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    weights_path = (
        media_root / "yolo_train" / "job-202" / "train" / "weights" / "best.pt"
    )
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    weights_path.write_bytes(b"fake-weights")

    class FakeBoxes:
        xyxy = [[2.0, 3.0, 20.0, 16.0]]
        cls = [0]
        conf = [0.91]

        def __len__(self):
            return 1

    class FakeResult:
        boxes = FakeBoxes()

    class FakeYOLO:
        names = {0: "plastic_bag"}

        def __init__(self, weights):
            self.weights = weights

        def predict(self, source, conf, imgsz, verbose=False):
            assert imgsz == 640
            assert abs(conf - 0.25) < 1e-9
            assert verbose is False
            return [FakeResult()]

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.ensure_ultralytics_installed",
        lambda reason: True,
    )
    monkeypatch.setitem(
        sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO)
    )

    resp = client.post(
        f"/api/v1/tasks/{task_id}/yolo-train-jobs/{job_id}/validate-image",
        headers=testuser_token_headers,
        files={"file": ("sample.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["task_name"] == "pytest-validate-runtime-task"
    assert data["box_count"] == 1
    assert data["detections"][0]["label"] == "plastic_bag"


def test_validate_plate_detection_returns_box_and_four_corners(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    test_user_id,
    media_root,
    monkeypatch,
    db,
):
    weights_path = (
        media_root
        / "yolo_train"
        / "job-plate-detection"
        / "plate_detection"
        / "plate_detection_best.pt"
    )
    weights_path.parent.mkdir(parents=True)
    weights_path.write_bytes(b"fake-weights")
    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            job_type="plate_detection",
            model_weights="yolov8n-pose.pt",
            weights_path=str(weights_path),
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    class FakeBoxes:
        xyxy = [[2.0, 3.0, 28.0, 20.0]]
        cls = [0]
        conf = [0.97]

        def __len__(self):
            return 1

    class FakeKeypoints:
        xy = [[[3.0, 4.0], [27.0, 4.0], [27.0, 19.0], [3.0, 19.0]]]
        conf = [[0.98, 0.99, 0.97, 0.98]]

    class FakeResult:
        boxes = FakeBoxes()
        keypoints = FakeKeypoints()

    class FakeYOLO:
        names = {0: "doubleEP"}

        def __init__(self, weights):
            assert Path(weights) == weights_path

        def predict(self, source, conf, imgsz, verbose=False):
            assert source.size == (32, 24)
            assert conf == 0.25
            assert imgsz == 640
            assert verbose is False
            return [FakeResult()]

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.ensure_ultralytics_installed",
        lambda reason: True,
    )
    monkeypatch.setitem(
        sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO)
    )

    resp = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/validate-image",
        headers=testuser_token_headers,
        files={"file": ("sample.png", _png_bytes(), "image/png")},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["job_type"] == "plate_detection"
    assert data["model_source"] == "plate_detection_best.pt"
    assert data["box_count"] == 1
    assert data["detections"][0]["label"] == "doubleEP"
    assert len(data["detections"][0]["corners"]) == 4
    assert data["detections"][0]["corners"][1] == {
        "x": 27.0,
        "y": 4.0,
        "confidence": 0.99,
    }


def test_validate_plate_recognition_returns_four_component_payload(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    test_user_id,
    media_root,
    monkeypatch,
    db,
):
    component_dir = media_root / "yolo_train" / "job-plate" / "plate_components"
    component_dir.mkdir(parents=True)
    paths = {
        "pose": component_dir / "plate_pose_best.pt",
        "ocr": component_dir / "plate_ocr_best.pt",
        "color": component_dir / "plate_color_best.pt",
        "plate_type": component_dir / "plate_type_best.pt",
    }
    for path in paths.values():
        path.write_bytes(b"fake-weights")
    artifacts = {
        name: {
            "pt": str(path),
            **({"class_names": ["doubleEP"]} if name == "pose" else {}),
            **(
                {
                    "preferred_length": 6,
                    "crop_ratios": [0.45, 0.5, 0.55],
                }
                if name == "ocr"
                else {}
            ),
        }
        for name, path in paths.items()
    }
    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=test_user_id,
            status=YoloTrainStatus.COMPLETED.value,
            job_type="plate_recognition",
            model_weights="yolov8n-pose.pt",
            weights_path=str(paths["pose"]),
            component_artifacts_json=json.dumps(artifacts),
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    def fake_plate_infer(image, *, artifacts, metadata, confidence, imgsz):
        assert image.size == (32, 24)
        assert set(artifacts) == {"pose", "ocr", "color", "plate_type"}
        assert metadata["pose"]["class_names"] == ["doubleEP"]
        assert confidence == 0.25
        assert imgsz == 640
        return [
            {
                "confidence": 0.97,
                "x1": 2.0,
                "y1": 3.0,
                "x2": 28.0,
                "y2": 20.0,
                "corners": [
                    {"x": 3.0, "y": 4.0, "confidence": 0.98},
                    {"x": 27.0, "y": 4.0, "confidence": 0.99},
                    {"x": 27.0, "y": 19.0, "confidence": 0.97},
                    {"x": 3.0, "y": 19.0, "confidence": 0.98},
                ],
                "plate_number": "123456",
                "plate_number_confidence": 0.83,
                "color": "white",
                "color_confidence": 0.99,
                "plate_type": "whitePlate",
                "plate_type_confidence": 0.98,
            }
        ]

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.ensure_ultralytics_installed",
        lambda reason: True,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.plate_recognition_infer.infer_plate_recognition",
        fake_plate_infer,
    )

    resp = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/validate-image",
        headers=testuser_token_headers,
        files={"file": ("sample.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["job_type"] == "plate_recognition"
    assert data["model_source"] == "4-component PT"
    assert data["box_count"] == 1
    assert len(data["detections"][0]["corners"]) == 4
    assert data["detections"][0]["plate_number"] == "123456"
    assert data["detections"][0]["color"] == "white"
    assert data["detections"][0]["plate_type"] == "whitePlate"
