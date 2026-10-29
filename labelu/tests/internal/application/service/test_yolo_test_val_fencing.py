from __future__ import annotations

from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def test_test_val_summary_is_written_once_for_the_same_source_version(db):
    task = Task(
        name="test-val-fence",
        description="",
        tips="",
        config="{}",
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.FINISHED.value,
        created_by=1,
    )
    db.add(task)
    db.flush()
    job = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        status=YoloTrainStatus.COMPLETED.value,
        model_weights="yolo11n.pt",
        epochs=10,
        metrics_summary='{"base":true}',
    )
    db.add(job)
    db.flush()
    job_id = int(job.id)
    db.commit()

    first = crud_yolo_train_job.update_metrics_summary_if_current(
        db,
        job_id=job_id,
        expected_metrics_summary='{"base":true}',
        metrics_summary='{"test_val_evaluation_operation_id":"first"}',
    )
    db.commit()

    second = crud_yolo_train_job.update_metrics_summary_if_current(
        db,
        job_id=job_id,
        expected_metrics_summary='{"base":true}',
        metrics_summary='{"test_val_evaluation_operation_id":"stale"}',
    )
    db.commit()

    persisted = crud_yolo_train_job.get(db, job_id)
    assert first is True
    assert second is False
    assert persisted.metrics_summary == '{"test_val_evaluation_operation_id":"first"}'


def test_test_val_summary_fence_rejects_job_that_is_no_longer_completed(db):
    task = Task(
        name="test-val-fence-status",
        description="",
        tips="",
        config="{}",
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.FINISHED.value,
        created_by=1,
    )
    db.add(task)
    db.flush()
    job = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        status=YoloTrainStatus.PROCESSING.value,
        model_weights="yolo11n.pt",
        epochs=10,
        metrics_summary='{"base":true}',
    )
    db.add(job)
    db.flush()

    applied = crud_yolo_train_job.update_metrics_summary_if_current(
        db,
        job_id=int(job.id),
        expected_metrics_summary='{"base":true}',
        metrics_summary='{"stale":true}',
    )
    assert applied is False
    db.rollback()
