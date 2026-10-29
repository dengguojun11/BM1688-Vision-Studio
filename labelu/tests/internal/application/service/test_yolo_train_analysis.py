from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from labelu.internal.common.config import settings


def _patch_media_root(tmp_path, monkeypatch) -> Path:
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(media))
    return media


def test_build_analysis_reads_results_logs_and_preview_assets(tmp_path, monkeypatch):
    media = _patch_media_root(tmp_path, monkeypatch)
    work = media / settings.YOLO_TRAIN_WORK_SUBDIR / "job-12"
    train = work / "train"
    train.mkdir(parents=True)
    (train / "results.csv").write_text(
        "\n".join(
            [
                "epoch,time,train/box_loss,val/box_loss,metrics/mAP50(B),metrics/mAP50-95(B),metrics/precision(B),metrics/recall(B),lr/pg0",
                "1,60,2.5,2.2,0.42,0.12,0.42,0.31,0.001",
                "2,135,1.8,1.6,0.68,0.28,0.58,0.49,0.0008",
            ]
        ),
        encoding="utf-8",
    )
    (work / "process.log").write_text(
        "\n".join(
            [
                '[2026-06-08T10:00:00] model.train kwargs={"batch": 5}',
                "[2026-06-08T10:01:00] epoch=1/300 mAP50=0.4200",
                "[2026-06-08T10:02:00] current_batch=1/2",
                "[2026-06-08T10:02:00] epoch=2/300 mAP50=0.6800",
                *[f"[2026-06-08T10:03:{i:02d}] heartbeat={i}" for i in range(18)],
            ]
        ),
        encoding="utf-8",
    )
    preview = train / "val_batch0_pred.jpg"
    preview.write_bytes(b"fake-image")

    from labelu.internal.application.service import yolo_train_analysis as service

    data = service.build_train_analysis(
        job_id=12,
        job_payload={
            "id": 12,
            "task_id": 3,
            "status": "PROCESSING",
            "epochs": 300,
            "current_epoch": 2,
            "train_sample_count": 10,
            "val_sample_count": 4,
            "test_sample_count": 2,
            "total_sample_count": 16,
            "metrics_summary": json.dumps(
                {
                    "classes": ["person", "car"],
                    "per_class_val_metrics": [
                        {
                            "index": 0,
                            "label": "person",
                            "map50": 0.7,
                            "map50_95": 0.5,
                            "precision": 0.8,
                            "recall": 0.6,
                        },
                        {
                            "index": 1,
                            "label": "car",
                            "map50": 0.8,
                            "map50_95": 0.6,
                            "precision": 0.9,
                            "recall": 0.7,
                        },
                    ],
                }
            ),
            "progress_snapshot": "epoch=2 mAP50-95=0.2800",
        },
    )

    assert data["progress"]["current_epoch"] == 2
    assert data["progress"]["percent"] == 1
    assert data["status_cards"]["total_samples"]["value"] == 16
    assert data["status_cards"]["sample_split"]["train"] == 10
    assert data["status_cards"]["sample_split"]["val"] == 4
    assert data["status_cards"]["sample_split"]["test"] == 2
    assert data["status_cards"]["current_batch"]["value"] == 1
    assert data["status_cards"]["current_batch"]["total"] == 2
    assert data["status_cards"]["ips"]["value"] == 0.1
    assert data["status_cards"]["eta"]["value"] == "6h 12m"
    assert data["series"]["train_box_loss"][0]["value"] == 2.5
    assert data["series"]["map50"][1]["value"] == 0.68
    assert data["series"]["map50_95"][1]["value"] == 0.28
    assert data["class_performance"][0]["label"] == "person"
    assert data["class_performance"][0]["map50"] == 0.7
    assert data["class_performance"][1]["label"] == "car"
    assert data["logs"][0]["message"] == "heartbeat=8"
    assert data["logs"][-1]["message"] == "heartbeat=17"
    assert data["preview_images"][0]["asset_id"] == "train:val_batch0_pred.jpg"


def test_build_analysis_returns_all_preview_assets(tmp_path, monkeypatch):
    media = _patch_media_root(tmp_path, monkeypatch)
    train = media / settings.YOLO_TRAIN_WORK_SUBDIR / "job-13" / "train"
    train.mkdir(parents=True)
    for i in range(10):
        (train / f"val_batch{i}_pred.jpg").write_bytes(b"fake-image")

    from labelu.internal.application.service import yolo_train_analysis as service

    data = service.build_train_analysis(
        job_id=13,
        job_payload={
            "id": 13,
            "task_id": 3,
            "status": "COMPLETED",
            "epochs": 10,
        },
    )

    assert len(data["preview_images"]) == 10


def test_build_analysis_includes_ultralytics_box_curve_assets(tmp_path, monkeypatch):
    media = _patch_media_root(tmp_path, monkeypatch)
    train = media / settings.YOLO_TRAIN_WORK_SUBDIR / "job-14" / "train"
    train.mkdir(parents=True)
    for name in ("results.png", "BoxPR_curve.png", "BoxF1_curve.png"):
        (train / name).write_bytes(b"fake-image")

    from labelu.internal.application.service import yolo_train_analysis as service

    data = service.build_train_analysis(
        job_id=14,
        job_payload={"id": 14, "status": "COMPLETED", "epochs": 10},
    )

    by_name = {row["filename"]: row for row in data["preview_images"]}
    assert by_name["results.png"]["kind"] == "training_summary"
    assert by_name["BoxPR_curve.png"]["kind"] == "metric_curve"
    assert by_name["BoxF1_curve.png"]["asset_id"] == "train:BoxF1_curve.png"


def test_plate_analysis_reads_evaluation_pose_results_and_assets(tmp_path, monkeypatch):
    media = _patch_media_root(tmp_path, monkeypatch)
    train = (
        media
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / "job-15"
        / "plate_components"
        / "pose_train"
        / "train"
    )
    train.mkdir(parents=True)
    (train / "results.csv").write_text(
        "epoch,train/box_loss,metrics/mAP50(B),metrics/mAP50-95(B)\n1,1.2,0.8,0.5\n",
        encoding="utf-8",
    )
    (train / "BoxPR_curve.png").write_bytes(b"fake-image")
    final_train = train.parents[1] / "pose_final_fit" / "train"
    final_train.mkdir(parents=True)
    (final_train / "results.csv").write_text(
        "epoch,metrics/mAP50(B)\n1,0.999\n",
        encoding="utf-8",
    )

    from labelu.internal.application.service import yolo_train_analysis as service

    data = service.build_train_analysis(
        job_id=15,
        job_payload={
            "id": 15,
            "job_type": "plate_recognition",
            "status": "COMPLETED",
            "epochs": 1,
        },
    )

    assert data["analysis_source"] == {
        "key": "plate_pose_train",
        "relative_path": "plate_components/pose_train/train",
    }
    assert data["series"]["map50"] == [{"epoch": 1, "value": 0.8}]
    assert data["preview_images"][0]["asset_id"] == ("plate_pose_train:BoxPR_curve.png")
    assert service.resolve_analysis_asset_path(
        15, "plate_pose_train:BoxPR_curve.png"
    ) == (train / "BoxPR_curve.png")


def test_plate_analysis_returns_each_component_model_metrics_and_assets(
    tmp_path, monkeypatch
):
    media = _patch_media_root(tmp_path, monkeypatch)
    root = media / settings.YOLO_TRAIN_WORK_SUBDIR / "job-16"
    pose = root / "plate_components" / "pose_train" / "train"
    pose.mkdir(parents=True)
    (pose / "results.csv").write_text(
        "epoch,metrics/mAP50(B)\n1,0.8\n", encoding="utf-8"
    )
    (pose / "BoxPR_curve.png").write_bytes(b"pose")
    for component in ("ocr", "color", "plate_type"):
        analysis = root / "plate_components" / "evaluation" / component / "analysis"
        analysis.mkdir(parents=True)
        (analysis / "results.csv").write_text(
            "epoch,train/loss,lr/pg0\n1,0.42,0.001\n",
            encoding="utf-8",
        )
        (analysis / "confusion_matrix.png").write_bytes(component.encode())
    for filename in (
        "plate_pose_best.pt",
        "plate_ocr_best.pt",
        "plate_color_best.pt",
        "plate_type_best.pt",
    ):
        (root / "plate_components" / filename).write_bytes(b"pt")

    from labelu.internal.application.service import yolo_train_analysis as service

    component_metrics = {
        key: {
            "label": label,
            "status": "COMPLETED",
            "train_sample_count": 8,
            "metrics": (
                {"box_map50": 0.8}
                if key == "pose"
                else {
                    "val": {"accuracy": 0.75, "correct": 3, "total": 4},
                    "test": {"accuracy": 1.0, "correct": 2, "total": 2},
                }
            ),
        }
        for key, label in (
            ("pose", "车牌定位与四角"),
            ("ocr", "车牌号码 OCR"),
            ("color", "车牌颜色"),
            ("plate_type", "车牌类型"),
        )
    }
    artifacts = {
        key: {
            "pt": str(
                root
                / "plate_components"
                / f"plate_{'type' if key == 'plate_type' else key}_best.pt"
            ),
            "evaluation_stage": {"train_sample_count": 8, "final_loss": 0.42},
        }
        for key in ("pose", "ocr", "color", "plate_type")
    }
    artifacts["pose"]["pt"] = str(root / "plate_components" / "plate_pose_best.pt")

    data = service.build_train_analysis(
        job_id=16,
        job_payload={
            "id": 16,
            "job_type": "plate_recognition",
            "status": "COMPLETED",
            "epochs": 10,
            "component_metrics": component_metrics,
            "component_artifacts": artifacts,
            "component_status": {
                key: {"status": "COMPLETED"} for key in component_metrics
            },
        },
    )

    analyses = {row["key"]: row for row in data["component_analyses"]}
    assert list(analyses) == ["pose", "ocr", "color", "plate_type"]
    assert analyses["pose"]["model_filename"] == "plate_pose_best.pt"
    assert analyses["ocr"]["series"]["train_loss"] == [{"epoch": 1, "value": 0.42}]
    assert analyses["ocr"]["metrics"]["val"]["accuracy"] == 0.75
    assert analyses["ocr"]["model_available"] is True
    assert analyses["color"]["preview_images"][0]["asset_id"] == (
        "plate_color:confusion_matrix.png"
    )
    assert service.resolve_analysis_asset_path(
        16, "plate_color:confusion_matrix.png"
    ) == (
        root
        / "plate_components"
        / "evaluation"
        / "color"
        / "analysis"
        / "confusion_matrix.png"
    )


def test_build_analysis_reads_runtime_gpu_memory_when_csv_has_no_gpu_columns(
    tmp_path, monkeypatch
):
    media = _patch_media_root(tmp_path, monkeypatch)
    train = media / settings.YOLO_TRAIN_WORK_SUBDIR / "job-18" / "train"
    train.mkdir(parents=True)
    (train / "results.csv").write_text(
        "epoch,train/box_loss\n1,1.5\n",
        encoding="utf-8",
    )

    from labelu.internal.application.service import yolo_train_analysis as service

    monkeypatch.setattr(
        service,
        "_read_runtime_gpu_memory",
        lambda: {
            "label": "显存占用",
            "value": 61.5,
            "used_mb": 14000,
            "total_mb": 22716,
        },
    )

    data = service.build_train_analysis(
        job_id=18,
        job_payload={
            "id": 18,
            "task_id": 3,
            "status": "PROCESSING",
            "epochs": 10,
            "current_epoch": 1,
        },
    )

    assert data["series"]["gpu_memory"] == []
    assert data["status_cards"]["gpu_memory"]["value"] == 61.5
    assert data["status_cards"]["gpu_memory"]["used_mb"] == 14000
    assert data["status_cards"]["gpu_memory"]["total_mb"] == 22716


def test_build_analysis_falls_back_to_metrics_summary_for_legacy_completed_job(
    tmp_path, monkeypatch
):
    _patch_media_root(tmp_path, monkeypatch)

    from labelu.internal.application.service import yolo_train_analysis as service

    data = service.build_train_analysis(
        job_id=25,
        job_payload={
            "id": 25,
            "task_id": 30,
            "status": "COMPLETED",
            "epochs": 150,
            "train_sample_count": 682,
            "val_sample_count": 210,
            "metrics_summary": json.dumps(
                {
                    "dataset": {"train": 682, "val": 210, "test": 157},
                    "final_metrics": {
                        "epoch": 35,
                        "map50_95": 0.37971,
                        "map50": 0.81515,
                        "precision": 0.84008,
                        "recall": 0.75649,
                    },
                    "test_val_metrics": {
                        "status": "ok",
                        "map50": 0.89221,
                        "map50_95": 0.46491,
                    },
                }
            ),
        },
    )

    assert data["progress"]["current_epoch"] == 35
    assert data["series"]["map50"][0] == {"epoch": 35, "value": 0.81515}
    assert data["series"]["map50_95"][0] == {"epoch": 35, "value": 0.37971}
    assert data["status_cards"]["sample_split"]["test"] == 157
    assert data["status_cards"]["total_samples"]["value"] == 1049
    assert data["status_cards"]["test_map50"]["value"] == 0.89221


def test_build_analysis_uses_results_csv_best_epoch_for_historical_job(
    tmp_path, monkeypatch
):
    media = _patch_media_root(tmp_path, monkeypatch)
    train = media / settings.YOLO_TRAIN_WORK_SUBDIR / "job-24" / "train"
    train.mkdir(parents=True)
    (train / "results.csv").write_text(
        "\n".join(
            [
                "epoch,metrics/mAP50(B),metrics/mAP50-95(B),metrics/precision(B),metrics/recall(B)",
                "48,0.73,0.48784,0.77,0.68",
                "70,0.69,0.45467,0.72,0.62",
            ]
        ),
        encoding="utf-8",
    )

    from labelu.internal.application.service import yolo_train_analysis as service

    data = service.build_train_analysis(
        job_id=24,
        job_payload={
            "id": 24,
            "task_id": 141,
            "status": "COMPLETED",
            "epochs": 70,
            "metrics_summary": json.dumps(
                {"final_metrics": {"epoch": 70, "map50_95": 0.45467}}
            ),
        },
    )

    assert data["metric_contract"]["authoritative_source"] == ("results_csv_best_epoch")
    assert data["metric_contract"]["best_epoch_metrics"]["epoch"] == 48
    assert data["metric_contract"]["last_epoch_metrics"]["epoch"] == 70
    assert data["status_cards"]["map50_95"]["value"] == 0.48784
    assert data["health_diagnosis"]["post_best_regression"] == 0.0332


def test_build_analysis_uses_best_pt_and_exposes_job_health(tmp_path, monkeypatch):
    _patch_media_root(tmp_path, monkeypatch)

    from labelu.internal.application.service import yolo_train_analysis as service

    data = service.build_train_analysis(
        job_id=24,
        job_payload={
            "id": 24,
            "task_id": 141,
            "status": "COMPLETED",
            "epochs": 70,
            "metrics_summary": json.dumps(
                {
                    "metrics_contract_version": 2,
                    "classes": [
                        "person_electricbike",
                        "head_electricbikeHelmet",
                    ],
                    "class_instance_counts": [
                        {
                            "index": 0,
                            "label": "person_electricbike",
                            "train_instances": 557,
                        },
                        {
                            "index": 1,
                            "label": "head_electricbikeHelmet",
                            "train_instances": 27,
                        },
                    ],
                    "last_epoch_metrics": {"epoch": 70, "map50_95": 0.45467},
                    "best_epoch_metrics": {"epoch": 48, "map50_95": 0.48784},
                    "best_pt_val_metrics": {
                        "status": "ok",
                        "weights": "best.pt",
                        "map50": 0.71,
                        "map50_95": 0.48,
                        "precision": 0.68,
                        "recall": 0.61,
                    },
                    "per_class_val_metrics": [
                        {
                            "index": 0,
                            "label": "person_electricbike",
                            "recall": 0.71,
                        },
                        {
                            "index": 1,
                            "label": "head_electricbikeHelmet",
                            "recall": 0.2702,
                        },
                    ],
                }
            ),
        },
    )

    assert data["status_cards"]["map50_95"]["value"] == 0.48
    assert data["metric_contract"]["authoritative_source"] == "best_pt_val_metrics"
    assert data["health_diagnosis"]["status"] == "critical"
    assert data["health_diagnosis"]["weak_class"]["label"] == (
        "head_electricbikeHelmet"
    )
    assert data["health_diagnosis"]["class_imbalance_ratio"] == 20.6
    assert {notice["code"] for notice in data["health_diagnosis"]["notices"]} == {
        "post_best_regression",
        "weak_class_recall",
        "class_imbalance",
    }


def test_runtime_estimate_uses_epoch_duration_after_resume(tmp_path, monkeypatch):
    media = _patch_media_root(tmp_path, monkeypatch)
    work = media / settings.YOLO_TRAIN_WORK_SUBDIR / "job-22"
    train = work / "train"
    train.mkdir(parents=True)
    results = train / "results.csv"
    results.write_text(
        "epoch,time,metrics/mAP50(B)\n1,60,0.5\n91,600,0.68\n92,900,0.7\n",
        encoding="utf-8",
    )
    (work / "process.log").write_text(
        "\n".join(
            [
                '[2026-06-08T09:14:40] model.train kwargs={"batch": 8}',
                "[2026-06-08T09:14:40] phase=running detail=epochs=150",
                "[2026-06-08T09:20:00] epoch=91/150 mAP50=0.6800",
                "[2026-06-08T09:25:00] epoch=92/150 mAP50=0.7000",
                '[2026-06-08T17:38:54] model.train kwargs={"resume": true}',
                "[2026-06-08T17:38:54] phase=running detail=resume from last.pt",
            ]
        ),
        encoding="utf-8",
    )
    import os
    import time

    mtime = time.mktime(datetime(2026, 6, 8, 17, 30, 0).timetuple())
    os.utime(results, (mtime, mtime))

    from labelu.internal.application.service import yolo_train_analysis as service

    data = service.build_train_analysis(
        job_id=22,
        job_payload={
            "id": 22,
            "task_id": 3,
            "status": "PROCESSING",
            "epochs": 150,
            "current_epoch": 92,
            "train_sample_count": 4909,
            "val_sample_count": 1402,
        },
    )

    assert data["status_cards"]["current_batch"]["total"] == 614
    assert data["status_cards"]["current_batch"]["value"] is None
    assert data["status_cards"]["ips"]["value"] == 16.4
    assert data["status_cards"]["eta"]["value"] == "4h 50m"


def test_resolve_analysis_asset_rejects_path_traversal(tmp_path, monkeypatch):
    media = _patch_media_root(tmp_path, monkeypatch)
    work = media / settings.YOLO_TRAIN_WORK_SUBDIR / "job-5" / "train"
    work.mkdir(parents=True)
    (work / "preview.png").write_bytes(b"ok")
    (tmp_path / "secret.png").write_bytes(b"no")

    from labelu.internal.application.service import yolo_train_analysis as service

    safe = service.resolve_analysis_asset_path(5, "train:preview.png")
    assert safe == work / "preview.png"
    assert service.resolve_analysis_asset_path(5, "train:../secret.png") is None
    assert service.resolve_analysis_asset_path(5, "../secret.png") is None
    assert service.resolve_analysis_asset_path(5, "unknown:preview.png") is None
