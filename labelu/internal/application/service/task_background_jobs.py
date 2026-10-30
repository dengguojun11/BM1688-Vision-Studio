from __future__ import annotations

import json
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
    group: str


TASK_BACKGROUND_JOB_LABELS = {
    "auto_label": "AI批量标注",
    "yolo_preannotate": "YOLO批量标注",
    "tagtool_preannotate": "全局标签批量",
    "locateanything_preannotate": "LA批量标注",
    "bounds_check": "全局标签批量完整性检查",
    "vlx_seek_preannotate": "VLX-Seek批量标注",
}

# Independent execution groups.  Global-tag work and geometry work touch
# different annotation tools, so they may run concurrently; jobs inside one
# group stay serialized.
GROUP_TAGS = "tags"
GROUP_GEOMETRY = "geometry"
GROUP_BOUNDS = "bounds"
GROUP_AUTO = "auto"
GROUP_UNKNOWN = "unknown"


def _auto_label_job_group(job) -> str:
    if getattr(job, "job_kind", None) == crud_auto_label_job.AUTO_SAMPLE_PREANNOTATE_JOB_KIND:
        # The automatic worker fills both tags and geometry; it keeps running
        # on its own and never blocks manual actions, so operators can start a
        # current-image/batch action without stopping it.
        return GROUP_AUTO
    try:
        payload = json.loads(getattr(job, "payload_json", None) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    mode = str((payload or {}).get("mode") or "").strip().upper()
    if mode == "BOX_AND_TAGS":
        return GROUP_GEOMETRY
    if mode == "TAGS_ONLY":
        return GROUP_TAGS
    # Jobs created before the mode was persisted: keep the historical
    # conservative behavior (block everything).
    return GROUP_UNKNOWN


def _job_group(kind: str, job) -> str:
    if kind == "auto_label":
        return _auto_label_job_group(job)
    if kind == "tagtool_preannotate":
        return GROUP_TAGS
    if kind in {
        "yolo_preannotate",
        "locateanything_preannotate",
        "vlx_seek_preannotate",
    }:
        return GROUP_GEOMETRY
    if kind == "bounds_check":
        return GROUP_BOUNDS
    return GROUP_UNKNOWN


def _conflicts(requested_group: str, active_group: str) -> bool:
    if active_group == GROUP_AUTO:
        return False
    if requested_group == GROUP_AUTO:
        return False
    if GROUP_UNKNOWN in {requested_group, active_group}:
        return True
    return requested_group == active_group


def get_active_task_background_job(
    db: Session,
    task_id: int,
    *,
    group: str | None = None,
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
        if not job:
            continue
        active_group = _job_group(kind, job)
        if group is not None and not _conflicts(group, active_group):
            continue
        return ActiveTaskBackgroundJob(
            kind=kind,
            label=TASK_BACKGROUND_JOB_LABELS[kind],
            job_id=int(job.id),
            group=active_group,
        )
    return None


def assert_no_active_task_background_job(
    db: Session,
    task_id: int,
    *,
    group: str = GROUP_UNKNOWN,
) -> None:
    active_job = get_active_task_background_job(db, task_id, group=group)
    if active_job is None:
        return
    raise LabelUException(
        code=ErrorCode.CODE_30002_VALIDATION_ERROR,
        status_code=status.HTTP_400_BAD_REQUEST,
        message=(
            f"当前任务已有{active_job.label}正在运行，请等待完成后再启动"
            "同类型的批量任务（全局标签与目标框批量可并行，但同一类型需串行）"
        ),
    )
