import json
from types import SimpleNamespace

from labelu.internal.application.service import yolo_train
from labelu.internal.application.service.yolo_training import plate_summary


def _metrics_summary():
    return {
        "training_profile": "plate_recognition_v1",
        "components": {
            "pose": {
                "status": "COMPLETED",
                "evaluation_metrics": {
                    "metrics/precision(B)": 1.0,
                    "metrics/recall(B)": 0.99808,
                    "metrics/mAP50(B)": 0.995,
                    "metrics/mAP50-95(B)": 0.86057,
                    "metrics/mAP50(P)": 0.995,
                    "metrics/mAP50-95(P)": 0.995,
                },
            },
            "ocr": {
                "status": "COMPLETED",
                "evaluation_metrics": {
                    "train": {"accuracy": 0.80, "correct": 446, "total": 557},
                    "val": {"accuracy": 0.39189, "correct": 29, "total": 74},
                    "test": {"accuracy": 0.43564, "correct": 44, "total": 101},
                },
            },
            "color": {
                "status": "COMPLETED",
                "evaluation_metrics": {
                    "train": {"accuracy": 0.99, "total": 557},
                    "val": {"accuracy": 0.95946, "total": 74},
                    "test": {"accuracy": 0.99010, "total": 101},
                },
            },
            "plate_type": {
                "status": "COMPLETED",
                "evaluation_metrics": {
                    "train": {"accuracy": 0.99, "total": 557},
                    "val": {"accuracy": 0.95946, "total": 74},
                    "test": {"accuracy": 1.0, "total": 101},
                },
            },
        },
        "quality_warnings": ["车牌颜色缺少训练样本：black, green"],
    }


def test_plate_sample_counts_use_all_manifest_splits():
    manifest = {
        "composition": {
            "splits": {
                "train": {"total_images": 557},
                "val": {"total_images": 74},
                "test": {"total_images": 101},
            }
        }
    }

    assert plate_summary.sample_counts(json.dumps(manifest)) == {
        "train": 557,
        "val": 74,
        "test": 101,
        "total": 732,
    }


def test_plate_component_progress_reports_four_completed_components():
    statuses = {
        key: {"status": "COMPLETED"} for key in ("pose", "ocr", "color", "plate_type")
    }

    progress = plate_summary.component_progress(
        json.dumps(statuses), json.dumps(_metrics_summary())
    )

    assert progress["completed"] == 4
    assert progress["total"] == 4
    assert progress["percent"] == 100
    assert progress["summary"] == "4/4 组件完成"


def test_plate_detection_summary_reports_only_pose_component():
    job = SimpleNamespace(
        id=261,
        epochs=50,
        job_type="plate_detection",
        component_status_json=json.dumps({"pose": {"status": "PROCESSING"}}),
        metrics_summary=json.dumps(
            {
                "training_profile": "plate_recognition_v1",
                "training_target": "plate_detection",
                "components": {"pose": {"status": "PROCESSING"}},
            }
        ),
    )

    fields = yolo_train._plate_summary_fields(job)

    assert fields["component_progress"]["total"] == 1
    assert [item["key"] for item in fields["component_progress"]["items"]] == ["pose"]
    assert list(fields["component_metrics"]) == ["pose"]
    assert fields["composite_assessment"] is None


def test_plate_component_progress_includes_live_epoch_for_each_component(
    tmp_path, monkeypatch
):
    media_root = tmp_path / "media"
    component_root = media_root / "yolo_train" / "job-256" / "plate_components"
    pose_dir = component_root / "pose_train" / "train"
    pose_dir.mkdir(parents=True)
    (pose_dir / "results.csv").write_text(
        "epoch,train/loss\n17,0.5\n18,0.4\n", encoding="utf-8"
    )
    progress_dir = component_root / "progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / "ocr.json").write_text(
        json.dumps(
            {
                "current_epoch": 9,
                "target_epochs": 50,
                "phase": "train",
                "loss": 0.8,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(yolo_train.settings, "MEDIA_ROOT", str(media_root))
    monkeypatch.setattr(yolo_train.settings, "YOLO_TRAIN_WORK_SUBDIR", "yolo_train")
    job = SimpleNamespace(
        id=256,
        epochs=50,
        job_type="plate_recognition",
        component_status_json=json.dumps(
            {
                "pose": {"status": "PROCESSING"},
                "ocr": {"status": "PENDING"},
                "color": {"status": "PENDING"},
                "plate_type": {"status": "PENDING"},
            }
        ),
        metrics_summary=json.dumps({"training_profile": "plate_recognition_v1"}),
    )

    live = yolo_train._plate_live_component_progress(job)
    assert live["pose"]["current_epoch"] == 18
    assert live["ocr"]["current_epoch"] == 9
    progress = yolo_train._plate_summary_fields(job)["component_progress"]
    items = {item["key"]: item for item in progress["items"]}
    assert items["pose"]["percent"] == 36
    assert items["ocr"]["percent"] == 18
    assert progress["percent"] == 14


def test_plate_composite_assessment_fails_when_ocr_accuracy_is_low():
    assessment = plate_summary.composite_assessment(
        json.dumps(_metrics_summary()),
        pose_map50_threshold=0.5,
        pose_map50_95_threshold=0.5,
    )

    assert assessment is not None
    assert assessment["status"] == "不达标"
    assert "accuracy=0.3919" in assessment["summary"]
    assert assessment["warnings"] == ["车牌颜色缺少训练样本：black, green"]
    metrics = plate_summary.component_metrics(json.dumps(_metrics_summary()))
    assert metrics["pose"]["metrics"]["pose_map50_95"] == 0.995
    assert metrics["ocr"]["metrics"]["test"]["total"] == 101


def test_job_244_shape_is_mapped_to_training_response_fields():
    manifest = {
        "composition": {
            "splits": {
                "train": {"total_images": 557},
                "val": {"total_images": 74},
                "test": {"total_images": 101},
            }
        }
    }
    job = SimpleNamespace(
        id=244,
        job_type="plate_recognition",
        metrics_summary=json.dumps(_metrics_summary()),
        dataset_manifest_json=json.dumps(manifest),
        component_status_json=json.dumps(
            {
                key: {"status": "COMPLETED"}
                for key in ("pose", "ocr", "color", "plate_type")
            }
        ),
    )

    assert yolo_train._sample_counts_for_job(job) == {
        "train": 557,
        "val": 74,
        "test": 101,
        "total": 732,
    }
    fields = yolo_train._plate_summary_fields(job)
    assert fields["component_progress"]["summary"] == "4/4 组件完成"
    assert fields["component_metrics"]["ocr"]["metrics"]["test"]["accuracy"] == 0.43564
    assert fields["composite_assessment"]["status"] == "不达标"
    assert yolo_train._validation_pass_for_job(job).status == "不达标"
    assert (
        yolo_train._composite_training_assessment_for_job(job).status_label == "不达标"
    )
