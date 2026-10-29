from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from labelu.internal.application.command.yolo_quality_policy import CreateOrUpdateYoloQualityPolicyCommand
from labelu.internal.application.response.yolo_train import (
    YoloValidationPassAssessment,
    YoloTrainJobResponse,
    YoloTrainingValMetrics,
)
from labelu.internal.application.service import yolo_quality_policy as yolo_quality_policy_service
from labelu.internal.application.service import yolo_train as yolo_train_service
from labelu.internal.application.service.yolo_training import summary


def test_training_val_metrics_recovers_auto_training_evaluation_recall():
    job = SimpleNamespace(
        weights_path=None,
        metrics_summary=json.dumps(
            {
                "auto_training_evaluation": {
                    "overall_metrics": {"overall_recall": 0.9876},
                    "decision_preview": {"overall_recall_threshold": 0.95},
                }
            }
        ),
    )

    metrics = yolo_train_service._training_val_metrics_for_job(job)

    assert metrics is not None
    assert metrics.overall_recall == 0.9876
    assert metrics.overall_recall_threshold == 0.95
    assert metrics.source == "auto_training_evaluation"
    assert metrics.summary_line == "overall_recall=0.9876"


def test_read_train_phase_returns_detail(tmp_path: Path):
    work = tmp_path / "job-1"
    work.mkdir(parents=True)
    (work / "phase.json").write_text(
        json.dumps(
            {
                "phase": "running",
                "detail": "resume from last.pt",
                "runtime_pid": 4321,
            }
        ),
        encoding="utf-8",
    )

    phase, label, runtime_pid, detail = summary.read_train_phase(
        lambda _job_id: work,
        {"running": "训练中"},
        1,
    )

    assert phase == "running"
    assert label is not None and "resume from last.pt" in label
    assert runtime_pid == 4321
    assert detail == "resume from last.pt"


def test_test_metrics_recover_image_count_and_drop_invalid_zero_instances(monkeypatch):
    job = SimpleNamespace(
        metrics_summary=json.dumps(
            {
                "test_val_metrics": {
                    "status": "ok",
                    "split": "test",
                    "map50_95": 0.5639,
                    "map50": 0.6941,
                    "precision": 0.7055,
                    "recall": 0.6765,
                    "images": 0,
                    "instances": 0,
                }
            }
        )
    )
    monkeypatch.setattr(yolo_train_service, "_test_sample_count_for_job", lambda _job: 64)

    metrics = yolo_train_service._test_val_metrics_for_job(job)

    assert metrics is not None
    assert metrics.images == 64
    assert metrics.instances is None


def test_job_to_response_exposes_structured_train_steps():
    job = SimpleNamespace(
        id=12,
        task_id=8,
        status="PROCESSING",
        category="det",
        model_weights="yolo11n.pt",
        epochs=120,
        imgsz=640,
        weights_path="/tmp/job-12/train/weights/best.pt",
        metrics_summary=None,
        error_message=None,
        ai_eval_full_status=None,
        ai_eval_next_status=None,
        created_at=None,
        updated_at=None,
        execution_mode="local",
    )

    resp = summary.job_to_response(
        lambda _job_id: ("running", "训练中", 9876, "epoch=30/120 train_started"),
        lambda _job_id: (30, "30,0.12,0.78"),
        lambda _job: {
            "test_sample_count": 10,
            "training_val_metrics": None,
            "test_val_metrics": None,
            "val_test_comparison": None,
        },
        lambda _job: None,
        lambda _job: {"train": 100, "val": 20, "test": 10, "total": 130},
        lambda _job: [8],
        lambda _job: {},
        lambda _job: {},
        lambda _job: {
            "runtime_status": "RUNNING",
            "runtime_pid": 9876,
            "is_restartable": False,
            "restart_reason": None,
        },
        lambda _ids: "#8",
        lambda _job: None,
        lambda _job: None,
        lambda _job: {},
        lambda _job: {
            "execution_mode": "local",
            "train_node_id": None,
            "train_node_name": None,
            "remote_status": None,
            "remote_phase": None,
            "remote_upload_pct": None,
        },
        lambda _job: {
            "train_gpu_device": "0",
            "train_gpu_mode": "single",
            "train_gpu_label": "GPU0",
        },
        YoloTrainJobResponse,
        "PROCESSING",
        "PENDING",
        "FAILED",
        "CANCELLED",
        job,
    )

    assert resp.train_phase_detail == "epoch=30/120 train_started"
    assert resp.current_step_key == "running"
    assert len(resp.train_steps) == 6
    running_step = next(item for item in resp.train_steps if item.key == "running")
    assert running_step.status == "running"
    assert running_step.summary == "epoch 30 / 120"
    assert running_step.detail == "epoch=30/120 train_started"
    assert running_step.root_path == "{MEDIA_ROOT}/yolo_train/job-12/train/"
    assert len(running_step.substeps) == 4
    assert running_step.substeps[0].status == "done"
    assert running_step.substeps[-1].status == "running"
    assert running_step.substeps[-1].summary == "训练中"
    assert "训练过程中会不断刷新 results.csv" in running_step.substeps[-1].detail
    assert "当前状态：epoch=30/120 train_started" in running_step.substeps[-1].detail
    assert running_step.artifacts == []
    assert any(item.path == "train/results.csv" for item in running_step.substeps[-1].artifacts)

    completed_step = next(item for item in resp.train_steps if item.key == "completed")
    assert completed_step.status == "waiting"
    assert all(item.status == "waiting" for item in completed_step.substeps)


def test_read_dataset_validation_report_builds_dataset_ready_checks(tmp_path: Path):
    work = tmp_path / "job-206" / "reports"
    work.mkdir(parents=True)
    (work / "split_summary.csv").write_text(
        "split,images,labels_found,missing_labels,boxes\n"
        "train,841,841,0,3265\n"
        "val,240,240,0,924\n"
        "test,120,120,0,475\n",
        encoding="utf-8-sig",
    )
    (work / "class_distribution.csv").write_text(
        "class_id,name,train,val,test\n"
        "0,vehicle_motorbike,340,101,54\n"
        "1,nonvehicle_electricbike,840,216,103\n",
        encoding="utf-8-sig",
    )

    report = summary.read_dataset_validation_report(
        lambda _job_id: tmp_path / "job-206",
        206,
    )

    assert report["exists"] is True
    assert report["ok"] is True
    assert report["error_count"] == 0
    assert report["class_count"] == 2

    checks = summary.dataset_ready_checks_from_report(report)
    assert len(checks) == 5
    assert all(item["status"] == "done" for item in checks)
    assert "已读取 data.yaml，并解析出 2 个类别" in checks[0]["detail"]
    assert "train: 图片 841，标签 841，缺失 0，框 3265" in checks[1]["detail"]
    assert "未发现缺失标签、类别越界、列数错误、非数值或归一化越界框" in checks[2]["detail"]


def test_job_to_response_can_carry_quality_assessment():
    job = SimpleNamespace(
        id=99,
        task_id=8,
        status="COMPLETED",
        category="det",
        model_weights="yolo11n.pt",
        epochs=100,
        imgsz=640,
        weights_path="/tmp/job-99/train/weights/best.pt",
        metrics_summary=None,
        error_message=None,
        ai_eval_full_status=None,
        ai_eval_next_status=None,
        created_at=None,
        updated_at=None,
        execution_mode="local",
    )

    resp = summary.job_to_response(
        lambda _job_id: (None, None, None, None),
        lambda _job_id: (None, None),
        lambda _job: {
            "test_sample_count": 10,
            "training_val_metrics": None,
            "test_val_metrics": None,
            "val_test_comparison": None,
        },
        lambda _job: None,
        lambda _job: {"train": 100, "val": 20, "test": 10, "total": 130},
        lambda _job: [8],
        lambda _job: {},
        lambda _job: {},
        lambda _job: {
            "runtime_status": "COMPLETED",
            "runtime_pid": None,
            "is_restartable": False,
            "restart_reason": None,
        },
        lambda _ids: "#8",
        lambda _job: None,
        lambda _job: None,
        lambda _job: {},
        lambda _job: {
            "execution_mode": "local",
            "train_node_id": None,
            "train_node_name": None,
            "remote_status": None,
            "remote_phase": None,
            "remote_upload_pct": None,
        },
        lambda _job: {
            "train_gpu_device": "0",
            "train_gpu_mode": "single",
            "train_gpu_label": "GPU0",
        },
        YoloTrainJobResponse,
        "PROCESSING",
        "PENDING",
        "FAILED",
        "CANCELLED",
        job,
    )

    resp.quality_assessment = {"training_quality": {"status": "达标"}}
    assert resp.quality_assessment["training_quality"]["status"] == "达标"


def test_job_to_response_uses_task_rect_preset_slug_for_quality_policy(tmp_path, monkeypatch):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))
    yolo_quality_policy_service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="strict-scene",
            scene_name="严格场景",
            rect_preset_slug="strict_scene",
            status="active",
            source="manual",
            training_quality={"map50_95_pass": 0.70, "map50_warn": 0.65},
        )
    )

    monkeypatch.setattr(
        yolo_train_service,
        "yolo_summary",
        SimpleNamespace(
            job_to_response=lambda *args, **kwargs: YoloTrainJobResponse(
                id=1,
                task_id=2,
                status="COMPLETED",
                category="det",
                training_val_metrics=YoloTrainingValMetrics(
                    map50_95=0.66,
                    map50=0.76,
                    summary_line="val ok",
                ),
                test_val_metrics=None,
                val_test_comparison=None,
                total_sample_count=120,
            )
        ),
    )
    monkeypatch.setattr(yolo_train_service, "_latest_edge_validation_for_job", lambda job: None)

    job = SimpleNamespace(id=1, task_id=2)
    task = SimpleNamespace(config=json.dumps({"defaultRectPresetId": "strict_scene"}, ensure_ascii=False))

    resp = yolo_train_service._job_to_response(job, task)

    assert resp.quality_assessment is not None
    assert resp.quality_assessment.training_quality.status == "条件达标"
    assert any("0.70" in item for item in resp.quality_assessment.training_quality.reasons)


def test_job_to_response_includes_validation_pass():
    job = SimpleNamespace(
        id=77,
        task_id=3,
        status="COMPLETED",
        category="det",
        model_weights="yolo11n.pt",
        epochs=80,
        imgsz=640,
        weights_path="/tmp/job-77/train/weights/best.pt",
        metrics_summary=None,
        error_message=None,
        ai_eval_full_status=None,
        ai_eval_next_status=None,
        created_at=None,
        updated_at=None,
        execution_mode="local",
    )

    expected = YoloValidationPassAssessment(status="达标", detail="mAP50-95=0.7000，达标线≥0.5000")
    resp = summary.job_to_response(
        lambda _job_id: (None, None, None, None),
        lambda _job_id: (None, None),
        lambda _job: {
            "test_sample_count": 12,
            "training_val_metrics": YoloTrainingValMetrics(
                map50_95=0.7,
                map50=0.82,
                precision=0.8,
                recall=0.78,
                summary_line="mAP50-95=0.7000 mAP50=0.8200 P=0.8000 R=0.7800",
            ),
            "test_val_metrics": None,
            "val_test_comparison": None,
        },
        lambda _job: expected,
        lambda _job: {"train": 100, "val": 20, "test": 12, "total": 132},
        lambda _job: [3],
        lambda _job: {},
        lambda _job: {},
        lambda _job: {
            "runtime_status": "COMPLETED",
            "runtime_pid": None,
            "is_restartable": False,
            "restart_reason": None,
        },
        lambda _ids: "#3",
        lambda _job: None,
        lambda _job: None,
        lambda _job: {},
        lambda _job: {
            "execution_mode": "local",
            "train_node_id": None,
            "train_node_name": None,
            "remote_status": None,
            "remote_phase": None,
            "remote_upload_pct": None,
        },
        lambda _job: {
            "train_gpu_device": "0",
            "train_gpu_mode": "single",
            "train_gpu_label": "GPU0",
        },
        YoloTrainJobResponse,
        "PROCESSING",
        "PENDING",
        "FAILED",
        "CANCELLED",
        job,
    )

    assert resp.validation_pass is not None
    assert resp.validation_pass.status == "达标"
    assert "0.5000" in (resp.validation_pass.detail or "")
