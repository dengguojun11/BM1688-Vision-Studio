import json
from datetime import datetime

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_annotation_review
from labelu.internal.application.command.annotation_review import (
    CreateAnnotationReviewCommand,
    TransitionAnnotationReviewCommand,
)
from labelu.internal.application.response.annotation_review import (
    AnnotationReviewResponse,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import (
    is_task_superuser,
    user_has_task_access,
)
from labelu.internal.common.label_contract import (
    annotation_sha256 as canonical_annotation_sha256,
)
from labelu.internal.domain.models.annotation_review import AnnotationReview
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User


def _error(
    message: str,
    code=ErrorCode.CODE_30003_CLIENT_ERROR,
    http_status=status.HTTP_404_NOT_FOUND,
):
    return LabelUException(code=code, status_code=http_status, message=message)


def _task_or_raise(db: Session, task_id: int, user: User) -> Task:
    task = (
        db.query(Task)
        .filter(Task.id == int(task_id), Task.deleted_at.is_(None))
        .one_or_none()
    )
    if task is None:
        raise _error("任务不存在")
    if not user_has_task_access(user, task):
        raise _error(
            "无权访问该任务的审核记录",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    return task


def _response(row: AnnotationReview) -> AnnotationReviewResponse:
    return AnnotationReviewResponse(
        id=row.id,
        task_id=row.task_id,
        sample_id=row.sample_id,
        annotation_sha256=row.annotation_sha256,
        source_type=row.source_type,
        review_status=row.review_status,
        review_reason=row.review_reason,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        arbitrator_id=row.arbitrator_id,
        arbitrated_at=row.arbitrated_at,
        arbitration_decision=row.arbitration_decision,
        arbitration_reason=row.arbitration_reason,
        invalidated_by=row.invalidated_by,
        invalidated_at=row.invalidated_at,
        invalidation_reason=row.invalidation_reason,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def create(
    db: Session, cmd: CreateAnnotationReviewCommand, user: User
) -> AnnotationReviewResponse:
    task = _task_or_raise(db, cmd.task_id, user)
    sample = (
        db.query(TaskSample)
        .filter(TaskSample.id == cmd.sample_id, TaskSample.deleted_at.is_(None))
        .first()
    )
    if sample is None or sample.task_id != task.id:
        raise _error("任务或样本不存在", http_status=status.HTTP_404_NOT_FOUND)
    # Bind the review to the current annotation snapshot.  Pre-annotation
    # sources may carry their own digest, so only enforce the check for formal
    # annotations where ``TaskSample.data`` is authoritative.
    if cmd.source_type == "FORMAL":
        try:
            current_data = json.loads(sample.data or "{}")
        except (TypeError, ValueError):
            current_data = {}
        if canonical_annotation_sha256(current_data) != cmd.annotation_sha256:
            raise _error(
                "标注已发生变化，请刷新后重新发起审核",
                ErrorCode.CODE_57114_AUTO_TRAIN_REVIEW_HASH_CONFLICT,
                status.HTTP_409_CONFLICT,
            )
    existing = crud_annotation_review.get_snapshot(
        db,
        sample_id=cmd.sample_id,
        annotation_sha256=cmd.annotation_sha256,
        source_type=cmd.source_type,
    )
    if existing:
        return _response(existing)
    row = AnnotationReview(
        task_id=cmd.task_id,
        sample_id=cmd.sample_id,
        annotation_sha256=cmd.annotation_sha256,
        source_type=cmd.source_type,
        review_status="PENDING",
        created_by=user.id,
    )
    with begin_transaction(db):
        crud_annotation_review.create(db, row)
    return _response(row)


async def list_by(
    db: Session,
    *,
    page: int,
    size: int,
    status_filter: str | None,
    user: User,
    search: str | None = None,
):
    rows, total = crud_annotation_review.list_by(
        db,
        page=page,
        size=size,
        user_id=None if is_task_superuser(user) else int(user.id),
        status=status_filter,
        search=search,
    )
    return [_response(row) for row in rows], total


async def get(db: Session, review_id: int, user: User) -> AnnotationReviewResponse:
    row = crud_annotation_review.get(db, review_id)
    if row is None:
        raise _error("审核记录不存在")
    _task_or_raise(db, row.task_id, user)
    return _response(row)


async def transition(
    db: Session, review_id: int, cmd: TransitionAnnotationReviewCommand, user: User
) -> AnnotationReviewResponse:
    row = crud_annotation_review.get(db, review_id)
    if row is None:
        raise _error("审核记录不存在")
    _task_or_raise(db, row.task_id, user)
    if cmd.target_status in {"ARBITRATED", "INVALIDATED"} and not is_task_superuser(
        user
    ):
        raise _error(
            "仅管理人员可执行仲裁或作废",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    current = row.review_status
    allowed = {
        "PENDING": {"IN_REVIEW"},
        "IN_REVIEW": {"APPROVED", "REJECTED", "ARBITRATION"},
        "ARBITRATION": {"ARBITRATED"},
        "APPROVED": {"INVALIDATED"},
        "REJECTED": {"INVALIDATED"},
        "ARBITRATED": {"INVALIDATED"},
        "INVALIDATED": set(),
    }
    target = cmd.target_status
    if target not in allowed.get(current, set()):
        raise _error(
            f"审核状态不能从 {current} 转为 {target}",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if target in {"REJECTED", "ARBITRATION", "INVALIDATED"} and not cmd.reason:
        raise _error(
            "驳回、仲裁和作废必须填写原因",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if target == "ARBITRATED" and cmd.arbitration_decision is None:
        raise _error(
            "仲裁必须给出最终决定",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    now = datetime.now()
    with begin_transaction(db):
        row.review_status = target
        row.updated_at = now
        if target in {"APPROVED", "REJECTED", "ARBITRATION"}:
            row.reviewed_by = user.id
            row.reviewed_at = now
            row.review_reason = cmd.reason
        elif target == "ARBITRATED":
            row.arbitrator_id = user.id
            row.arbitrated_at = now
            row.arbitration_decision = cmd.arbitration_decision
            row.arbitration_reason = cmd.reason
        elif target == "INVALIDATED":
            row.invalidated_by = user.id
            row.invalidated_at = now
            row.invalidation_reason = cmd.reason
        db.flush()
        db.refresh(row)
    return _response(row)
