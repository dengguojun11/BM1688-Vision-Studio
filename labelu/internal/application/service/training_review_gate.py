"""Shared admission checks for third-party samples entering model training."""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy.orm import Session

from labelu.internal.common.label_contract import annotation_sha256
from labelu.internal.domain.models.auto_training import SampleHumanReview
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.sample_edit_log import SampleEditLog
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User


def task_requires_human_review(task: Task | Any) -> bool:
    return str(getattr(task, "source_type", "internal") or "internal").strip() == "third_party"


def current_sample_annotation(sample: TaskSample) -> dict[str, Any] | None:
    try:
        value = json.loads(sample.data or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def current_sample_annotation_hash(sample: TaskSample) -> str | None:
    annotation = current_sample_annotation(sample)
    return annotation_sha256(annotation) if annotation is not None else None


def has_human_annotation_edit(db: Session, *, sample_id: int, annotation_hash: str) -> bool:
    rows = (
        db.query(SampleEditLog, User)
        .join(User, User.id == SampleEditLog.user_id)
        .filter(
            SampleEditLog.sample_id == int(sample_id),
            SampleEditLog.action == "annotation_edit",
            SampleEditLog.new_state == SampleState.DONE.value,
            User.account_type == "human",
            User.is_service_account.is_(False),
            User.interactive_login_enabled.is_(True),
        )
        .order_by(SampleEditLog.created_at.desc(), SampleEditLog.id.desc())
        .all()
    )
    for log, _user in rows:
        try:
            data = json.loads(log.new_data or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and annotation_sha256(data) == annotation_hash:
            return True
    return False


def current_approved_review(
    db: Session, *, sample: TaskSample, require_human_edit: bool = True
) -> SampleHumanReview | None:
    annotation_hash = current_sample_annotation_hash(sample)
    if not annotation_hash:
        return None
    if require_human_edit and not has_human_annotation_edit(
        db, sample_id=int(sample.id), annotation_hash=annotation_hash
    ):
        return None
    return (
        db.query(SampleHumanReview)
        .filter(
            SampleHumanReview.sample_id == int(sample.id),
            SampleHumanReview.annotation_sha256 == annotation_hash,
            SampleHumanReview.review_status == "APPROVED",
            SampleHumanReview.invalidated_at.is_(None),
        )
        .order_by(SampleHumanReview.reviewed_at.desc(), SampleHumanReview.id.desc())
        .first()
    )


def sample_is_trainable(db: Session, *, task: Task, sample: TaskSample) -> bool:
    if not task_requires_human_review(task):
        return True
    return current_approved_review(db, sample=sample) is not None


def filter_trainable_samples(
    db: Session, *, task: Task, samples: Iterable[TaskSample]
) -> list[TaskSample]:
    rows = list(samples)
    if not task_requires_human_review(task):
        return rows
    return [sample for sample in rows if sample_is_trainable(db, task=task, sample=sample)]
