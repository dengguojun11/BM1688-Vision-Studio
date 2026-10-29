from __future__ import annotations

from dataclasses import dataclass

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_auto_label_job,
    crud_locateanything_preannotate_job,
    crud_sample_bounds_check_job,
    crud_tagtool_preannotate_job,
    crud_yolo_preannotate_job,
)
from labelu.internal.common.error_code import ErrorCode, LabelUException


@dataclass(frozen=True)
class ActiveTaskBackgroundJob:
    kind: str
    label: str
    job_id: int


TASK_BACKGROUND_JOB_LABELS = {
    "auto_label": "AI批量标注",
    "yolo_preannotate": "YOLO批量标注",
    "tagtool_preannotate": "全局标签批量",
    "locateanything_preannotate": "LA批量标注",
    "bounds_check": "全局标签批量完整性检查",
}


def get_active_task_background_job(
    db: Session,
    task_id: int,
) -> ActiveTaskBackgroundJob | None:
    checks = [
        ("auto_label", crud_auto_label_job.get_active_by_task_id),
        ("yolo_preannotate", crud_yolo_preannotate_job.get_active_by_task_id),
        ("tagtool_preannotate", crud_tagtool_preannotate_job.get_active_by_task_id),
        ("locateanything_preannotate", crud_locateanything_preannotate_job.get_active_by_task_id),
        ("bounds_check", crud_sample_bounds_check_job.get_active_by_task_id),
    ]
    for kind, getter in checks:
        job = getter(db=db, task_id=task_id)
        if job:
            return ActiveTaskBackgroundJob(
                kind=kind,
                label=TASK_BACKGROUND_JOB_LABELS[kind],
                job_id=int(job.id),
            )
    return None


def assert_no_active_task_background_job(db: Session, task_id: int) -> None:
    active_job = get_active_task_background_job(db, task_id)
    if active_job is None:
        return
    raise LabelUException(
        code=ErrorCode.CODE_30002_VALIDATION_ERROR,
        status_code=status.HTTP_400_BAD_REQUEST,
        message=f"当前任务已有{active_job.label}正在运行，请等待完成后再启动新的批量任务",
    )
