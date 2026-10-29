"""训练作业彻底删除 API。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, TaskStatus
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_board_validation import YoloBoardValidation
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob
from labelu.internal.domain.models.yolo_eval_report import YoloEvalReport
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


@pytest.fixture
def yolo_train_enabled(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def finished_task(db):
    from labelu.internal.domain.models.task import Task

    with db.begin():
        t = Task(
            name="pytest-yolo-delete",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(t)
        db.flush()
        task_id = t.id
    yield task_id


@pytest.fixture
def completed_job(db, finished_task, media_root):
    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / "job-99"
    weights = work / "train" / "weights" / "best.pt"
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(b"fake-pt")
    (work / "dataset").mkdir(parents=True, exist_ok=True)

    with db.begin():
        job = YoloTrainJob(
            id=99,
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=str(weights),
        )
        db.add(job)
        db.flush()
        exp_dir = media_root / settings.YOLO_BM1688_EXPORT_SUBDIR / "7"
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "model.onnx").write_bytes(b"onnx")
        exp = YoloBm1688Export(
            id=7,
            user_id=1,
            task_id=finished_task,
            yolo_train_job_id=99,
            status="COMPLETED",
            onnx_rel_path=f"{settings.YOLO_BM1688_EXPORT_SUBDIR}/7/model.onnx",
        )
        db.add(exp)
        db.add(
            YoloEvalReport(
                yolo_train_job_id=99,
                provider_key="volc",
                report_kind="full_analysis",
                body="ok",
            )
        )
        db.add(
            StrategyTaskIntegration(
                external_task_id="strategy-task-delete-cache",
                schema_version="v1",
                task_name="delete cache",
                task_type="model_capability",
                dataset_s3_uri="",
                dataset_image_prefix="",
                metadata_key="",
                bucket="",
                prefix="",
                labeling_required=False,
                labeling_labels_json="[]",
                target_json="{}",
                data_window_json="{}",
                experiment_json="{}",
                platform_result_json='{"model_training_jobs":[{"job_id":99,"status":"COMPLETED"}]}',
                derived_train_job_id=99,
                created_by=1,
                updated_by=1,
            )
        )
    yield finished_task, 99


def test_delete_train_job_purges_db_and_disk(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    completed_job,
    db,
):
    task_id, job_id = completed_job
    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}"
    exp_dir = media_root / settings.YOLO_BM1688_EXPORT_SUBDIR / "7"
    assert work.is_dir()
    assert exp_dir.is_dir()

    r = client.delete(
        f"/api/v1/tasks/{task_id}/yolo-train-jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] is True

    r2 = client.get(
        f"/api/v1/tasks/{task_id}/yolo-train-jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert r2.status_code == 404
    assert not work.exists()
    assert not exp_dir.exists()

    db.expire_all()
    integration = (
        db.query(StrategyTaskIntegration)
        .filter(StrategyTaskIntegration.external_task_id == "strategy-task-delete-cache")
        .one()
    )
    assert integration.derived_train_job_id is None
    assert integration.platform_result_json is None


def test_delete_train_job_rejects_when_actually_running(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    db,
    monkeypatch,
):
    from labelu.internal.application.service import yolo_train as yolo_train_svc

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.PROCESSING.value,
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        job_id = job.id

    monkeypatch.setattr(yolo_train_svc, "_ACTIVE_TRAIN_JOBS", {int(job_id)})

    r = client.delete(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert r.status_code == 400


def test_delete_cancelled_job_succeeds_while_active_set(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    media_root,
    db,
    monkeypatch,
):
    """用户已暂停（CANCELLED）时，即使训练线程尚未从 _ACTIVE_TRAIN_JOBS 摘除也应允许删除。"""
    from labelu.internal.application.service import yolo_train as yolo_train_svc

    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / "job-cancel-del"
    work.mkdir(parents=True, exist_ok=True)

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.CANCELLED.value,
            model_weights="yolo11n.pt",
            error_message="用户暂停训练",
        )
        db.add(job)
        db.flush()
        job_id = job.id

    work.rename(work.parent / f"job-{job_id}")
    monkeypatch.setattr(yolo_train_svc, "_ACTIVE_TRAIN_JOBS", {int(job_id)})

    r = client.delete(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] is True
    assert int(job_id) not in yolo_train_svc._ACTIVE_TRAIN_JOBS


def test_delete_train_job_with_board_validation_succeeds(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    finished_task,
    db,
):
    """存在 yolo_board_validation 外键时不应再报「数据库执行出错」。"""
    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / "job-bv"
    weights = work / "train" / "weights" / "best.pt"
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(b"fake-pt")

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=str(weights),
        )
        db.add(job)
        db.flush()
        job_id = job.id
        work.rename(work.parent / f"job-{job_id}")
        exp = YoloBm1688Export(
            user_id=1,
            task_id=finished_task,
            yolo_train_job_id=job_id,
            status="COMPLETED",
            onnx_rel_path=f"{settings.YOLO_BM1688_EXPORT_SUBDIR}/22/model.onnx",
        )
        db.add(exp)
        db.flush()
        exp_id = exp.id
        db.add(
            YoloBoardValidation(
                export_id=exp_id,
                task_id=finished_task,
                yolo_train_job_id=job_id,
                status="COMPLETED",
                report_json='{"ok": true}',
            )
        )

    r = client.delete(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["deleted"] is True
    assert not (media_root / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}").exists()


def test_delete_train_job_with_edge_validation_succeeds(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    finished_task,
    db,
):
    """删除训练作业前应清理引用 BM1688 导出的边端验证记录。"""
    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / "job-edge-del"
    work.mkdir(parents=True, exist_ok=True)

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        job_id = job.id
        work.rename(work.parent / f"job-{job_id}")
        exp = YoloBm1688Export(
            user_id=1,
            task_id=finished_task,
            yolo_train_job_id=job_id,
            status="COMPLETED",
            onnx_rel_path=f"{settings.YOLO_BM1688_EXPORT_SUBDIR}/23/model.onnx",
        )
        db.add(exp)
        db.flush()
        edge_job = YoloEdgeValidationJob(
            user_id=1,
            task_id=finished_task,
            export_id=exp.id,
            status="COMPLETED",
            mode="run_only",
            sample_ids_json="[]",
            thresholds_json="{}",
            image_count=0,
            labeled_image_count=0,
            unlabeled_image_count=0,
        )
        db.add(edge_job)

    response = client.delete(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["deleted"] is True


def test_delete_stale_processing_job_succeeds(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    media_root,
    db,
):
    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / "job-stale"
    work.mkdir(parents=True, exist_ok=True)

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.PROCESSING.value,
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        job_id = job.id

    work.rename(work.parent / f"job-{job_id}")

    r = client.delete(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] is True
